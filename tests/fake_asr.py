"""Engine ASR giả, dùng để test riêng phép toán offset.

Không cần GPU, không cần model, không phụ thuộc chất lượng nhận dạng — chỉ kiểm
tra đúng một thứ: timestamp trả về có phải là **thời gian tuyệt đối so với đầu
file** hay không.
"""

from __future__ import annotations

from pathlib import Path

from zhsub.asr.base import AsrOutput, AsrSentence, AsrToken
from zhsub.media import probe_duration

SENTENCE_PERIOD = 10.0  # một câu mỗi 10 giây
SENTENCE_OFFSET = 1.0  # câu đầu bắt đầu ở giây thứ 1 của chunk
SENTENCE_LEN = 2.0
CHARS = "今天天气很好"


class FakeEngine:
    """Sinh câu ở các mốc cố định **so với đầu chunk được truyền vào**.

    Nếu ``transcribe_file`` cộng offset đúng thì mốc tuyệt đối phải bằng
    ``chunk_start + mốc tương đối``; cộng sai bao nhiêu cũng lộ ra ngay.
    """

    def __init__(self, device: str = "cpu") -> None:
        self.name = "fake"
        self.version = "0"
        self.models = {"asr": "fake"}
        self.device = device
        self.calls: list[float] = []

    def transcribe(self, wav_path: str | Path) -> AsrOutput:
        duration = probe_duration(wav_path)
        self.calls.append(duration)
        return AsrOutput(sentences=list(_sentences(duration)))


def _sentences(duration: float):
    t = SENTENCE_OFFSET
    while t + SENTENCE_LEN <= duration:
        step = SENTENCE_LEN / len(CHARS)
        tokens = [
            AsrToken(text=ch, start=t + k * step, end=t + (k + 1) * step)
            for k, ch in enumerate(CHARS)
        ]
        tokens[-1].punct_after = "。"
        yield AsrSentence(text=CHARS + "。", start=t, end=t + SENTENCE_LEN, tokens=tokens)
        t += SENTENCE_PERIOD


def expected_relative_starts(duration: float) -> list[float]:
    out = []
    t = SENTENCE_OFFSET
    while t + SENTENCE_LEN <= duration:
        out.append(t)
        t += SENTENCE_PERIOD
    return out
