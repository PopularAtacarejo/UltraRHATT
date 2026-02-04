import base64
import binascii
import json
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from pydantic import BaseModel

import requests
from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from lideres_router import (
    _ensure_token as _ensure_leader_token,
    _load_lideres,
    _persist_lideres,
    _serialize_setores,
    _write_local_lideres,
)
from sync_service import enqueue_pending
from app_paths import DATA_DIR

router = APIRouter(prefix="/api/funcionarios", tags=["funcionarios"])

STORAGE_DIR = DATA_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads" / "funcionarios"
STORAGE_FILE = STORAGE_DIR / "funcionarios_registros.json"
FUNCIONARIOS_DATA_FILE = DATA_DIR / "funcionarios-ativos.json"
DESLIGADOS_DIR = DATA_DIR / "desligados"
EX_FUNCIONARIOS_FILE = DESLIGADOS_DIR / "Ex-funcionarios.json"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DESLIGADOS_DIR.mkdir(parents=True, exist_ok=True)

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "PopularAtacarejo")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Candidatos")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_PATH = os.getenv("GITHUB_FUNCIONARIOS_PATH", "funcionarios-ativos.json")
GITHUB_DESLIGADOS_PATH = os.getenv("GITHUB_DESLIGADOS_PATH", "desligados/Ex-funcionarios.json")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

GITHUB_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
}
if GITHUB_TOKEN:
    GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


ENCRYPTION_SCHEMA = "funcionarios.encrypted.v1"
ENCRYPTION_KEY_ENV = "FUNCIONARIOS_ENCRYPTION_KEY"
_ENCRYPTION_KEY_CACHE: bytes | None = None


class EncryptionError(Exception):
    """Erro interno relacionado Ã  chave/objeto de criptografia."""


def _normalize_base64(value: str) -> str:
    padding = -len(value) % 4
    return value + ("=" * padding)


def _load_encryption_key() -> bytes:
    global _ENCRYPTION_KEY_CACHE
    if _ENCRYPTION_KEY_CACHE is not None:
        return _ENCRYPTION_KEY_CACHE

    raw = (os.getenv(ENCRYPTION_KEY_ENV) or "").strip()
    if not raw:
        raise EncryptionError(
            f"VariÃ¡vel {ENCRYPTION_KEY_ENV} nÃ£o estÃ¡ configurada. Defina uma chave AES-128/192/256 em base64, hex ou texto puro."
        )

    decoded = None
    try:
        decoded = base64.urlsafe_b64decode(_normalize_base64(raw))
    except (binascii.Error, ValueError):
        pass

    if decoded and len(decoded) in (16, 24, 32):
        _ENCRYPTION_KEY_CACHE = decoded
        return _ENCRYPTION_KEY_CACHE

    try:
        decoded = bytes.fromhex(raw)
    except ValueError:
        decoded = None

    if decoded and len(decoded) in (16, 24, 32):
        _ENCRYPTION_KEY_CACHE = decoded
        return _ENCRYPTION_KEY_CACHE

    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) in (16, 24, 32):
        _ENCRYPTION_KEY_CACHE = raw_bytes
        return _ENCRYPTION_KEY_CACHE

    raise EncryptionError(
        f"A chave informada em {ENCRYPTION_KEY_ENV} nÃ£o tem 16/24/32 bytes vÃ¡lidos apÃ³s decodificaÃ§Ã£o."
    )


def _get_aesgcm_class():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM
    except ImportError as exc:
        raise EncryptionError(
            "Instale o pacote 'cryptography' para habilitar a criptografia dos funcionÃ¡rios."
        ) from exc


def _is_encrypted_blob(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and data.get("schema") == ENCRYPTION_SCHEMA
        and isinstance(data.get("nonce"), str)
        and isinstance(data.get("payload"), str)
    )


