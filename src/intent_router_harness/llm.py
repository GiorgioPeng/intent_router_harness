from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Protocol
from urllib import error, request


class LLMConfigurationError(RuntimeError):
    """Raised when the local LLM environment is incomplete."""


class LLMRequestError(RuntimeError):
    """Raised when an OpenAI-compatible LLM request fails."""


@dataclass(frozen=True, slots=True)
class LLMSettings:
    """OpenAI-compatible chat completion settings."""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    timeout_seconds: float = 30.0


class LLMClient(Protocol):
    settings: Any

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return an OpenAI-compatible chat completion response."""


def load_env_file(path: str | Path) -> dict[str, str]:
    """Load a simple dotenv file without mutating process environment."""
    env_path = Path(path).expanduser()
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_llm_settings(env_file: str | Path = ".env.local") -> LLMSettings:
    """Load OpenAI-compatible settings from `.env.local` and the process environment."""
    file_values = load_env_file(env_file)

    def get(name: str) -> str | None:
        return os.getenv(name) or file_values.get(name)

    base_url = get("ROUTER_LLM_API_BASE_URL")
    api_key = get("ROUTER_LLM_API_KEY")
    model = get("ROUTER_LLM_MODEL")
    if not base_url or not api_key or not model:
        raise LLMConfigurationError(
            "ROUTER_LLM_API_BASE_URL, ROUTER_LLM_API_KEY, and ROUTER_LLM_MODEL are required"
        )
    return LLMSettings(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=float(get("ROUTER_LLM_TEMPERATURE") or "0"),
        timeout_seconds=float(get("ROUTER_LLM_TIMEOUT_SECONDS") or "30"),
    )


class OpenAICompatibleLLMClient:
    """Small synchronous OpenAI-compatible chat completion client."""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Call `/chat/completions` and return decoded JSON."""
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        last_error: Exception | None = None
        for url in _candidate_chat_completion_urls(self.settings.base_url):
            try:
                return self._post_json(url, payload)
            except LLMRequestError as exc:
                last_error = exc
                if "HTTP 404" not in str(exc):
                    break
        if last_error is None:
            raise LLMRequestError("no chat completion endpoint candidates generated")
        raise last_error

    def smoke(self) -> str:
        """Run a tiny deterministic smoke prompt."""
        response = self.chat(
            [
                {
                    "role": "system",
                    "content": "你是连通性检查器。请用最短答案回复。",
                },
                {
                    "role": "user",
                    "content": "只返回：OK",
                },
            ],
            max_tokens=8,
        )
        try:
            return str(response["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("chat completion response did not contain choices[0].message.content") from exc

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.settings.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            raise LLMRequestError(f"HTTP {exc.code} from {url}: {_truncate(raw_error, 300)}") from exc
        except error.URLError as exc:
            raise LLMRequestError(f"request failed for {url}: {exc.reason}") from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMRequestError(f"LLM response was not JSON: {_truncate(raw, 300)}") from exc
        if not isinstance(decoded, dict):
            raise LLMRequestError("LLM response JSON must be an object")
        return decoded


def _candidate_chat_completion_urls(base_url: str) -> list[str]:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return [stripped]
    candidates = [f"{stripped}/chat/completions"]
    if not stripped.endswith("/v1"):
        candidates.append(f"{stripped}/v1/chat/completions")
    return candidates


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}..."
