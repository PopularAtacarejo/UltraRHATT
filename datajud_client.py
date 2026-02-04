import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

DATAJUD_API_KEY = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="
DATAJUD_ENDPOINTS: Dict[str, str] = {
    "tst": "https://api-publica.datajud.cnj.jus.br/api_publica_tst/_search",
    "tse": "https://api-publica.datajud.cnj.jus.br/api_publica_tse/_search",
    "stj": "https://api-publica.datajud.cnj.jus.br/api_publica_stj/_search",
    "stm": "https://api-publica.datajud.cnj.jus.br/api_publica_stm/_search",

    "trf1": "https://api-publica.datajud.cnj.jus.br/api_publica_trf1/_search",
    "trf2": "https://api-publica.datajud.cnj.jus.br/api_publica_trf2/_search",
    "trf3": "https://api-publica.datajud.cnj.jus.br/api_publica_trf3/_search",
    "trf4": "https://api-publica.datajud.cnj.jus.br/api_publica_trf4/_search",
    "trf5": "https://api-publica.datajud.cnj.jus.br/api_publica_trf5/_search",
    "trf6": "https://api-publica.datajud.cnj.jus.br/api_publica_trf6/_search",

    "tjac": "https://api-publica.datajud.cnj.jus.br/api_publica_tjac/_search",
    "tjal": "https://api-publica.datajud.cnj.jus.br/api_publica_tjal/_search",
    "tjam": "https://api-publica.datajud.cnj.jus.br/api_publica_tjam/_search",
    "tjap": "https://api-publica.datajud.cnj.jus.br/api_publica_tjap/_search",
    "tjba": "https://api-publica.datajud.cnj.jus.br/api_publica_tjba/_search",
    "tjce": "https://api-publica.datajud.cnj.jus.br/api_publica_tjce/_search",
    "tjdft": "https://api-publica.datajud.cnj.jus.br/api_publica_tjdft/_search",
    "tjes": "https://api-publica.datajud.cnj.jus.br/api_publica_tjes/_search",
    "tjgo": "https://api-publica.datajud.cnj.jus.br/api_publica_tjgo/_search",
    "tjma": "https://api-publica.datajud.cnj.jus.br/api_publica_tjma/_search",
    "tjmg": "https://api-publica.datajud.cnj.jus.br/api_publica_tjmg/_search",
    "tjms": "https://api-publica.datajud.cnj.jus.br/api_publica_tjms/_search",
    "tjmt": "https://api-publica.datajud.cnj.jus.br/api_publica_tjmt/_search",
    "tjpa": "https://api-publica.datajud.cnj.jus.br/api_publica_tjpa/_search",
    "tjpb": "https://api-publica.datajud.cnj.jus.br/api_publica_tjpb/_search",
    "tjpe": "https://api-publica.datajud.cnj.jus.br/api_publica_tjpe/_search",
    "tjpi": "https://api-publica.datajud.cnj.jus.br/api_publica_tjpi/_search",
    "tjpr": "https://api-publica.datajud.cnj.jus.br/api_publica_tjpr/_search",
    "tjrj": "https://api-publica.datajud.cnj.jus.br/api_publica_tjrj/_search",
    "tjrn": "https://api-publica.datajud.cnj.jus.br/api_publica_tjrn/_search",
    "tjro": "https://api-publica.datajud.cnj.jus.br/api_publica_tjro/_search",
    "tjrr": "https://api-publica.datajud.cnj.jus.br/api_publica_tjrr/_search",
    "tjrs": "https://api-publica.datajud.cnj.jus.br/api_publica_tjrs/_search",
    "tjsc": "https://api-publica.datajud.cnj.jus.br/api_publica_tjsc/_search",
    "tjse": "https://api-publica.datajud.cnj.jus.br/api_publica_tjse/_search",
    "tjsp": "https://api-publica.datajud.cnj.jus.br/api_publica_tjsp/_search",
    "tjto": "https://api-publica.datajud.cnj.jus.br/api_publica_tjto/_search",

    "trt1": "https://api-publica.datajud.cnj.jus.br/api_publica_trt1/_search",
    "trt2": "https://api-publica.datajud.cnj.jus.br/api_publica_trt2/_search",
    "trt3": "https://api-publica.datajud.cnj.jus.br/api_publica_trt3/_search",
    "trt4": "https://api-publica.datajud.cnj.jus.br/api_publica_trt4/_search",
    "trt5": "https://api-publica.datajud.cnj.jus.br/api_publica_trt5/_search",
    "trt6": "https://api-publica.datajud.cnj.jus.br/api_publica_trt6/_search",
    "trt7": "https://api-publica.datajud.cnj.jus.br/api_publica_trt7/_search",
    "trt8": "https://api-publica.datajud.cnj.jus.br/api_publica_trt8/_search",
    "trt9": "https://api-publica.datajud.cnj.jus.br/api_publica_trt9/_search",
    "trt10": "https://api-publica.datajud.cnj.jus.br/api_publica_trt10/_search",
    "trt11": "https://api-publica.datajud.cnj.jus.br/api_publica_trt11/_search",
    "trt12": "https://api-publica.datajud.cnj.jus.br/api_publica_trt12/_search",
    "trt13": "https://api-publica.datajud.cnj.jus.br/api_publica_trt13/_search",
    "trt14": "https://api-publica.datajud.cnj.jus.br/api_publica_trt14/_search",
    "trt15": "https://api-publica.datajud.cnj.jus.br/api_publica_trt15/_search",
    "trt16": "https://api-publica.datajud.cnj.jus.br/api_publica_trt16/_search",
    "trt17": "https://api-publica.datajud.cnj.jus.br/api_publica_trt17/_search",
    "trt18": "https://api-publica.datajud.cnj.jus.br/api_publica_trt18/_search",
    "trt19": "https://api-publica.datajud.cnj.jus.br/api_publica_trt19/_search",
    "trt20": "https://api-publica.datajud.cnj.jus.br/api_publica_trt20/_search",
    "trt21": "https://api-publica.datajud.cnj.jus.br/api_publica_trt21/_search",
    "trt22": "https://api-publica.datajud.cnj.jus.br/api_publica_trt22/_search",
    "trt23": "https://api-publica.datajud.cnj.jus.br/api_publica_trt23/_search",
    "trt24": "https://api-publica.datajud.cnj.jus.br/api_publica_trt24/_search",

    "tre-ac": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-ac/_search",
    "tre-al": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-al/_search",
    "tre-am": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-am/_search",
    "tre-ap": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-ap/_search",
    "tre-ba": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-ba/_search",
    "tre-ce": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-ce/_search",
    "tre-dft": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-dft/_search",
    "tre-es": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-es/_search",
    "tre-go": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-go/_search",
    "tre-ma": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-ma/_search",
    "tre-mg": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-mg/_search",
    "tre-ms": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-ms/_search",
    "tre-mt": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-mt/_search",
    "tre-pa": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-pa/_search",
    "tre-pb": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-pb/_search",
    "tre-pe": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-pe/_search",
    "tre-pi": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-pi/_search",
    "tre-pr": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-pr/_search",
    "tre-rj": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-rj/_search",
    "tre-rn": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-rn/_search",
    "tre-ro": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-ro/_search",
    "tre-rr": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-rr/_search",
    "tre-rs": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-rs/_search",
    "tre-sc": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-sc/_search",
    "tre-se": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-se/_search",
    "tre-sp": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-sp/_search",
    "tre-to": "https://api-publica.datajud.cnj.jus.br/api_publica_tre-to/_search",

    "tjmmg": "https://api-publica.datajud.cnj.jus.br/api_publica_tjmmg/_search",
    "tjmrs": "https://api-publica.datajud.cnj.jus.br/api_publica_tjmrs/_search",
    "tjmsp": "https://api-publica.datajud.cnj.jus.br/api_publica_tjmsp/_search",
}


