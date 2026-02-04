from fastapi import FastAPI, UploadFile, Form, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests
import json
from datetime import datetime, timedelta
import base64
import os
import threading
import time
from typing import List, Optional, Dict, Any
import re
from cachetools import TTLCache
import hashlib
import random
import secrets
from dotenv import load_dotenv
import urllib.parse
from pathlib import Path
import html
import xml.etree.ElementTree as ET
from pydantic import BaseModel
from aton_agent import AtonAgent
from funcoes_api import router as funcoes_router
from setores_api import router as setores_router
from funcionarios_router import router as funcionarios_router
from lideres_router import router as lideres_router, _load_lideres as _load_lideres_admin
from atestados_router import router as atestados_router
from feriados_service import get_feriados, refresh_feriados
from profile_router import router as profile_router
from datajud_client import call_datajud_all
from experiencia_router import router as experiencia_router
from sync_service import start_startup_sync_thread, enqueue_pending, is_sync_target

from app_paths import APP_DIR, DATA_DIR, RESOURCE_DIR, ensure_data_seed

app = FastAPI()
BASE_DIR = DATA_DIR
RESOURCE_BASE = RESOURCE_DIR

# ==================== CONFIGURAÇÕES ====================
load_dotenv(APP_DIR / ".env")
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "Candidatos"
GITHUB_OWNER = "PopularAtacarejo"

DEFAULT_BACKUP_DIR = os.getenv("BACKUP_DIR") or str(DATA_DIR)
BACKUP_FILE_NAME = os.getenv("BACKUP_FILE_NAME") or "backup-dados-funcionarios.json"

BACKUP_STATUS = {
    "last_run": None,
    "last_error": None,
    "last_path": None,
    "next_run": None
}
BACKUP_PROGRESS = {
    "status": "idle",
    "mode": None,
    "current": 0,
    "total": 0,
    "message": "",
    "updated_at": None
}

headers = {
    "Accept": "application/vnd.github.v3+json"
}

ENV_ALLOWLIST = {
    "GITHUB_TOKEN",
    "GITHUB_OWNER",
    "GITHUB_REPO",
    "GITHUB_BRANCH",
    "GITHUB_FUNCIONARIOS_PATH",
    "GITHUB_DESLIGADOS_PATH",
    "GITHUB_LIDERES_PATH",
    "FUNCIONARIOS_ENCRYPTION_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ATON_SITE_URL",
    "ATON_SITE_TITLE",
    "ATON_CONFIG_URL",
    "ATON_CONFIG_PATH",
    "CONFIGURACOES_URL",
    "DATAJUD_TIMEOUT",
    "DATAJUD_WORKERS",
    "LOCAL_BACKUP_DIR",
    "BACKUP_DIR",
    "BACKUP_FILE_NAME",
    "AUTH_FILE_PATH",
    "PORT",
    "APP_URL",
}

DATA_SOURCE_KEYS = {
    "candidatos.json": "candidatos_url",
    "vagas.json": "vagas_url",
    "lideres.json": "lideres_url",
    "setores.json": "setores_url",
    "funcoes.json": "funcoes_url",
    "funcionarios-ativos.json": "funcionarios_ativos_url",
    "feriados.json": "feriados_url",
    "empresas.json": "empresas_url",
    "configuracoes.json": "configuracoes_url",
}

CONFIG_LOCAL_PATH = DATA_DIR / "data" / "configuracoes_local.json"

ensure_data_seed()

def load_local_config() -> dict:
    try:
        if CONFIG_LOCAL_PATH.exists():
            return json.loads(CONFIG_LOCAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_local_config(payload: dict) -> None:
    try:
        CONFIG_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_LOCAL_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"Falha ao salvar configuracoes locais: {exc}")

def _get_local_data_sources() -> dict:
    local = load_local_config()
    if isinstance(local, dict) and isinstance(local.get("data_sources"), dict):
        return local.get("data_sources") or {}
    return {}

def _get_data_source_url(path: str) -> Optional[str]:
    key = DATA_SOURCE_KEYS.get(path)
    if not key:
        return None
    data_sources = _get_local_data_sources()
    url = (data_sources.get(key) or "").strip()
    return url or None

def apply_github_settings(github: Optional[dict], persist: bool = False) -> None:
    global GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, BRANCH, headers
    if not github:
        return
    owner = (github.get("owner") or "").strip()
    repo = (github.get("repo") or github.get("repository") or "").strip()
    branch = (github.get("branch") or "").strip()
    token = github.get("token")

    if owner:
        GITHUB_OWNER = owner
        os.environ["GITHUB_OWNER"] = owner
    if repo:
        GITHUB_REPO = repo
        os.environ["GITHUB_REPO"] = repo
    if branch:
        BRANCH = branch
        os.environ["GITHUB_BRANCH"] = branch
    if token is not None:
        GITHUB_TOKEN = token
        if token:
            os.environ["GITHUB_TOKEN"] = token
        else:
            os.environ.pop("GITHUB_TOKEN", None)

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    if persist:
        current = load_local_config()
        current["github"] = {
            "owner": GITHUB_OWNER,
            "repo": GITHUB_REPO,
            "branch": BRANCH,
            "token": GITHUB_TOKEN
        }
        save_local_config(current)
    _refresh_env_dependents()

def _refresh_env_dependents() -> None:
    global GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, BRANCH, headers, LOCAL_BACKUP_DIR
    token = os.getenv("GITHUB_TOKEN")
    owner = os.getenv("GITHUB_OWNER", "PopularAtacarejo")
    repo = os.getenv("GITHUB_REPO", "Candidatos")
    branch = os.getenv("GITHUB_BRANCH") or os.getenv("BRANCH") or (globals().get("BRANCH") or "main")

    if token is not None:
        GITHUB_TOKEN = token
    if owner:
        GITHUB_OWNER = owner
    if repo:
        GITHUB_REPO = repo
    if branch:
        BRANCH = branch

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    local_backup_override = os.getenv("LOCAL_BACKUP_DIR")
    if local_backup_override:
        LOCAL_BACKUP_DIR = local_backup_override

    try:
        import funcionarios_router
        import lideres_router
        import cadastro_funcionarios_api
        import experiencia_router
        import setores_api
        import funcoes_api
        import profile_store
        import sync_service
    except Exception:
        return

    github_path = os.getenv("GITHUB_FUNCIONARIOS_PATH")
    github_desligados = os.getenv("GITHUB_DESLIGADOS_PATH")
    github_lideres = os.getenv("GITHUB_LIDERES_PATH")

    funcionarios_router.GITHUB_OWNER = GITHUB_OWNER
    funcionarios_router.GITHUB_REPO = GITHUB_REPO
    funcionarios_router.GITHUB_BRANCH = BRANCH
    funcionarios_router.GITHUB_TOKEN = GITHUB_TOKEN
    if github_path:
        funcionarios_router.GITHUB_PATH = github_path
    if github_desligados:
        funcionarios_router.GITHUB_DESLIGADOS_PATH = github_desligados
    funcionarios_router.GITHUB_HEADERS = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        funcionarios_router.GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"
    if os.getenv("FUNCIONARIOS_ENCRYPTION_KEY"):
        funcionarios_router._ENCRYPTION_KEY_CACHE = None

    lideres_router.GITHUB_OWNER = GITHUB_OWNER
    lideres_router.GITHUB_REPO = GITHUB_REPO
    lideres_router.GITHUB_BRANCH = BRANCH
    lideres_router.GITHUB_TOKEN = GITHUB_TOKEN
    if github_lideres:
        lideres_router.GITHUB_PATH = github_lideres
    lideres_router.GITHUB_HEADERS = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        lideres_router.GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

    cadastro_funcionarios_api.GITHUB_OWNER = GITHUB_OWNER
    cadastro_funcionarios_api.GITHUB_REPO = GITHUB_REPO
    cadastro_funcionarios_api.GITHUB_BRANCH = BRANCH
    cadastro_funcionarios_api.GITHUB_TOKEN = GITHUB_TOKEN
    if github_path:
        cadastro_funcionarios_api.GITHUB_PATH = github_path
    if github_lideres:
        cadastro_funcionarios_api.GITHUB_LIDERES_PATH = github_lideres
    cadastro_funcionarios_api.GITHUB_HEADERS = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        cadastro_funcionarios_api.GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

    experiencia_router.GITHUB_OWNER = GITHUB_OWNER
    experiencia_router.GITHUB_REPO = GITHUB_REPO
    experiencia_router.GITHUB_BRANCH = BRANCH
    experiencia_router.GITHUB_TOKEN = GITHUB_TOKEN

    setores_api.GITHUB_OWNER = GITHUB_OWNER
    setores_api.GITHUB_REPO = GITHUB_REPO
    setores_api.GITHUB_TOKEN = GITHUB_TOKEN
    setores_api.BRANCH = BRANCH
    setores_api.headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        setores_api.headers["Authorization"] = f"token {GITHUB_TOKEN}"

    funcoes_api.GITHUB_OWNER = GITHUB_OWNER
    funcoes_api.GITHUB_REPO = GITHUB_REPO
    funcoes_api.BRANCH = BRANCH

    profile_store.GITHUB_OWNER = GITHUB_OWNER
    profile_store.GITHUB_REPO = GITHUB_REPO
    profile_store.GITHUB_TOKEN = GITHUB_TOKEN
    profile_store.BRANCH = BRANCH
    profile_store.RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}"
    profile_store.HEADERS = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        profile_store.HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

    sync_service.GITHUB_OWNER = GITHUB_OWNER
    sync_service.GITHUB_REPO = GITHUB_REPO
    sync_service.GITHUB_BRANCH = BRANCH
    sync_service.GITHUB_TOKEN = GITHUB_TOKEN
    if local_backup_override:
        sync_service.DEFAULT_LOCAL_BASE = Path(local_backup_override)
        sync_service.CANDIDATOS_LOCAL_FALLBACK = sync_service.DEFAULT_LOCAL_BASE / "candidatos.json"

def apply_env_settings(env_payload: Optional[dict], persist: bool = False) -> None:
    if not isinstance(env_payload, dict):
        return
    updates = {}
    removed = set()
    for key, raw in env_payload.items():
        if key not in ENV_ALLOWLIST:
            continue
        value = raw
        if value is None:
            value = ""
        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value == "":
            if key in os.environ:
                os.environ.pop(key, None)
            removed.add(key)
            continue
        os.environ[key] = value
        updates[key] = value

    if updates or removed:
        _refresh_env_dependents()

    if persist:
        current = load_local_config()
        env_current = dict(current.get("env") or {})
        env_current.update(updates)
        for key in removed:
            env_current.pop(key, None)
        current["env"] = env_current
        save_local_config(current)

AUTH_FILE_PATH = "auth.json"

# Backup local (duplo salvamento)
LOCAL_BACKUP_DIR = os.getenv("LOCAL_BACKUP_DIR") or str(DATA_DIR)

_local_config = load_local_config()
if isinstance(_local_config, dict):
    if _local_config.get("env"):
        apply_env_settings(_local_config.get("env"), persist=False)
    if _local_config.get("github"):
        apply_github_settings(_local_config.get("github"), persist=False)

if GITHUB_TOKEN:
    headers["Authorization"] = f"token {GITHUB_TOKEN}"
    print(f"Token GitHub carregado (primeiros 5 caracteres): {GITHUB_TOKEN[:5]}...")
else:
    print("AVISO: Token GitHub não encontrado! Operações de escrita podem falhar.")

