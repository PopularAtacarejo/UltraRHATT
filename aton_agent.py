import argparse
import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from openai import OpenAI

DEFAULT_MODEL = "deepseek/deepseek-r1-0528:free"
OPEN_ROUTER_BASE = "https://openrouter.ai/api/v1"
CBO_SOURCE_URL = "https://raw.githubusercontent.com/PopularAtacarejo/Candidatos/refs/heads/main/CBO.json"
DEFAULT_CONFIG_PATH = "configuracoes.json"

SYSTEM_PROMPT = (
    "Você é Aton, um agente virtual de RH focado em legislação trabalhista brasileira e rotinas de pessoas. "
    "Leia com atenção o conteúdo hospedado no GitHub deste projeto e use essas referências antes de responder. "
    "Quando perguntarem sobre CBO, cargos ou funções, consulte a base oficial em "
    "https://raw.githubusercontent.com/PopularAtacarejo/Candidatos/refs/heads/main/CBO.json e cite o código e "
    "a descrição relevantes. "
    "Responda com clareza, objetividade e cordialidade, mantendo um humor seco e sutil, sem ofensas. "
    "Priorize dados públicos (documentos, leis, planilhas ou JSON) e, se algo não estiver claro, faça perguntas "
    "de esclarecimento antes de criar suposições."
)

def _load_agent_settings(config_path: Optional[str] = None, config_url: Optional[str] = None) -> Dict[str, Any]:
    source_url = config_url or os.getenv("ATON_CONFIG_URL") or os.getenv("CONFIGURACOES_URL")
    source_path = config_path or os.getenv("ATON_CONFIG_PATH") or DEFAULT_CONFIG_PATH
    payload: Dict[str, Any] = {}
    if source_url:
        try:
            response = requests.get(source_url, timeout=12)
            response.raise_for_status()
            payload = response.json() or {}
        except Exception as exc:
            print(f"Atenção: não foi possível carregar configurações da IA ({exc}).")
    elif source_path:
        try:
            candidate = Path(source_path)
            if candidate.exists():
                payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Atenção: não foi possível ler {source_path} ({exc}).")

    if "configuracoes" in payload and isinstance(payload["configuracoes"], dict):
        payload = payload["configuracoes"]
    if not isinstance(payload, dict):
        return {}
    agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    return agent or {}

