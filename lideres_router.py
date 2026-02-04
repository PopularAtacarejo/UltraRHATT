import base64
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from sync_service import enqueue_pending
from app_paths import DATA_DIR
from typing import List, Optional

import requests
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/lideres", tags=["lideres"])

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "PopularAtacarejo")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Candidatos")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_PATH = os.getenv("GITHUB_LIDERES_PATH", "lideres.json")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
}
if GITHUB_TOKEN:
    GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

def _ensure_token() -> None:
    """Compatibilidade: usado por outros módulos."""
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub nÃ£o configurado para salvar os lÃ­deres.")

STORAGE_DIR = DATA_DIR / "data"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
LIDERES_LOCAL_FILE = STORAGE_DIR / "lideres.json"
FUNCIONARIOS_LOCAL_FILE = STORAGE_DIR / "funcionarios_registros.json"


MAX_SETORES_POR_LIDER = 10


class LiderBase(BaseModel):
    nome: str = Field(..., min_length=3)
    email: Optional[str] = ""
    telefone: Optional[str] = ""
    observacoes: Optional[str] = ""
    setores_responsaveis: List[str] = Field(default_factory=list)


class LiderUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None
    telefone: Optional[str] = None
    observacoes: Optional[str] = None
    setores_responsaveis: Optional[List[str]] = None


def _serialize_setores(values: List[str]) -> List[str]:
    sanitized = [item.strip() for item in values if item and item.strip()]
    unique = []
    for item in sanitized:
        normalized = item.capitalize()
        if normalized not in unique:
            unique.append(normalized)
    return unique[:MAX_SETORES_POR_LIDER]


def _get_lideres_file() -> tuple[Optional[str], Optional[str]]:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    try:
        response = requests.get(url, headers=GITHUB_HEADERS, timeout=15, params={"ref": GITHUB_BRANCH})
    except requests.RequestException as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao acessar GitHub: {exc}")

    if response.status_code == 200:
        payload = response.json()
        content = payload.get("content", "")
        sha = payload.get("sha")
        if content:
            decoded = base64.b64decode(content).decode("utf-8")
            return decoded, sha
        return "", sha
    if response.status_code == 404:
        return None, None
    raise HTTPException(status_code=500, detail="Erro ao consultar o arquivo de lÃ­deres no GitHub")


def _persist_lideres(lideres: List[dict], sha: Optional[str], message: str) -> None:
    payload = json.dumps(lideres, indent=2, ensure_ascii=False)
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    body = {
        "message": message,
        "branch": GITHUB_BRANCH,
        "content": base64.b64encode(payload.encode("utf-8")).decode("utf-8")
    }
    if sha:
        body["sha"] = sha
    response = requests.put(url, headers=GITHUB_HEADERS, json=body, timeout=30)
    if response.status_code not in (200, 201):
        enqueue_pending(GITHUB_PATH, lideres, message)
        raise HTTPException(status_code=500, detail="NÃ£o foi possÃ­vel salvar os lÃ­deres no GitHub")


def _load_lideres() -> tuple[List[dict], Optional[str]]:
    content, sha = _get_lideres_file()
    if content is None:
        return [], sha
    if not content:
        return [], sha
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed, sha
        raise HTTPException(status_code=500, detail="Arquivo de lÃ­deres corrompido")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Formato invÃ¡lido do arquivo de lÃ­deres: {exc}")


def _write_local_lideres(lideres: List[dict]) -> None:
    LIDERES_LOCAL_FILE.write_text(json.dumps(lideres, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_funcionarios() -> List[dict]:
    if FUNCIONARIOS_LOCAL_FILE.exists():
        try:
            data = json.loads(FUNCIONARIOS_LOCAL_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/funcionarios-ativos.json"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return []


def _attach_employees(lider: dict) -> dict:
    setores = set((lider.get("setores_responsaveis") or []))
    funcionarios = _load_funcionarios()
    supervised = [
        {
            "id": funcionario.get("id"),
            "nome": funcionario.get("nome_completo"),
            "setor": funcionario.get("setor"),
            "funcao": funcionario.get("funcao"),
            "data_admissao": funcionario.get("data_admissao")
        }
        for funcionario in funcionarios
        if funcionario.get("setor") in setores
    ]
    result = lider.copy()
    result["colaboradores"] = supervised
    result["quantidade_colaboradores"] = len(supervised)
    return result


@router.get("/")
async def list_lideres():
    lideres, _ = _load_lideres()
    sorted_list = sorted(lideres, key=lambda l: l.get("nome", ""))
    return {
        "ok": True,
        "count": len(sorted_list),
        "lideres": sorted_list
    }


@router.get("/{lider_id}")
async def get_lider_detail(lider_id: str):
    lideres, _ = _load_lideres()
    lider = next((item for item in lideres if item.get("id") == lider_id), None)
    if not lider:
        raise HTTPException(status_code=404, detail="LÃ­der nÃ£o encontrado")
    payload = _attach_employees(lider)
    return {"ok": True, "lider": payload}


@router.post("/")
async def create_lider(payload: LiderBase = Body(...)):
    lideres, sha = _load_lideres()
    lider = payload.dict()
    lider["setores_responsaveis"] = _serialize_setores(lider["setores_responsaveis"])
    lider["id"] = uuid.uuid4().hex
    lider["criado_em"] = datetime.utcnow().isoformat() + "Z"
    lider["atualizado_em"] = lider["criado_em"]
    lideres.append(lider)
    _write_local_lideres(lideres)
    _persist_lideres(lideres, sha, f"Criar lÃ­der: {lider['nome']}")
    return {"ok": True, "lid": lider}


@router.put("/{lider_id}")
async def update_lider(lider_id: str, payload: LiderUpdate = Body(...)):
    lideres, sha = _load_lideres()
    lider = next((item for item in lideres if item.get("id") == lider_id), None)
    if not lider:
        raise HTTPException(status_code=404, detail="LÃ­der nÃ£o encontrado")
    update_data = payload.dict(exclude_unset=True)
    if "setores_responsaveis" in update_data:
        update_data["setores_responsaveis"] = _serialize_setores(update_data["setores_responsaveis"])
    lider.update(update_data)
    lider["atualizado_em"] = datetime.utcnow().isoformat() + "Z"
    _write_local_lideres(lideres)
    _persist_lideres(lideres, sha, f"Atualizar lÃ­der: {lider['nome']}")
    return {"ok": True, "lider": lider}


@router.delete("/{lider_id}")
async def delete_lider(lider_id: str):
    lideres, sha = _load_lideres()
    filtered = [item for item in lideres if item.get("id") != lider_id]
    if len(filtered) == len(lideres):
        raise HTTPException(status_code=404, detail="LÃ­der nÃ£o encontrado")
    _write_local_lideres(filtered)
    _persist_lideres(filtered, sha, f"Excluir lÃ­der: {lider_id}")
    return {"ok": True}