def get_repo_default_branch() -> str:
    """Busca o branch padrão do repositório no GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Status ao buscar repositório: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            default_branch = data.get("default_branch", "main")
            print(f"Branch padrão encontrado: {default_branch}")
            return default_branch
        elif response.status_code == 404:
            print(f"ERRO: Repositório {GITHUB_OWNER}/{GITHUB_REPO} não encontrado!")
        else:
            print(f"ERRO ao buscar repositório: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Erro de rede ao buscar repositório: {str(e)}")
    
    return "main"


BRANCH = os.getenv("GITHUB_BRANCH") or (globals().get("BRANCH") or None) or get_repo_default_branch()
print(f"Usando branch: {BRANCH}")

# Cache para vagas (10 minutos)
vagas_cache = TTLCache(maxsize=1, ttl=600)

# ==================== UTILIDADES ====================
def validate_cpf(cpf: str) -> bool:
    """Valida CPF brasileiro"""
    cpf = re.sub(r'[^\d]', '', cpf)
    
    if len(cpf) != 11:
        return False
    
    if cpf == cpf[0] * 11:
        return False
    
    # Cálculo do primeiro dígito verificador
    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    resto = soma % 11
    digito1 = 0 if resto < 2 else 11 - resto
    
    if digito1 != int(cpf[9]):
        return False
    
    # Cálculo do segundo dígito verificador
    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    resto = soma % 11
    digito2 = 0 if resto < 2 else 11 - resto
    
    return digito2 == int(cpf[10])

def sanitize_filename(name: str) -> str:
    """Remove caracteres especiais do nome do arquivo"""
    name = re.sub(r'[^\w\s.-]', '', name)
    name = re.sub(r'\s+', '_', name)
    return name[:100]

def check_repo_access() -> bool:
    """Verifica se temos acesso ao repositório"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Verificação de acesso ao repositório: {response.status_code}")
        
        if response.status_code == 200:
            print("Acesso ao repositório confirmado")
            return True
        elif response.status_code == 404:
            print(f"ERRO CRÍTICO: Repositório {GITHUB_OWNER}/{GITHUB_REPO} não existe!")
            return False
        elif response.status_code == 403:
            print(f"ERRO: Token GitHub não tem permissão para acessar o repositório")
            return False
        else:
            print(f"ERRO de acesso ao repositório: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Erro ao verificar acesso ao repositório: {str(e)}")
        return False

def create_github_file(file_path: str, content: str, message: str) -> bool:
    """Cria um arquivo no GitHub via API"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
    
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    data = {
        "message": message,
        "content": content_b64,
        "branch": BRANCH
    }
    
    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        print(f"Tentativa de criar {file_path}: Status {response.status_code}")
        
        if response.status_code in [200, 201]:
            print(f"Arquivo {file_path} criado com sucesso")
            return True
        else:
            print(f"Erro ao criar {file_path}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Exceção ao criar {file_path}: {str(e)}")
        return False

def initialize_repository() -> bool:
    """Inicializa o repositório com arquivos necessários se não existirem"""
    print("Inicializando repositório...")
    
    # Verificar se o repositório existe
    if not check_repo_access():
        print("Não é possível acessar o repositório. Verifique o token e as permissões.")
        return False
    
    # Lista de arquivos a serem criados se não existirem
    files_to_check = [
        {
            "path": "vagas.json",
            "content": json.dumps([
                {"nome": "Auxiliar de Limpeza"},
                {"nome": "Vendedor"},
                {"nome": "Caixa"},
                {"nome": "Estoquista"},
                {"nome": "Repositor"},
                {"nome": "Atendente"},
                {"nome": "Gerente"},
                {"nome": "Supervisor"},
                {"nome": "Operador de Caixa"}
            ], indent=2, ensure_ascii=False),
            "message": "Criar arquivo de vagas inicial"
        },
        {
            "path": "candidatos.json",
            "content": "[]",
            "message": "Criar arquivo de candidatos inicial"
        },
        {
            "path": "curriculos/README.md",
            "content": "# Pasta de Currículos\n\nEsta pasta armazena os currículos enviados pelos candidatos.",
            "message": "Criar pasta curriculos com arquivo README"
        }
    ]
    
    created_count = 0
    
    for file_info in files_to_check:
        # Verificar se o arquivo já existe
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_info['path']}"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                print(f"Arquivo {file_info['path']} já existe")
                continue
        except:
            pass
        
        # Criar o arquivo
        if create_github_file(file_info["path"], file_info["content"], file_info["message"]):
            created_count += 1
            # Aguardar um pouco para não sobrecarregar a API
            import time
            time.sleep(1)
    
    print(f"Inicialização concluída. {created_count} arquivos criados.")
    return created_count > 0

# Inicializar o repositório ao iniciar o servidor
@app.on_event("startup")
def _startup_tasks() -> None:
    print("=== Inicializando servidor ===")
    threading.Thread(target=initialize_repository, daemon=True).start()
    start_startup_sync_thread()

def parse_iso_date(date_str: str) -> Optional[datetime]:
    """Converte string ISO para datetime com tratamento de erros"""
    if not date_str:
        return None
    
    try:
        # Remove o 'Z' se existir e converte para formato ISO
        if date_str.endswith('Z'):
            date_str = date_str.replace('Z', '+00:00')
        
        # Tenta converter diretamente
        return datetime.fromisoformat(date_str)
    except:
        # Tenta outros formatos comuns
        try:
            return datetime.strptime(date_str.split('.')[0], "%Y-%m-%dT%H:%M:%S")
        except:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except:
                return None

def is_candidate_expired(candidate: dict) -> bool:
    """Verifica se a candidatura expirou (mais de 90 dias)"""
    if "enviado_em" not in candidate:
        return False
    
    candidate_date = parse_iso_date(candidate["enviado_em"])
    if not candidate_date:
        return False
    
    # Calcula diferença em dias
    days_diff = (datetime.now() - candidate_date).days
    return days_diff >= 90

def clean_expired_candidates() -> int:
    """Remove automaticamente candidaturas expiradas (mais de 90 dias) e seus currículos"""
    print("Iniciando limpeza de candidaturas expiradas...")
    
    cached = fetch_content_from_github("candidatos.json")
    if isinstance(cached, list):
        if clean_expired:
            return [c for c in cached if not is_candidate_expired(c)]
        return cached

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/candidatos.json"
    
    try:
        # Obtém o arquivo atual
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Erro ao buscar candidatos para limpeza: {response.status_code}")
            return 0
        
        # Decodifica o conteúdo
        content = response.json()["content"]
        decoded = base64.b64decode(content).decode("utf-8")
        candidates = json.loads(decoded)
        sha = response.json()["sha"]
        
        # Separa candidatos ativos e expirados
        active_candidates = []
        expired_candidates = []
        deleted_files = []
        
        for candidate in candidates:
            if is_candidate_expired(candidate):
                expired_candidates.append(candidate)
                # Extrai o nome do arquivo da URL para exclusão
                if "arquivo_url" in candidate:
                    try:
                        # Pega o nome do arquivo da URL
                        file_url = candidate["arquivo_url"]
                        # Extrai o caminho do arquivo (parte após o branch)
                        path_match = re.search(f"{BRANCH}/(.+)", file_url)
                        if path_match:
                            file_path = path_match.group(1)
                            deleted_files.append(file_path)
                    except:
                        print(f"Não foi possível extrair caminho do arquivo para: {candidate.get('nome', 'Unknown')}")
            else:
                active_candidates.append(candidate)
        
        if not expired_candidates:
            print("Nenhuma candidatura expirada encontrada.")
            return 0
        
        print(f"Encontradas {len(expired_candidates)} candidaturas expiradas para remoção.")
        
        # Remove os arquivos de currículo expirados
        deleted_count = 0
        for file_path in deleted_files:
            if delete_github_file(file_path):
                deleted_count += 1
                # Aguarda um pouco para não sobrecarregar a API
                import time
                time.sleep(0.5)
        
        # Atualiza o arquivo JSON removendo os candidatos expirados
        updated_content = json.dumps(active_candidates, indent=2, ensure_ascii=False)
        content_b64 = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")
        
        update_data = {
            "message": f"Limpeza automática: Removidas {len(expired_candidates)} candidaturas expiradas",
            "content": content_b64,
            "sha": sha,
            "branch": BRANCH
        }
        
        update_response = requests.put(url, headers=headers, json=update_data, timeout=30)
        
        if update_response.status_code in [200, 201]:
            print(f"✅ Limpeza concluída: {len(expired_candidates)} candidaturas expiradas removidas, {deleted_count} arquivos deletados.")
            return len(expired_candidates)
        else:
            print(f"❌ Erro ao atualizar candidatos.json: {update_response.status_code} - {update_response.text}")
            return 0
            
    except Exception as e:
        print(f"❌ Erro durante limpeza: {str(e)}")
        return 0

def delete_github_file(file_path: str) -> bool:
    """Deleta um arquivo do GitHub via API"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
    
    # Primeiro obtém o SHA do arquivo
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            sha = response.json()["sha"]
            
            # Agora deleta o arquivo
            data = {
                "message": f"Removendo currículo expirado: {file_path}",
                "sha": sha,
                "branch": BRANCH
            }
            
            delete_response = requests.delete(url, headers=headers, json=data, timeout=30)
            
            if delete_response.status_code in [200, 204]:
                print(f"✅ Arquivo {file_path} deletado com sucesso")
                return True
            else:
                print(f"❌ Erro ao deletar {file_path}: {delete_response.status_code} - {delete_response.text}")
                return False
        else:
            print(f"Arquivo {file_path} não encontrado ou já deletado")
            return False
    except Exception as e:
        print(f"Erro ao deletar {file_path}: {str(e)}")
        return False

