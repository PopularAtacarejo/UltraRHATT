from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple, List

import base64
import json
import os
import requests
from fastapi import APIRouter, HTTPException
from dotenv import load_dotenv
from app_paths import APP_DIR, DATA_DIR
from sync_service import enqueue_pending

from funcionarios_router import (
    _fetch_remote_funcionarios,
    _write_remote_funcionarios,
    _save_local_funcionarios,
    _find_funcionario_by_identifier,
)

load_dotenv(APP_DIR / ".env")
load_dotenv()

router = APIRouter(prefix="/api/experiencia", tags=["experiencia"])

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "PopularAtacarejo")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Candidatos")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPROVADOS_PATH = "reprovados.json"
BASE_DIR = DATA_DIR


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _build_experience_entry(funcionario: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    admission = _parse_date(funcionario.get("data_admissao"))
    if not admission:
        return None
    today = date.today()
    days = (today - admission).days
    if days < 0:
        days = 0

    phase = "fase1" if days <= 45 else "fase2" if days <= 90 else "encerrada"
    remaining_45 = max(45 - days, 0)
    remaining_90 = max(90 - days, 0)
    prova1_in = max(40 - days, 0)
    prova2_in = max(85 - days, 0)

    return {
        "id": funcionario.get("id"),
        "nome_completo": funcionario.get("nome_completo"),
        "cpf": funcionario.get("cpf"),
        "setor": funcionario.get("setor"),
        "funcao": funcionario.get("funcao"),
        "empresa": funcionario.get("empresa"),
        "data_admissao": admission.isoformat(),
        "dias_experiencia": days,
        "fase": phase,
        "resta_45": remaining_45,
        "resta_90": remaining_90,
        "prova1_em": prova1_in,
        "prova2_em": prova2_in,
        "raw": funcionario,
    }


def _load_local_funcionarios() -> List[Dict[str, Any]]:
    local_path = BASE_DIR / "funcionarios-ativos.json"
    if not local_path.exists():
        return []
    try:
        payload = json.loads(local_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
    except json.JSONDecodeError:
        pass
    return []

def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def _load_reprovados() -> Tuple[List[dict], Optional[str]]:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{REPROVADOS_PATH}"
    response = requests.get(url, headers=_github_headers(), timeout=15, params={"ref": GITHUB_BRANCH})
    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Erro ao acessar reprovados.json no GitHub.")
    payload = response.json()
    content = payload.get("content") or ""
    if not content:
        return [], payload.get("sha")
    decoded = base64.b64decode(content).decode("utf-8")
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"reprovados.json invalido: {exc}")
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="reprovados.json em formato inesperado.")
    return data, payload.get("sha")


def _save_reprovados(items: List[dict], sha: Optional[str], message: str) -> None:
    if not GITHUB_TOKEN:
        enqueue_pending(REPROVADOS_PATH, items, message)
        raise HTTPException(status_code=500, detail="Token do GitHub nao configurado.")
    try:
        (BASE_DIR / "reprovados.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{REPROVADOS_PATH}"
    content = json.dumps(items, ensure_ascii=False, indent=2)
    payload = {
        "message": message,
        "branch": GITHUB_BRANCH,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha
    response = requests.put(url, headers=_github_headers(), json=payload, timeout=20)
    if response.status_code not in (200, 201):
        enqueue_pending(REPROVADOS_PATH, items, message)
        raise HTTPException(status_code=500, detail="Falha ao salvar reprovados.json no GitHub.")


@router.get("/")
async def list_experiencia():
    try:
        funcionarios, _ = _fetch_remote_funcionarios()
    except HTTPException:
        funcionarios = _load_local_funcionarios()
    results = []
    for item in funcionarios:
        if not item.get("em_experiencia"):
            continue
        entry = _build_experience_entry(item)
        if entry:
            results.append(entry)
    results.sort(key=lambda item: item.get("dias_experiencia", 0), reverse=True)
    return {"ok": True, "count": len(results), "experiencia": results}


@router.get("/reprovados")
async def list_reprovados():
    reprovados, _ = _load_reprovados()
    return {"ok": True, "count": len(reprovados), "reprovados": reprovados}


@router.post("/{identifier}/efetivar")
async def efetivar_funcionario(identifier: str):
    funcionarios, sha = _fetch_remote_funcionarios()
    target = _find_funcionario_by_identifier(funcionarios, identifier)
    if not target:
        raise HTTPException(status_code=404, detail="Funcionario nao encontrado.")

    target["em_experiencia"] = False
    target["situacao"] = "efetivado"
    target["efetivado_em"] = datetime.utcnow().isoformat() + "Z"

    _write_remote_funcionarios(funcionarios, sha, f"Efetivar funcionario {target.get('nome_completo', '')}")
    _save_local_funcionarios(funcionarios)
    return {"ok": True, "funcionario": target}


@router.post("/{identifier}/desligar")
async def desligar_funcionario(identifier: str):
    funcionarios, sha = _fetch_remote_funcionarios()
    target = _find_funcionario_by_identifier(funcionarios, identifier)
    if not target:
        raise HTTPException(status_code=404, detail="Funcionario nao encontrado.")

    target["em_experiencia"] = False
    target["situacao"] = "desligado"
    target["data_saida"] = datetime.utcnow().date().isoformat()
    target["desligado_em"] = datetime.utcnow().isoformat() + "Z"

    _write_remote_funcionarios(funcionarios, sha, f"Desligar funcionario {target.get('nome_completo', '')}")
    _save_local_funcionarios(funcionarios)
    return {"ok": True, "funcionario": target}


@router.post("/{identifier}/reprovar")
async def reprovar_funcionario(identifier: str, payload: Dict[str, Any]):
    motivo = (payload.get("motivo") or "").strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="Informe o motivo da reprovacao.")

    funcionarios, sha = _fetch_remote_funcionarios()
    target = _find_funcionario_by_identifier(funcionarios, identifier)
    if not target:
        raise HTTPException(status_code=404, detail="Funcionario nao encontrado.")

    entry = _build_experience_entry(target) or {}
    fase = entry.get("fase") or "fase1"
    data_admissao = entry.get("data_admissao") or target.get("data_admissao")
    dias = entry.get("dias_experiencia") or 0

    reprovados, sha_rep = _load_reprovados()
    reprovado = {
        "id": target.get("id"),
        "nome_completo": target.get("nome_completo"),
        "cpf": target.get("cpf"),
        "empresa": target.get("empresa"),
        "setor": target.get("setor"),
        "funcao": target.get("funcao"),
        "data_admissao": data_admissao,
        "dias_experiencia": dias,
        "fase": fase,
        "motivo": motivo,
        "reprovado_em": datetime.utcnow().isoformat() + "Z",
        "raw": target,
    }
    reprovados.append(reprovado)
    _save_reprovados(reprovados, sha_rep, f"Reprovar funcionario {target.get('nome_completo', '')}")

    funcionarios = [item for item in funcionarios if item is not target]
    _write_remote_funcionarios(funcionarios, sha, f"Remover funcionario reprovado {target.get('nome_completo', '')}")
    _save_local_funcionarios(funcionarios)

    return {"ok": True, "reprovado": reprovado}
