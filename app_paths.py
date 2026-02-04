from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)).resolve()
else:
    APP_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = APP_DIR


def _resolve_documents_dir() -> Path:
    user_profile = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    candidates: list[Path] = []
    onedrive = (
        os.environ.get("OneDrive")
        or os.environ.get("OneDriveConsumer")
        or os.environ.get("OneDriveCommercial")
    )
    if onedrive:
        candidates.append(Path(onedrive) / "Documentos")
        candidates.append(Path(onedrive) / "Documents")
    candidates.append(user_profile / "Documentos")
    candidates.append(user_profile / "Documents")

    for base in candidates:
        if base.exists():
            return base
    return user_profile / "Documents"

def _resolve_data_dir() -> Path:
    override = os.environ.get("APP_DATA_DIR") or os.environ.get("DATA_DIR")
    if override:
        return Path(override).expanduser()
    return _resolve_documents_dir() / "Dados Funcionarios"


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

SEED_FILES = [
    "funcionarios-ativos.json",
    "candidatos.json",
    "empresas.json",
    "funcoes.json",
    "setores.json",
    "feriados.json",
    "vagas.json",
    "reprovados.json",
    "funcionarios-ativos.json",
    "data/funcionarios_registros.json",
    "data/lideres.json",
    "data/feriados.json",
    "data/atestados.json",
    "data/aton_mensagens.json",
    "data/configuracoes_local.json",
    "desligados/Ex-funcionarios.json",
    "Advertencia/Advertencia.json",
]


def ensure_data_seed() -> None:
    for folder in ("data", "uploads", "Advertencia", "desligados"):
        (DATA_DIR / folder).mkdir(parents=True, exist_ok=True)

    for rel_path in SEED_FILES:
        dest = DATA_DIR / rel_path
        if dest.exists():
            continue
        src = RESOURCE_DIR / rel_path
        if not src.exists() and APP_DIR != RESOURCE_DIR:
            alt = APP_DIR / rel_path
            if alt.exists():
                src = alt
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
