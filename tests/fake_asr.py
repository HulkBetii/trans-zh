"""A fake ASR engine used to test the offset arithmetic in isolation.

No GPU, no model, no dependence on recognition quality — it verifies exactly one
thing: whether returned timestamps are **absolute relative to the start of the
file**.
"""

from __future__ import annotations

from pathlib import Path

from zhsub.asr.base import AsrOutput, AsrSentence, AsrToken
from zhsub.media import probe_duration

SENTENCE_PERIOD = 10.0  # one sentence every 10 seconds
SENTENCE_OFFSET = 1.0  # first sentence starts 1 second into the chunk
SENTENCE_LEN = 2.0
CHARS = "今天天气很好"


class FakeEngine:
    """Emits sentences at fixed offsets **relative to the chunk it is handed**.

    If ``transcribe_file`` adds offsets correctly, the absolute timestamps must
    equal ``chunk_start + relative offset``; any error in that arithmetic shows up
    immediately.
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
