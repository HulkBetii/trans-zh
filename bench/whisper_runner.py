"""Chạy faster-whisper cho benchmark.

Script dùng một lần, **cố ý không** implement :class:`~zhsub.asr.base.ASREngine`.
v1 chỉ có FunASR trong thư viện; whisper có mặt ở đây thuần tuý để có số mà so
sánh. Nếu benchmark cho thấy whisper thắng thì lúc đó mới thêm nó vào ``asr/``.
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
    """Returns ``(tokens, full_text, elapsed_sec)`` với ``tokens = [(text, start, end)]``.

    ``word_timestamps=True`` là bắt buộc: timestamp cấp segment mặc định của
    faster-whisper quá thô để so sánh công bằng với timestamp cấp token của
    paraformer.
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
    for seg in segments:  # generator — vòng lặp này mới là lúc thực sự chạy
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
