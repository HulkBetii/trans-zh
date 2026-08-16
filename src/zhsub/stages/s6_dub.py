"""S6 TTS engine for Vietnamese subtitle timelines.

The preview and full-output paths deliberately share :class:`CuePlan` and the
same content-addressed clip cache. A preview therefore becomes a free cache hit
when the operator later renders the complete timeline.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..config import Config
from ..dub import SpeechClient, assemble, decode_pcm, speech_bounds
from ..dub.audio import AudioPlacement
from ..dub.calibrate import (
    VOICE_CALIBRATION_MISMATCH,
    VOICE_NOT_CALIBRATED,
    UncalibratedVoiceError,
    calibration_hash,
)
from ..dub.spoken import (
    effective_spoken_hash,
    effective_spoken_items,
    ensure_speech_doc,
    get_speech_state,
    resolve_voice_id,
)
from ..jsonio import read_doc, sha256_json_canonical, sha256_text, write_doc
from ..media import probe_duration
from ..models import (
    IngestDoc,
    SegmentsDoc,
    TtsCalibration,
    TtsCueReport,
    TtsReport,
    TtsWarning,
)
from ..progress import Cancelled, RunContext, ensure_context

log = logging.getLogger(__name__)

SPEECH_NORMALIZATION_VERSION = "speech-normalize-v1"


@dataclass(frozen=True, slots=True)
class CuePlan:
    segment_id: int
    start_sec: float
    room_sec: float
    text: str
    speed: float
    clip_path: Path
    spoken_text_hash: str


@dataclass(frozen=True, slots=True)
class TtsPlan:
    work_dir: Path
    lang: str
    voice_id: str
    speech_revision: str
    effective_spoken_hash: str
    input_hash: str
    calibration: TtsCalibration
    cues: tuple[CuePlan, ...]
    total_duration_sec: float


def plan_speed(
    syllables: int,
    room_sec: float,
    cfg: Config,
    calibration: TtsCalibration | None = None,
) -> float:
    """Return a speed in the configured range for one cue."""
    overhead = calibration.overhead_sec if calibration is not None else cfg.dub.overhead_sec
    per_syllable = (
        calibration.sec_per_syllable
        if calibration is not None
        else cfg.dub.sec_per_syllable
    )
    natural = overhead + syllables * per_syllable
    speed = cfg.dub.base_speed
    if room_sec > 0 and natural / speed > room_sec:
        speed = min(cfg.dub.max_speed, natural / room_sec)
    return max(speed, cfg.dub.base_speed)


def _rooms(segments: list, total_sec: float) -> list[float]:
    starts = [segment.start for segment in segments]
    return [
        (starts[index + 1] if index + 1 < len(starts) else total_sec) - starts[index]
        for index in range(len(starts))
    ]


def _coerce_calibration(
    calibration: TtsCalibration | Mapping[str, Any] | None,
    voice_id: str,
    job_name: str,
) -> TtsCalibration:
    """Nhận đúng số đo của đúng giọng, không thì từ chối.

    Trước đây `None` được thay bằng hằng số trong zhsub.toml, ĐÓNG DẤU voice_id là
    giọng đang chọn — nên phép kiểm ngay dưới không bao giờ bắt được gì, và số đo
    của một giọng lặng lẽ được áp cho mọi giọng khác. Không có fallback ở đây nữa:
    ai muốn dùng hằng số TOML thì phải tự dựng TtsCalibration và tự chịu trách
    nhiệm về việc nó thuộc giọng nào (xem `_resolve_dub_calibration` trong cli).
    """
    if calibration is None:
        raise UncalibratedVoiceError(
            VOICE_NOT_CALIBRATED.format(voice=voice_id, job=job_name)
        )
    result = (
        calibration
        if isinstance(calibration, TtsCalibration)
        else TtsCalibration.model_validate(calibration)
    )
    if result.voice_id != voice_id:
        raise UncalibratedVoiceError(
            VOICE_CALIBRATION_MISMATCH.format(
                selected=voice_id, configured=result.voice_id, job=job_name
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class CueDraft:
    """Một cue trước khi biết hiệu chuẩn: đủ để hiển thị, chưa đủ để tổng hợp."""

    segment_id: int
    start_sec: float
    room_sec: float
    text: str


@dataclass(frozen=True, slots=True)
class _Draft:
    voice_id: str
    state: Any
    rows: list
    segments: list
    total_sec: float
    rooms: list[float]


def _draft_cues(root: Path, cfg: Config, lang: str, voice_id: str | None) -> _Draft:
    """Phần của build_plan không cần biết hiệu chuẩn là gì."""
    if lang != "vi":
        raise ValueError("TTS currently supports Vietnamese only")
    selected_voice = resolve_voice_id(
        root,
        cfg.dub.configured_voice_id(),
        override_voice_id=voice_id,
    )
    rows = effective_spoken_items(root, selected_voice)
    segments = read_doc(root / "segments.json", SegmentsDoc).segments
    total_sec = _source_duration(root, segments)
    return _Draft(
        voice_id=selected_voice,
        state=get_speech_state(root, selected_voice),
        rows=rows,
        segments=segments,
        total_sec=total_sec,
        rooms=_rooms(segments, total_sec),
    )


def build_draft(
    work_dir: str | Path,
    cfg: Config,
    lang: str = "vi",
    *,
    voice_id: str | None = None,
) -> tuple[CueDraft, ...]:
    """Liệt kê cue cho một giọng CHƯA hiệu chuẩn.

    Bắt buộc phải có, không phải tiện ích: nút "Hiệu chuẩn 8 mẫu" chỉ mở khi
    workspace đếm được từ 8 cue trở lên. Nếu chưa hiệu chuẩn mà workspace rỗng
    thì nút biến mất đúng với những giọng cần nó nhất — chưa đo thành không thể đo.
    """
    root = Path(work_dir)
    draft = _draft_cues(root, cfg, lang, voice_id)
    row_by_id = {row.segment_id: row for row in draft.rows}
    cues: list[CueDraft] = []
    for segment, room in zip(draft.segments, draft.rooms):
        row = row_by_id.get(segment.id)
        if row is None:
            raise ValueError(f"missing effective spoken text for segment {segment.id}")
        cues.append(
            CueDraft(
                segment_id=segment.id,
                start_sec=segment.start,
                room_sec=room,
                text=row.effective_spoken_text,
            )
        )
    return tuple(cues)


def _source_duration(work_dir: Path, segments: list) -> float:
    ingest_path = work_dir / "ingest.json"
    if ingest_path.is_file():
        ingest = read_doc(ingest_path, IngestDoc)
        return max(
            float(ingest.media.duration_sec),
            max((segment.end for segment in segments), default=0.0),
        )
    return max((segment.end for segment in segments), default=0.0)


def build_plan(
    work_dir: str | Path,
    cfg: Config,
    lang: str = "vi",
    *,
    voice_id: str | None = None,
    calibration: TtsCalibration | Mapping[str, Any] | None = None,
) -> TtsPlan:
    """Build the immutable cue/input plan shared by preview and full render."""
    root = Path(work_dir)
    draft = _draft_cues(root, cfg, lang, voice_id)
    selected_voice = draft.voice_id
    state = draft.state
    spoken_rows = draft.rows
    row_by_id = {row.segment_id: row for row in spoken_rows}
    segments = draft.segments
    total_sec = draft.total_sec
    rooms = draft.rooms
    selected_calibration = _coerce_calibration(calibration, selected_voice, root.name)
    selected_calibration_hash = calibration_hash(selected_calibration)
    clips_dir = root / "dub" / "vi"
    clips_dir.mkdir(parents=True, exist_ok=True)

    cues: list[CuePlan] = []
    for segment, room in zip(segments, rooms):
        row = row_by_id.get(segment.id)
        if row is None:
            raise ValueError(f"missing effective spoken text for segment {segment.id}")
        text = row.effective_spoken_text
        speed = plan_speed(len(text.split()), room, cfg, selected_calibration)
        spoken_hash = sha256_text(text)
        digest = sha256_json_canonical(
            {
                "segment_id": segment.id,
                "text": text,
                "speed": round(speed, 4),
                "voice_id": selected_voice,
                "calibration_hash": selected_calibration_hash,
                "normalization_version": SPEECH_NORMALIZATION_VERSION,
            }
        )[:16]
        cues.append(
            CuePlan(
                segment_id=segment.id,
                start_sec=segment.start,
                room_sec=room,
                text=text,
                speed=speed,
                clip_path=clips_dir / f"{segment.id:05d}-{digest}.mp3",
                spoken_text_hash=spoken_hash,
            )
        )

    effective_hash = effective_spoken_hash(spoken_rows)
    input_hash = sha256_json_canonical(
        {
            "lang": "vi",
            "voice_id": selected_voice,
            "speech_revision": state.revision,
            "effective_spoken_hash": effective_hash,
            "calibration_hash": selected_calibration_hash,
            "total_duration_sec": total_sec,
            "cues": [
                [cue.segment_id, cue.start_sec, cue.room_sec, cue.text, cue.speed]
                for cue in cues
            ],
        }
    )
    return TtsPlan(
        work_dir=root,
        lang="vi",
        voice_id=selected_voice,
        speech_revision=state.revision,
        effective_spoken_hash=effective_hash,
        input_hash=input_hash,
        calibration=selected_calibration,
        cues=tuple(cues),
        total_duration_sec=total_sec,
    )


def _persist_selected_voice(
    work_dir: str | Path,
    cfg: Config,
    voice_id: str | None,
) -> str:
    selected_voice = resolve_voice_id(
        work_dir,
        cfg.dub.configured_voice_id(),
        override_voice_id=voice_id,
    )
    ensure_speech_doc(work_dir, selected_voice)
    return selected_voice


def _fetch_clip(
    cue: CuePlan,
    client: SpeechClient,
    voice_id: str,
    *,
    force: bool = False,
) -> tuple[Path, bool]:
    if cue.clip_path.is_file() and cue.clip_path.stat().st_size > 0 and not force:
        return cue.clip_path, True
    task_id = client.synthesize(cue.text, voice_id, cue.speed)
    metadata = client.wait(task_id)
    url = metadata.get("audio_url")
    if not url:
        raise RuntimeError(f"cue {cue.segment_id} completed without audio_url")
    client.download(url, cue.clip_path)
    return cue.clip_path, False


def _synthesize_all(
    plan: TtsPlan,
    cfg: Config,
    *,
    force: bool,
    ctx: RunContext,
) -> dict[int, bool]:
    """Synthesize with bounded dispatch so cancellation does not submit new paid work."""
    if ctx.cancelled():
        raise Cancelled("TTS cancelled before dispatch")
    client = SpeechClient(cfg.dub.base_url, cfg.dub.api_key())
    pending: dict[Future[tuple[Path, bool]], CuePlan] = {}
    results: dict[int, bool] = {}
    failures: list[tuple[int, str]] = []
    iterator = iter(plan.cues)
    exhausted = False
    completed = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, cfg.dub.concurrency)) as pool:
            while pending or not exhausted:
                if ctx.cancelled():
                    # Let already submitted provider tasks finish and preserve their
                    # cache files; do not submit any further paid requests.
                    for future in as_completed(list(pending)):
                        try:
                            _, cache_hit = future.result()
                            results[pending[future].segment_id] = cache_hit
                        except Exception as exc:  # noqa: BLE001 - cancellation is primary
                            log.warning("TTS cue failed while cancelling: %s", exc)
                    raise Cancelled("TTS cancelled after in-flight cues finished")

                while not exhausted and len(pending) < max(1, cfg.dub.concurrency):
                    cue = next(iterator, None)
                    if cue is None:
                        exhausted = True
                        break
                    future = pool.submit(
                        _fetch_clip,
                        cue,
                        client,
                        plan.voice_id,
                        force=force,
                    )
                    pending[future] = cue

                if not pending:
                    continue
                future = next(as_completed(list(pending)))
                cue = pending.pop(future)
                completed += 1
                try:
                    _, cache_hit = future.result()
                    results[cue.segment_id] = cache_hit
                except Exception as exc:  # noqa: BLE001 - report all failed cues together
                    failures.append((cue.segment_id, f"{type(exc).__name__}: {exc}"))
                    log.warning("TTS cue %d failed: %s", cue.segment_id, exc)
                    # Provider calls are paid. Once a cue has exhausted its own
                    # retries, drain only the already submitted work instead of
                    # purchasing the rest of a render that must fail anyway.
                    exhausted = True
                ctx.report(
                    completed / max(len(plan.cues), 1),
                    f"vi: {completed}/{len(plan.cues)} cue",
                )
    finally:
        client.close()

    if failures:
        ids = ", ".join(str(segment_id) for segment_id, _ in failures[:10])
        raise RuntimeError(
            f"TTS: {len(failures)}/{len(plan.cues)} cue failed (ids: {ids}); "
            "completed cue cache was preserved"
        )
    return results


def preview_cue(
    work_dir: str | Path,
    cfg: Config,
    segment_id: int,
    *,
    lang: str = "vi",
    voice_id: str | None = None,
    calibration: TtsCalibration | Mapping[str, Any] | None = None,
    force: bool = False,
) -> Path:
    """Synthesize one paid preview using the same cache as full rendering."""
    selected_voice = _persist_selected_voice(work_dir, cfg, voice_id)
    plan = build_plan(work_dir, cfg, lang, voice_id=selected_voice, calibration=calibration)
    cue = next((item for item in plan.cues if item.segment_id == segment_id), None)
    if cue is None:
        raise ValueError(f"unknown subtitle segment id: {segment_id}")
    client = SpeechClient(cfg.dub.base_url, cfg.dub.api_key())
    try:
        path, _ = _fetch_clip(cue, client, plan.voice_id, force=force)
        return path
    finally:
        client.close()


def run(
    work_dir,
    cfg: Config,
    lang: str,
    out_dir,
    force: bool = False,
    ctx: RunContext | None = None,
    *,
    voice_id: str | None = None,
    calibration: TtsCalibration | Mapping[str, Any] | None = None,
    subtitle_approval_signature: str = "",
) -> Path:
    """Render the complete Vietnamese MP3 timeline and persist its report."""
    if lang != "vi":
        raise ValueError("TTS currently supports Vietnamese only")
    ctx = ensure_context(ctx)
    selected_voice = _persist_selected_voice(work_dir, cfg, voice_id)
    plan = build_plan(work_dir, cfg, lang, voice_id=selected_voice, calibration=calibration)
    cache_hits = _synthesize_all(plan, cfg, force=force, ctx=ctx)
    ctx.raise_if_cancelled()

    clips: list[tuple[float, bytes]] = []
    durations: dict[int, float] = {}
    for cue in plan.cues:
        ctx.raise_if_cancelled()
        clip = cue.clip_path
        if not clip.is_file() or clip.stat().st_size == 0:
            raise RuntimeError(f"missing synthesized clip for cue {cue.segment_id}")
        begin, finish = speech_bounds(clip, probe_duration(clip))
        pcm = decode_pcm(clip, cfg.dub.sample_rate, begin, finish)
        clips.append((cue.start_sec, pcm))
        durations[cue.segment_id] = len(pcm) / (cfg.dub.sample_rate * 2)
        if durations[cue.segment_id] > cue.room_sec + 0.05:
            log.warning(
                "TTS cue %d duration %.2fs exceeds room %.2fs",
                cue.segment_id,
                durations[cue.segment_id],
                cue.room_sec,
            )

    destination = Path(out_dir) / f"{Path(work_dir).name}.vi.mp3"
    placements: list[AudioPlacement] = []
    ctx.raise_if_cancelled()
    # Positional argument keeps compatibility with simple test doubles that accept
    # only *args while still collecting actual placement drift in production.
    assemble(
        clips,
        cfg.dub.sample_rate,
        destination,
        plan.total_duration_sec,
        cfg.dub.min_gap_sec,
        placements,
    )
    # If cancellation arrived during ffmpeg assembly, leave the audio without a
    # current report. The cached clips remain reusable on the next render.
    ctx.raise_if_cancelled()
    if len(placements) != len(plan.cues):
        placements = [
            AudioPlacement(
                requested_start_sec=cue.start_sec,
                actual_start_sec=cue.start_sec,
                duration_sec=durations[cue.segment_id],
                drift_sec=0.0,
            )
            for cue in plan.cues
        ]

    warnings: list[TtsWarning] = []
    cue_reports: list[TtsCueReport] = []
    for cue, placement in zip(plan.cues, placements):
        if placement.duration_sec > cue.room_sec + 0.05:
            warnings.append(
                TtsWarning(
                    segment_id=cue.segment_id,
                    kind="duration_overrun",
                    detail="Spoken audio is longer than the available timeline room.",
                    value=placement.duration_sec,
                    limit=cue.room_sec,
                )
            )
        if placement.drift_sec > 0.001:
            warnings.append(
                TtsWarning(
                    segment_id=cue.segment_id,
                    kind="start_drift",
                    detail="Cue was shifted to preserve the configured breathing gap.",
                    value=placement.drift_sec,
                    limit=0.0,
                )
            )
        cue_reports.append(
            TtsCueReport(
                segment_id=cue.segment_id,
                spoken_text_hash=cue.spoken_text_hash,
                speed=cue.speed,
                cache_hit=cache_hits.get(cue.segment_id, False),
                requested_start_sec=placement.requested_start_sec,
                actual_start_sec=placement.actual_start_sec,
                duration_sec=placement.duration_sec,
                room_sec=cue.room_sec,
                drift_sec=placement.drift_sec,
            )
        )

    report = TtsReport(
        output=str(destination),
        voice_id=plan.voice_id,
        voice_hash=sha256_text(f"ai33_vbee:{plan.voice_id}"),
        calibration_hash=calibration_hash(plan.calibration),
        input_hash=plan.input_hash,
        speech_revision=plan.speech_revision,
        subtitle_approval_signature=subtitle_approval_signature,
        duration_sec=plan.total_duration_sec,
        cues=cue_reports,
        warnings=warnings,
    )
    write_doc(Path(work_dir) / "tts_report.vi.json", report)
    return destination