def get_existing_candidates(clean_expired: bool = False) -> List[dict]:
    """Obtém candidatos existentes do arquivo JSON no GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/candidatos.json"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            content = response.json()["content"]
            decoded = base64.b64decode(content).decode("utf-8")
            candidates = json.loads(decoded)
            
            # Executa limpeza se solicitado
            if clean_expired:
                # Remove candidatos expirados da lista retornada
                active_candidates = [c for c in candidates if not is_candidate_expired(c)]
                return active_candidates
            
            return candidates
        elif response.status_code == 404:
            # Tentar criar o arquivo se não existir
            print("Arquivo candidatos.json não encontrado, tentando criar...")
            if create_github_file("candidatos.json", "[]", "Criar arquivo de candidatos"):
                return []
        return []
    except Exception as e:
        print(f"Erro ao buscar candidatos: {str(e)}")
        return []

def normalize_vagas_data(vagas_data) -> List[dict]:
    """Normaliza as vagas para garantir o campo 'nome'"""
    normalized = []

    if isinstance(vagas_data, dict):
        vagas_data = [vagas_data]

    if isinstance(vagas_data, list):
        for vaga in vagas_data:
            if isinstance(vaga, dict) and "nome" in vaga:
                normalized.append({"nome": vaga["nome"]})
            elif isinstance(vaga, str):
                normalized.append({"nome": vaga})

    return normalized

def fetch_content_from_github(path: str):
    """Busca um arquivo no GitHub e retorna o JSON decodificado"""
    data_source_url = _get_data_source_url(path)
    if data_source_url:
        try:
            response = requests.get(data_source_url, timeout=10)
            if response.status_code == 200:
                return response.json()
            print(f"Erro ao buscar {path} via URL: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"Erro de rede ao buscar {path} via URL: {str(e)}")
        except json.JSONDecodeError as e:
            print(f"Erro ao decodificar JSON de {path} via URL: {str(e)}")
        except Exception as e:
            print(f"Erro inesperado ao buscar {path} via URL: {str(e)}")

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            payload = response.json()
            content = payload.get("content")
            if content:
                decoded = base64.b64decode(content).decode("utf-8")
                return json.loads(decoded)
        else:
            print(f"Erro ao buscar {path} via API: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Erro de rede ao buscar {path}: {str(e)}")
    except json.JSONDecodeError as e:
        print(f"Erro ao decodificar JSON de {path}: {str(e)}")
    except Exception as e:
        print(f"Erro inesperado ao buscar {path}: {str(e)}")

    return None

def fetch_github_file(path: str) -> Optional[dict]:
    """Busca um arquivo no GitHub e retorna payload completo (content + sha)."""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None

def load_github_json(path: str, default: Optional[dict] = None):
    payload = fetch_content_from_github(path)
    if payload is None:
        return default if default is not None else {}
    return payload

def save_github_json(path: str, payload: Any, message: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    content = json.dumps(payload, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    sha = None
    current = fetch_github_file(path)
    if current and isinstance(current, dict):
        sha = current.get("sha")
    data = {
        "message": message,
        "content": content_b64,
        "branch": BRANCH
    }
    if sha:
        data["sha"] = sha
    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        ok = response.status_code in [200, 201]
        if not ok and is_sync_target(path) and isinstance(payload, list):
            enqueue_pending(path, payload, message)
        return ok
    except Exception:
        if is_sync_target(path) and isinstance(payload, list):
            enqueue_pending(path, payload, message)
        return False

def hash_password(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def strip_user_sensitive(user: dict) -> dict:
    clean = dict(user)
    clean.pop("senha", None)
    return clean

def load_auth_users() -> List[dict]:
    users = load_github_json(AUTH_FILE_PATH, default=[])
    if isinstance(users, list):
        return users
    return []

def save_auth_users(users: List[dict], message: str) -> bool:
    return save_github_json(AUTH_FILE_PATH, users, message)

# Vagas admin (full payload)
def normalize_vaga_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower())
    return slug.strip("-")

def load_admin_vagas() -> List[dict]:
    vagas = load_github_json("vagas.json", default=[])
    if isinstance(vagas, dict):
        vagas = [vagas]
    if not isinstance(vagas, list):
        return []
    normalized = []
    for vaga in vagas:
        if not isinstance(vaga, dict):
            continue
        if not vaga.get("id") and vaga.get("nome"):
            vaga = {**vaga, "id": normalize_vaga_id(vaga.get("nome"))}
        normalized.append(vaga)
    return normalized

def save_admin_vagas(vagas: List[dict], message: str) -> bool:
    return save_github_json("vagas.json", vagas, message)

def find_vaga_index(vagas: List[dict], vaga_id: str) -> Optional[int]:
    if not vaga_id:
        return None
    for idx, vaga in enumerate(vagas):
        if (vaga.get("id") or "").lower() == vaga_id.lower():
            return idx
        if vaga.get("nome") and normalize_vaga_id(vaga.get("nome")) == vaga_id.lower():
            return idx
    return None

# Empresas admin (full payload)
def normalize_empresa_id(value: str, cnpj: str = "") -> str:
    digits = re.sub(r"\D", "", cnpj or "")
    if digits:
        return digits
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower())
    return slug.strip("-")

def load_admin_empresas() -> List[dict]:
    empresas = load_github_json("empresas.json", default=[])
    if isinstance(empresas, dict):
        empresas = [empresas]
    if not isinstance(empresas, list):
        return []
    normalized = []
    for empresa in empresas:
        if not isinstance(empresa, dict):
            continue
        cnpj_digits = re.sub(r"\D", "", empresa.get("cnpj", ""))
        if cnpj_digits:
            empresa = {**empresa, "cnpj": cnpj_digits}
        if not empresa.get("id"):
            empresa = {**empresa, "id": normalize_empresa_id(empresa.get("razao_social") or empresa.get("nome_fantasia"), cnpj_digits)}
        normalized.append(empresa)
    return normalized

def save_admin_empresas(empresas: List[dict], message: str) -> bool:
    try:
        (BASE_DIR / "empresas.json").write_text(
            json.dumps(empresas, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass
    return save_github_json("empresas.json", empresas, message)

def find_empresa_index(empresas: List[dict], empresa_id: str) -> Optional[int]:
    if not empresa_id:
        return None
    raw = (empresa_id or "").strip().lower()
    digits = re.sub(r"\D", "", empresa_id)
    for idx, empresa in enumerate(empresas):
        emp_id = (empresa.get("id") or "").lower()
        if emp_id and emp_id == raw:
            return idx
        emp_cnpj = re.sub(r"\D", "", empresa.get("cnpj", ""))
        if digits and emp_cnpj and emp_cnpj == digits:
            return idx
    return None


# Tokens em memÃ³ria (sessÃ£o simples)
AUTH_TOKENS: Dict[str, dict] = {}

def issue_auth_token(user: dict) -> str:
    token = secrets.token_urlsafe(24)
    AUTH_TOKENS[token] = {
        "email": user.get("email"),
        "issued_at": datetime.now().isoformat()
    }
    return token

def _get_auth_token(request: Request) -> Optional[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return None
    parts = auth.split()
    if len(parts) >= 2 and parts[0].lower() in {"bearer", "token"}:
        return parts[1].strip()
    return auth

def get_vagas_from_github() -> List[dict]:
    """Tenta buscar vagas via API (com token) e depois pela URL RAW"""
    print(f"Buscando vagas no GitHub... BRANCH={BRANCH}")
    
    # Primeiro tenta via API com token
    api_data = fetch_content_from_github("vagas.json")
    normalized = normalize_vagas_data(api_data) if api_data else []
    if normalized:
        print(f"Vagas encontradas via API: {len(normalized)}")
        return normalized

    # Se falhar, tenta via URL pública
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/vagas.json"
    print(f"Tentando buscar vagas via URL RAW: {raw_url}")
    try:
        response = requests.get(raw_url, timeout=10)
        print(f"Status da resposta RAW: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            normalized = normalize_vagas_data(data)
            print(f"Vagas encontradas via RAW: {len(normalized)}")
            return normalized
        elif response.status_code == 404:
            print(f"Arquivo vagas.json não encontrado")
            # Tentar criar o arquivo
            if create_github_file("vagas.json", json.dumps([
                {"nome": "Auxiliar de Limpeza"},
                {"nome": "Vendedor"},
                {"nome": "Caixa"},
                {"nome": "Estoquista"},
                {"nome": "Repositor"},
                {"nome": "Atendente"},
                {"nome": "Gerente"},
                {"nome": "Supervisor"},
                {"nome": "Operador de Caixa"}
            ], indent=2, ensure_ascii=False), "Criar arquivo de vagas"):
                # Tentar novamente após criar
                response = requests.get(raw_url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    return normalize_vagas_data(data)
        else:
            print(f"Erro ao buscar vagas: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Erro de rede ao buscar vagas: {str(e)}")
    except json.JSONDecodeError as e:
        print(f"Erro ao decodificar JSON de vagas: {str(e)}")
    except Exception as e:
        print(f"Erro inesperado ao buscar vagas: {str(e)}")

    return []

def check_duplicate_candidate(cpf: str, vaga: str) -> bool:
    """Verifica se já existe candidatura para o CPF e vaga nos últimos 90 dias"""
    # Primeiro executa limpeza automática
    cleaned = clean_expired_candidates()
    if cleaned > 0:
        print(f"✅ {cleaned} candidaturas expiradas removidas durante verificação")
    
    existing = get_existing_candidates()
    cpf_clean = re.sub(r'[^\d]', '', cpf)
    vaga_lower = vaga.lower().strip()
    
    for existing_candidate in existing:
        existing_cpf = re.sub(r'[^\d]', '', existing_candidate.get("cpf", ""))
        existing_vaga = existing_candidate.get("vaga", "").lower().strip()
        
        if existing_cpf == cpf_clean and existing_vaga == vaga_lower:
            if "enviado_em" in existing_candidate:
                candidate_date = parse_iso_date(existing_candidate["enviado_em"])
                if candidate_date:
                    days_diff = (datetime.now() - candidate_date).days
                    if days_diff < 90:
                        return True
                else:
                    # Se não conseguir parsear a data, assume como duplicata por segurança
                    return True
    
    return False

def save_candidate(candidate: dict) -> dict:
    """Salva candidato no arquivo JSON do GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/candidatos.json"
    
    # Obtém candidatos existentes
    existing = get_existing_candidates()
    
    # Gera ID único
    cpf_clean = re.sub(r'[^\d]', '', candidate["cpf"])
    vaga_lower = candidate["vaga"].lower().strip()
    candidate_id = hashlib.md5(f"{cpf_clean}_{vaga_lower}_{datetime.now().timestamp()}".encode()).hexdigest()[:12]
    
    # Adiciona metadados
    candidate["id"] = candidate_id
    candidate["enviado_em"] = datetime.now().isoformat()
    candidate["status"] = "Novo"
    candidate["processado_em"] = datetime.now().isoformat()
    
    existing.append(candidate)
    
    # Prepara conteúdo para GitHub
    content = json.dumps(existing, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    # Obtém SHA do arquivo atual
    sha = None
    try:
        current_response = requests.get(url, headers=headers)
        if current_response.status_code == 200:
            sha = current_response.json()["sha"]
        elif current_response.status_code == 404:
            print("Arquivo candidatos.json não existe, será criado")
    except Exception as e:
        print(f"Erro ao buscar SHA: {str(e)}")
    
    # Atualiza arquivo no GitHub
    data = {
        "message": f"Candidatura: {candidate['nome']} para {candidate['vaga']}",
        "content": content_b64,
        "branch": BRANCH
    }
    
    if sha:
        data["sha"] = sha
    
    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        print(f"Status ao salvar candidato: {response.status_code}")
        
        if response.status_code in [200, 201]:
            return {"success": True, "data": response.json()}
        else:
            print(f"Erro ao salvar candidato: {response.status_code} - {response.text}")
            return {"success": False, "reason": "github_error", "details": response.text}
    except Exception as e:
        print(f"Exceção ao salvar candidato: {str(e)}")
        return {"success": False, "reason": "exception", "details": str(e)}

def save_curriculum_to_github(file: UploadFile, candidate_name: str, cpf: str, vaga: str) -> str:
    """Salva arquivo do currículo na pasta curriculos do GitHub"""
    cpf_clean = re.sub(r'[^\d]', '', cpf)[:11]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Prepara nome do arquivo
    safe_name = sanitize_filename(candidate_name)
    safe_vaga = sanitize_filename(vaga)
    
    # Mantém extensão original
    original_filename = file.filename
    if "." in original_filename:
        ext = original_filename.split(".")[-1].lower()
        if ext not in ["pdf", "doc", "docx"]:
            ext = "pdf"
    else:
        ext = "pdf"
    
    # Nome do arquivo: CPF_Nome_Vaga_Data.Extensão
    filename = f"{cpf_clean}_{safe_name}_{safe_vaga}_{timestamp}.{ext}"
    filename = filename.replace(" ", "_")
    
    # Lê conteúdo do arquivo
    file_content = file.file.read()
    
    # Verifica tamanho (máximo 5MB)
    if len(file_content) > 5 * 1024 * 1024:
        raise ValueError("Arquivo muito grande. Máximo 5MB.")
    
    content_b64 = base64.b64encode(file_content).decode("utf-8")
    
    # URL para upload na pasta curriculos
    file_path = f"curriculos/{filename}"
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
    
    data = {
        "message": f"Currículo: {candidate_name} - {vaga}",
        "content": content_b64,
        "branch": BRANCH
    }
    
    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        print(f"Status ao salvar currículo: {response.status_code}")
        
        if response.status_code in [200, 201]:
            raw_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{file_path}"
            print(f"Currículo salvo com sucesso: {raw_url}")
            return raw_url
        elif response.status_code == 404:
            # Pasta não existe, tentar criar
            print("Pasta curriculos não existe, criando...")
            if create_github_file("curriculos/README.md", 
                                  "# Pasta de Currículos\n\nEsta pasta armazena os currículos enviados pelos candidatos.",
                                  "Criar pasta curriculos"):
                # Tentar novamente após criar a pasta
                response = requests.put(url, headers=headers, json=data, timeout=30)
                if response.status_code in [200, 201]:
                    raw_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{file_path}"
                    print(f"Currículo salvo após criar pasta: {raw_url}")
                    return raw_url
                else:
                    raise Exception(f"Erro ao salvar currículo após criar pasta: {response.status_code}")
            else:
                raise Exception("Não foi possível criar a pasta curriculos")
        else:
            raise Exception(f"Erro ao salvar currículo: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exceção ao salvar currículo: {str(e)}")
        raise Exception(f"Erro ao salvar currículo: {str(e)}")

def _get_local_base_dir() -> Path:
    return Path(LOCAL_BACKUP_DIR)

def _get_local_dated_dir(kind: str, when: Optional[datetime]) -> Path:
    when = when or datetime.now()
    return _get_local_base_dir() / kind / f"{when.year}" / f"{when.month:02d}"

def _upsert_local_index(index_path: Path, entry: dict) -> None:
    existing = []
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    if isinstance(existing, list):
        entry_id = entry.get("id")
        if entry_id:
            replaced = False
            for i, item in enumerate(existing):
                if isinstance(item, dict) and item.get("id") == entry_id:
                    existing[i] = entry
                    replaced = True
                    break
            if not replaced:
                existing.append(entry)
        else:
            existing.append(entry)
    else:
        existing = [entry]
    index_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

def save_curriculum_locally(file: UploadFile, filename: str, when: Optional[datetime]) -> Path:
    base_dir = _get_local_dated_dir("curriculos", when)
    base_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = sanitize_filename(filename)
    if not safe_filename:
        safe_filename = "curriculo"
    file_path = base_dir / safe_filename
    # Reposiciona o ponteiro do arquivo para garantir leitura completa
    try:
        file.file.seek(0)
    except Exception:
        pass
    content = file.file.read()
    with open(file_path, "wb") as out_file:
        out_file.write(content)
    return file_path

def save_candidate_locally(candidate: dict, local_file_path: Optional[Path]) -> Path:
    when = parse_iso_date(candidate.get("enviado_em")) or datetime.now()
    base_dir = _get_local_dated_dir("candidatos", when)
    base_dir.mkdir(parents=True, exist_ok=True)
    candidate_id = candidate.get("id") or hashlib.md5(
        f"{candidate.get('cpf','')}_{candidate.get('vaga','')}_{when.timestamp()}".encode()
    ).hexdigest()[:12]
    file_path = base_dir / f"{candidate_id}.json"
    payload = dict(candidate)
    if local_file_path:
        payload["arquivo_local"] = str(local_file_path)
    file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _upsert_local_index(_get_local_base_dir() / "candidatos.json", payload)
    return file_path

# ==================== MIDDLEWARE CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas admin externas
app.include_router(funcoes_router)
app.include_router(setores_router)
app.include_router(funcionarios_router)
app.include_router(atestados_router)
app.include_router(profile_router)
app.include_router(experiencia_router)

# ==================== BACKUP AUTOMÁTICO ====================
BACKUP_FILES = [
    ("funcionarios_ativos", BASE_DIR / "funcionarios-ativos.json"),
    ("funcionarios_registros", BASE_DIR / "data" / "funcionarios_registros.json"),
    ("lideres", BASE_DIR / "data" / "lideres.json"),
    ("setores", BASE_DIR / "setores.json"),
    ("funcoes", BASE_DIR / "funcoes.json"),
    ("feriados", BASE_DIR / "feriados.json"),
    ("feriados_data", BASE_DIR / "data" / "feriados.json"),
    ("atestados", BASE_DIR / "data" / "atestados.json"),
    ("aton_mensagens", BASE_DIR / "data" / "aton_mensagens.json"),
    ("vagas", BASE_DIR / "vagas.json"),
    ("empresas", BASE_DIR / "empresas.json"),
]

def _read_json_file(path: Path):
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        print(f"Erro ao ler backup de {path}: {exc}")
        return None

def _update_progress(status: str, mode: Optional[str], current: int, total: int, message: str):
    BACKUP_PROGRESS["status"] = status
    BACKUP_PROGRESS["mode"] = mode
    BACKUP_PROGRESS["current"] = current
    BACKUP_PROGRESS["total"] = total
    BACKUP_PROGRESS["message"] = message
    BACKUP_PROGRESS["updated_at"] = datetime.utcnow().isoformat() + "Z"

def build_backup_payload(progress_total: int = 0, progress_start: int = 0) -> dict:
    datasets = {}
    counts = {}
    current = progress_start
    for key, path in BACKUP_FILES:
        data = _read_json_file(path)
        if data is None:
            current += 1
            if progress_total:
                _update_progress("running", "backup", current, progress_total, f"Ignorado: {key}")
            continue
        datasets[key] = data
        counts[key] = len(data) if isinstance(data, list) else (len(data.keys()) if isinstance(data, dict) else 1)
        current += 1
        if progress_total:
            _update_progress("running", "backup", current, progress_total, f"Processado: {key}")
    payload = {
        "meta": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "version": 1,
            "app": "Candidatos",
            "backup_file": BACKUP_FILE_NAME,
            "backup_dir": DEFAULT_BACKUP_DIR
        },
        "datasets": datasets,
        "integrity": {
            "counts": counts
        }
    }
    return payload