def build_query_payload(cpf: str) -> Dict[str, Any]:
    return {
        "query": {
            "match": {
                "cpf": cpf
            }
        }
    }


def _perform_request(tribunal: str, cpf: str, timeout: int = 10) -> Dict[str, Any]:
    """Trigger a Datajud request for a known tribunal."""
    url = DATAJUD_ENDPOINTS.get(tribunal)
    if not url:
        raise ValueError(f"Tribunal desconhecido: {tribunal}")

    headers = {
        "Authorization": f"APIKey {DATAJUD_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = build_query_payload(cpf)
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def call_datajud(tribunal: str, cpf: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Public helper that mirrors the previous behavior for a single tribunal."""
    if tribunal not in DATAJUD_ENDPOINTS:
        return None
    return _perform_request(tribunal, cpf, timeout)


def _normalise_hits(response: Dict[str, Any], tribunal: str) -> List[Dict[str, Any]]:
    hits = response.get("hits", {}).get("hits") or []
    for hit in hits:
        source = hit.setdefault("_source", {})
        source.setdefault("tribunal", tribunal)
    return hits


def call_datajud_all(
    cpf: str,
    timeout: int = 10,
    max_workers: int = 8
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, int]]:
    """
    Queries every supported tribunal concurrently (limited by max_workers) and returns
    the aggregated hits plus a summary by tribunal and some metadata.
    """
    worker_count = max(1, min(max_workers, len(DATAJUD_ENDPOINTS)))
    aggregated_hits: List[Dict[str, Any]] = []
    summary_map: Dict[str, Dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_tribunal = {
            executor.submit(_perform_request, tribunal, cpf, timeout): tribunal
            for tribunal in DATAJUD_ENDPOINTS
        }

        for future in as_completed(future_to_tribunal):
            tribunal = future_to_tribunal[future]
            try:
                response = future.result()
                hits = _normalise_hits(response, tribunal)
                aggregated_hits.extend(hits)
                summary_map[tribunal] = {"tribunal": tribunal, "hits": len(hits)}
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", "n/a")
                summary_map[tribunal] = {
                    "tribunal": tribunal,
                    "hits": 0,
                    "error": f"HTTP {status}: {str(exc)}"
                }
            except Exception as exc:
                summary_map[tribunal] = {
                    "tribunal": tribunal,
                    "hits": 0,
                    "error": str(exc)
                }

    summary = [summary_map[tribunal] for tribunal in DATAJUD_ENDPOINTS]
    metadata = {
        "total_tribunais": len(DATAJUD_ENDPOINTS),
        "tribunais_com_resultados": sum(1 for entry in summary if entry.get("hits", 0) > 0),
        "total_hits": len(aggregated_hits)
    }

    return {"hits": {"hits": aggregated_hits}}, summary, metadata
