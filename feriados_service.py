from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from cachetools import TTLCache
from app_paths import DATA_DIR
from fastapi import HTTPException

FERIADOS_PATH = DATA_DIR / "feriados.json"
CACHE = TTLCache(maxsize=1, ttl=600)

DEFAULT_PAYLOAD = {
    "location": {"country": "BR", "state": "AL", "city": "Arapiraca"},
    "holidays": [
        {"date": "01-01", "name": "Ano Novo / Confraternização Universal", "type": "feriado", "scope": "nacional"},
        {"date": "02-01", "name": "Confraternização Universal", "type": "feriado", "scope": "nacional"},
        {"date": "02-02", "name": "Nossa Senhora do Bom Conselho", "type": "feriado", "scope": "municipal", "location": "Arapiraca"},
        {"date": None, "name": "Carnaval", "type": "ponto_facultativo", "scope": "nacional", "observations": "Data móvel (fevereiro ou março)"},
        {"date": None, "name": "Sexta-Feira da Paixão", "type": "feriado", "scope": "nacional", "observations": "Data móvel"},
        {"date": "21-04", "name": "Tiradentes", "type": "feriado", "scope": "nacional"},
        {"date": "01-05", "name": "Dia do Trabalhador", "type": "feriado", "scope": "nacional"},
        {"date": None, "name": "Corpus Christi", "type": "feriado", "scope": "municipal", "location": "Arapiraca", "observations": "Data móvel"},
        {"date": "24-06", "name": "São João", "type": "feriado", "scope": "estadual", "location": "Alagoas"},
        {"date": "29-06", "name": "São Pedro", "type": "feriado", "scope": "estadual", "location": "Alagoas"},
        {"date": "07-09", "name": "Independência do Brasil", "type": "feriado", "scope": "nacional"},
        {"date": "16-09", "name": "Emancipação Política de Alagoas", "type": "feriado", "scope": "estadual", "location": "Alagoas"},
        {"date": "12-10", "name": "Nossa Senhora Aparecida", "type": "feriado", "scope": "nacional"},
        {"date": "28-10", "name": "Dia do Servidor Público", "type": "ponto_facultativo", "scope": "nacional"},
        {"date": "30-10", "name": "Emancipação Política de Arapiraca", "type": "feriado", "scope": "municipal", "location": "Arapiraca"},
        {"date": "02-11", "name": "Finados", "type": "feriado", "scope": "nacional"},
        {"date": "15-11", "name": "Proclamação da República", "type": "feriado", "scope": "nacional"},
        {"date": "20-11", "name": "Consciência Negra", "type": "feriado", "scope": "estadual", "location": "Alagoas"},
        {"date": "30-11", "name": "Dia do Evangélico", "type": "feriado", "scope": "estadual", "location": "Alagoas"},
        {"date": "25-12", "name": "Natal", "type": "feriado", "scope": "nacional"}
    ]
}


def _ensure_local_file() -> None:
    if FERIADOS_PATH.exists():
        return
    FERIADOS_PATH.write_text(json.dumps(DEFAULT_PAYLOAD, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_feriados() -> Dict[str, Any]:
    _ensure_local_file()
    raw = FERIADOS_PATH.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Arquivo de feriados inválido: {exc}")
    raise HTTPException(status_code=500, detail="Conteúdo inválido em feriados.json")


def _normalize_holidays(items, year: int):
    normalized = []
    for entry in (items or []):
        entry = dict(entry)
        raw_date = entry.get("date")
        iso_date = None
        if isinstance(raw_date, str) and raw_date.count("-") == 1:
            day, month = raw_date.split("-")
            try:
                iso_date = datetime(year, int(month), int(day)).strftime("%Y-%m-%d")
            except ValueError:
                iso_date = None
        if iso_date:
            entry["date"] = iso_date
        normalized.append(entry)
    return normalized


def _persist(data: Dict[str, Any]) -> None:
    FERIADOS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_feriados(year: int) -> Dict[str, Any]:
    if "payload" in CACHE:
        payload = CACHE["payload"]
    else:
        payload = _load_feriados()
        CACHE["payload"] = payload
    normalized = _normalize_holidays(payload.get("holidays", []), year)
    return {
        "ano": year,
        "location": payload.get("location", {}),
        "holidays": normalized
    }


def refresh_feriados(year: int) -> Dict[str, Any]:
    payload = _load_feriados()
    CACHE["payload"] = payload
    normalized = _normalize_holidays(payload.get("holidays", []), year)
    return {
        "ano": year,
        "location": payload.get("location", {}),
        "holidays": normalized
    }


def add_manual_holiday(year: int, holiday: Dict[str, Any]) -> Dict[str, Any]:
    payload = _load_feriados()
    payload.setdefault("holidays", [])
    payload.setdefault("location", {"country": "BR", "state": "AL", "city": "Arapiraca"})
    payload["holidays"].append(holiday)
    _persist(payload)
    CACHE["payload"] = payload
    normalized = _normalize_holidays(payload.get("holidays", []), year)
    return {
        "ano": year,
        "location": payload.get("location", {}),
        "holidays": normalized
    }
