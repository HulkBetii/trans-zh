"""Any OpenAI-compatible ``/chat/completions`` endpoint.

Covers OpenAI itself, Ollama (``http://localhost:11434/v1``), LM Studio, vLLM,
DeepSeek, Groq, OpenRouter and most proxies — the whole point of routing through
one implementation is that switching between them is a config edit, not a code
change.
"""

from __future__ import annotations

import json
import logging

import httpx

from .base import LLMError, LLMProvider

log = logging.getLogger(__name__)

# Reasoning models reject an explicit temperature. Rather than maintaining a list
# of which ones, detect the refusal and retry once without the parameter.
_TEMPERATURE_REJECTED = ("temperature", "unsupported_value", "unsupported_parameter")


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_sec: float = 120.0,
    ) -> None:
        super().__init__(model, temperature, max_retries, timeout_sec)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._send_temperature = True
        self._send_json_mode = True
        self._client = httpx.Client(timeout=timeout_sec)

    def _payload(self, system: str, user: str, json_mode: bool) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._send_temperature:
            body["temperature"] = self.temperature
        if json_mode and self._send_json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool = False) -> str:
        # cache_system is unused here: OpenAI-compatible endpoints cache the prompt
        # prefix automatically when it repeats, with no field to set.
        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=self._payload(system, user, json_mode),
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"lỗi mạng: {exc}") from exc

        # Probe-and-disable rather than maintaining a compatibility matrix: which
        # endpoint supports which optional field changes faster than any such list.
        if resp.status_code == 400:
            detail = resp.text.lower()
            if self._send_temperature and any(k in detail for k in _TEMPERATURE_REJECTED):
                log.info("Model %s không nhận temperature — bỏ tham số và thử lại", self.model)
                self._send_temperature = False
                raise LLMError("temperature bị từ chối, đã tắt cho các lần sau")
            if self._send_json_mode and "response_format" in detail:
                log.info("Endpoint không nhận response_format — bỏ và thử lại")
                self._send_json_mode = False
                raise LLMError("response_format bị từ chối, đã tắt cho các lần sau")

        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"phản hồi lạ: {exc} | {resp.text[:300]}") from exc

    def __del__(self):  # pragma: no cover
        try:
            self._client.close()
        except Exception:
            pass
