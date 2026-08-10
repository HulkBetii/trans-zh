"""ChatGPT's web UI driven by Playwright, exposed as an :class:`LLMProvider`.

No API key and no per-token billing — the browser reuses a logged-in ChatGPT
session. What it costs instead is fragility: the selectors belong to a UI OpenAI
changes without notice, and driving that UI automatically is against OpenAI's
terms of service. Treat it as the fallback for when no API budget exists, not as
the default path.
"""

from __future__ import annotations

import logging

from ...config import ChatGPTWebConfig
from ..base import LLMError, LLMProvider
from .chat import ChatGPTResponseError, ask
from .session import get_session

log = logging.getLogger(__name__)

# The composer is a single text box: no system role, no response_format. Both have
# to be folded into the one prompt.
_SYSTEM_SEPARATOR = "\n\n---\n\n"
_JSON_INSTRUCTION = "\n\nReply with raw JSON only: no markdown fences, no commentary."


def compose_prompt(system: str, user: str, json_mode: bool) -> str:
    prompt = f"{system}{_SYSTEM_SEPARATOR}{user}" if system else user
    return prompt + _JSON_INSTRUCTION if json_mode else prompt


class ChatGPTWebProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        web: ChatGPTWebConfig,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_sec: float = 300.0,
    ) -> None:
        # temperature has no equivalent in the web UI; it stays on the base class
        # only so every provider reports the same fields.
        super().__init__(model, temperature, max_retries, timeout_sec)
        self._web = web

    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool = False) -> str:
        # cache_system is meaningless here: there is no prompt cache behind the web
        # UI, and each call opens a fresh conversation regardless.
        session = get_session(self._web)
        with session.page() as page:
            try:
                return session.run(ask(page, compose_prompt(system, user, json_mode), int(self.timeout_sec)))
            except ChatGPTResponseError as exc:
                # Retryable: a slow or truncated answer usually comes back fine on the
                # next attempt. Login and Playwright errors deliberately propagate as
                # they are, so a dead browser fails fast instead of retrying blind.
                raise LLMError(f"ChatGPT web: {exc}") from exc
