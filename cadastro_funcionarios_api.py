"""
Script independente para receber cadastros de funcionários e armazenar os dados localmente.
Roda um servidor FastAPI com um único endpoint `/api/funcionarios`.
"""

import base64
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv
from app_paths import APP_DIR, DATA_DIR

STORAGE_DIR = DATA_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads" / "funcionarios"
STORAGE_FILE = STORAGE_DIR / "funcionarios_registros.json"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(APP_DIR / ".env")
load_dotenv()

GITHUB_LIDERES_PATH = os.getenv("GITHUB_LIDERES_PATH", "lideres.json")
MAX_SETORES_POR_LIDER = 10
LIDERES_LOCAL_FILE = STORAGE_DIR / "lideres.json"

app = FastAPI(title="Cadastro de Funcionários", docs_url="/cadastro-docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "PopularAtacarejo")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Candidatos")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_PATH = os.getenv("GITHUB_FUNCIONARIOS_PATH", "funcionarios-ativos.json")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

GITHUB_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
}
if GITHUB_TOKEN:
    GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


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


def _serialize_setores(values: list) -> list:
    sanitized = [item.strip() for item in values if item and item.strip()]
    unique = []
    for item in sanitized:
        normalized = item.capitalize()
        if normalized not in unique:
            unique.append(normalized)
        if len(unique) >= MAX_SETORES_POR_LIDER:
            break
    return unique


