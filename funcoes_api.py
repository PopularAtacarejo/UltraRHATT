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
BRANCH = os.getenv('GITHUB_BRANCH') or os.getenv('BRANCH') or 'main'
FILE_PATH = 'funcoes.json'

def _get_github_token() -> Optional[str]:
    return os.getenv('GITHUB_TOKEN')


def _get_headers() -> dict:
    headers = {'Accept': 'application/vnd.github.v3+json'}
    token = _get_github_token()
    if token:
        headers['Authorization'] = f'token {token}'
    return headers


def _save_local_funcoes(funcoes: List[dict]) -> None:
    try:
        (BASE_DIR / "funcoes.json").write_text(
            json.dumps(funcoes, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass

router = APIRouter(prefix='/api/admin/funcoes', tags=['funcoes'])


class FuncaoBase(BaseModel):
    nome: str = Field(..., min_length=3)
    codigo_cbo: str = Field(..., min_length=1)
    descricao: Optional[str] = ''


def get_funcoes_file() -> tuple[Optional[str], Optional[str]]:
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FILE_PATH}'

    response = requests.get(url, headers=_get_headers(), timeout=15)
    if response.status_code == 200:
        payload = response.json()
        content = base64.b64decode(payload['content']).decode('utf-8')
        return content, payload.get('sha')

    if response.status_code == 404:
        return None, None

    raise RuntimeError('Erro ao acessar o arquivo de funções no GitHub')


def persist_funcoes(funcoes: List[dict], sha: Optional[str], message: str) -> None:
    _save_local_funcoes(funcoes)
    if not _get_github_token():
        enqueue_pending(FILE_PATH, funcoes, message)
        raise RuntimeError('Token do GitHub não configurado')
    payload = json.dumps(funcoes, indent=2, ensure_ascii=False)
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FILE_PATH}'
    data = {
        'message': message,
        'branch': BRANCH,
        'content': base64.b64encode(payload.encode('utf-8')).decode('utf-8')
    }
    if sha:
        data['sha'] = sha

    response = requests.put(url, headers=_get_headers(), json=data, timeout=30)
    if response.status_code not in (200, 201):
        enqueue_pending(FILE_PATH, funcoes, message)
        raise RuntimeError('Não foi possível salvar as funções no GitHub')


def load_funcoes() -> tuple[List[dict], Optional[str]]:
    content, sha = get_funcoes_file()
    if not content:
        return [], sha
    parsed = json.loads(content)
    if not isinstance(parsed, list):
        raise RuntimeError('O arquivo de funções está corrompido')
    return parsed, sha


def sort_funcoes(funcoes: List[dict]) -> List[dict]:
    return sorted(funcoes, key=lambda item: item.get('salvo_em') or '', reverse=True)


@router.get('/')
@router.get('', include_in_schema=False)
async def list_funcoes():
    try:
        funcoes, _ = load_funcoes()
        return {'ok': True, 'count': len(funcoes), 'funcoes': sort_funcoes(funcoes)}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao listar funções')


def ensure_github_token() -> None:
    if not _get_github_token():
        raise HTTPException(status_code=500, detail='Token do GitHub não configurado')


@router.post('/')
@router.post('', include_in_schema=False)
async def create_funcao(payload: FuncaoBase):

    try:
        funcoes, sha = load_funcoes()
        item = payload.dict()
        item['id'] = uuid.uuid4().hex
        item['salvo_em'] = datetime.utcnow().isoformat()
        funcoes.append(item)
        persist_funcoes(funcoes, sha, f"Registrar função: {item.get('nome')}")
        return {'ok': True, 'funcao': item}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao salvar a função')


@router.put('/{funcao_id}')
async def update_funcao(funcao_id: str, payload: FuncaoBase):

    try:
        funcoes, sha = load_funcoes()
        target = next((item for item in funcoes if item.get('id') == funcao_id), None)
        if not target:
            raise HTTPException(status_code=404, detail='Função não encontrada')

        target.update(payload.dict())
        target['atualizado_em'] = datetime.utcnow().isoformat()
        persist_funcoes(funcoes, sha, f"Atualizar função: {target.get('nome')}")
        return {'ok': True, 'funcao': target}
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao atualizar a função')


@router.delete('/{funcao_id}')
async def delete_funcao(funcao_id: str):

    try:
        funcoes, sha = load_funcoes()
        filtered = [item for item in funcoes if item.get('id') != funcao_id]
        if len(filtered) == len(funcoes):
            raise HTTPException(status_code=404, detail='Função não encontrada')

        persist_funcoes(filtered, sha, f"Excluir função: {funcao_id}")
        return {'ok': True}
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail='Erro interno ao excluir a função')


