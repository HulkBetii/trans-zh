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
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..jsonio import sha256_json_canonical
from ..media import probe_duration
from ..models import TtsCalibration, TtsCalibrationPoint
from .audio import speech_bounds
from .client import SpeechClient
from .spoken import effective_spoken_items, resolve_voice_id

log = logging.getLogger(__name__)

CALIBRATION_SAMPLE_COUNT = 8


class UncalibratedVoiceError(ValueError):
    """S6 từ chối đoán hằng số tốc độ đọc cho một giọng chưa được đo.

    Kế thừa ValueError có chủ đích: các `except ValueError` sẵn có ở tầng web vẫn
    bắt được, nên chỗ nào chưa kịp xử lý riêng thì vẫn xuống nhẹ nhàng thay vì 500.
    """


VOICE_NOT_CALIBRATED = (
    "Giọng {voice!r} chưa có số đo tốc độ đọc. Phải đo trước khi lồng tiếng: "
    "chạy `zhsub dub-calibrate {job}`, hoặc bấm 'Hiệu chuẩn 8 mẫu' trong Studio."
)

VOICE_CALIBRATION_MISMATCH = (
    "Job đang dùng giọng {selected!r}, nhưng overhead_sec/sec_per_syllable trong "
    "[dub] là số đo của giọng {configured!r}. Dùng chéo hai giọng thì S6 tính sai "
    "tốc độ đọc mà không báo lỗi gì. Đo lại cho giọng đang dùng: "
    "`zhsub dub-calibrate {job}`."
)


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
    """Khớp bình phương tối thiểu, trả về (overhead_sec, sec_per_syllable).

    Overhead không được âm. Có giọng đọc gần như không có phần đầu/cuối nào sau
    khi cắt lặng, lại ngắt nhịp nhiều hơn ở câu dài — với dữ liệu đó, đường thẳng
    khớp nhất cắt trục tung dưới 0. Số đo không sai, chỉ là mô hình hai tham số
    chạm biên miền hợp lệ của nó: overhead âm nghĩa là cue một âm tiết được dự
    đoán dài âm giây, và mọi phép tính tốc độ phía sau thành vô nghĩa.

    Gặp trường hợp đó thì khớp lại với overhead ghim ở 0, tức đường thẳng qua gốc
    tọa độ. Phải khớp LẠI slope chứ không phải chỉ ép overhead xuống 0 rồi giữ
    slope cũ: trên giọng đã gặp, ép suông làm tổng bình phương sai số 1.84 còn
    khớp lại chỉ 0.65.
    """
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        raise ValueError("mọi mẫu có cùng số âm tiết — không khớp được")
    slope = (n * sxy - sx * sy) / denom
    overhead = (sy - slope * sx) / n
    if overhead < 0:
        if sxx == 0:
            raise ValueError("mọi mẫu rỗng — không khớp được")
        overhead, slope = 0.0, sxy / sxx
    if slope <= 0:
        # Câu dài không hề đọc lâu hơn câu ngắn: mẫu hỏng, không phải giọng lạ.
        raise ValueError(
            "thời lượng không tăng theo số âm tiết — mẫu đo hỏng, "
            "hãy kiểm tra audio trả về từ nhà cung cấp"
        )
    return overhead, slope


def calibration_hash(calibration: TtsCalibration) -> str:
    """Hash only the calibration values that affect synthesized audio.

    SQLite intentionally stores the reusable coefficients rather than the
    original probe files and points. Including provenance fields here made the
    same calibration hash differently after it was reloaded from the database.
    """
    return sha256_json_canonical(
        {
            "provider": calibration.provider,
            "voice_id": calibration.voice_id,
            "sample_count": calibration.sample_count,
            "overhead_sec": calibration.overhead_sec,
            "sec_per_syllable": calibration.sec_per_syllable,
        }
    )


CALIBRATION_PROBE_VERSION = "calibrate-probe-v1"


def _probe_path(probe_dir: Path, text: str, voice_id: str) -> Path:
    """Đặt tên probe theo nội dung, không theo vị trí trong danh sách mẫu.

    Vị trí là thuộc tính của lần CHỌN mẫu, không phải của câu: sửa một lời đọc là
    thứ tự sắp xếp đổi, và mọi file vẫn còn đúng sẽ trượt cache rồi thành rác. Số
    âm tiết thì gắn với câu nên ổn định, lại tiện: `ls dub/_calibrate/` hiện ngay
    dải đòn bẩy mà phép khớp dựa vào — thứ duy nhất giúp chẩn đoán một fit xấu.
    """
    digest = sha256_json_canonical(
        {
            "text": text,
            "voice_id": voice_id,
            # Probe luôn đo ở tốc độ 1.0; ghim vào khóa để bất biến đó thành văn bản.
            "speed": 1.0,
            "probe_version": CALIBRATION_PROBE_VERSION,
        }
    )[:16]
    return probe_dir / f"{len(text.split()):03d}-{digest}.mp3"


_LEGACY_PROBE = re.compile(r"^\d{2}\.mp3$")


