"""S2 chia đôi chunk hỏng thay vì rơi thẳng xuống ngắt theo rule."""

from __future__ import annotations

from zhsub.config import Config
from zhsub.llm.base import LLMError
from zhsub.models import AsrDoc, EngineInfo, Token
from zhsub.stages.s2_segment import _segment_chunk, _worse


def _doc(n_tokens: int = 200) -> AsrDoc:
    return AsrDoc(
        engine=EngineInfo(name="fake", version="0", device="cpu"),
        audio_duration_sec=n_tokens * 0.5,
        tokens=[
            Token(i=i, text="中", start=i * 0.5, end=i * 0.5 + 0.4) for i in range(n_tokens)
        ],
        raw_segments=[],
    )


class _Provider:
    """Từ chối nửa đầu, chấp nhận nửa sau — mô phỏng bộ lọc phản ứng theo nội dung."""

    model = "fake"

    def __init__(self, refuse_longer_than: int) -> None:
        self.refuse_longer_than = refuse_longer_than
        self.calls: list[int] = []

    def complete(self, system: str, user: str, *args, **kwargs) -> str:
        self.calls.append(len(user))
        if len(user) > self.refuse_longer_than:
            raise LLMError("ChatGPT chặn nội dung")
        # Chép lại nguyên văn, chèn một dấu ngắt ở giữa.
        mid = len(user) // 2
        return user[:mid] + "|" + user[mid:]


def test_a_refused_chunk_is_split_instead_of_given_up_on():
    """Bộ lọc phản ứng với nội dung, nên nửa lành lọt được.

    Đo trên video thật: S4 bị chặn ở batch 60 câu nhưng chia xuống 30 câu thì lọt,
    còn S2 vì chỉ biết thử lại nguyên xi nên mất trọn 36% video.
    """
    doc = _doc(200)
    provider = _Provider(refuse_longer_than=120)  # cả chunk 200 bị chặn, nửa 100 lọt

    breaks, method = _segment_chunk(doc, 0, 200, provider, Config())

    assert method != "rule_fallback"
    assert len(provider.calls) > 1  # đã thử chunk nhỏ hơn
    assert breaks


def test_splitting_stops_and_falls_back_when_everything_is_refused():
    doc = _doc(200)
    provider = _Provider(refuse_longer_than=0)  # chặn mọi kích thước

    breaks, method = _segment_chunk(doc, 0, 200, provider, Config())

    assert method == "rule_fallback"
    assert breaks  # vẫn có ranh giới câu, chỉ là do rule sinh


def test_a_tiny_chunk_is_not_split_further():
    """Dưới ngưỡng thì mỗi lần chia chỉ thêm một lần gọi mà chẳng cứu được gì."""
    doc = _doc(20)
    provider = _Provider(refuse_longer_than=0)

    _segment_chunk(doc, 0, 20, provider, Config())

    assert len(provider.calls) <= Config().segment.max_retries + 1


def test_method_label_takes_the_worse_half():
    assert _worse("llm", "rule_fallback") == "rule_fallback"
    assert _worse("llm", "llm+repair") == "llm+repair"
    assert _worse("llm", "llm") == "llm"
