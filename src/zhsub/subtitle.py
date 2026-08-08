"""Đọc/ghi file phụ đề.

Đọc file được sửa tay là chỗ dễ vỡ nhất: Subtitle Edit trên Windows hay lưu
UTF-8 kèm BOM, còn phụ đề tiếng Trung tải trên mạng thì thường là GB18030. Đoán
sai encoding thì toàn bộ CER trong benchmark thành vô nghĩa, nên thử lần lượt
theo thứ tự và báo rõ nếu thất bại.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "cp1252")


@dataclass(slots=True)
class Cue:
    start: float  # giây
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
    """Đọc .srt / .ass. Trả về danh sách cue theo thứ tự thời gian."""
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
    """Ghi SRT bằng UTF-8 có BOM — Subtitle Edit và các player Windows nhận đúng
    tiếng Trung/tiếng Việt mà không phải chọn encoding tay."""
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
