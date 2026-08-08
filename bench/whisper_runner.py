"""Run faster-whisper for the benchmark.

A throwaway script that **deliberately** does not implement
:class:`~zhsub.asr.base.ASREngine`. v1 ships FunASR only; whisper exists here
purely to produce comparison numbers. If the benchmark says whisper wins, that is
when it earns a place in ``asr/``.
"""

from __future__ import annotations

import time
from pathlib import Path

DEFAULT_MODEL = "large-v3"


def transcribe(
    wav_path: str | Path,
    model_size: str = DEFAULT_MODEL,
    device: str = "cuda",
    compute_type: str | None = None,
) -> tuple[list[tuple[str, float, float]], str, float]:
    """Returns ``(tokens, full_text, elapsed_sec)`` with ``tokens = [(text, start, end)]``.

    ``word_timestamps=True`` is mandatory: faster-whisper's default segment-level
    timestamps are far too coarse to compare fairly against paraformer's
    token-level ones.
    """
    from faster_whisper import WhisperModel

    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    started = time.perf_counter()
    segments, _info = model.transcribe(
        str(wav_path),
        language="zh",
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    tokens: list[tuple[str, float, float]] = []
    parts: list[str] = []
    for seg in segments:  # a generator — this loop is where the work actually happens
        parts.append(seg.text)
        if seg.words:
            for w in seg.words:
                text = w.word.strip()
                if text:
                    tokens.append((text, float(w.start), float(w.end)))
        else:
            text = seg.text.strip()
            if text:
                tokens.append((text, float(seg.start), float(seg.end)))
    elapsed = time.perf_counter() - started

    return tokens, "".join(parts), elapsed
