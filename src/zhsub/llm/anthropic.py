"""Anthropic Messages API.

Kept separate from the OpenAI-compatible path because the request shape differs in
the one way that matters here: the system prompt is a top-level field, and prompt
caching is opt-in via ``cache_control``. Since the glossary sits in the system
prompt unchanged for a whole job, marking it cacheable is what keeps a large
glossary from being re-billed on every one of ~96 batches.
"""

from __future__ import annotations

import json

import httpx

from .base import LLMError, LLMProvider

API_VERSION = "2023-06-01"
# Below roughly this size the cache write costs more than it saves.
_MIN_CACHEABLE_CHARS = 2000


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_sec: float = 180.0,
        max_tokens: int = 8192,
    ) -> None:
        super().__init__(model, temperature, max_retries, timeout_sec)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_tokens = max_tokens
        self._client = httpx.Client(timeout=timeout_sec)

    def _system_field(self, system: str, cache_system: bool) -> list[dict]:
        block: dict = {"type": "text", "text": system}
        if cache_system and len(system) >= _MIN_CACHEABLE_CHARS:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool = False) -> str:
        # json_mode is unused: the Messages API has no response_format field, and the
        # prompts already demand bare JSON, which extract_json cleans up.
        try:
            resp = self._client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    "system": self._system_field(system, cache_system),
                    "messages": [{"role": "user", "content": user}],
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"lỗi mạng: {exc}") from exc

        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LLMError(f"phản hồi lạ: {exc} | {resp.text[:300]}") from exc

    def __del__(self):  # pragma: no cover
        try:
            self._client.close()
        except Exception:
            pass