def _encrypt_payload(payload: Any) -> str:
    AESGCM = _get_aesgcm_class()
    key = _load_encryption_key()
    cipher = AESGCM(key)
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ciphertext = cipher.encrypt(nonce, plaintext, None)
    blob = {
        "schema": ENCRYPTION_SCHEMA,
        "nonce": base64.b64encode(nonce).decode("utf-8"),
        "payload": base64.b64encode(ciphertext).decode("utf-8"),
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    return json.dumps(blob, ensure_ascii=False, indent=2)


def _decrypt_payload(blob: dict, context: str) -> Any:
    AESGCM = _get_aesgcm_class()
    key = _load_encryption_key()
    try:
        nonce = base64.b64decode(blob["nonce"])
        ciphertext = base64.b64decode(blob["payload"])
    except (KeyError, binascii.Error, TypeError) as exc:
        raise EncryptionError(f"Blob criptografado invÃ¡lido para {context}: {exc}") from exc

    try:
        cipher = AESGCM(key)
        plaintext = cipher.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise EncryptionError(
            f"Erro ao descriptografar {context}. Verifique a chave e o conteÃºdo armazenado."
        ) from exc

    try:
        return json.loads(plaintext.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise EncryptionError(f"ConteÃºdo descriptografado de {context} nÃ£o Ã© JSON vÃ¡lido: {exc}") from exc


def _decode_github_content(content: str, context: str) -> str:
    try:
        return base64.b64decode(content).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"ConteÃºdo do GitHub para {context} estÃ¡ corrompido: {exc}"
        ) from exc


def _parse_remote_json(raw: str, context: str) -> List[dict]:
    if not raw.strip():
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"ConteÃºdo de {context} no GitHub nÃ£o Ã© JSON vÃ¡lido: {exc}"
        ) from exc

    if _is_encrypted_blob(parsed):
        try:
            decrypted = _decrypt_payload(parsed, context)
        except EncryptionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if isinstance(decrypted, list):
            return decrypted
        raise HTTPException(
            status_code=500,
            detail=f"Data de {context} descriptografada estÃ¡ em formato inesperado"
        )

    if isinstance(parsed, list):
        return parsed

    raise HTTPException(
        status_code=500,
        detail=f"Formato inesperado para {context} no GitHub"
    )


def _fetch_remote_funcionarios():
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    try:
        response = requests.get(
            url,
            headers=GITHUB_HEADERS,
            timeout=10,
            params={"ref": GITHUB_BRANCH}
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao acessar o GitHub: {exc}")

    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao consultar o arquivo {GITHUB_PATH} no GitHub: {response.status_code}"
        )

    payload = response.json()
    content = payload.get("content", "")
    sha = payload.get("sha")

    if not content:
        return [], sha

    decoded = _decode_github_content(content, "funcionarios ativos")
    funcionarios = _parse_remote_json(decoded, "funcionarios ativos")
    return funcionarios, sha