def write_backup_file() -> str:
    backup_dir = Path(DEFAULT_BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    file_path = backup_dir / BACKUP_FILE_NAME
    payload = build_backup_payload()
    tmp_path = backup_dir / f".{BACKUP_FILE_NAME}.tmp"
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    tmp_path.replace(file_path)
    BACKUP_STATUS["last_run"] = payload["meta"]["generated_at"]
    BACKUP_STATUS["last_error"] = None
    BACKUP_STATUS["last_path"] = str(file_path)
    return str(file_path)

def run_backup_task():
    total_steps = len(BACKUP_FILES) + 1
    _update_progress("running", "backup", 0, total_steps, "Iniciando backup...")
    try:
        payload = build_backup_payload(progress_total=total_steps, progress_start=0)
        _update_progress("running", "backup", total_steps - 1, total_steps, "Salvando arquivo...")
        backup_dir = Path(DEFAULT_BACKUP_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)
        file_path = backup_dir / BACKUP_FILE_NAME
        tmp_path = backup_dir / f".{BACKUP_FILE_NAME}.tmp"
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(file_path)
        BACKUP_STATUS["last_run"] = payload["meta"]["generated_at"]
        BACKUP_STATUS["last_error"] = None
        BACKUP_STATUS["last_path"] = str(file_path)
        _update_progress("completed", "backup", total_steps, total_steps, "Backup concluído.")
    except Exception as exc:
        BACKUP_STATUS["last_error"] = str(exc)
        _update_progress("error", "backup", 0, total_steps, f"Erro: {exc}")
        raise

def _seconds_until_next_backup(hour=14, minute=0) -> int:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    delta = (target - now).total_seconds()
    BACKUP_STATUS["next_run"] = target.isoformat()
    return max(1, int(delta))

def backup_scheduler_loop():
    while True:
        try:
            delay = _seconds_until_next_backup()
            time.sleep(delay)
            run_backup_task()
        except Exception as exc:
            BACKUP_STATUS["last_error"] = str(exc)
            print(f"Erro ao executar backup automático: {exc}")
            time.sleep(60)

@app.on_event("startup")
async def start_backup_scheduler():
    thread = threading.Thread(target=backup_scheduler_loop, daemon=True)
    thread.start()

@app.get("/api/backup/status")
async def get_backup_status():
    status = {
        **BACKUP_STATUS,
        "backup_dir": DEFAULT_BACKUP_DIR,
        "backup_file": BACKUP_FILE_NAME,
    }
    if not status.get("next_run"):
        _seconds_until_next_backup()
        status["next_run"] = BACKUP_STATUS.get("next_run")
    return {"ok": True, "status": status}

@app.post("/api/backup/run")
async def run_backup_now():
    if BACKUP_PROGRESS.get("status") == "running":
        raise HTTPException(status_code=409, detail="Já existe um processo de backup/restore em execução.")
    thread = threading.Thread(target=run_backup_task, daemon=True)
    thread.start()
    return {"ok": True, "message": "Backup iniciado."}

@app.get("/api/backup/progress")
async def get_backup_progress():
    return {"ok": True, "progress": BACKUP_PROGRESS}

RESTORE_FILES = {
    "funcionarios_ativos": BASE_DIR / "funcionarios-ativos.json",
    "funcionarios_registros": BASE_DIR / "data" / "funcionarios_registros.json",
    "lideres": BASE_DIR / "data" / "lideres.json",
    "setores": BASE_DIR / "setores.json",
    "funcoes": BASE_DIR / "funcoes.json",
    "feriados": BASE_DIR / "feriados.json",
    "feriados_data": BASE_DIR / "data" / "feriados.json",
    "atestados": BASE_DIR / "data" / "atestados.json",
    "aton_mensagens": BASE_DIR / "data" / "aton_mensagens.json",
    "vagas": BASE_DIR / "vagas.json",
    "empresas": BASE_DIR / "empresas.json",
}

def _write_json_atomic(path: Path, payload: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    tmp_path.replace(path)

def run_restore_task(payload: dict):
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        raise ValueError("Backup inválido: datasets ausente.")
    keys = [key for key in datasets.keys() if key in RESTORE_FILES]
    total_steps = max(1, len(keys))
    _update_progress("running", "restore", 0, total_steps, "Iniciando restauração...")
    for idx, key in enumerate(keys, start=1):
        target_path = RESTORE_FILES.get(key)
        _write_json_atomic(target_path, datasets.get(key))
        _update_progress("running", "restore", idx, total_steps, f"Restaurado: {key}")
    _update_progress("completed", "restore", total_steps, total_steps, "Restauração concluída.")

@app.post("/api/backup/restore")
async def restore_backup(file: UploadFile = File(...)):
    if BACKUP_PROGRESS.get("status") == "running":
        raise HTTPException(status_code=409, detail="Já existe um processo de backup/restore em execução.")
    try:
        content = await file.read()
        payload = json.loads(content.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Arquivo de backup inválido: {exc}")
    thread = threading.Thread(target=run_restore_task, args=(payload,), daemon=True)
    thread.start()
    return {"ok": True, "message": "Restauração iniciada."}
app.include_router(lideres_router)

# ==================== ARQUIVOS ESTÃTICOS ====================
for mount, folder, base_dir in [
    ("/scripts", "scripts", RESOURCE_BASE),
    ("/styles", "styles", RESOURCE_BASE),
    ("/assets", "assets", RESOURCE_BASE),
    ("/uploads", "uploads", BASE_DIR),
]:
    path = base_dir / folder
    if path.exists():
        app.mount(mount, StaticFiles(directory=str(path)), name=folder)
# ==================== ENDPOINTS ====================

@app.get("/funcionarios-ativos.json")
async def serve_funcionarios_ativos():
    data_path = BASE_DIR / "funcionarios-ativos.json"
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo de funcionários ativos não encontrado.")
    return FileResponse(data_path, media_type="application/json")

@app.get("/")
async def root():
    dashboard = RESOURCE_BASE / "dashboard.html"
    if dashboard.exists():
        return RedirectResponse(url="/dashboard.html")
    return {"message": "API de Candidaturas - Popular Atacarejo", "status": "online"}

@app.get("/{page_name}.html")
async def serve_html(page_name: str):
    page_path = RESOURCE_BASE / f"{page_name}.html"
    if not page_path.exists():
        raise HTTPException(status_code=404, detail="Pagina nao encontrada.")
    return FileResponse(str(page_path))

@app.get("/health")
async def health():
    repo_accessible = check_repo_access()
    return {
        "ok": True, 
        "timestamp": datetime.now().isoformat(), 
        "service": "candidaturas-api",
        "github_repo_accessible": repo_accessible,
        "branch": BRANCH
    }

@app.get("/api/feriados")
async def api_feriados(year: Optional[int] = None):
    try:
        target_year = year or datetime.now().year
        payload = get_feriados(target_year)
        return {"ok": True, **payload}
    except HTTPException as exc:
        raise exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/feriados/refresh")
async def api_feriados_refresh(year: Optional[int] = None):
    try:
        target_year = year or datetime.now().year
        payload = refresh_feriados(target_year)
        return {"ok": True, **payload}
    except HTTPException as exc:
        raise exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/wakeup")
async def wakeup():
    return {"ok": True, "message": "Servidor ativo", "timestamp": datetime.now().isoformat()}

@app.get("/status")
async def status():
    # Executa limpeza automática ao verificar status
    cleaned = clean_expired_candidates()
    status_msg = "online"
    if cleaned > 0:
        status_msg = f"online (limpeza: {cleaned} expirados removidos)"
    
    return {
        "status": status_msg, 
        "timestamp": datetime.now().isoformat(), 
        "branch": BRANCH,
        "limpeza_executada": cleaned
    }

@app.get("/api/vagas")
async def get_vagas():
    """Retorna vagas do arquivo JSON no GitHub"""
    try:
        # Verifica cache primeiro
        if "vagas_data" in vagas_cache:
            return vagas_cache["vagas_data"]
        
        # Busca vagas do GitHub
        vagas_data = get_vagas_from_github()
        
        # Se não encontrar vagas no GitHub, retorna lista padrão
        if not vagas_data:
            print("Nenhuma vaga encontrada no GitHub, retornando lista padrão")
            vagas_data = [
                {"nome": "Auxiliar de Limpeza"},
                {"nome": "Vendedor"},
                {"nome": "Caixa"},
                {"nome": "Estoquista"},
                {"nome": "Repositor"},
                {"nome": "Atendente"},
                {"nome": "Gerente"},
                {"nome": "Supervisor"},
                {"nome": "Operador de Caixa"}
            ]
        
        # Filtra apenas os objetos que têm o campo 'nome'
        vagas_filtradas = []
        for vaga in vagas_data:
            if isinstance(vaga, dict) and "nome" in vaga:
                vagas_filtradas.append({"nome": vaga["nome"]})
        
        # Armazena no cache
        vagas_cache["vagas_data"] = vagas_filtradas
        
        return vagas_filtradas
        
    except Exception as e:
        print(f"Erro ao obter vagas: {str(e)}")
        # Fallback para vagas padrão
        return [
            {"nome": "Auxiliar de Limpeza"},
            {"nome": "Vendedor"},
            {"nome": "Caixa"},
            {"nome": "Estoquista"}
        ]

@app.get("/api/admin/vagas")
async def admin_list_vagas(status: Optional[str] = None, search: Optional[str] = None):
    """Lista vagas com filtros para o painel admin"""
    try:
        vagas = load_admin_vagas()
        status_filter = (status or "").strip().lower()
        if status_filter:
            vagas = [v for v in vagas if (v.get("status") or "ativa").lower() == status_filter]

        search_filter = (search or "").strip().lower()
        if search_filter:
            def matches(vaga: dict) -> bool:
                nome = (vaga.get("nome") or "").lower()
                dept = (vaga.get("departamento") or vaga.get("inserido_por") or "").lower()
                return (search_filter in nome) or (search_filter in dept)
            vagas = [v for v in vagas if matches(v)]

        return {"ok": True, "vagas": vagas}
    except Exception as e:
        return {"ok": False, "message": str(e)}

@app.post("/api/admin/vagas")
async def admin_create_vaga(payload: dict):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub nÃ£o configurado.")
    nome = (payload.get("nome") or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome da vaga e obrigatorio.")

    vagas = load_admin_vagas()
    vaga_id = normalize_vaga_id(payload.get("id") or nome)
    if any((v.get("id") or "").lower() == vaga_id.lower() for v in vagas):
        raise HTTPException(status_code=409, detail="Ja existe uma vaga com este id.")

    creator = (payload.get("created_by") or payload.get("criado_por") or payload.get("inserido_por") or "").strip()
    now_iso = datetime.now().isoformat()
    nova = {
        "id": vaga_id,
        "nome": nome,
        "status": (payload.get("status") or "ativa").strip(),
        "departamento": payload.get("departamento"),
        "localizacao": payload.get("localizacao"),
        "salario": payload.get("salario"),
        "tipo_contrato": payload.get("tipo_contrato"),
        "vagas_disponiveis": payload.get("vagas_disponiveis"),
        "descricao": payload.get("descricao"),
        "requisitos": payload.get("requisitos"),
        "beneficios": payload.get("beneficios"),
        "created_by": creator or None,
        "created_at": payload.get("created_at") or now_iso,
        "inserido_em": payload.get("inserido_em") or now_iso
    }
    nova = {k: v for k, v in nova.items() if v is not None}
    vagas.append(nova)

    if not save_admin_vagas(vagas, f"Criar vaga {vaga_id}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar vagas no GitHub.")
    return {"ok": True, "message": "Vaga criada com sucesso", "vaga": nova}

@app.get("/api/admin/vagas/{vaga_id}")
async def admin_get_vaga(vaga_id: str):
    vagas = load_admin_vagas()
    idx = find_vaga_index(vagas, vaga_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    return {"ok": True, "vaga": vagas[idx]}

@app.put("/api/admin/vagas/{vaga_id}")
async def admin_update_vaga(vaga_id: str, payload: dict):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub nÃ£o configurado.")
    vagas = load_admin_vagas()
    idx = find_vaga_index(vagas, vaga_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    vaga = dict(vagas[idx])

    if payload.get("nome"):
        vaga["nome"] = payload.get("nome").strip()
    if payload.get("status"):
        vaga["status"] = payload.get("status").strip()
    for key in ["departamento", "localizacao", "salario", "tipo_contrato", "vagas_disponiveis", "descricao", "requisitos", "beneficios"]:
        if key in payload:
            vaga[key] = payload.get(key)

    vaga["updated_at"] = datetime.now().isoformat()
    vagas[idx] = vaga

    if not save_admin_vagas(vagas, f"Atualizar vaga {vaga_id}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar vagas no GitHub.")
    return {"ok": True, "message": "Vaga atualizada com sucesso", "vaga": vaga}

@app.delete("/api/admin/vagas/{vaga_id}")
async def admin_delete_vaga(vaga_id: str):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub nÃ£o configurado.")
    vagas = load_admin_vagas()
    idx = find_vaga_index(vagas, vaga_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    vaga = vagas.pop(idx)
    if not save_admin_vagas(vagas, f"Excluir vaga {vaga_id}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar vagas no GitHub.")
    return {"ok": True, "message": "Vaga excluida com sucesso", "vaga": vaga}




@app.get("/api/empresas")
async def list_empresas_public(search: Optional[str] = None):
    try:
        empresas = load_admin_empresas()
        search_filter = (search or "").strip().lower()
        if search_filter:
            search_digits = re.sub(r"\D", "", search_filter)
            def matches(empresa: dict) -> bool:
                razao = (empresa.get("razao_social") or "").lower()
                fantasia = (empresa.get("nome_fantasia") or "").lower()
                cnpj_digits = re.sub(r"\D", "", empresa.get("cnpj", ""))
                return (
                    search_filter in razao
                    or search_filter in fantasia
                    or (search_digits and search_digits in cnpj_digits)
                )
            empresas = [e for e in empresas if matches(e)]
        empresas = sorted(empresas, key=lambda e: (e.get("razao_social") or e.get("nome_fantasia") or "").lower())
        return {"ok": True, "empresas": empresas}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

@app.get("/api/admin/lideres")
async def admin_list_lideres():
    try:
        lideres, _ = _load_lideres_admin()
        sorted_list = sorted(lideres, key=lambda l: l.get("nome", ""))
        return {"ok": True, "lideres": sorted_list}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

@app.get("/api/admin/empresas")
async def admin_list_empresas(search: Optional[str] = None):
    try:
        empresas = load_admin_empresas()
        search_filter = (search or "").strip().lower()
        if search_filter:
            search_digits = re.sub(r"\D", "", search_filter)
            def matches(empresa: dict) -> bool:
                razao = (empresa.get("razao_social") or "").lower()
                fantasia = (empresa.get("nome_fantasia") or "").lower()
                cnpj_digits = re.sub(r"\D", "", empresa.get("cnpj", ""))
                return (
                    search_filter in razao
                    or search_filter in fantasia
                    or (search_digits and search_digits in cnpj_digits)
                )
            empresas = [e for e in empresas if matches(e)]
        empresas = sorted(empresas, key=lambda e: (e.get("razao_social") or e.get("nome_fantasia") or "").lower())
        return {"ok": True, "empresas": empresas}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

@app.post("/api/admin/empresas")
async def admin_create_empresa(payload: dict):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub nao configurado.")
    cnpj_digits = re.sub(r"\D", "", payload.get("cnpj", ""))
    if len(cnpj_digits) != 14:
        raise HTTPException(status_code=400, detail="CNPJ invalido.")

    empresas = load_admin_empresas()
    if any(re.sub(r"\D", "", (e.get("cnpj") or "")) == cnpj_digits for e in empresas):
        raise HTTPException(status_code=409, detail="Empresa ja cadastrada com este CNPJ.")

    empresa_id = (payload.get("id") or "").strip() or normalize_empresa_id(payload.get("razao_social") or payload.get("nome_fantasia"), cnpj_digits)
    now_iso = datetime.now().isoformat()
    nova = dict(payload)
    nova["id"] = empresa_id
    nova["cnpj"] = cnpj_digits
    nova.setdefault("salvo_em", now_iso)

    empresas.append(nova)
    if not save_admin_empresas(empresas, f"Criar empresa {empresa_id}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar empresas no GitHub.")
    return {"ok": True, "message": "Empresa criada com sucesso", "empresa": nova}

@app.put("/api/admin/empresas/{empresa_id}")
async def admin_update_empresa(empresa_id: str, payload: dict):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub nao configurado.")
    empresas = load_admin_empresas()
    idx = find_empresa_index(empresas, empresa_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Empresa nao encontrada.")

    empresa = dict(empresas[idx])
    if "cnpj" in payload:
        cnpj_digits = re.sub(r"\D", "", payload.get("cnpj", ""))
        if len(cnpj_digits) != 14:
            raise HTTPException(status_code=400, detail="CNPJ invalido.")
        empresa["cnpj"] = cnpj_digits
        if not empresa.get("id"):
            empresa["id"] = normalize_empresa_id(empresa.get("razao_social") or empresa.get("nome_fantasia"), cnpj_digits)

    for key, value in payload.items():
        if key == "cnpj":
            continue
        if isinstance(value, str):
            empresa[key] = value.strip()
        else:
            empresa[key] = value

    empresa["atualizado_em"] = datetime.now().isoformat()
    empresas[idx] = empresa
    if not save_admin_empresas(empresas, f"Atualizar empresa {empresa.get('id') or empresa_id}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar empresas no GitHub.")
    return {"ok": True, "message": "Empresa atualizada com sucesso", "empresa": empresa}

@app.delete("/api/admin/empresas/{empresa_id}")
async def admin_delete_empresa(empresa_id: str):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub nao configurado.")
    empresas = load_admin_empresas()
    idx = find_empresa_index(empresas, empresa_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="Empresa nao encontrada.")
    empresa = empresas.pop(idx)
    if not save_admin_empresas(empresas, f"Excluir empresa {empresa.get('id') or empresa_id}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar empresas no GitHub.")
    return {"ok": True, "message": "Empresa excluida com sucesso", "empresa": empresa}

@app.post("/api/cleanup")
async def manual_cleanup():
    """Endpoint manual para limpeza de candidaturas expiradas"""
    try:
        cleaned = clean_expired_candidates()
        return {
            "ok": True,
            "message": f"✅ Limpeza executada: {cleaned} candidaturas expiradas removidas",
            "removed_count": cleaned
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"❌ Erro durante limpeza: {str(e)}"
        }

@app.get("/api/candidatos/ativos")
async def get_candidatos_ativos():
    """Retorna apenas candidaturas ativas (menos de 90 dias)"""
    try:
        candidates = get_existing_candidates(clean_expired=True)
        # Filtra apenas os ativos
        active_candidates = [c for c in candidates if not is_candidate_expired(c)]
        return {
            "ok": True,
            "count": len(active_candidates),
            "candidatos": active_candidates
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }

@app.get("/api/admin/candidatos")
async def admin_list_candidatos(status: Optional[str] = None, search: Optional[str] = None, expirados: Optional[bool] = False):
    """Lista candidaturas para o painel admin"""
    try:
        candidatos = get_existing_candidates(clean_expired=False)

        if not expirados:
            candidatos = [c for c in candidatos if not is_candidate_expired(c)]

        status_filter = (status or "").strip().lower()
        if status_filter:
            candidatos = [c for c in candidatos if (c.get("status") or "").lower() == status_filter]

        search_filter = (search or "").strip().lower()
        if search_filter:
            search_digits = re.sub(r"\D", "", search_filter)
            def matches(candidate: dict) -> bool:
                nome = (candidate.get("nome") or "").lower()
                email = (candidate.get("email") or "").lower()
                cpf_digits = re.sub(r"\D", "", candidate.get("cpf") or "")
                return (search_filter in nome) or (search_filter in email) or (search_digits and search_digits in cpf_digits)
            candidatos = [c for c in candidatos if matches(c)]

        candidatos.sort(
            key=lambda c: (parse_iso_date(c.get("enviado_em") or "") or datetime.min),
            reverse=True
        )

        return {
            "ok": True,
            "count": len(candidatos),
            "candidatos": candidatos
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }

@app.delete("/api/admin/candidatos/{candidate_id}")
async def admin_delete_candidato(candidate_id: str):
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/candidatos.json"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="Arquivo candidatos.json não encontrado.")
        payload = response.json()
        content = payload.get("content") or ""
        sha = payload.get("sha")
        decoded = base64.b64decode(content).decode("utf-8")
        candidatos = json.loads(decoded)
        if not isinstance(candidatos, list):
            raise HTTPException(status_code=500, detail="Formato inválido em candidatos.json.")

        target = next((c for c in candidatos if str(c.get("id")) == str(candidate_id)), None)
        if not target:
            raise HTTPException(status_code=404, detail="Candidatura não encontrada.")

        candidatos = [c for c in candidatos if str(c.get("id")) != str(candidate_id)]

        updated_content = json.dumps(candidatos, indent=2, ensure_ascii=False)
        content_b64 = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")
        update_data = {
            "message": f"Excluir candidatura: {target.get('nome', 'candidato')} ({candidate_id})",
            "content": content_b64,
            "sha": sha,
            "branch": BRANCH
        }
        update_response = requests.put(url, headers=headers, json=update_data, timeout=30)
        if update_response.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail=f"Erro ao atualizar candidatos.json: {update_response.status_code}")

        if target.get("arquivo_url"):
            try:
                file_url = target.get("arquivo_url", "")
                path_match = re.search(f"{BRANCH}/(.+)", file_url)
                if path_match:
                    delete_github_file(path_match.group(1))
            except Exception:
                pass

        return {"ok": True, "message": "Candidatura excluída com sucesso."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao excluir candidatura: {exc}")

@app.post("/api/admin/candidatos/{candidate_id}/visualizar")
async def admin_visualizar_candidato(candidate_id: str, payload: dict):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub não configurado.")

    viewer = (payload.get("visualizador") or "").strip() or "Usuario"
    candidatos = load_github_json("candidatos.json", default=[])
    if not isinstance(candidatos, list):
        raise HTTPException(status_code=500, detail="Formato inválido em candidatos.json.")

    target = next((c for c in candidatos if str(c.get("id")) == str(candidate_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Candidatura não encontrada.")

    timestamp = datetime.utcnow().isoformat()
    target["ultimo_visualizador"] = viewer
    target["ultimo_visualizado_em"] = timestamp
    history = target.get("view_history")
    if not isinstance(history, list):
        history = []
    history.append({"viewer": viewer, "timestamp": timestamp})
    target["view_history"] = history[-50:]

    ok = save_github_json("candidatos.json", candidatos, f"Registrar visualização {candidate_id}")
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao registrar visualização.")

    return {"ok": True, "candidato": target}

@app.get("/api/admin/dashboard")
async def admin_dashboard():
    """Retorna estatÃ­sticas e listas para o painel do dashboard"""
    try:
        candidatos = get_existing_candidates(clean_expired=False)
        vagas = get_vagas_from_github()

        now = datetime.now()
        status_map: Dict[str, int] = {}
        vaga_map: Dict[str, int] = {}
        city_map: Dict[str, int] = {}
        expired_count = 0
        recent_candidates = []

        for candidato in candidatos:
            status = (candidato.get("status") or "Novo").strip()
            status_map[status] = status_map.get(status, 0) + 1

            vaga = (candidato.get("vaga") or "NÃ£o informada").strip()
            vaga_map[vaga] = vaga_map.get(vaga, 0) + 1

            cidade = (candidato.get("cidade") or "NÃ£o informada").strip()
            city_map[cidade] = city_map.get(cidade, 0) + 1

            enviado_em = parse_iso_date(candidato.get("enviado_em") or "")
            if enviado_em:
                if (now - enviado_em).days >= 90:
                    expired_count += 1
                if (now - enviado_em).days <= 7:
                    recent_candidates.append((enviado_em, candidato))

        recent_candidates.sort(key=lambda item: item[0], reverse=True)
        candidatos_recentes = [c for _, c in recent_candidates[:10]]

        vagas_recentes = []
        for vaga in vagas:
            inserido_em = parse_iso_date(vaga.get("inserido_em") or "")
            vagas_recentes.append((inserido_em or datetime.min, vaga))
        vagas_recentes.sort(key=lambda item: item[0], reverse=True)
        vagas_recentes = [v for _, v in vagas_recentes[:5]]

        total_vagas = len(vagas)
        vagas_ativas = len([v for v in vagas if (v.get("status") or "ativa").lower() == "ativa"])
        vagas_inativas = total_vagas - vagas_ativas

        stats = {
            "total_vagas": total_vagas,
            "vagas_ativas": vagas_ativas,
            "vagas_inativas": vagas_inativas,
            "total_candidatos": len(candidatos),
            "candidatos_ativos": max(0, len(candidatos) - expired_count),
            "candidatos_expirados": expired_count,
            "candidatos_7_dias": len(candidatos_recentes),
            "candidatos_por_status": status_map,
            "candidatos_por_vaga": vaga_map,
            "candidatos_por_cidade": city_map
        }

        return {
            "ok": True,
            "estatisticas": stats,
            "vagas_recentes": vagas_recentes,
            "candidatos_recentes": candidatos_recentes
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }

@app.post("/api/admin/consulta-cpf")
async def admin_consulta_cpf(payload: dict):
    cpf_raw = (payload.get("cpf") or "").strip()
    cpf_digits = re.sub(r"[^\d]", "", cpf_raw)
    if not cpf_digits or len(cpf_digits) != 11 or not validate_cpf(cpf_digits):
        raise HTTPException(status_code=400, detail="CPF inválido.")

    timeout = int(os.getenv("DATAJUD_TIMEOUT", "12"))
    max_workers = int(os.getenv("DATAJUD_WORKERS", "8"))

    try:
        result, summary, metadata = call_datajud_all(
            cpf_digits,
            timeout=timeout,
            max_workers=max_workers
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao consultar Datajud: {exc}")

    return {
        "ok": True,
        "cpf": cpf_digits,
        "result": result,
        "summary": summary,
        "metadata": metadata
    }

@app.post("/api/enviar")
async def enviar_curriculo(
    nome: str = Form(...),
    cpf: str = Form(...),
    telefone: str = Form(...),
    email: str = Form(...),
    cep: str = Form(...),
    cidade: str = Form(...),
    bairro: str = Form(...),
    rua: str = Form(...),
    transporte: str = Form(...),
    vaga: str = Form(...),
    arquivo: UploadFile = File(...)
):
    """Recebe e salva candidatura no GitHub"""
    
    # Verificar token GitHub
    if not GITHUB_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="❌ Token do GitHub não configurado. Configure a variável de ambiente GITHUB_TOKEN."
        )
    
    # Verificar acesso ao repositório
    if not check_repo_access():
        raise HTTPException(
            status_code=500,
            detail="❌ Não é possível acessar o repositório do GitHub. Verifique as permissões do token."
        )
    
    # Validações básicas
    if not nome or len(nome.strip()) < 3:
        raise HTTPException(status_code=400, detail="Nome inválido (mínimo 3 caracteres)")
    
    if not validate_cpf(cpf):
        raise HTTPException(status_code=400, detail="CPF inválido")
    
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_regex, email):
        raise HTTPException(status_code=400, detail="Email inválido")
    
    # Valida arquivo
    if not arquivo.filename:
        raise HTTPException(status_code=400, detail="Nenhum arquivo selecionado")
    
    ext = arquivo.filename.lower().split('.')[-1] if '.' in arquivo.filename else ''
    if ext not in ['pdf', 'doc', 'docx']:
        raise HTTPException(status_code=400, detail="Formato inválido. Use PDF, DOC ou DOCX.")
    
    try:
        # Verifica duplicidade ANTES de fazer qualquer operação
        if check_duplicate_candidate(cpf, vaga):
            raise HTTPException(
                status_code=409,
                detail="⚠️ Já existe uma candidatura registrada para esta vaga com o mesmo CPF. Aguarde 90 dias antes de reenviar."
            )
        
        # Salva arquivo na pasta curriculos do GitHub
        arquivo_url = save_curriculum_to_github(arquivo, nome, cpf, vaga)
        
        # Prepara dados do candidato
        candidato = {
            "nome": nome.strip(),
            "cpf": cpf,
            "telefone": telefone,
            "email": email.lower().strip(),
            "cep": cep,
            "cidade": cidade,
            "bairro": bairro,
            "rua": rua,
            "transporte": transporte,
            "vaga": vaga,
            "arquivo_url": arquivo_url,
            "arquivo_nome": arquivo.filename,
            "tamanho_arquivo": arquivo.size
        }
        
        # Salva dados no GitHub
        result = save_candidate(candidato)
        
        if result["success"]:
            # Backup local obrigatorio: falha local invalida o envio
            try:
                local_filename = None
                if arquivo_url:
                    try:
                        local_filename = os.path.basename(urllib.parse.urlparse(arquivo_url).path)
                    except Exception:
                        local_filename = None
                if not local_filename:
                    local_filename = arquivo.filename or "curriculo"
                local_file_path = save_curriculum_locally(
                    arquivo,
                    local_filename,
                    parse_iso_date(candidato.get("enviado_em")) or datetime.now()
                )
                save_candidate_locally(candidato, local_file_path)
            except Exception as e:
                print(f"Erro ao salvar localmente: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail="❌ Erro ao salvar candidatura localmente. Tente novamente em alguns instantes."
                )
            return {
                "ok": True,
                "message": "✅ Sua candidatura foi enviada com sucesso! Agradecemos seu interesse e entraremos em contato caso seu perfil seja selecionado.",
                "id": candidato.get("id"),
                "arquivo_url": arquivo_url
            }
        else:
            # Se chegou aqui, é um erro no GitHub que não é duplicidade
            raise HTTPException(
                status_code=500,
                detail="❌ Erro ao salvar candidatura. Tente novamente em alguns instantes."
            )
                
    except HTTPException as he:
        # Re-lançar as HTTPExceptions que já foram levantadas
        raise he
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"Erro interno no envio: {str(e)}")
        # Mensagem genérica para o usuário
        raise HTTPException(
            status_code=500, 
            detail="❌ Erro ao salvar candidatura. Tente novamente em alguns instantes."
        )



# ==================== ATON NOTIFICACOES ====================
NOTIFICATION_CACHE = TTLCache(maxsize=200, ttl=300)
NEWS_CACHE = TTLCache(maxsize=1, ttl=1800)
WEATHER_CACHE = TTLCache(maxsize=1, ttl=900)
GEOCODE_CACHE = TTLCache(maxsize=1, ttl=86400)
BIRTHDAY_CACHE = TTLCache(maxsize=1, ttl=300)

DEFAULT_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=recursos+humanos+gest%C3%A3o+de+pessoas&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://news.google.com/rss/search?q=recursos+humanos+Brasil&hl=pt-BR&gl=BR&ceid=BR:pt-419",
]

MONTH_CAMPAIGNS = {
    1: ["Janeiro Branco (saude mental)"],
    2: ["Fevereiro Roxo (lupus, fibromialgia e Alzheimer)", "Fevereiro Laranja (leucemia)"],
    3: ["Marco Lilas (prevencao ao cancer do colo do utero)", "Marco Azul-Marinho (cancer colorretal)"],
    4: ["Abril Azul (conscientizacao sobre o autismo)"],
    5: ["Maio Amarelo (seguranca no transito)", "Maio Vermelho (hepatites)"],
    6: ["Junho Vermelho (doacao de sangue)"],
    7: ["Julho Amarelo (hepatites virais)", "Julho Verde (cancer de cabeca e pescoco)"] ,
    8: ["Agosto Dourado (aleitamento materno)", "Agosto Lilas (enfrentamento a violencia contra a mulher)"],
    9: ["Setembro Amarelo (prevencao ao suicidio)", "Setembro Verde (doacao de orgaos)"] ,
    10: ["Outubro Rosa (prevencao ao cancer de mama)"],
    11: ["Novembro Azul (prevencao ao cancer de prostata)"],
    12: ["Dezembro Vermelho (HIV e ISTs)"],
}

MONTH_NAMES = {
    1: "janeiro",
    2: "fevereiro",
    3: "marco",
    4: "abril",
    5: "maio",
    6: "junho",
    7: "julho",
    8: "agosto",
    9: "setembro",
    10: "outubro",
    11: "novembro",
    12: "dezembro",
}

WEATHER_CODE_MAP = {
    0: "ceu limpo",
    1: "predominantemente limpo",
    2: "parcialmente nublado",
    3: "nublado",
    45: "neblina",
    48: "neblina com gelo",
    51: "chuvisco fraco",
    53: "chuvisco moderado",
    55: "chuvisco intenso",
    61: "chuva fraca",
    63: "chuva moderada",
    65: "chuva forte",
    66: "chuva congelante fraca",
    67: "chuva congelante forte",
    71: "neve fraca",
    73: "neve moderada",
    75: "neve forte",
    80: "pancadas de chuva fraca",
    81: "pancadas de chuva moderada",
    82: "pancadas de chuva forte",
    95: "trovoadas",
    96: "trovoadas com granizo",
    99: "trovoadas fortes com granizo",
}

# ==================== ATON CHAT ====================
ATON_HISTORY_PATH = DATA_DIR / "data" / "aton_mensagens.json"
ATON_MAX_HISTORY = 40
ATON_SITE_URL = os.getenv("ATON_SITE_URL") or "https://popular-atacarejo.local"
ATON_SITE_TITLE = os.getenv("ATON_SITE_TITLE") or "Popular RH"

class AtonChatRequest(BaseModel):
    question: str
    user_name: Optional[str] = "Usuario"
    user_id: Optional[str] = None

def _aton_user_key(user_id: Optional[str], user_name: Optional[str]) -> str:
    raw = (user_id or user_name or "usuario").strip()
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    return cleaned or "usuario"

def _load_aton_payload() -> Dict[str, Any]:
    if not ATON_HISTORY_PATH.exists():
        return {"chat": {}, "notifications": {}}
    try:
        payload = json.loads(ATON_HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"chat": {}, "notifications": {}}
        if "chat" in payload or "notifications" in payload:
            chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}
            notifications = payload.get("notifications") if isinstance(payload.get("notifications"), dict) else {}
            return {"chat": chat, "notifications": notifications}
        return {"chat": payload, "notifications": {}}
    except Exception:
        return {"chat": {}, "notifications": {}}

def _save_aton_payload(chat: Dict[str, List[Dict[str, Any]]], notifications: Dict[str, Any]) -> None:
    ATON_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"chat": chat, "notifications": notifications}
    ATON_HISTORY_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

def _load_aton_history() -> Dict[str, List[Dict[str, Any]]]:
    payload = _load_aton_payload()
    return payload.get("chat") or {}

def _load_aton_notifications() -> Dict[str, Any]:
    payload = _load_aton_payload()
    return payload.get("notifications") or {}

def _get_user_history(user_key: str) -> List[Dict[str, Any]]:
    history = _load_aton_history().get(user_key, [])
    if isinstance(history, list):
        return [entry for entry in history if entry.get("role") in {"user", "assistant"}]
    return []

def _set_user_history(user_key: str, history: List[Dict[str, Any]]) -> None:
    chat = _load_aton_history()
    notifications = _load_aton_notifications()
    chat[user_key] = history[-ATON_MAX_HISTORY:]
    _save_aton_payload(chat, notifications)

def _get_daily_notifications(user_key: str, date_key: str) -> List[str]:
    notifications = _load_aton_notifications()
    user_log = notifications.get(user_key) if isinstance(notifications, dict) else {}
    if not isinstance(user_log, dict):
        return []
    daily = user_log.get(date_key) or []
    return [str(item) for item in daily if item]

def _record_daily_notification(user_key: str, date_key: str, message: str) -> None:
    if not message:
        return
    chat = _load_aton_history()
    notifications = _load_aton_notifications()
    user_log = notifications.get(user_key)
    if not isinstance(user_log, dict):
        user_log = {}
    daily = user_log.get(date_key)
    if not isinstance(daily, list):
        daily = []
    if message not in daily:
        daily.append(message)
    user_log[date_key] = daily[-20:]
    notifications[user_key] = user_log
    _save_aton_payload(chat, notifications)

HR_TIPS = [
    "um feedback curto e frequente tende a reduzir retrabalho e aumentar engajamento",
    "clareza de expectativas no primeiro mes melhora a retencao de novos colaboradores",
    "reconhecimento publico e especifico costuma aumentar a motivacao do time",
    "uma boa escuta ativa diminui conflitos e melhora a confianca",
    "processos seletivos com etapas objetivas ajudam a reduzir vieses",
]


def _polite_prefix(user_name: str) -> str:
    cleaned = (user_name or "").strip()
    return f"Ola, {cleaned}! " if cleaned else "Ola! "


def _load_feriados_file() -> dict:
    file_path = DATA_DIR / "feriados.json"
    if not file_path.exists():
        return {"location": {"state": "AL", "city": "Arapiraca"}, "holidays": []}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {"location": {"state": "AL", "city": "Arapiraca"}, "holidays": []}


def _normalize_holidays(items, year: int):
    normalized = []
    for entry in items or []:
        entry = dict(entry)
        raw_date = entry.get("date")
        iso_date = None
        if isinstance(raw_date, str):
            if raw_date.count("-") == 1:
                day, month = raw_date.split("-")
                try:
                    iso_date = datetime(year, int(month), int(day)).strftime("%Y-%m-%d")
                except ValueError:
                    iso_date = None
            elif raw_date.count("-") == 2 and len(raw_date) >= 10:
                iso_date = raw_date[:10]
        if iso_date:
            entry["date"] = iso_date
        normalized.append(entry)
    return normalized


def _get_feriados_payload() -> dict:
    payload = _load_feriados_file()
    year = datetime.now().year
    payload["holidays"] = _normalize_holidays(payload.get("holidays", []), year)
    payload.setdefault("location", {"state": "AL", "city": "Arapiraca"})
    return payload


def _get_default_location() -> dict:
    payload = _get_feriados_payload()
    return payload.get("location") or {"state": "AL", "city": "Arapiraca"}


def _find_next_holiday(scope: Optional[str] = None, location: Optional[str] = None) -> Optional[str]:
    try:
        payload = _get_feriados_payload()
        holidays = payload.get("holidays") or []
        today = datetime.now().date()
        candidates = []
        for holiday in holidays:
            if scope and holiday.get("scope") != scope:
                continue
            if location:
                loc = (holiday.get("location") or "").lower()
                if location.lower() not in loc and holiday.get("scope") != "nacional":
                    continue
            raw_date = holiday.get("date")
            if not raw_date:
                continue
            try:
                date_obj = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if date_obj >= today:
                candidate_date = date_obj
            else:
                try:
                    candidate_date = date_obj.replace(year=today.year + 1)
                except ValueError:
                    continue
            candidates.append((candidate_date, holiday))
        if not candidates:
            return None
        next_date, next_holiday = sorted(candidates, key=lambda item: item[0])[0]
        name = next_holiday.get("name") or "Feriado"
        return f"Proximo feriado {scope or 'nacional'}: {name} em {next_date.strftime('%d/%m/%Y')}."
    except Exception:
        return None


def _geocode_city(city: str, state: str) -> Optional[dict]:
    cache_key = f"geo::{city}::{state}".lower()
    if cache_key in GEOCODE_CACHE:
        return GEOCODE_CACHE[cache_key]
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "pt", "format": "json", "state": state, "country": "BR"},
            timeout=8,
        )
        if response.status_code != 200:
            return None
        payload = response.json() or {}
        results = payload.get("results") or []
        if not results:
            return None
        result = results[0]
        GEOCODE_CACHE[cache_key] = result
        return result
    except Exception:
        return None


def _get_weather_summary() -> Optional[str]:
    if "weather" in WEATHER_CACHE:
        return WEATHER_CACHE["weather"]
    location = _get_default_location()
    city = (location.get("city") or "Arapiraca").strip()
    state = (location.get("state") or "AL").strip()
    geocode = _geocode_city(city, state)
    if not geocode:
        return None
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": geocode.get("latitude"),
                "longitude": geocode.get("longitude"),
                "current_weather": True,
                "timezone": "America/Maceio",
            },
            timeout=8,
        )
        if response.status_code != 200:
            return None
        payload = response.json() or {}
        current = payload.get("current_weather") or {}
        temp = current.get("temperature")
        code = current.get("weathercode")
        description = WEATHER_CODE_MAP.get(code, "tempo estavel")
        if temp is None:
            return None
        summary = f"Previsao agora em {city}/{state}: {temp:.0f}C e {description}."
        WEATHER_CACHE["weather"] = summary
        return summary
    except Exception:
        return None


def _get_birthdays_today() -> Optional[str]:
    if "birthdays" in BIRTHDAY_CACHE:
        return BIRTHDAY_CACHE["birthdays"]
    try:
        file_path = DATA_DIR / "funcionarios-ativos.json"
        if not file_path.exists():
            return None
        employees = json.loads(file_path.read_text(encoding="utf-8"))
        today = datetime.now().date()
        names = []
        for emp in employees:
            birth = emp.get("data_nascimento") or emp.get("dataNascimento")
            if not birth:
                continue
            try:
                birth_date = datetime.strptime(birth[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if birth_date.month == today.month and birth_date.day == today.day:
                name = (emp.get("nome_completo") or emp.get("nome") or "").strip()
                if name:
                    names.append(name)
        if names:
            message = "Aniversariantes de hoje: " + ", ".join(names) + "."
        else:
            message = "Hoje nao ha aniversariantes registrados na base."
        BIRTHDAY_CACHE["birthdays"] = message
        return message
    except Exception:
        return None

def _get_birthdays_today_list() -> List[str]:
    try:
        file_path = DATA_DIR / "funcionarios-ativos.json"
        if not file_path.exists():
            return []
        employees = json.loads(file_path.read_text(encoding="utf-8"))
        today = datetime.now().date()
        names = []
        for emp in employees:
            birth = emp.get("data_nascimento") or emp.get("dataNascimento")
            if not birth:
                continue
            try:
                birth_date = datetime.strptime(birth[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if birth_date.month == today.month and birth_date.day == today.day:
                name = (emp.get("nome_completo") or emp.get("nome") or "").strip()
                if name:
                    names.append(name)
        return names
    except Exception:
        return []

def _get_month_holidays() -> List[Dict[str, Any]]:
    payload = _get_feriados_payload()
    holidays = payload.get("holidays") or []
    today = datetime.now().date()
    month = today.month
    year = today.year
    results = []
    for holiday in holidays:
        raw_date = holiday.get("date")
        if not raw_date:
            continue
        try:
            date_obj = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if date_obj.month == month and date_obj.year == year:
            results.append({
                "name": holiday.get("name") or "Feriado",
                "date": date_obj.strftime("%d/%m/%Y"),
                "scope": holiday.get("scope") or ""
            })
    return results

def _get_month_holidays_summary() -> Optional[str]:
    holidays = _get_month_holidays()
    month_label = MONTH_NAMES.get(datetime.now().month, "este mes")
    if not holidays:
        return f"Sem feriados registrados para {month_label}."
    parts = [f"{item['name']} ({item['date']})" for item in holidays]
    return f"Feriados de {month_label}: " + ", ".join(parts) + "."

def _get_daily_digest() -> Optional[str]:
    birthdays = _get_birthdays_today_list()
    birthdays_text = (
        "Aniversariantes de hoje: " + ", ".join(birthdays) + "."
        if birthdays else "Hoje nao ha aniversariantes registrados na base."
    )
    holidays_text = _get_month_holidays_summary() or ""
    if holidays_text:
        return f"{birthdays_text} {holidays_text}"
    return birthdays_text


def _fetch_news_headline() -> Optional[str]:
    if "news" in NEWS_CACHE:
        return NEWS_CACHE["news"]
    for feed_url in DEFAULT_NEWS_FEEDS:
        try:
            response = requests.get(feed_url, timeout=8)
            if response.status_code != 200:
                continue
            root = ET.fromstring(response.text)
            item = root.find(".//item")
            if item is None:
                continue
            title = item.findtext("title") or ""
            source = item.findtext("source") or ""
            title = html.unescape(title).strip()
            source = html.unescape(source).strip()
            if not title:
                continue
            news = f"Novidade em RH: {title}" + (f" ({source})" if source else "") + "."
            NEWS_CACHE["news"] = news
            return news
        except Exception:
            continue
    return None


def _get_month_campaign() -> Optional[str]:
    month = datetime.now().month
    campaigns = MONTH_CAMPAIGNS.get(month) or []
    if not campaigns:
        return None
    month_label = MONTH_NAMES.get(month, "este mes")
    campaign_text = "; ".join(campaigns)
    return f"Campanha de {month_label}: {campaign_text}."


def _get_hr_tip() -> str:
    tip = random.choice(HR_TIPS)
    return f"Curiosidade de RH: {tip}."


def build_random_notification(user_name: str = "Usuario", exclude: Optional[List[str]] = None) -> str:
    exclude = set(exclude or [])
    topics = [
        "daily_digest",
        "campaign",
        "holiday_national",
        "holiday_state",
        "holiday_month",
        "weather",
        "birthdays",
        "news",
        "hr_tip",
    ]
    random.shuffle(topics)

    for topic in topics:
        if topic == "daily_digest":
            message = _get_daily_digest()
        elif topic == "campaign":
            message = _get_month_campaign()
        elif topic == "holiday_national":
            message = _find_next_holiday("nacional")
        elif topic == "holiday_state":
            message = _find_next_holiday("estadual", "Alagoas")
        elif topic == "holiday_month":
            message = _get_month_holidays_summary()
        elif topic == "weather":
            message = _get_weather_summary()
        elif topic == "birthdays":
            message = _get_birthdays_today()
        elif topic == "news":
            message = _fetch_news_headline()
        elif topic == "hr_tip":
            message = _get_hr_tip()
        else:
            message = None

        if message:
            final_message = _polite_prefix(user_name) + message + " Se precisar, posso detalhar."
            if final_message not in exclude:
                return final_message

    timestamp = datetime.now().strftime("%H:%M")
    return _polite_prefix(user_name) + f"Nenhuma novidade nova por agora ({timestamp})."


@app.get("/api/aton/notification")
async def aton_notification(user_name: Optional[str] = "Usuario", user_id: Optional[str] = None):
    resolved_name = (user_name or "Usuario").strip() or "Usuario"
    user_key = _aton_user_key(user_id, resolved_name)
    today_key = datetime.now().strftime("%Y-%m-%d")
    daily_sent = _get_daily_notifications(user_key, today_key)
    message = build_random_notification(resolved_name, exclude=daily_sent)
    _record_daily_notification(user_key, today_key, message)
    return {"ok": True, "message": message}

@app.get("/api/aton/history")
async def aton_history(user_name: Optional[str] = "Usuario", user_id: Optional[str] = None):
    user_key = _aton_user_key(user_id, user_name)
    history = _get_user_history(user_key)
    return {"ok": True, "history": history}

@app.post("/api/aton/chat")
async def aton_chat(payload: AtonChatRequest):
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Pergunta vazia.")

    user_name = (payload.user_name or "Usuario").strip() or "Usuario"
    user_id = payload.user_id
    user_key = _aton_user_key(user_id, user_name)

    history = _get_user_history(user_key)
    try:
        agent = AtonAgent(site_url=ATON_SITE_URL, site_title=ATON_SITE_TITLE)
        if history:
            agent.apply_history(history[-ATON_MAX_HISTORY:])
        answer = agent.ask(question, quiet=True, user_name=user_name, user_id=user_id)
    except Exception as exc:
        print(f"Erro no Aton Assistant: {exc}")
        raise HTTPException(status_code=500, detail="Falha ao consultar o Aton. Verifique a chave da IA.")

    updated_history = history + [
        {"role": "user", "content": question, "label": user_name},
        {"role": "assistant", "content": answer, "label": "Aton Assistant"},
    ]
    _set_user_history(user_key, updated_history)

    return {"ok": True, "answer": answer}

# ==================== CONFIGURACOES / AUTH ====================

@app.post("/api/auth/login")
async def auth_login(payload: dict):
    email = (payload.get("email") or "").strip().lower()
    senha = payload.get("senha") or ""
    if not email or not senha:
        raise HTTPException(status_code=400, detail="Email e senha sao obrigatorios.")
    users = load_auth_users()
    user = next((u for u in users if (u.get("email") or "").lower() == email), None)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    if user.get("senha") != hash_password(senha):
        raise HTTPException(status_code=401, detail="Credenciais invalidas.")
    token = issue_auth_token(user)
    return {"ok": True, "token": token, "user": strip_user_sensitive(user)}

@app.get("/api/auth/me")
async def auth_me(request: Request):
    token = _get_auth_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Token ausente.")
    record = AUTH_TOKENS.get(token)
    if not record:
        raise HTTPException(status_code=401, detail="Token invalido.")
    email = (record.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Token invalido.")
    users = load_auth_users()
    user = next((u for u in users if (u.get("email") or "").lower() == email), None)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    return {"ok": True, "user": strip_user_sensitive(user)}

@app.get("/api/admin/configuracoes")
async def get_configuracoes():
    config = load_github_json("configuracoes.json", default={})
    if not isinstance(config, dict):
        config = {}
    local_config = load_local_config()
    if isinstance(local_config, dict) and local_config.get("github"):
        merged_github = dict(config.get("github", {}))
        merged_github.update(local_config.get("github") or {})
        config["github"] = merged_github
    if isinstance(local_config, dict) and local_config.get("env"):
        merged_env = dict(config.get("env", {}))
        merged_env.update(local_config.get("env") or {})
        config["env"] = merged_env
    if isinstance(local_config, dict) and local_config.get("data_sources"):
        merged_sources = dict(config.get("data_sources", {}))
        merged_sources.update(local_config.get("data_sources") or {})
        config["data_sources"] = merged_sources
    users = [strip_user_sensitive(u) for u in load_auth_users()]
    config["usuarios"] = users
    return {"ok": True, "configuracoes": config, "usuarios": users}

@app.put("/api/admin/configuracoes")
async def update_configuracoes(payload: dict):
    github_payload = payload.get("github", {}) if isinstance(payload, dict) else {}
    if not isinstance(github_payload, dict):
        github_payload = {}
    env_payload = payload.get("env", {}) if isinstance(payload, dict) else {}
    if not isinstance(env_payload, dict):
        env_payload = {}
    apply_env_settings(env_payload, persist=True)
    apply_github_settings(github_payload, persist=True)
    data_sources = payload.get("data_sources", {}) if isinstance(payload, dict) else {}
    if not isinstance(data_sources, dict):
        data_sources = {}
    current_local = load_local_config()
    if isinstance(current_local, dict):
        current_local["data_sources"] = data_sources
        save_local_config(current_local)
    if not GITHUB_TOKEN:
        return {
            "ok": True,
            "local_only": True,
            "message": "Token do GitHub não configurado. Configurações salvas localmente."
        }
    github_config = {
        "owner": (github_payload.get("owner") or "").strip(),
        "repo": (github_payload.get("repo") or github_payload.get("repository") or "").strip(),
        "branch": (github_payload.get("branch") or "").strip() or "main"
    }
    config = {
        "data_sources": payload.get("data_sources", {}),
        "github": github_config,
        "agent": payload.get("agent", {}),
        "pages": payload.get("pages", {}),
    }
    ok = save_github_json("configuracoes.json", config, "Atualizar configuracoes do painel")
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao salvar configuracoes no GitHub.")
    return {"ok": True}


@app.post("/api/admin/configuracoes/github-init")
async def init_github_repository(payload: dict):
    github_payload = payload.get("github", {}) if isinstance(payload, dict) else {}
    if not isinstance(github_payload, dict):
        github_payload = {}
    apply_github_settings(github_payload, persist=True)
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub n?o configurado.")
    owner = (GITHUB_OWNER or "").strip()
    repo = (GITHUB_REPO or "").strip()
    if not owner or not repo:
        raise HTTPException(status_code=400, detail="Informe dono e nome do repositorio.")

    repo_url = f"https://api.github.com/repos/{owner}/{repo}"
    response = requests.get(repo_url, headers=headers, timeout=10)
    if response.status_code == 404:
        user_login = None
        user_response = requests.get("https://api.github.com/user", headers=headers, timeout=10)
        if user_response.status_code == 200:
            user_login = user_response.json().get("login")
        visibility = (github_payload.get("visibility") or "private").lower()
        repo_payload = {
            "name": repo,
            "private": visibility != "public",
            "auto_init": True,
            "description": github_payload.get("description") or "Repositorio de dados do painel"
        }
        if user_login and owner.lower() == user_login.lower():
            create_url = "https://api.github.com/user/repos"
        else:
            create_url = f"https://api.github.com/orgs/{owner}/repos"
        create_response = requests.post(create_url, headers=headers, json=repo_payload, timeout=20)
        if create_response.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail=f"Erro ao criar repositorio: {create_response.text}")
    elif response.status_code not in [200, 403]:
        raise HTTPException(status_code=500, detail=f"Erro ao acessar repositorio: {response.status_code}")

    branch_override = (github_payload.get("branch") or "").strip()
    if branch_override:
        globals()["BRANCH"] = branch_override
    else:
        globals()["BRANCH"] = get_repo_default_branch()

    initialize_repository()
    return {"ok": True}

@app.post("/api/admin/configuracoes/usuarios")

async def create_usuario(payload: dict):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub não configurado.")
    email = (payload.get("email") or "").strip().lower()
    nome = (payload.get("nome") or "").strip()
    funcao = (payload.get("funcao") or "").strip()
    paginas = payload.get("paginas") or payload.get("pages") or []
    if not email or not nome or not funcao:
        raise HTTPException(status_code=400, detail="Nome, e-mail e funcao sao obrigatorios.")
    users = load_auth_users()
    if any((u.get("email") or "").lower() == email for u in users):
        raise HTTPException(status_code=409, detail="Usuario ja existe.")
    senha_plana = secrets.token_urlsafe(8)
    user = {
        "email": email,
        "senha": hash_password(senha_plana),
        "nome": nome,
        "funcao": funcao,
    }
    if paginas:
        user["paginas"] = paginas
    users.append(user)
    if not save_auth_users(users, f"Criar usuario {email}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar auth.json no GitHub.")
    return {"ok": True, "senha": senha_plana, "user": strip_user_sensitive(user)}

@app.put("/api/admin/configuracoes/usuarios/{email}")
async def update_usuario(email: str, payload: dict):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="Token do GitHub não configurado.")
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email invalido.")
    users = load_auth_users()
    updated = None
    for user in users:
        if (user.get("email") or "").lower() == email_norm:
            if payload.get("nome"):
                user["nome"] = payload.get("nome").strip()
            if payload.get("email"):
                user["email"] = payload.get("email").strip().lower()
            if payload.get("funcao"):
                user["funcao"] = payload.get("funcao").strip()
            if payload.get("senha"):
                user["senha"] = hash_password(payload.get("senha"))
            paginas = payload.get("paginas") or payload.get("pages")
            if paginas is not None:
                if paginas:
                    user["paginas"] = paginas
                else:
                    user.pop("paginas", None)
            updated = user
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    if not save_auth_users(users, f"Atualizar usuario {email_norm}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar auth.json no GitHub.")
    return {"ok": True, "user": strip_user_sensitive(updated)}
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

