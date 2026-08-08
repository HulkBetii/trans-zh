"""Scriptable fake LLM providers.

Real models are non-deterministic and cost money, so the acceptance criteria are
tested against providers that fail in exactly the ways real ones do: dropping an
id, merging two segments, wrapping JSON in prose.
"""

from __future__ import annotations

import json

from zhsub.llm.base import LLMProvider

_JSON_MARKER = "TRANSLATE THIS JSON:"


def parse_request(user: str) -> tuple[list[dict], str]:
    """Split a S4 user message into its items and the scene-context preamble.

    The context block deliberately sits outside the JSON, so a fake provider has to
    find the payload the same way a real model does.
    """
    context, _, payload = user.partition(_JSON_MARKER)
    data = json.loads(payload if payload else user)
    return data.get("to_translate", []), context


class FakeProvider(LLMProvider):
    """Translates by prefixing, and can be told to misbehave on cue."""

    def __init__(
        self,
        model: str = "fake-model",
        drop_ids: set[int] | None = None,
        drop_once: bool = True,
        merge_first_two: bool = False,
        wrap_in_prose: bool = False,
        fail_times: int = 0,
    ) -> None:
        super().__init__(model, temperature=0.0, max_retries=3, timeout_sec=10.0)
        self.drop_ids = drop_ids or set()
        self.drop_once = drop_once
        self.merge_first_two = merge_first_two
        self.wrap_in_prose = wrap_in_prose
        self.fail_times = fail_times
        self.calls = 0
        self.translated_ids: list[int] = []
        self._dropped = False

    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool = False) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            from zhsub.llm.base import LLMError

            raise LLMError("lỗi giả lập")

        items, _ = parse_request(user)
        prefix = "VI:" if "VIETNAMESE" in system else "EN:"

        out = []
        for i, item in enumerate(items):
            ident = int(item["id"])
            if ident in self.drop_ids and not (self.drop_once and self._dropped):
                self._dropped = True
                continue
            if self.merge_first_two and i == 0 and len(items) > 1:
                out.append({"id": ident, "translation": f"{prefix}{item['text']} {items[1]['text']}"})
                continue
            if self.merge_first_two and i == 1:
                continue
            out.append({"id": ident, "translation": f"{prefix}{item['text']}"})
            self.translated_ids.append(ident)

        body = json.dumps({"translations": out}, ensure_ascii=False)
        if self.wrap_in_prose:
            return f"Đây là bản dịch bạn cần:\n```json\n{body}\n```\nHy vọng hữu ích!"
        return body


class GlossaryAwareProvider(FakeProvider):
    """Applies the glossary mapping it is given, so term consistency can be checked."""

    def __init__(self, mapping: dict[str, str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.mapping = mapping

    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool = False) -> str:
        self.calls += 1
        items, _ = parse_request(user)
        out = []
        for item in items:
            text = item["text"]
            for zh, vi in self.mapping.items():
                text = text.replace(zh, vi)
            out.append({"id": int(item["id"]), "translation": text})
        return json.dumps({"translations": out}, ensure_ascii=False)


class SegmentingProvider(LLMProvider):
    """Inserts a break every ``every`` characters, echoing the input otherwise."""

    def __init__(self, every: int = 12, corrupt: bool = False) -> None:
        super().__init__("fake-segmenter", 0.0, 2, 10.0)
        self.every = every
        self.corrupt = corrupt

    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool = False) -> str:
        text = user
        if self.corrupt:
            # Mimic a model that "helpfully" rewrites a character mid-stream.
            text = text[:5] + "X" + text[6:] if len(text) > 6 else text
        return "|".join(text[i : i + self.every] for i in range(0, len(text), self.every))
