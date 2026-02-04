from datetime import datetime, timedelta
import json
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from app_paths import DATA_DIR

router = APIRouter(prefix="/api/atestados", tags=["atestados"])

ATESTADO_DIR = DATA_DIR / "atestado"
ATESTADOS_FILE = ATESTADO_DIR / "atestado.json"
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}


def _ensure_storage() -> None:
    ATESTADO_DIR.mkdir(parents=True, exist_ok=True)


def _load_atestados() -> List[dict]:
    if not ATESTADOS_FILE.exists():
        return []
    try:
        return json.loads(ATESTADOS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _persist_atestados(atestados: List[dict]) -> None:
    ATESTADOS_FILE.write_text(json.dumps(atestados, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_cpf(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(filter(str.isdigit, value))


def _calculate_final_date(start_date: str, dias: int) -> str:
    try:
        parsed = datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Data de início inválida. Use YYYY-MM-DD.")
    if dias < 1:
        raise HTTPException(status_code=400, detail="Informe um período de pelo menos 1 dia.")
    final = parsed + timedelta(days=dias - 1)
    return final.date().isoformat()


async def _save_document(file: UploadFile) -> dict:
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Nenhum documento enviado.")
    extension = Path(file.filename).suffix.lstrip(".").lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato inválido. Envie PDF, JPG, JPEG ou PNG.")

    filename = f"{uuid.uuid4().hex}.{extension}"
    destination = ATESTADO_DIR / filename
    contents = await file.read()
    destination.write_bytes(contents)

    return {
        "original_name": file.filename,
        "stored_path": destination.as_posix(),
        "size": len(contents),
        "mimetype": file.content_type or "application/octet-stream"
    }


@router.get("/")
async def list_atestados(q: Optional[str] = None):
    entries = _load_atestados()
    if q:
        needle = q.strip().lower()
        entries = [
            record
            for record in entries
            if needle in (record.get("funcionario_nome") or "").lower()
            or needle in (record.get("funcionario_cpf") or "")
        ]
    return {"ok": True, "count": len(entries), "atestados": entries}


@router.get("/{atestado_id}")
async def get_atestado_detail(atestado_id: str):
    entries = _load_atestados()
    entry = next((item for item in entries if item.get("id") == atestado_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Atestado não encontrado")
    return {"ok": True, "atestado": entry}


@router.post("/")
async def register_atestado(
    funcionario_nome: str = Form(...),
    funcionario_cpf: str = Form(...),
    funcionario_id: Optional[str] = Form(None),
    empresa: Optional[str] = Form(""),
    setor: Optional[str] = Form(""),
    funcao: Optional[str] = Form(""),
    data_inicio: str = Form(...),
    dias: int = Form(...),
    observacoes: Optional[str] = Form(""),
    documento: UploadFile = File(...),
):
    _ensure_storage()

    cpf_clean = _normalize_cpf(funcionario_cpf)
    if len(cpf_clean) != 11:
        raise HTTPException(status_code=400, detail="CPF inválido")

    final_date = _calculate_final_date(data_inicio, dias)
    metadata = await _save_document(documento)

    record = {
        "id": uuid.uuid4().hex,
        "funcionario_id": funcionario_id,
        "funcionario_nome": funcionario_nome.strip(),
        "funcionario_cpf": cpf_clean,
        "empresa": (empresa or "").strip(),
        "setor": (setor or "").strip(),
        "funcao": (funcao or "").strip(),
        "data_inicio": data_inicio,
        "periodo_dias": dias,
        "data_final": final_date,
        "observacoes": (observacoes or "").strip(),
        "documento": metadata,
        "registrado_em": datetime.utcnow().isoformat() + "Z",
    }

    entries = _load_atestados()
    entries.append(record)
    _persist_atestados(entries)

    return {"ok": True, "atestado": record}
