"""Cắt lặng và ghép các đoạn TTS lên đúng mốc thời gian của phụ đề.

Cắt lặng là bước có giá trị nhất trong cả stage. Đo trên 8 mẫu của giọng
``vbee_n_hn_male_duyonyx_oaistable_vc``: mọi file trả về đều có 0.215-0.223 giây
lặng ở đầu và 0.27-0.46 giây ở cuối — đó là padding của nhà cung cấp, không phải
giọng đọc. Bỏ nó thu lại ~0.55 giây mỗi câu, và riêng việc đó đưa 183/184 cue của
một video thật từ "chật" về "dư chỗ", nên không phải ép model viết ngắn lại.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from ..media import MediaError, _require, _run

log = logging.getLogger(__name__)

# -40dB: đủ thấp để không cắt nhầm phụ âm nhẹ đầu câu, đủ cao để bắt được padding.
SILENCE_THRESHOLD_DB = -40
SILENCE_MIN_SEC = 0.08

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")


def speech_bounds(path: Path, total_sec: float) -> tuple[float, float]:
    """Khoảng (đầu, cuối) chứa tiếng thật, bỏ padding hai đầu.

    Chỉ cắt lặng ở **hai đầu**. Lặng ở giữa là nhịp ngắt câu của người đọc — cắt
    nó đi thì câu dính vào nhau, nghe như đọc vội.
    """
    ffmpeg = _require("ffmpeg")
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(path),
         "-af", f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:d={SILENCE_MIN_SEC}",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log_text = proc.stderr or ""
    starts = [float(m) for m in _SILENCE_START.findall(log_text)]
    ends = [float(m) for m in _SILENCE_END.findall(log_text)]

    # Lặng đầu chỉ tính khi nó bắt đầu từ giây 0; lặng cuối chỉ khi nó chạy tới hết file.
    begin = ends[0] if starts and starts[0] <= 0.01 and ends else 0.0
    finish = total_sec
    if starts and starts[-1] > begin:
        trailing_is_final = not ends or ends[-1] >= total_sec - 0.05
        if trailing_is_final:
            finish = starts[-1]
    if finish - begin < 0.05:  # toàn bộ là lặng: giữ nguyên còn hơn trả về rỗng
        return 0.0, total_sec
    return begin, finish


def decode_pcm(path: Path, sample_rate: int, start_sec: float, end_sec: float) -> bytes:
    """Giải mã một khoảng thành PCM 16-bit mono, để ghép bằng cách chép byte."""
    ffmpeg = _require("ffmpeg")
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error",
         "-ss", f"{start_sec:.6f}", "-to", f"{end_sec:.6f}", "-i", str(path),
         "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"Không giải mã được {path.name}: {proc.stderr.decode(errors='replace')[:300]}")
    return proc.stdout


def assemble(
    clips: list[tuple[float, bytes]], sample_rate: int, dst: Path, total_sec: float,
    min_gap_sec: float = 0.25,
) -> Path:
    """Đặt từng đoạn tiếng vào đúng mốc thời gian, phần trống để im lặng.

    Ghép theo mốc chứ không nối đuôi nhau, vì tổng thời lượng đọc ngắn hơn timeline
    khoảng 25% — nối đuôi thì tiếng chạy trước hình mỗi lúc một xa, tới cuối video
    lệch tới vài phút.

    ``min_gap_sec`` là nhịp thở tối thiểu giữa hai câu. Bắt buộc phải có: cắt lặng
    đã bỏ đuôi im lặng của nhà cung cấp, nên khi một đoạn lấn giờ và bị đẩy lùi thì
    nó dán khít vào đuôi đoạn trước — nghe thử thì chỗ giao giữa các câu gấp gáp
    hẳn. Không dựa vào padding sẵn có vì nó dao động 0.27-0.46s tuỳ câu.
    """
    bytes_per_sample = 2
    buf = bytearray(int(total_sec * sample_rate) * bytes_per_sample)
    gap_bytes = int(min_gap_sec * sample_rate) * bytes_per_sample
    pushed = 0
    prev_end = -gap_bytes  # câu đầu tiên không bị đẩy khỏi mốc của nó

    for start_sec, pcm in clips:
        offset = int(start_sec * sample_rate) * bytes_per_sample
        if offset < prev_end + gap_bytes:
            pushed += 1
            offset = prev_end + gap_bytes
        if offset + len(pcm) > len(buf):
            buf.extend(b"\x00" * (offset + len(pcm) - len(buf)))
        buf[offset : offset + len(pcm)] = pcm
        prev_end = offset + len(pcm)
    overlaps = pushed

    if overlaps:
        log.warning("S6: %d đoạn chồng lên nhau, đã đẩy lùi thay vì ghi đè", overlaps)

    ffmpeg = _require("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp.mp3")
    try:
        proc = subprocess.run(
            [ffmpeg, "-nostdin", "-y", "-v", "error",
             "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "-",
             "-codec:a", "libmp3lame", "-q:a", "3", str(tmp)],
            input=bytes(buf), capture_output=True,
        )
        if proc.returncode != 0:
            raise MediaError(f"Không ghép được audio: {proc.stderr.decode(errors='replace')[:300]}")
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst
