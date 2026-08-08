"""ASR interface plus the chunk-offset arithmetic.

``FunASRParaformer`` is the first and only implementation in v1. Adding
``SenseVoice`` or ``FasterWhisper`` later means implementing :class:`ASREngine`
and nothing else — no stage needs to change.

All offset arithmetic lives here as **pure functions**, separate from any engine,
so it can be tested with a fake engine: no GPU, no real audio. This is the single
easiest place to introduce a silent timestamp bug, so it is also the easiest
place to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class AsrToken:
    """One timestamped token: usually a single character in Chinese, a word in English."""

    text: str
    start: float
    end: float
    punct_after: str | None = None


@dataclass(slots=True)
class AsrSentence:
    text: str  # punctuated, for display
    start: float
    end: float
    tokens: list[AsrToken] = field(default_factory=list)


@dataclass(slots=True)
class AsrOutput:
    sentences: list[AsrSentence] = field(default_factory=list)
    # Sentences where the timestamp count did not match the token count and time
    # had to be distributed evenly. Recorded in asr.json so the result's
    # trustworthiness is visible rather than assumed.
    degraded_sentences: int = 0


@runtime_checkable
class ASREngine(Protocol):
    name: str
    version: str
    models: dict[str, str]
    device: str

    def transcribe(self, wav_path: str | Path) -> AsrOutput:
        """Transcribe a whole file. Timestamps are relative to the start of **that file**."""
        ...


# ---------------------------------------------------------------------------
# Chunk merging — pure functions
# ---------------------------------------------------------------------------


def plan_chunks(
    duration_sec: float,
    threshold_sec: float,
    chunk_sec: float,
    overlap_sec: float,
) -> list[tuple[float, float]]:
    """Split a file into ``[start, end)`` windows, in seconds.

    Below ``threshold_sec`` this returns a single window covering the whole file:
    FunASR already segments on VAD and returns absolute timestamps, so wrapping
    another offset layer around it only creates opportunities to get it wrong.

    Windows overlap by ``overlap_sec`` so a sentence straddling a boundary is not
    lost; :func:`merge_outputs` removes the resulting duplicates.
    """
    if duration_sec <= 0:
        return [(0.0, 0.0)]
    if duration_sec <= threshold_sec:
        return [(0.0, duration_sec)]
    if chunk_sec <= overlap_sec:
        raise ValueError(f"chunk_sec ({chunk_sec}) must be greater than overlap_sec ({overlap_sec})")

    step = chunk_sec - overlap_sec
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_sec:
        end = min(start + chunk_sec, duration_sec)
        windows.append((start, end))
        if end >= duration_sec:
            break
        start += step
    return windows


def shift_output(out: AsrOutput, offset: float) -> AsrOutput:
    """Shift every timestamp by ``offset`` seconds. Does not mutate the input."""
    if offset == 0.0:
        return out
    return AsrOutput(
        sentences=[
            AsrSentence(
                text=s.text,
                start=s.start + offset,
                end=s.end + offset,
                tokens=[replace(t, start=t.start + offset, end=t.end + offset) for t in s.tokens],
            )
            for s in out.sentences
        ],
        degraded_sentences=out.degraded_sentences,
    )


def merge_outputs(parts: list[tuple[float, AsrOutput]]) -> AsrOutput:
    """Merge per-chunk results, producing timestamps **absolute to the start of the file**.

    Args:
        parts: ``(offset_seconds, chunk_result)`` pairs, where each result carries
            timestamps relative to the start of its own chunk.

    A sentence starting before the end of the last accepted sentence is dropped
    as an overlap duplicate. The cut is made on time rather than text because two
    chunks transcribing the same audio usually produce slightly different
    characters.
    """
    merged: list[AsrSentence] = []
    degraded = 0
    frontier = float("-inf")

    for offset, out in parts:
        degraded += out.degraded_sentences
        for sent in shift_output(out, offset).sentences:
            if sent.start < frontier - 1e-6:
                continue
            merged.append(sent)
            frontier = max(frontier, sent.end)

    merged.sort(key=lambda s: (s.start, s.end))
    return AsrOutput(sentences=merged, degraded_sentences=degraded)
