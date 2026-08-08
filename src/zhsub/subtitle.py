"""Subtitle file I/O.

Reading hand-edited files is the fragile part: Subtitle Edit on Windows likes to
save UTF-8 with a BOM, and Chinese subtitles found online are usually GB18030.
Guessing the encoding wrong makes every CER number in the benchmark meaningless,
so candidates are tried in order and failure is reported loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "cp1252")


@dataclass(slots=True)
class Cue:
    start: float  # seconds
    end: float
    text: str


def _detect_encoding(path: Path) -> str:
    raw = path.read_bytes()
    for enc in _ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(
        "zhsub", raw, 0, 1,
        f"Không đoán được encoding của {path}. Lưu lại file bằng UTF-8 rồi thử lại.",
    )


def read_subtitle(path: str | Path) -> list[Cue]:
    """Read .srt / .ass. Returns cues in chronological order."""
    import pysubs2

    path = Path(path)
    subs = pysubs2.load(str(path), encoding=_detect_encoding(path))
    cues = [
        Cue(start=ev.start / 1000.0, end=ev.end / 1000.0, text=ev.plaintext.strip())
        for ev in subs
        if not ev.is_comment and ev.plaintext.strip()
    ]
    cues.sort(key=lambda c: (c.start, c.end))
    return cues


def _fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[Cue], path: str | Path) -> Path:
    """Write SRT as UTF-8 with BOM, so Subtitle Edit and Windows players render
    Chinese and Vietnamese correctly without the user picking an encoding."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{_fmt_ts(cue.start)} --> {_fmt_ts(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8-sig", newline="\r\n")
    return path