def drop_legacy_probes(probe_dir: Path) -> int:
    """Xóa probe đặt tên theo vị trí (``00.mp3``) của bản trước khi có cache.

    Xóa chứ không đổi tên, dù đổi tên nghe hấp dẫn hơn vì tiết kiệm 8 lượt TTS.
    Muốn đổi tên thì phải ĐOÁN rằng câu thứ i hôm nay vẫn đúng là câu đã sinh ra
    ``0i.mp3`` hôm trước — chỉ đúng khi lời đọc của job chưa hề đổi. Đoán sai thì
    gán nhầm audio cho câu, và phép khớp ra một hệ số sai mà không có dấu hiệu
    nào: đúng họ lỗi mà cả đợt này đang dọn.

    Tám lượt TTS rẻ hơn một hiệu chuẩn sai âm thầm.
    """
    stale = [path for path in probe_dir.glob("*.mp3") if _LEGACY_PROBE.match(path.name)]
    for path in stale:
        path.unlink()
    if stale:
        log.info("Bỏ %d probe tên cũ, sẽ đo lại (không đoán câu nào ứng với file nào)", len(stale))
    return len(stale)


def _fetch_probe(
    client: SpeechClient, text: str, voice_id: str, path: Path, *, force: bool
) -> bool:
    """Trả True khi dùng lại được file cũ. Cùng khuôn với s6_dub._fetch_clip."""
    if path.is_file() and path.stat().st_size > 0 and not force:
        return True
    task_id = client.synthesize(text, voice_id, 1.0)
    url = client.wait(task_id).get("audio_url")
    if not url:
        raise RuntimeError("task xong nhưng không có audio_url")
    client.download(url, path)
    return False


def calibration_revision(record: Any) -> str:
    """Chữ ký của một dòng số đo trong SQLite, dùng cho khóa lạc quan."""
    return sha256_json_canonical(
        {
            "provider": record.provider,
            "voice_id": record.voice_id,
            "overhead_sec": record.overhead_sec,
            "sec_per_syllable": record.sec_per_syllable,
            "sample_count": record.sample_count,
        }
    )


def calibration_from_record(record: Any) -> TtsCalibration:
    """SQLite -> TtsCalibration. Một bản duy nhất: trước đây có ba, mỗi nơi một kiểu."""
    return TtsCalibration(
        voice_id=record.voice_id,
        overhead_sec=record.overhead_sec,
        sec_per_syllable=record.sec_per_syllable,
        sample_count=record.sample_count,
        samples_hash=calibration_revision(record),
        created_at=(
            datetime.fromtimestamp(record.updated_at, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
            if getattr(record, "updated_at", None)
            else ""
        ),
        points=[],
    )


def calibration_from_config(cfg: Config) -> TtsCalibration | None:
    """Hai hằng số trong zhsub.toml, gắn với ĐÚNG giọng ghi cùng chỗ.

    Không đoán tên giọng: chính chỗ đó là lỗi cũ. Chưa đặt voice_id thì không có
    số đo nào để nói tới.
    """
    voice_id = cfg.dub.configured_voice_id()
    if voice_id is None:
        return None
    return TtsCalibration(
        voice_id=voice_id,
        overhead_sec=cfg.dub.overhead_sec,
        sec_per_syllable=cfg.dub.sec_per_syllable,
        samples_hash="config",
        created_at="config",
        points=[],
    )


def calibrate_voice(
    work_dir,
    cfg: Config,
    lang: str = "vi",
    samples: int = CALIBRATION_SAMPLE_COUNT,
    *,
    voice_id: str | None = None,
    sample_texts: Sequence[str] | None = None,
    progress: Callable[[float, str], None] | None = None,
    force: bool = False,
) -> TtsCalibration:
    """Measure one voice on exactly eight effective spoken samples.

    ``sample_texts`` lets the voice library persist one shared calibration corpus;
    the legacy CLI leaves it unset and derives a spread of samples from the job.

    ``force`` tổng hợp lại cả những mẫu đã có trên đĩa. Mặc định là dùng lại, để
    một lần thử lại sau lỗi hay sau khi hủy không mất thêm lượt TTS nào.
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
            cfg.dub.configured_voice_id(),
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
    drop_legacy_probes(probe_dir)

    log.info("Đo giọng %s trên %d câu mẫu", selected_voice, len(texts))
    points: list[tuple[int, float]] = []
    reused = 0
    try:
        for index, text in enumerate(texts):
            # Luôn ở tốc độ 1.0: hai hằng số này là mốc gốc, mọi tốc độ khác suy ra
            # từ chúng. Đo ở tốc độ khác rồi dùng làm mốc là tự nhân sai số.
            clip = _probe_path(probe_dir, text, selected_voice)
            if _fetch_probe(client, text, selected_voice, clip, force=force):
                reused += 1

            begin, finish = speech_bounds(clip, probe_duration(clip))
            syllables = len(text.split())
            points.append((syllables, finish - begin))
            log.info("  %2d âm tiết -> %.2fs (đã cắt lặng)", syllables, finish - begin)
            if progress is not None:
                progress((index + 1) / CALIBRATION_SAMPLE_COUNT, f"{index + 1}/8 mẫu")
    finally:
        client.close()
    if reused:
        log.info("Dùng lại %d/%d mẫu đã có, không tốn lượt TTS", reused, len(texts))

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