class AtonAgent:
    """Helper para conversar com o modelo DeepSeek via OpenRouter."""

    def __init__(
        self,
        site_url: Optional[str] = None,
        site_title: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        base_url: str = OPEN_ROUTER_BASE,
        api_key: Optional[str] = None,
        additional_behavior: Optional[str] = None,
    ) -> None:
        self.site_url = site_url
        self.site_title = site_title
        agent_settings = _load_agent_settings()
        resolved_model = model
        if resolved_model == DEFAULT_MODEL and agent_settings.get("model"):
            resolved_model = agent_settings["model"]
        resolved_key = api_key or agent_settings.get("openrouter_key")
        resolved_behavior = additional_behavior
        if not resolved_behavior and agent_settings.get("behavior"):
            resolved_behavior = agent_settings["behavior"]
        elif resolved_behavior and agent_settings.get("behavior"):
            resolved_behavior = f"{resolved_behavior.strip()} {agent_settings['behavior']}".strip()
        self.model = resolved_model
        self.base_url = base_url
        self.api_key = resolved_key
        self.additional_behavior = resolved_behavior or ""
        self.agent_name = str(agent_settings.get("name") or "Aton").strip()
        self.greeting_enabled = self._read_bool(agent_settings, "greeting_enabled", True)
        self.include_time = self._read_bool(agent_settings, "include_time", True)
        self.use_data_links = self._read_bool(agent_settings, "use_data_links", True)
        self.cbo_enabled = self._read_bool(agent_settings, "cbo_enabled", True)
        self.generation_settings = {
            "temperature": self._read_number(agent_settings, "temperature"),
            "top_p": self._read_number(agent_settings, "top_p"),
            "max_tokens": self._read_number(agent_settings, "max_tokens", is_int=True),
            "presence_penalty": self._read_number(agent_settings, "presence_penalty"),
            "frequency_penalty": self._read_number(agent_settings, "frequency_penalty"),
        }
        self.client = self._build_client()
        self.repo_root = Path(__file__).resolve().parent
        self.cbo_entries: List[Dict[str, Any]] = []
        self.data_links = self._normalize_data_links(agent_settings.get("data_links"))
        self.data_links_cache: Dict[str, str] = {}
        self.data_links_context_added = False
        prompt = str(agent_settings.get("system_prompt_override") or SYSTEM_PROMPT)
        extra_prompt_parts = []
        persona = str(agent_settings.get("persona") or "").strip()
        tone = str(agent_settings.get("tone") or "").strip()
        language = str(agent_settings.get("language") or "").strip()
        response_style = str(agent_settings.get("response_style") or "").strip()
        if self.agent_name:
            extra_prompt_parts.append(f"Seu nome e {self.agent_name}.")
        if persona:
            extra_prompt_parts.append(f"Persona: {persona}.")
        if tone:
            extra_prompt_parts.append(f"Tom de voz: {tone}.")
        if language:
            extra_prompt_parts.append(f"Idioma principal: {language}.")
        if response_style:
            extra_prompt_parts.append(f"Estilo de resposta: {response_style}.")
        if extra_prompt_parts:
            prompt = f"{prompt.rstrip()} " + " ".join(extra_prompt_parts)
        if self.additional_behavior:
            prompt = f"{prompt.rstrip()} {self.additional_behavior}"
        self.system_prompt = prompt
        self.history = [{"role": "system", "content": self.system_prompt}]
        self.user_memory: Dict[str, List[Dict[str, str]]] = {}


    def _read_bool(self, settings: Dict[str, Any], key: str, default: bool) -> bool:
        if key not in settings:
            return default
        value = settings.get(key)
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "sim", "yes", "on"}
        return default

    def _read_number(
        self,
        settings: Dict[str, Any],
        key: str,
        default: Optional[float] = None,
        is_int: bool = False,
    ) -> Optional[float]:
        if key not in settings:
            return default
        value = settings.get(key)
        if value is None or value == "":
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        if is_int:
            return int(number)
        return number

    def _build_client(self) -> OpenAI:
        api_key = self.api_key or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Abra as portas definindo OPENROUTER_API_KEY antes de instanciar Aton."
            )
        return OpenAI(base_url=self.base_url, api_key=api_key)

    def _build_extra_headers(self) -> dict:
        headers = {}
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_title:
            headers["X-Title"] = self.site_title
        return headers

    def _normalize_data_links(self, raw_links: Any) -> List[Dict[str, str]]:
        if not raw_links:
            return []
        if isinstance(raw_links, list):
            normalized = []
            for item in raw_links:
                if isinstance(item, str):
                    normalized.append({"label": "", "url": item})
                elif isinstance(item, dict):
                    normalized.append(
                        {"label": str(item.get("label") or ""), "url": str(item.get("url") or "")}
                    )
            return [link for link in normalized if link.get("label") or link.get("url")]
        return []

    def _fetch_data_link_content(self, url: str) -> Optional[str]:
        if not url:
            return None
        if url in self.data_links_cache:
            return self.data_links_cache[url]
        try:
            response = requests.get(url, timeout=12)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            text = ""
            if "application/json" in content_type or url.lower().endswith(".json"):
                try:
                    payload = response.json()
                    text = json.dumps(payload, ensure_ascii=False, indent=2)
                except Exception:
                    text = response.text
            else:
                text = response.text
            text = text.strip()
            if len(text) > 1800:
                text = text[:1800] + "..."
            self.data_links_cache[url] = text
            return text
        except Exception as exc:
            print(f"Atenção: não foi possível ler fonte adicional ({url}) ({exc}).")
            return None

    def _build_data_links_context(self) -> List[str]:
        context_lines = []
        for link in self.data_links[:6]:
            url = link.get("url", "").strip()
            label = link.get("label", "").strip()
            content = self._fetch_data_link_content(url)
            if content:
                title = f"{label} - {url}" if label else url
                context_lines.append(f"{title}\n{content}")
            elif url:
                context_lines.append(f"Fonte adicional: {url}")
        return context_lines

    def _ensure_data_links_context(self) -> None:
        if self.data_links_context_added or not self.data_links:
            return
        context_lines = self._build_data_links_context()
        if context_lines:
            self.history.append(
                {
                    "role": "system",
                    "content": "Fontes adicionais configuradas:\n" + "\n\n".join(context_lines),
                }
            )
        self.data_links_context_added = True

    def _build_greeting(self, user_name: str) -> str:
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 12:
            prefix = "Bom dia"
        elif 12 <= hour < 18:
            prefix = "Boa tarde"
        else:
            prefix = "Boa noite"
        return f"{prefix}, {user_name}"

    def _sanitize_user_key(self, raw: str) -> str:
        token = re.sub(r"[^a-zA-Z0-9_-]", "_", (raw or "").strip())
        return token or "usuario"

    def _parse_cbo_text(self, raw_text: str) -> List[dict]:
        entries = []
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            code = parts[0]
            if not re.match(r"^\d{4}-\d{2}$", code):
                continue
            entry_type = parts[-1]
            title = " ".join(parts[1:-1])
            entries.append({"code": code, "title": title, "type": entry_type})
        return entries

    def _ensure_cbo_entries(self) -> None:
        if self.cbo_entries:
            return
        try:
            response = requests.get(CBO_SOURCE_URL, timeout=20)
            response.raise_for_status()
            self.cbo_entries = self._parse_cbo_text(response.text)
        except requests.RequestException as exc:
            print(f"Atenção: não foi possível carregar CBO ({exc}).")

    def _build_cbo_context(self, question: str) -> Optional[str]:
        self._ensure_cbo_entries()
        if not self.cbo_entries:
            return None
        terms = [token for token in re.split(r"[^a-z0-9à-ú]+", question.lower()) if len(token) > 2]
        if not terms:
            terms = [question.lower()]
        results = []
        for entry in self.cbo_entries:
            title_lower = entry["title"].lower()
            if any(term in title_lower for term in terms):
                results.append(entry)
                if len(results) >= 5:
                    break
        if not results:
            return None
        lines = [f"{entry['code']} — {entry['title']} ({entry['type']})" for entry in results]
        return "\n".join(lines)

    def apply_history(self, entries: List[Dict[str, Any]]) -> None:
        """Adiciona mensagens anteriormente registradas ao histórico."""
        for entry in entries:
            role = entry.get("role")
            content = entry.get("content")
            if not role or not content:
                continue
            role = role if role in {"user", "assistant", "system"} else "user"
            self.history.append({"role": role, "content": content})

    def _append_to_memory(self, user_key: str, role: str, content: str) -> None:
        """Guarda uma entrada no histórico interno e na memória por usuário."""
        entry = {"role": role, "content": content}
        self.history.append(entry)
        self.user_memory.setdefault(user_key, []).append(entry)

    def ask(
        self,
        question: str,
        quiet: bool = False,
        references: Optional[List[str]] = None,
        user_name: str = "Usuário",
        user_id: Optional[str] = None,
    ) -> str:
        """Envia uma pergunta para o agente e retorna a resposta."""
        refs = list(references or [])
        user_key = self._sanitize_user_key(user_id or user_name or "usuario")
        greeting = self._build_greeting(user_name)
        timestamp = datetime.now()
        if self.greeting_enabled and self.include_time:
            question_payload = f"{greeting}. Hora local: {timestamp.strftime('%H:%M')}.\n{question}"
        elif self.greeting_enabled:
            question_payload = f"{greeting}.\n{question}"
        elif self.include_time:
            question_payload = f"Hora local: {timestamp.strftime('%H:%M')}.\n{question}"
        else:
            question_payload = question
        if self.use_data_links:
            self._ensure_data_links_context()
        trigger_terms = {"cbo", "funcao", "cargo", "ocupacao"}
        if self.cbo_enabled and any(term in question.lower() for term in trigger_terms):
            cbo_context = self._build_cbo_context(question)
            if cbo_context:
                refs.append(f"CBO consultado:\\n{cbo_context}\\nFonte: {CBO_SOURCE_URL}")

        if refs:
            self.history.append(
                {
                    "role": "system",
                    "content": "Referências situacionais: " + " | ".join(refs),
                }
            )
        self._append_to_memory(user_key, "user", question_payload)
        generation_params = {k: v for k, v in self.generation_settings.items() if v is not None}
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.history,
            extra_headers=self._build_extra_headers(),
            extra_body={},
            **generation_params,
        )
        content = response.choices[0].message.content.strip()
        self._append_to_memory(user_key, "assistant", content)
        if not quiet:
            print(content)
        return content

    def reset_history(self) -> None:
        """Zera o histórico sem perder o prompt de sistema."""
        self.history = [{"role": "system", "content": self.system_prompt}]

    def read_repo_file(self, relative_path: str) -> str:
        """Retorna o conteúdo de um arquivo dentro do repositório local."""
        candidate = (self.repo_root / relative_path).resolve()
        repo_root_str = str(self.repo_root)
        candidate_str = str(candidate)
        if not candidate_str.startswith(repo_root_str):
            raise ValueError("Referência fora do repositório não é permitida.")
        if not candidate.exists():
            raise FileNotFoundError(f"{relative_path} não existe no repositório.")
        return candidate.read_text(encoding="utf-8")

    def fetch_github_file(self, relative_path: str) -> str:
        """Lê (somente leitura) um arquivo do GitHub configurado para este projeto."""
        owner = os.getenv("GITHUB_OWNER", "PopularAtacarejo")
        repo = os.getenv("GITHUB_REPO", "Candidatos")
        branch = os.getenv("GITHUB_BRANCH", "main")
        token = os.getenv("GITHUB_TOKEN")
        api_headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            api_headers["Authorization"] = f"token {token}"
        path = relative_path.lstrip("/")
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        response = requests.get(url, headers=api_headers, params={"ref": branch}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        content = payload.get("content")
        if not content:
            return ""
        decoded = base64.b64decode(content).decode("utf-8")
        return decoded

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Converse com Aton para tirar dúvidas de RH e legislação trabalhista."
    )
    parser.add_argument("question", help="Pergunta que será enviada para o agente Aton.")
    parser.add_argument(
        "--reference",
        "-r",
        action="append",
        dest="references",
        help="Contexto adicional para ser enviado como referências.",
    )
    parser.add_argument(
        "--read",
        "-f",
        help="Lê um arquivo local do repositório e o adiciona ao histórico antes de enviar a questão.",
    )
    parser.add_argument(
        "--user",
        "-u",
        default="Usuário",
        help="Nome exibido nas saudações e usado para identificar a memória.",
    )
    parser.add_argument(
        "--user-id",
        "-k",
        help="Identificador mais curto ou interno para nomear o arquivo de memória.",
    )
    args = parser.parse_args()
    agent = AtonAgent(site_url="https://popular-atacarejo.local", site_title="Popular RH")
    if args.read:
        content = agent.read_repo_file(args.read)
        agent.history.append(
            {
                "role": "system",
                "content": f"Contexto local ({args.read}):\n{content[:2400]}",
            }
        )
    agent.ask(
        args.question,
        references=args.references,
        user_name=args.user,
        user_id=args.user_id,
    )


if __name__ == "__main__":
    main()