def _write_local_lideres(lideres: list) -> None:
    try:
        LIDERES_LOCAL_FILE.write_text(json.dumps(lideres, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print("Aviso: não foi possível salvar o arquivo local de líderes:", exc)


def _fetch_lideres_from_github() -> tuple[str, Optional[str]]:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_LIDERES_PATH}"
    try:
        response = requests.get(url, headers=GITHUB_HEADERS, timeout=15, params={"ref": GITHUB_BRANCH})
    except requests.RequestException as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao acessar o GitHub para líderes: {exc}")

    if response.status_code == 200:
        payload = response.json()
        content = payload.get("content", "")
        sha = payload.get("sha")
        decoded = base64.b64decode(content).decode("utf-8") if content else ""
        return decoded, sha
    if response.status_code == 404:
        return "", None
    raise HTTPException(status_code=500, detail="Erro ao consultar o arquivo de líderes no GitHub")


def _load_lideres() -> tuple[list, Optional[str]]:
    try:
        content, sha = _fetch_lideres_from_github()
        if not content:
            return [], sha
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Formato inválido do arquivo de líderes: {exc}")
        if isinstance(parsed, list):
            return parsed, sha
        raise HTTPException(status_code=500, detail="Arquivo de líderes corrompido")
    except HTTPException as exc:
        print("Aviso: não foi possível carregar os líderes do GitHub:", exc.detail)
        if LIDERES_LOCAL_FILE.exists():
            try:
                parsed = json.loads(LIDERES_LOCAL_FILE.read_text(encoding="utf-8"))
                if isinstance(parsed, list):
                    return parsed, None
            except json.JSONDecodeError as json_exc:
                print("Aviso: arquivo local de líderes inválido:", json_exc)
        return [], None


def _persist_lideres(lideres: list, sha: Optional[str], message: str) -> None:
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub não configurado para salvar os líderes.")
    payload = json.dumps(lideres, ensure_ascii=False, indent=2)
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_LIDERES_PATH}"
    body = {
        "message": message,
        "branch": GITHUB_BRANCH,
        "content": base64.b64encode(payload.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha
    response = requests.put(url, headers=GITHUB_HEADERS, json=body, timeout=30)
    if response.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail="Não foi possível salvar os líderes no GitHub")


def _sync_leader_assignment(record: dict) -> None:
    if not record.get("lider_gestor"):
        return
    setor = (record.get("setor") or "").strip()
    nome = (record.get("nome_completo") or "").strip()
    if not setor or not nome:
        return
    lideres, sha = _load_lideres()
    now = datetime.utcnow().isoformat() + "Z"
    existing = next((item for item in lideres if item.get("nome", "").strip().lower() == nome.lower()), None)
    updated = False
    if existing:
        current_setores = existing.get("setores_responsaveis") or []
        sanitized = _serialize_setores([setor] + current_setores)
        if sanitized != current_setores:
            existing["setores_responsaveis"] = sanitized
            updated = True
        if existing.get("nome") != nome:
            existing["nome"] = nome
            updated = True
        if updated:
            existing["atualizado_em"] = now
    else:
        lideres.append({
            "id": uuid.uuid4().hex,
            "nome": nome,
            "email": "",
            "telefone": "",
            "observacoes": "",
            "setores_responsaveis": _serialize_setores([setor]),
            "criado_em": now,
            "atualizado_em": now,
        })
        updated = True
    if updated:
        _write_local_lideres(lideres)
        try:
            _persist_lideres(lideres, sha, f"Sincronizar líder via cadastro de {nome}")
        except HTTPException as exc:
            print("Aviso: não foi possível sincronizar líderes com o GitHub:", exc.detail)
        except Exception as exc:
            print("Aviso inesperado ao sincronizar líderes:", exc)


def _push_to_github(record: dict) -> None:
    if not GITHUB_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Token do GitHub não configurado para salvar os funcionários ativos."
        )

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

    sha = None
    funcionarios = []

    if response.status_code == 200:
        payload = response.json()
        content = payload.get("content", "")
        sha = payload.get("sha")
        if content:
            decoded = base64.b64decode(content).decode("utf-8")
            try:
                funcionarios = json.loads(decoded)
            except (json.JSONDecodeError, ValueError):
                funcionarios = []
    elif response.status_code not in (404,):
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao consultar o arquivo {GITHUB_PATH} no GitHub: {response.status_code}"
        )

    funcionarios.append(record)
    serialized = json.dumps(funcionarios, ensure_ascii=False, indent=2)
    payload = {
        "message": "Atualiza lista de funcionários ativos",
        "content": base64.b64encode(serialized.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        push_response = requests.put(url, headers=GITHUB_HEADERS, json=payload, timeout=10)
    except requests.RequestException as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar no GitHub: {exc}")

    if push_response.status_code not in (200, 201):
        raise HTTPException(
            status_code=500,
            detail="Não foi possível atualizar o arquivo de funcionários ativos no GitHub."
        )


@app.post("/api/funcionarios")
async def cadastrar_funcionario(
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
    lider_responsavel: str = Form(...),
    lider_gestor: Optional[str] = Form(None),
    cep: Optional[str] = Form(None),
    rua: Optional[str] = Form(None),
    numero: Optional[str] = Form(None),
    bairro: Optional[str] = Form(None),
    cidade: Optional[str] = Form(None),
    estado: Optional[str] = Form(None),
    complemento: Optional[str] = Form(None),
    filhos: Optional[List[str]] = Form(None),
):
    cpf_clean = "".join(filter(str.isdigit, cpf))
    if len(cpf_clean) != 11:
        raise HTTPException(status_code=400, detail="CPF inválido")

    lider_flag = str(lider_gestor).lower() in {"true", "on", "1", "sim"}

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
        "lider_responsavel": lider_responsavel.strip(),
        "lider_gestor": lider_flag,
        "cep": (cep or "").strip(),
        "rua": (rua or "").strip(),
        "numero": (numero or "").strip(),
        "bairro": (bairro or "").strip(),
        "cidade": (cidade or "").strip(),
        "estado": (estado or "").strip(),
        "complemento": (complemento or "").strip(),
        "filhos": [(data or "").strip() for data in filhos or [] if data],
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
    if lider_flag:
        _sync_leader_assignment(record)

    return {
        "ok": True,
        "message": "Colaborador registrado com sucesso!",
        "id": record["id"],
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8011))
    uvicorn.run(app, host="0.0.0.0", port=port)
