import base64
import json
import os
import uuid
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from app_paths import APP_DIR, DATA_DIR
from sync_service import enqueue_pending

load_dotenv(APP_DIR / ".env")
load_dotenv()

BASE_DIR = DATA_DIR

GITHUB_OWNER = os.getenv('GITHUB_OWNER') or 'PopularAtacarejo'
GITHUB_REPO = os.getenv('GITHUB_REPO') or 'Candidatos'
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
BRANCH = os.getenv('GITHUB_BRANCH') or os.getenv('BRANCH') or 'main'
FILE_PATH = 'setores.json'

headers = {'Accept': 'application/vnd.github.v3+json'}
if GITHUB_TOKEN:
    headers['Authorization'] = f'token {GITHUB_TOKEN}'

router = APIRouter(prefix='/api/admin/setores', tags=['setores'])


class SetorBase(BaseModel):
    nome: str = Field(..., min_length=3)
    responsavel: Optional[str] = ""
    descricao: Optional[str] = ""


class SetorUpdate(BaseModel):
    nome: Optional[str] = Field(None, min_length=3)
    responsavel: Optional[str] = Field(None)
    descricao: Optional[str] = None


def get_setores_file() -> tuple[Optional[str], Optional[str]]:
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FILE_PATH}'

    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code == 200:
        payload = response.json()
        content = base64.b64decode(payload['content']).decode('utf-8')
        return content, payload.get('sha')
    if response.status_code == 404:
        return None, None
    raise RuntimeError('Erro ao acessar o arquivo de setores no GitHub')


def persist_setores(setores: List[dict], sha: Optional[str], message: str) -> None:
    try:
        if not os.getenv('GITHUB_TOKEN'):
            enqueue_pending(FILE_PATH, setores, message)
            raise RuntimeError('Token do GitHub não configurado')
        (BASE_DIR / "setores.json").write_text(
            json.dumps(setores, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass
    payload = json.dumps(setores, indent=2, ensure_ascii=False)
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FILE_PATH}'
    data = {
        'message': message,
        'branch': BRANCH,
        'content': base64.b64encode(payload.encode('utf-8')).decode('utf-8')
    }
    if sha:
        data['sha'] = sha

    response = requests.put(url, headers=headers, json=data, timeout=30)
    if response.status_code not in (200, 201):
        enqueue_pending(FILE_PATH, setores, message)
        raise RuntimeError('Não foi possível salvar os setores no GitHub')


def load_setores() -> tuple[List[dict], Optional[str]]:
    content, sha = get_setores_file()
    if not content:
        return [], sha
    parsed = json.loads(content)
    if not isinstance(parsed, list):
        raise RuntimeError('O arquivo de setores está corrompido')
    return parsed, sha


def sort_setores(setores: List[dict]) -> List[dict]:
    return sorted(setores, key=lambda item: item.get('salvo_em') or '', reverse=True)


def find_setor_index(setores: List[dict], setor_id: str) -> Optional[int]:
    for idx, setor in enumerate(setores):
        if setor.get('id') == setor_id:
            return idx
    return None


@router.get('/')
async def list_setores():
    try:
        setores, _ = load_setores()
        return {'ok': True, 'count': len(setores), 'setores': sort_setores(setores)}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao listar setores')


@router.post('/')
async def create_setor(payload: SetorBase):

    try:
        setores, sha = load_setores()
        item = payload.dict()
        item['id'] = uuid.uuid4().hex
        item['salvo_em'] = datetime.utcnow().isoformat()
        setores.append(item)
        persist_setores(setores, sha, f"Registrar setor: {item.get('nome')}")
        return {'ok': True, 'setor': item}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao salvar o setor')


@router.put('/{setor_id}')
async def update_setor(setor_id: str, payload: SetorUpdate):

    try:
        setores, sha = load_setores()
        index = find_setor_index(setores, setor_id)
        if index is None:
            raise HTTPException(status_code=404, detail='Setor não encontrado')

        setor = setores[index]
        updated = False

        if payload.nome:
            setor['nome'] = payload.nome.strip()
            updated = True
        if payload.responsavel:
            setor['responsavel'] = payload.responsavel.strip()
            updated = True
        if payload.descricao is not None:
            setor['descricao'] = payload.descricao.strip()
            updated = True

        if not updated:
            raise HTTPException(status_code=400, detail='Nenhuma alteração enviada')

        setor['atualizado_em'] = datetime.utcnow().isoformat()

        persist_setores(setores, sha, f"Atualizar setor: {setor.get('nome')}")
        return {'ok': True, 'setor': setor}
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao atualizar o setor')


@router.delete('/{setor_id}')
async def delete_setor(setor_id: str):

    try:
        setores, sha = load_setores()
        index = find_setor_index(setores, setor_id)
        if index is None:
            raise HTTPException(status_code=404, detail='Setor não encontrado')

        setor = setores.pop(index)
        persist_setores(setores, sha, f"Excluir setor: {setor.get('nome')}")
        return {'ok': True, 'setor_id': setor_id}
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao excluir o setor')

