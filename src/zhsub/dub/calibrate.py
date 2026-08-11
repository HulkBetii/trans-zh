"""Đo tốc độ đọc của một giọng, để S6 tính được tốc độ từng cue.

``overhead_sec`` và ``sec_per_syllable`` trong ``[dub]`` là số đo của ĐÚNG MỘT
giọng. Đổi giọng mà không đo lại thì phần tính tốc độ sai — âm thầm, không báo
lỗi, chỉ hiện ra dưới dạng tiếng lệch khỏi hình. Đây là công cụ để đo lại.

Không lấy một tỉ lệ duy nhất mà khớp tuyến tính hai tham số:

    thời lượng = overhead + số_âm_tiết x hệ_số

Vì mỗi câu có phần đầu/cuối không tỉ lệ với độ dài. Với cue 1.5 giây, chính
overhead mới quyết định chứ không phải tốc độ đọc — mà cue ngắn là nhóm dễ tràn
nhất, nên gộp hai đại lượng làm một sẽ sai ở đúng chỗ quan trọng.

Đo SAU khi cắt lặng hai đầu, vì đó là thứ S6 thực sự ghép vào timeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from ..jsonio import read_doc
from ..media import probe_duration
from ..models import TranslationsDoc
from .audio import speech_bounds
from .client import SpeechClient

log = logging.getLogger(__name__)

MIN_SAMPLES = 3


def pick_samples(texts: list[str], count: int) -> list[str]:
    """Chọn câu trải đều theo số âm tiết, để phép khớp có đòn bẩy ở cả hai đầu.

    Lấy toàn câu dài thì ước lượng overhead sai bét, vì overhead chỉ lộ ra ở câu
    ngắn — nơi nó chiếm phần lớn thời lượng.
    """
    ordered = sorted((t for t in texts if t.strip()), key=lambda t: len(t.split()))
    if len(ordered) <= count:
        return ordered
    step = (len(ordered) - 1) / (count - 1)
    return [ordered[round(i * step)] for i in range(count)]


def fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Khớp bình phương tối thiểu, trả về (overhead_sec, sec_per_syllable)."""
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        raise ValueError("mọi mẫu có cùng số âm tiết — không khớp được")
    slope = (n * sxy - sx * sy) / denom
    return (sy - slope * sx) / n, slope


def run(work_dir, cfg: Config, lang: str, samples: int = 8) -> tuple[float, float]:
    work_dir = Path(work_dir)
    doc = read_doc(work_dir / f"translations.{lang}.json", TranslationsDoc)
    texts = pick_samples([i.translation for i in doc.items], samples)
    if len(texts) < MIN_SAMPLES:
        raise RuntimeError(f"cần ít nhất {MIN_SAMPLES} câu để đo, chỉ có {len(texts)}")

    voice_id = cfg.dub.require_voice()
    client = SpeechClient(cfg.dub.base_url, cfg.dub.api_key())
    probe_dir = work_dir / "dub" / "_calibrate"
    probe_dir.mkdir(parents=True, exist_ok=True)

    log.info("Đo giọng %s trên %d câu mẫu", voice_id, len(texts))
    points: list[tuple[int, float]] = []
    try:
        for index, text in enumerate(texts):
            # Luôn ở tốc độ 1.0: hai hằng số này là mốc gốc, mọi tốc độ khác suy ra
            # từ chúng. Đo ở tốc độ khác rồi dùng làm mốc là tự nhân sai số.
            task_id = client.synthesize(text, voice_id, 1.0)
            url = client.wait(task_id).get("audio_url")
            if not url:
                raise RuntimeError("task xong nhưng không có audio_url")
            clip = client.download(url, probe_dir / f"{index:02d}.mp3")

            begin, finish = speech_bounds(clip, probe_duration(clip))
            syllables = len(text.split())
            points.append((syllables, finish - begin))
            log.info("  %2d âm tiết -> %.2fs (đã cắt lặng)", syllables, finish - begin)
    finally:
        client.close()

    overhead, per_syllable = fit(points)
    log.info(
        "thời lượng = %.2fs + số_âm_tiết x %.3fs  (%.2f âm tiết/giây)",
        overhead, per_syllable, 1 / per_syllable if per_syllable else 0,
    )
    return overhead, per_syllable
