from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from datetime import datetime

import requests
from dotenv import load_dotenv
from app_paths import APP_DIR, DATA_DIR

load_dotenv(APP_DIR / ".env")
load_dotenv()

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "PopularAtacarejo")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Candidatos")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

BASE_DIR = DATA_DIR
DEFAULT_LOCAL_BASE = Path(os.getenv("LOCAL_BACKUP_DIR") or DATA_DIR)
CANDIDATOS_LOCAL_FALLBACK = DEFAULT_LOCAL_BASE / "candidatos.json"
PENDING_PATH = DATA_DIR / "data" / "sync_pending.json"


@dataclass
class SyncTarget:
    name: str
    remote_path: str
    local_path: Path
    key_fn: Callable[[dict], str]


def _read_pending() -> List[dict]:
    if not PENDING_PATH.exists():
        return []
    try:
        return json.loads(PENDING_PATH.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _write_pending(items: List[dict]) -> None:
    try:
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        PENDING_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def enqueue_pending(path: str, payload: List[dict], message: str) -> None:
    if payload is None:
        return
    items = _read_pending()
    items.append({
        "path": path,
        "payload": payload,
        "message": message,
        "queued_at": datetime.utcnow().isoformat() + "Z",
    })
    _write_pending(items)


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def _read_local(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _write_local(path: Path, payload: List[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_remote(path: str) -> Tuple[List[dict], Optional[str]]:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    response = requests.get(url, headers=_headers(), timeout=15, params={"ref": GITHUB_BRANCH})
    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        raise RuntimeError(f"Erro ao acessar {path}: {response.status_code}")
    payload = response.json()
    content = payload.get("content") or ""
    if not content:
        return [], payload.get("sha")
    decoded = base64.b64decode(content).decode("utf-8")
    data = json.loads(decoded)
    if isinstance(data, list):
        return data, payload.get("sha")
    if isinstance(data, dict):
        return [data], payload.get("sha")
    return [], payload.get("sha")


def _write_remote(path: str, payload: List[dict], sha: Optional[str], message: str) -> None:
    if not GITHUB_TOKEN:
        raise RuntimeError("Token do GitHub não configurado")
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    data = {
        "message": message,
        "branch": GITHUB_BRANCH,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        data["sha"] = sha
    response = requests.put(url, headers=_headers(), json=data, timeout=20)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Falha ao salvar {path} no GitHub")


def _merge_missing(remote: List[dict], local: List[dict], key_fn: Callable[[dict], str]) -> List[dict]:
    seen = {key_fn(item) for item in remote if key_fn(item)}
    merged = list(remote)
    for item in local:
        key = key_fn(item)
        if not key or key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _key_id_or(value: str, item: dict, fallback: str = "") -> str:
    raw = (item.get(value) or "").strip()
    if raw:
        return raw
    return fallback


def _key_funcionario(item: dict) -> str:
    return _key_id_or("id", item, (item.get("cpf") or "").replace(".", "").replace("-", ""))


def _key_candidato(item: dict) -> str:
    return _key_id_or("id", item, (item.get("cpf") or "") + "::" + (item.get("enviado_em") or ""))


def _key_empresa(item: dict) -> str:
    return _key_id_or("id", item, (item.get("cnpj") or "") or (item.get("razao_social") or "") or (item.get("nome_fantasia") or ""))


def _key_simple(item: dict) -> str:
    return _key_id_or("id", item, (item.get("nome") or ""))


def _key_advertencia(item: dict) -> str:
    return _key_id_or("id", item, (item.get("cpf") or "") + "::" + (item.get("data") or item.get("data_adv") or ""))


def _key_reprovado(item: dict) -> str:
    return _key_id_or("id", item, (item.get("cpf") or "") + "::" + (item.get("reprovado_em") or ""))


def _targets() -> List[SyncTarget]:
    base = BASE_DIR
    return [
        SyncTarget("funcionarios_ativos", "funcionarios-ativos.json", base / "funcionarios-ativos.json", _key_funcionario),
        SyncTarget("desligados", "desligados/Ex-funcionarios.json", base / "desligados" / "Ex-funcionarios.json", _key_funcionario),
        SyncTarget("candidatos", "candidatos.json", CANDIDATOS_LOCAL_FALLBACK, _key_candidato),
        SyncTarget("empresas", "empresas.json", base / "empresas.json", _key_empresa),
        SyncTarget("advertencias", "Advertencia/Advertencia.json", base / "Advertencia" / "Advertencia.json", _key_advertencia),
        SyncTarget("funcoes", "funcoes.json", base / "funcoes.json", _key_simple),
        SyncTarget("setores", "setores.json", base / "setores.json", _key_simple),
        SyncTarget("lideres", "lideres.json", base / "data" / "lideres.json", _key_simple),
        SyncTarget("reprovados", "reprovados.json", base / "reprovados.json", _key_reprovado),
    ]


def _target_by_path(path: str) -> Optional[SyncTarget]:
    for target in _targets():
        if target.remote_path == path:
            return target
    return None


def is_sync_target(path: str) -> bool:
    return _target_by_path(path) is not None


def run_startup_sync() -> None:
    pending = _read_pending()
    if pending:
        remaining = []
        for entry in pending:
            path = entry.get("path")
            payload = entry.get("payload")
            message = entry.get("message") or "Sync pendente"
            target = _target_by_path(path)
            if not target or not isinstance(payload, list):
                remaining.append(entry)
                continue
            try:
                remote_items, sha = _read_remote(target.remote_path)
                merged = _merge_missing(remote_items, payload, target.key_fn)
                if merged != remote_items:
                    _write_remote(target.remote_path, merged, sha, message)
                    remote_items = merged
                _write_local(target.local_path, remote_items)
            except Exception as exc:
                print(f"[sync] falha ao enviar pendente {path}: {exc}")
                remaining.append(entry)
        _write_pending(remaining)

    for target in _targets():
        try:
            remote_items, sha = _read_remote(target.remote_path)
        except Exception as exc:
            print(f"[sync] falha ao ler remoto {target.name}: {exc}")
            continue

        local_items = _read_local(target.local_path)
        merged = _merge_missing(remote_items, local_items, target.key_fn)

        # Push missing local items to GitHub if any
        if merged != remote_items:
            try:
                _write_remote(target.remote_path, merged, sha, f"Sync local -> remoto ({target.name})")
                remote_items = merged
            except Exception as exc:
                print(f"[sync] falha ao salvar remoto {target.name}: {exc}")
                # keep local as-is; will retry next startup

        # Update local backup with remote-preferred data
        _write_local(target.local_path, remote_items)


def start_startup_sync_thread() -> None:
    thread = threading.Thread(target=run_startup_sync, daemon=True)
    thread.start()

