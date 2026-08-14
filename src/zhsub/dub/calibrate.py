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
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..jsonio import sha256_json_canonical
from ..media import probe_duration
from ..models import TtsCalibration, TtsCalibrationPoint
from .audio import speech_bounds
from .client import SpeechClient
from .spoken import effective_spoken_items, resolve_voice_id

log = logging.getLogger(__name__)

MIN_SAMPLES = 3  # Legacy export; shared calibration itself always uses eight.
CALIBRATION_SAMPLE_COUNT = 8


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


def calibration_hash(calibration: TtsCalibration) -> str:
    return sha256_json_canonical(calibration.model_dump(mode="json"))


def calibrate_voice(
    work_dir,
    cfg: Config,
    lang: str = "vi",
    samples: int = CALIBRATION_SAMPLE_COUNT,
    *,
    voice_id: str | None = None,
    sample_texts: Sequence[str] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> TtsCalibration:
    """Measure one voice on exactly eight effective spoken samples.

    ``sample_texts`` lets the voice library persist one shared calibration corpus;
    the legacy CLI leaves it unset and derives a spread of samples from the job.
    """
    if lang != "vi":
        raise ValueError("TTS currently supports Vietnamese only")
    if samples != CALIBRATION_SAMPLE_COUNT:
        raise ValueError("TTS calibration requires exactly 8 samples")

    work_dir = Path(work_dir)
    if voice_id is not None:
        selected_voice = voice_id.strip()
        if not selected_voice:
            raise ValueError("voice_id must not be blank")
    else:
        selected_voice = resolve_voice_id(
            work_dir,
            cfg.dub.voice_id if cfg.dub.voice_id != "SET_ME" else None,
        )
    if sample_texts is None:
        rows = effective_spoken_items(work_dir, selected_voice)
        texts = pick_samples(
            [item.effective_spoken_text for item in rows], CALIBRATION_SAMPLE_COUNT
        )
    else:
        texts = pick_samples(list(sample_texts), CALIBRATION_SAMPLE_COUNT)
    if len(texts) != CALIBRATION_SAMPLE_COUNT:
        raise RuntimeError(
            f"cần đúng {CALIBRATION_SAMPLE_COUNT} câu để đo, chỉ có {len(texts)}"
        )

    client = SpeechClient(cfg.dub.base_url, cfg.dub.api_key())
    probe_dir = work_dir / "dub" / "_calibrate"
    probe_dir.mkdir(parents=True, exist_ok=True)

    log.info("Đo giọng %s trên %d câu mẫu", selected_voice, len(texts))
    points: list[tuple[int, float]] = []
    try:
        for index, text in enumerate(texts):
            # Luôn ở tốc độ 1.0: hai hằng số này là mốc gốc, mọi tốc độ khác suy ra
            # từ chúng. Đo ở tốc độ khác rồi dùng làm mốc là tự nhân sai số.
            task_id = client.synthesize(text, selected_voice, 1.0)
            url = client.wait(task_id).get("audio_url")
            if not url:
                raise RuntimeError("task xong nhưng không có audio_url")
            clip = client.download(url, probe_dir / f"{index:02d}.mp3")

            begin, finish = speech_bounds(clip, probe_duration(clip))
            syllables = len(text.split())
            points.append((syllables, finish - begin))
            log.info("  %2d âm tiết -> %.2fs (đã cắt lặng)", syllables, finish - begin)
            if progress is not None:
                progress((index + 1) / CALIBRATION_SAMPLE_COUNT, f"{index + 1}/8 mẫu")
    finally:
        client.close()

    overhead, per_syllable = fit(points)
    log.info(
        "thời lượng = %.2fs + số_âm_tiết x %.3fs  (%.2f âm tiết/giây)",
        overhead, per_syllable, 1 / per_syllable if per_syllable else 0,
    )
    return TtsCalibration(
        voice_id=selected_voice,
        sample_count=CALIBRATION_SAMPLE_COUNT,
        overhead_sec=round(overhead, 6),
        sec_per_syllable=round(per_syllable, 6),
        samples_hash=sha256_json_canonical(texts),
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        points=[
            TtsCalibrationPoint(syllables=syllables, duration_sec=duration)
            for syllables, duration in points
        ],
    )


def run(
    work_dir,
    cfg: Config,
    lang: str,
    samples: int = CALIBRATION_SAMPLE_COUNT,
    *,
    voice_id: str | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> tuple[float, float]:
    """Legacy CLI wrapper returning the two historical TOML constants."""
    result = calibrate_voice(
        work_dir,
        cfg,
        lang,
        samples,
        voice_id=voice_id,
        progress=progress,
    )
    return result.overhead_sec, result.sec_per_syllable