def _write_remote_funcionarios(funcionarios, sha, message):
    _save_local_funcionarios(funcionarios)
    if not GITHUB_TOKEN:
        enqueue_pending(GITHUB_PATH, funcionarios, message)
        raise HTTPException(
            status_code=500,
            detail="Token do GitHub nÃ£o configurado para salvar os funcionÃ¡rios ativos."
        )
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    try:
        serialized = _encrypt_payload(funcionarios)
    except EncryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    payload = {
        "message": message,
        "content": base64.b64encode(serialized.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    try:
        push_response = requests.put(url, headers=GITHUB_HEADERS, json=payload, timeout=10)
    except requests.RequestException as exc:
        enqueue_pending(GITHUB_PATH, funcionarios, message)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar no GitHub: {exc}")
    if push_response.status_code not in (200, 201):
        enqueue_pending(GITHUB_PATH, funcionarios, message)
        raise HTTPException(
            status_code=500,
            detail="NÃ£o foi possÃ­vel atualizar o arquivo de funcionÃ¡rios ativos no GitHub."
        )


def _save_local_funcionarios(funcionarios):
    try:
        FUNCIONARIOS_DATA_FILE.write_text(json.dumps(funcionarios, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Aviso: nÃ£o foi possÃ­vel salvar o arquivo local de funcionÃ¡rios ativos: {exc}")


def _fetch_remote_desligados():
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_DESLIGADOS_PATH}"
    try:
        response = requests.get(
            url,
            headers=GITHUB_HEADERS,
            timeout=10,
            params={"ref": GITHUB_BRANCH}
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao acessar o GitHub (desligados): {exc}")

    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao consultar o arquivo {GITHUB_DESLIGADOS_PATH} no GitHub: {response.status_code}"
        )

    payload = response.json()
    content = payload.get("content", "")
    sha = payload.get("sha")

    if not content:
        return [], sha

    decoded = _decode_github_content(content, "desligados")
    entries = _parse_remote_json(decoded, "desligados")
    return entries, sha


def _write_remote_desligados(entries, sha, message):
    _save_local_desligados(entries)
    if not GITHUB_TOKEN:
        enqueue_pending(GITHUB_DESLIGADOS_PATH, entries, message)
        raise HTTPException(
            status_code=500,
            detail="Token do GitHub nÃ£o configurado para salvar os desligados."
        )
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_DESLIGADOS_PATH}"
    try:
        serialized = _encrypt_payload(entries)
    except EncryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    payload = {
        "message": message,
        "content": base64.b64encode(serialized.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    try:
        push_response = requests.put(url, headers=GITHUB_HEADERS, json=payload, timeout=10)
    except requests.RequestException as exc:
        enqueue_pending(GITHUB_DESLIGADOS_PATH, entries, message)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar desligados no GitHub: {exc}")
    if push_response.status_code not in (200, 201):
        enqueue_pending(GITHUB_DESLIGADOS_PATH, entries, message)
        raise HTTPException(
            status_code=500,
            detail="NÃ£o foi possÃ­vel atualizar o arquivo de desligados no GitHub."
        )


def _save_local_desligados(entries):
    try:
        EX_FUNCIONARIOS_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Aviso: nÃ£o foi possÃ­vel salvar o arquivo local de desligados: {exc}")


def _append_to_desligados(record: dict):
    entries, sha = _fetch_remote_desligados()
    entries.append(record)
    _write_remote_desligados(entries, sha, f"Registra desligamento de {record.get('nome_completo', '')}")
    _save_local_desligados(entries)


def _persist_record(record: dict) -> None:
    existing = []
    if STORAGE_FILE.exists():
        try:
            existing = json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            existing = []
    existing.append(record)
    STORAGE_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


async def _save_uploaded_photo(file: UploadFile) -> Optional[str]:
    if not file or not file.filename:
        return None
    extension = Path(file.filename).suffix
    filename = f"{uuid.uuid4().hex}{extension}"
    destination = UPLOAD_DIR / filename
    contents = await file.read()
    destination.write_bytes(contents)
    return str(destination)


def _push_to_github(record: dict) -> None:
    funcionarios, sha = _fetch_remote_funcionarios()
    funcionarios.append(record)
    _write_remote_funcionarios(funcionarios, sha, "Atualiza lista de funcionÃ¡rios ativos")
    _save_local_funcionarios(funcionarios)

def _register_leader_from_record(record: dict) -> None:
    if not record.get("lider_gestor"):
        return

    try:
        _ensure_leader_token()
        lideres, sha = _load_lideres()
        name = (record.get("nome_completo") or "").strip()
        if not name:
            return
        setores = _serialize_setores([record.get("setor") or ""])
        timestamp = datetime.utcnow().isoformat() + "Z"
        existing = next(
            (item for item in lideres if (item.get("nome") or "").strip().lower() == name.lower()),
            None
        )
        if existing:
            combined = _serialize_setores((existing.get("setores_responsaveis") or []) + setores)
            existing["setores_responsaveis"] = combined
            existing["atualizado_em"] = timestamp
            _write_local_lideres(lideres)
            _persist_lideres(lideres, sha, f"Atualizar lÃ­der automÃ¡tico: {name}")
            return

        lider = {
            "id": uuid.uuid4().hex,
            "nome": name,
            "email": "",
            "telefone": "",
            "observacoes": "Criado automaticamente via cadastro de colaboradores.",
            "setores_responsaveis": setores,
            "criado_em": timestamp,
            "atualizado_em": timestamp,
        }
        lideres.append(lider)
        _write_local_lideres(lideres)
        _persist_lideres(lideres, sha, f"Criar lÃ­der automÃ¡tico: {name}")
    except HTTPException as exc:
        print(f"Falha ao registrar lÃ­der automÃ¡tico: {exc.detail}")
    except Exception as exc:
        print(f"Erro inesperado ao registrar lÃ­der automÃ¡tico: {exc}")


class FuncionarioUpdatePayload(BaseModel):
    nome_completo: Optional[str] = None
    cpf: Optional[str] = None
    data_nascimento: Optional[str] = None
    naturalidade: Optional[str] = None
    sexo: Optional[str] = None
    rg: Optional[str] = None
    pis: Optional[str] = None
    empresa: Optional[str] = None
    setor: Optional[str] = None
    funcao: Optional[str] = None
    cbo: Optional[str] = None
    matricula: Optional[str] = None
    data_admissao: Optional[str] = None
    salario: Optional[str] = None
    lider_responsavel: Optional[str] = None
    lider_gestor: Optional[bool] = None
    em_experiencia: Optional[bool] = None
    cep: Optional[str] = None
    rua: Optional[str] = None
    numero: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    complemento: Optional[str] = None
    filhos: Optional[List[str]] = None
    experiencias: Optional[List[str]] = None
    status: Optional[str] = None
    situacao: Optional[str] = None
    observacoes: Optional[str] = None
    updated_by: Optional[str] = None


class DesligamentoPayload(BaseModel):
    motivo: Optional[str] = None
    data_saida: Optional[str] = None
    observacoes: Optional[str] = None


@router.post("/")
async def registrar_funcionario(
    nome_completo: str = Form(...),
    cpf: str = Form(...),
    data_nascimento: str = Form(...),
    naturalidade: str = Form(...),
    sexo: str = Form(...),
    rg: str = Form(...),
    pis: str = Form(...),
    tamanho_fardamento: Optional[str] = Form(None),
    tamanho_calcado: Optional[str] = Form(None),
    foto: Optional[UploadFile] = File(None),
    empresa: str = Form(...),
    setor: str = Form(...),
    funcao: str = Form(...),
    cbo: str = Form(...),
    matricula: str = Form(...),
    data_admissao: str = Form(...),
    salario: str = Form(...),
    lider_responsavel: Optional[str] = Form(None),
    lider_gestor: Optional[str] = Form(None),
    em_experiencia: Optional[str] = Form(None),
    cep: Optional[str] = Form(None),
    rua: Optional[str] = Form(None),
    numero: Optional[str] = Form(None),
    bairro: Optional[str] = Form(None),
    cidade: Optional[str] = Form(None),
    estado: Optional[str] = Form(None),
    complemento: Optional[str] = Form(None),
    filhos: Optional[List[str]] = Form(None),
    experiencias: Optional[List[str]] = Form(None),
):
    cpf_clean = "".join(filter(str.isdigit, cpf))
    if len(cpf_clean) != 11:
        raise HTTPException(status_code=400, detail="CPF invÃ¡lido")

    lider_flag = str(lider_gestor).lower() in {"true", "on", "1", "sim"}
    lider_nome = (lider_responsavel or "").strip()

    if not lider_flag and not lider_nome:
        raise HTTPException(status_code=400, detail="Informe o lÃ­der responsÃ¡vel para este colaborador.")

    record = {
        "id": uuid.uuid4().hex,
        "nome_completo": nome_completo.strip(),
        "cpf": cpf_clean,
        "data_nascimento": data_nascimento,
        "naturalidade": naturalidade.strip(),
        "sexo": sexo,
        "rg": rg.strip(),
        "pis": pis.strip(),
        "tamanho_fardamento": (tamanho_fardamento or "").strip(),
        "tamanho_calcado": (tamanho_calcado or "").strip(),
        "empresa": empresa.strip(),
        "setor": setor.strip(),
        "funcao": funcao.strip(),
        "cbo": cbo.strip(),
        "matricula": matricula.strip(),
        "data_admissao": data_admissao,
        "salario": salario.strip(),
        "lider_responsavel": lider_nome,
        "lider_gestor": lider_flag,
        "em_experiencia": str(em_experiencia).lower() in {"true", "on", "1", "sim"},
        "cep": (cep or "").strip(),
        "rua": (rua or "").strip(),
        "numero": (numero or "").strip(),
        "bairro": (bairro or "").strip(),
        "cidade": (cidade or "").strip(),
        "estado": (estado or "").strip(),
        "complemento": (complemento or "").strip(),
        "filhos": [(data or "").strip() for data in filhos or [] if data],
        "experiencias": [(item or "").strip() for item in experiencias or [] if item and (item or "").strip()],
        "enviado_em": datetime.utcnow().isoformat() + "Z",
        "foto_path": None,
    }

    if foto:
        try:
            path = await _save_uploaded_photo(foto)
            record["foto_path"] = path
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Erro ao salvar a foto: {exc}")

    _persist_record(record)
    _push_to_github(record)
    _register_leader_from_record(record)

    return {
        "ok": True,
        "message": "Colaborador registrado com sucesso!",
        "id": record["id"],
    }


@router.put("/{funcionario_id}")
async def atualizar_funcionario(
    funcionario_id: str,
    payload: FuncionarioUpdatePayload = Body(...),
):
    funcionarios, sha = _fetch_remote_funcionarios()
    target = next((item for item in funcionarios if item.get("id") == funcionario_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="FuncionÃ¡rio nÃ£o encontrado.")

    updates = payload.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo enviado para atualizaÃ§Ã£o.")

    observacoes_raw = updates.pop("observacoes", None)
    observacoes = observacoes_raw.strip() if isinstance(observacoes_raw, str) else None
    updated_by = updates.pop("updated_by", None) or "Sistema"
    sanitized = {}
    for key, value in updates.items():
        if isinstance(value, str):
            sanitized[key] = value.strip()
        else:
            sanitized[key] = value

    if "cpf" in sanitized:
        cpf_digits = "".join(filter(str.isdigit, sanitized["cpf"]))
        if len(cpf_digits) != 11:
            raise HTTPException(status_code=400, detail="CPF invÃ¡lido")
        sanitized["cpf"] = cpf_digits

    if "filhos" in sanitized:
        sanitized["filhos"] = [(item or "").strip() for item in sanitized["filhos"] if item]
    if "experiencias" in sanitized:
        sanitized["experiencias"] = [(item or "").strip() for item in sanitized["experiencias"] if item]

    target.update(sanitized)
    timestamp = datetime.utcnow().isoformat() + "Z"
    target["atualizado_em"] = timestamp
    target["atualizado_por"] = updated_by

    changed_fields = sorted(sanitized.keys())
    history_description = observacoes or (
        f"Campos atualizados: {', '.join(changed_fields)}" if changed_fields else "AtualizaÃ§Ã£o registrada"
    )
    history_entry = {
        "data": timestamp,
        "autor": updated_by,
        "descricao": history_description,
        "campos": changed_fields,
    }
    historico = target.get("historico") or []
    historico.append(history_entry)
    target["historico"] = historico

    _persist_record({
        "tipo": "funcionario_atualizado",
        "funcionario_id": funcionario_id,
        "autor": updated_by,
        "atualizado_em": timestamp,
        "campos": changed_fields,
        "observacoes": observacoes or "",
    })

    _write_remote_funcionarios(funcionarios, sha, f"Atualiza colaborador {target.get('nome_completo', '')}")
    _save_local_funcionarios(funcionarios)

    if target.get("lider_gestor"):
        _register_leader_from_record(target)

    return {
        "ok": True,
        "message": "Colaborador atualizado com sucesso!",
        "funcionario": target,
    }


@router.post("/{funcionario_identifier}/desligar")
async def desligar_funcionario(
    funcionario_identifier: str,
    payload: DesligamentoPayload = Body(...),
):
    funcionarios, sha = _fetch_remote_funcionarios()
    target = _find_funcionario_by_identifier(funcionarios, funcionario_identifier)
    if not target:
        raise HTTPException(status_code=404, detail="FuncionÃ¡rio nÃ£o encontrado.")

    funcionarios = [item for item in funcionarios if item is not target]
    timestamp = datetime.utcnow().isoformat() + "Z"
    departure_date = payload.data_saida or target.get("data_saida") or datetime.utcnow().date().isoformat()
    motive = (payload.motivo or target.get("motivo_desligamento") or target.get("motivo") or "Desligamento").strip()
    desligamento = {
        **target,
        "motivo_desligamento": motive,
        "data_saida": departure_date,
        "observacoes_desligamento": (payload.observacoes or "").strip(),
        "desligado_em": timestamp,
    }

    _write_remote_funcionarios(funcionarios, sha, f"Desliga colaborador {target.get('nome_completo', '')}")
    _save_local_funcionarios(funcionarios)
    _append_to_desligados(desligamento)

    return {"ok": True, "desligado": desligamento}


def _load_funcionarios() -> List[dict]:
    if FUNCIONARIOS_DATA_FILE.exists():
        try:
            data = json.loads(FUNCIONARIOS_DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/funcionarios-ativos.json"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            try:
                return _parse_remote_json(response.text, "funcionarios ativos (raw)")
            except HTTPException as exc:
                print(f"Aviso ao carregar funcionarios ativos diretamente: {exc.detail}")
                return []
    except requests.RequestException:
        pass
    return []


def _find_funcionario_by_identifier(funcionarios: List[dict], identifier: str) -> Optional[dict]:
    if not identifier:
        return None
    target = next((item for item in funcionarios if item.get("id") == identifier), None)
    if target:
        return target
    digits = "".join(filter(str.isdigit, identifier))
    if digits:
        target = next(
            (item for item in funcionarios if "".join(filter(str.isdigit, item.get("cpf", ""))) == digits),
            None
        )
        if target:
            return target
    stripped = identifier.strip()
    if stripped:
        target = next((item for item in funcionarios if (item.get("matricula") or "").strip() == stripped), None)
        if target:
            return target
    return None


@router.get("/")
async def list_funcionarios():
    funcionarios = _load_funcionarios()
    ordered = sorted(funcionarios, key=lambda f: (f.get("nome_completo") or "").lower())
    return {"ok": True, "count": len(ordered), "funcionarios": ordered}


@router.get("", include_in_schema=False)
async def list_funcionarios_without_trailing_slash():
    return await list_funcionarios()

