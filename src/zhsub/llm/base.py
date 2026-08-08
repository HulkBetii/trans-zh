"""LLM provider abstraction.

Two shapes of call are needed and no more:

* :meth:`LLMProvider.complete` — plain text in, plain text out (S2 re-segmentation).
* :meth:`LLMProvider.complete_json` — same, but the reply must parse as JSON
  (S3 glossary, S4 translation).

``system`` is passed separately from ``user`` because the glossary lives in the
system prompt and stays byte-identical across a whole job, which is what lets
prompt caching make it nearly free after the first batch.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    """Base class. Subclasses only implement :meth:`_call`."""

    def __init__(self, model: str, temperature: float, max_retries: int, timeout_sec: float):
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout_sec = timeout_sec

    @abstractmethod
    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool) -> str:
        """One request. Raise :class:`LLMError` on a retryable failure."""

    def complete(
        self, system: str, user: str, cache_system: bool = True, json_mode: bool = False
    ) -> str:
        """Call with retry and exponential backoff plus jitter."""
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._call(system, user, cache_system, json_mode)
            except LLMError as exc:
                last = exc
                if attempt == self.max_retries:
                    break
                delay = min(2.0**attempt, 30.0) * (0.5 + random.random())
                log.warning(
                    "LLM lỗi (lần %d/%d): %s — thử lại sau %.1fs",
                    attempt + 1, self.max_retries + 1, exc, delay,
                )
                time.sleep(delay)
        raise LLMError(f"LLM thất bại sau {self.max_retries + 1} lần: {last}") from last

    def complete_json(self, system: str, user: str, cache_system: bool = True) -> Any:
        """Like :meth:`complete` but parses the reply as JSON.

        A malformed reply is retried rather than raised: the retry loop lives here
        so callers get either valid JSON or an exception, never half-parsed text.
        """
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            # json_mode constrains decoding where the endpoint supports it. Small
            # local models otherwise wrap JSON in prose often enough to burn retries.
            raw = self.complete(system, user, cache_system, json_mode=True)
            try:
                return json.loads(extract_json(raw))
            except (json.JSONDecodeError, ValueError) as exc:
                last = exc
                log.warning(
                    "LLM trả về JSON hỏng (lần %d/%d): %s | %r",
                    attempt + 1, self.max_retries + 1, exc, raw[:200],
                )
        raise LLMError(f"LLM không trả về JSON hợp lệ sau {self.max_retries + 1} lần: {last}")


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """Pull the JSON payload out of a reply.

    Models wrap JSON in markdown fences or add a sentence of preamble no matter how
    firmly the prompt forbids it, and smaller local models do it far more often.
    Rejecting those replies would burn retries on a formatting quirk that is
    trivially recoverable, so strip the wrapper instead.
    """
    text = text.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    # Fall back to the outermost bracketed span.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return text[start : end + 1]
    return text
