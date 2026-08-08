"""Thin wrappers around ffmpeg / ffprobe.

Kept separate from S0 because the benchmark also needs to convert a clip to WAV
without pulling in the whole ingest stage.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise MediaError(
            f"Không tìm thấy {tool} trong PATH. Trên Windows: winget install Gyan.FFmpeg "
            f"rồi mở lại terminal."
        )
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-15:]
        raise MediaError(f"{Path(cmd[0]).name} lỗi (exit {proc.returncode}):\n" + "\n".join(tail))
    return proc


def probe_duration(path: str | Path) -> float:
    """Media duration in seconds."""
    ffprobe = _require("ffprobe")
    proc = _run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
    )
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise MediaError(f"Không đọc được độ dài của {path}: {exc}") from exc


def to_wav(
    src: str | Path,
    dst: str | Path,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """Normalise to 16-bit PCM WAV, 16kHz mono by default — what FunASR expects.

    Writes to a temp file and renames, so an interrupted run cannot leave behind a
    truncated WAV that a later stage would treat as valid.
    """
    ffmpeg = _require("ffmpeg")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp.wav")
    try:
        _run(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-i", str(src),
                "-vn",
                "-ac", str(channels),
                "-ar", str(sample_rate),
                "-acodec", "pcm_s16le",
                "-f", "wav",
                str(tmp),
            ]
        )
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


def slice_wav(src: str | Path, dst: str | Path, start_sec: float, duration_sec: float) -> Path:
    """Extract a span of WAV. Only used by the outer chunk layer on very long files.

    ``-ss`` goes **before** ``-i`` so ffmpeg seeks on input; for PCM WAV that seek
    is sample-accurate, so there is none of the drift you get seeking inside a
    compressed container.
    """
    ffmpeg = _require("ffmpeg")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-nostdin", "-y",
            "-ss", f"{start_sec:.6f}",
            "-t", f"{duration_sec:.6f}",
            "-i", str(src),
            "-c", "copy",
            str(dst),
        ]
    )
    return dst
