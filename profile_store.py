import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_OWNER = os.getenv("GITHUB_OWNER") or "PopularAtacarejo"
GITHUB_REPO = os.getenv("GITHUB_REPO") or "Candidatos"
AUTH_FILE_PATH = os.getenv("AUTH_FILE_PATH") or "auth.json"
PHOTOS_DIR = "foto"
PHOTOS_INDEX_PATH = f"{PHOTOS_DIR}/fotos.json"

HEADERS = {"Accept": "application/vnd.github.v3+json"}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def _get_repo_default_branch() -> str:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get("default_branch", "main")
    except Exception:
        pass
    return "main"


BRANCH = os.getenv("GITHUB_BRANCH") or _get_repo_default_branch()
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}"

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _sanitize_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", (value or "").strip().lower())
    return cleaned or "usuario"


def _decode_github_content(payload: Dict[str, Any]) -> Any:
    content = payload.get("content")
    if not content:
        return None
    decoded = base64.b64decode(content).decode("utf-8")
    return json.loads(decoded)


def fetch_github_file(path: str) -> Optional[dict]:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def load_auth_users() -> List[dict]:
    payload = fetch_github_file(AUTH_FILE_PATH)
    if not payload:
        return []
    decoded = _decode_github_content(payload)
    if isinstance(decoded, list):
        return decoded
    return []


def save_auth_users(users: List[dict], message: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{AUTH_FILE_PATH}"
    content = json.dumps(users, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    current = fetch_github_file(AUTH_FILE_PATH)
    sha = current.get("sha") if current else None
    data = {
        "message": message,
        "content": content_b64,
        "branch": BRANCH,
    }
    if sha:
        data["sha"] = sha
    try:
        response = requests.put(url, headers=HEADERS, json=data, timeout=30)
        return response.status_code in [200, 201]
    except Exception:
        return False


def _upload_github_file(path: str, content_bytes: bytes, message: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    content_b64 = base64.b64encode(content_bytes).decode("utf-8")
    current = fetch_github_file(path)
    sha = current.get("sha") if current else None
    data = {"message": message, "content": content_b64, "branch": BRANCH}
    if sha:
        data["sha"] = sha
    try:
        response = requests.put(url, headers=HEADERS, json=data, timeout=30)
        return response.status_code in [200, 201]
    except Exception:
        return False


def _delete_github_file(path: str, message: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    current = fetch_github_file(path)
    if not current:
        return False
    sha = current.get("sha")
    data = {"message": message, "sha": sha, "branch": BRANCH}
    try:
        response = requests.delete(url, headers=HEADERS, json=data, timeout=30)
        return response.status_code in [200, 204]
    except Exception:
        return False


def load_photos_index() -> List[dict]:
    payload = fetch_github_file(PHOTOS_INDEX_PATH)
    if not payload:
        return []
    decoded = _decode_github_content(payload)
    if isinstance(decoded, list):
        return decoded
    return []


def save_photos_index(items: List[dict], message: str) -> bool:
    content = json.dumps(items, indent=2, ensure_ascii=False)
    return _upload_github_file(PHOTOS_INDEX_PATH, content.encode("utf-8"), message)


def strip_user_sensitive(user: dict) -> dict:
    clean = dict(user)
    clean.pop("senha", None)
    return clean


def find_user(email: str, users: Optional[List[dict]] = None) -> Tuple[Optional[dict], List[dict]]:
    email_norm = (email or "").strip().lower()
    users = users if users is not None else load_auth_users()
    for user in users:
        if (user.get("email") or "").lower() == email_norm:
            return user, users
    return None, users


def update_user(email: str, updates: dict) -> Tuple[Optional[dict], List[dict]]:
    email_norm = (email or "").strip().lower()
    users = load_auth_users()
    updated = None
    for user in users:
        if (user.get("email") or "").lower() == email_norm:
            user.update({k: v for k, v in updates.items() if v is not None})
            updated = user
            break
    return updated, users


def _safe_photo_filename(email: str, original_name: str) -> str:
    ext = Path(original_name or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".jpg"
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{_sanitize_id(email)}_{stamp}{ext}"


def save_profile_photo(email: str, file_bytes: bytes, original_name: str) -> str:
    filename = _safe_photo_filename(email, original_name)
    remote_path = f"{PHOTOS_DIR}/{filename}"
    ok = _upload_github_file(remote_path, file_bytes, f"Atualizar foto do perfil {email}")
    if not ok:
        raise RuntimeError("Falha ao enviar foto para o GitHub.")
    return f"{RAW_BASE}/{remote_path}"


def remove_profile_photo(photo_path: str) -> None:
    if not photo_path:
        return
    if photo_path.startswith(RAW_BASE):
        rel = photo_path.replace(f"{RAW_BASE}/", "", 1)
    else:
        rel = photo_path.lstrip("/")
    if rel.startswith(f"{PHOTOS_DIR}/"):
        _delete_github_file(rel, f"Remover foto {rel}")


def update_photos_index(email: str, name: str, photo_url: str) -> None:
    items = load_photos_index()
    now = datetime.now().isoformat()
    email_norm = (email or "").strip().lower()
    updated = False
    for item in items:
        if (item.get("email") or "").lower() == email_norm:
            item["email"] = email_norm
            item["nome"] = name or item.get("nome") or ""
            item["foto_url"] = photo_url
            item["updated_at"] = now
            updated = True
            break
    if not updated:
        items.append(
            {
                "email": email_norm,
                "nome": name or "",
                "foto_url": photo_url,
                "updated_at": now,
            }
        )
    save_photos_index(items, f"Atualizar fotos.json para {email_norm}")


def remove_from_photos_index(email: str) -> None:
    items = load_photos_index()
    email_norm = (email or "").strip().lower()
    filtered = [item for item in items if (item.get("email") or "").lower() != email_norm]
    if len(filtered) != len(items):
        save_photos_index(filtered, f"Remover foto index {email_norm}")
