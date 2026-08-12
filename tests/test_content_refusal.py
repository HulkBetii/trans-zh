"""Bộ lọc nội dung của ChatGPT chặn bản dịch phim án mạng."""

from __future__ import annotations

import pytest

from zhsub.llm.base import LLMError, LLMProvider, NonRetryableLLMError
from zhsub.llm.chatgpt_web.chat import find_refusal_fragment

REFUSAL = (
    "This content can’t be shown for safety reasons\n"
    "If this seems like a mistake, give this response a thumbs down."
)


def test_the_real_refusal_message_is_recognised():
    """Nguyên văn lấy từ log một video án mạng thật, dài đúng 168 ký tự."""
    assert find_refusal_fragment(REFUSAL) is not None


def test_an_ordinary_answer_is_not_mistaken_for_a_refusal():
    assert find_refusal_fragment('{"translations": [{"id": 0, "translation": "Anh ta chạy"}]}') is None


class _Provider(LLMProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__("m", 0.0, max_retries=3, timeout_sec=1.0)
        self.error = error
        self.calls = 0

    def _call(self, system, user, cache_system, json_mode):
        self.calls += 1
        raise self.error


def test_a_refusal_is_not_retried():
    """Gửi lại y nguyên văn bản đó chỉ nhận đúng lời từ chối đó.

    Đo trên video thật: mỗi lần bị chặn tốn 12 lần gọi (4 lượt trong complete_json
    nhân 3 lượt trong _translate_batch) trước khi tới được bước chia đôi batch.
    """
    provider = _Provider(NonRetryableLLMError("bị chặn"))

    with pytest.raises(NonRetryableLLMError):
        provider.complete("system", "user")

    assert provider.calls == 1


def test_an_ordinary_failure_is_still_retried():
    provider = _Provider(LLMError("mạng chập chờn"))

    with pytest.raises(LLMError):
        provider.complete("system", "user")

    assert provider.calls == 4  # max_retries + 1


def test_a_refusal_still_reaches_the_stage_recovery_paths():
    """Phải là LLMError, nếu không S2 và S4 không bắt được.

    S4 chia đôi batch thực sự cứu được: batch 60 câu bị chặn còn batch 30 câu lọt,
    nên biến từ chối thành lỗi chết người sẽ vứt luôn cả bản dịch.
    """
    assert issubclass(NonRetryableLLMError, LLMError)
