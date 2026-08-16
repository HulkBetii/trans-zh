"""Application service for the Vietnamese TTS workspace."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..dub.calibrate import (
    VOICE_NOT_CALIBRATED,
    calibrate_voice,
    calibration_from_record as _calibration_model,
    calibration_revision as _calibration_revision,
)
from ..dub.client import SpeechClient
from ..dub.spoken import (
    SpeechConflictError,
    SpeechValidationError,
    apply_spoken_changes,
    effective_spoken_items,
    ensure_speech_doc,
    get_speech_state,
)
from ..jsonio import read_doc, sha256_file, sha256_json_canonical
from ..jobs import Job, JobStore, Run
from ..models import SegmentsDoc, TtsCalibration, TtsReport
from ..progress import Progress, RunContext
from ..stages import s6_dub
from .schemas import (
    ArtifactResponse,
    RunResponse,
    TtsCueResponse,
    TtsVoicePage,
    TtsVoiceResponse,
    TtsWorkspaceResponse,
    VoiceCalibrationResponse,
)

PROVIDER = "ai33_vbee"
LANGUAGE = "vi"
#: Nguồn giọng của vbee. Mặc định của nhà cung cấp là chỉ giọng chính hãng (25
#: giọng); cả thư viện là 1268.
VOICE_OWNERSHIPS = ("all", "vbee", "community")


def _is_vietnamese(item: dict[str, Any]) -> bool:
    if str(item.get("language") or "").casefold() == "vietnamese":
        return True
    return any(
        str(child.get("locale") or "").casefold().startswith("vi")
        for child in item.get("languages") or []
        if isinstance(child, dict)
    )
CALIBRATION_SAMPLES = 8
log = logging.getLogger(__name__)


def _utc_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _voice_tier(item: dict[str, Any]) -> str | None:
    for value in item.get("descriptives") or []:
        if str(value).upper() in {"BASIC", "PREMIUM", "ADVANCED"}:
            return str(value).upper()
    return None


class TtsService:
    """Keep TTS workspace policy out of the HTTP handlers and scheduler."""

    def __init__(
        self,
        config: Config,
        store: JobStore,
        *,
        subtitle_signature: Callable[[Job], str],
    ) -> None:
        self.config = config
        self.store = store
        self.subtitle_signature = subtitle_signature
        self._voice_cache: dict[str, TtsVoiceResponse] = {}

    @property
    def provider_ready(self) -> bool:
        return self.config.dub.credential_available()

    def seed_config_calibration(self) -> None:
        """Make the measured legacy voice available to the new shared library."""
        voice_id = self.config.dub.configured_voice_id()
        if voice_id is None:
            return
        if self.store.get_voice_calibration(PROVIDER, voice_id) is None:
            self.store.save_voice_calibration(
                PROVIDER,
                voice_id,
                self.config.dub.overhead_sec,
                self.config.dub.sec_per_syllable,
                sample_count=CALIBRATION_SAMPLES,
                source_job_id="config",
                overwrite=False,
            )

    def started(self, job: Job) -> bool:
        work_dir = Path(job.work_dir)
        latest = self.store.latest_run(job.job_id, lane="tts")
        report = self._report(job)
        audio_path = self._audio_path(job, report)
        return bool(
            latest
            or (work_dir / "speech.vi.json").is_file()
            or (work_dir / "tts_report.vi.json").is_file()
            or audio_path.is_file()
            or self.store.get_tts_approval(job.job_id, LANGUAGE)
        )

    def activity_timestamp(self, job: Job) -> float | None:
        report = self._report(job)
        paths = [
            Path(job.work_dir) / "speech.vi.json",
            Path(job.work_dir) / "tts_report.vi.json",
            self._audio_path(job, report),
        ]
        timestamps = [
            path.stat().st_mtime
            for path in paths
            if path.is_file()
        ]
        latest = self.store.latest_run(job.job_id, lane="tts")
        if latest is not None:
            timestamps.append(latest.updated_at)
        approval = self.store.get_tts_approval(job.job_id, LANGUAGE)
        if approval is not None and approval.approved_at is not None:
            timestamps.append(approval.approved_at)
        return max(timestamps) if timestamps else None

    def _client(self) -> SpeechClient:
        if not self.provider_ready:
            raise RuntimeError(
                f"Chưa có API key TTS cho {self.config.dub.api_key_env}."
            )
        return SpeechClient(self.config.dub.base_url, self.config.dub.api_key())

    def voices(
        self,
        *,
        search: str = "",
        page: int = 1,
        page_size: int = 30,
        ownership: str = "all",
    ) -> TtsVoicePage:
        """Một trang thư viện giọng.

        Phân trang và tìm kiếm để nhà cung cấp làm, không làm lại trong bộ nhớ:
        thư viện có 1268 giọng khi tính cả nhóm cộng đồng, nên cách cũ (lấy 100 mục
        đầu rồi tự cắt trang) khiến trang thứ tư trở đi luôn rỗng và ô tìm kiếm chỉ
        soi được trong 100 mục đó.
        """
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("invalid voice page")
        if ownership not in VOICE_OWNERSHIPS:
            raise ValueError(f"invalid voice ownership: {ownership!r}")
        client = self._client()
        try:
            raw, total = client.voice_page(
                provider="vbee",
                language="Vietnamese",
                search=search,
                page=page,
                page_size=page_size,
                voice_ownership=ownership,
            )
            # Chốt chặn cuối: chỉ nhận giọng tiếng Việt. Đã truyền language cho nhà
            # cung cấp nên bình thường không bỏ gì; nếu có thì trừ khỏi tổng để số
            # đếm khớp danh sách đang hiện. Trên nhiều trang con số thành xấp xỉ,
            # nhưng danh sách đúng quan trọng hơn con số đúng — một giọng tiếng Anh
            # lọt vào sẽ đọc hỏng cả video.
            rows = [item for item in raw if _is_vietnamese(item)]
            total = max(0, total - (len(raw) - len(rows)))
            items = [
                TtsVoiceResponse(
                    voice_id=str(item.get("voice_id") or ""),
                    name=str(item.get("name") or item.get("voice_id") or ""),
                    description=item.get("description"),
                    locale=item.get("locale"),
                    gender=item.get("gender"),
                    age=item.get("age"),
                    category=item.get("category"),
                    tier=_voice_tier(item),
                    preview_url=item.get("preview_url"),
                    avatar_url=item.get("avatar_url"),
                    calibrated=self.store.get_voice_calibration(
                        PROVIDER, str(item.get("voice_id") or "")
                    )
                    is not None,
                )
                for item in rows
                if item.get("voice_id")
            ]
            self._voice_cache.update({item.voice_id: item for item in items})
            try:
                credits = client.credits()
            except Exception:  # noqa: BLE001 - voice browsing should survive credit errors
                credits = None
            return TtsVoicePage(
                items=items,
                page=page,
                page_size=page_size,
                total=total,
                has_more=page * page_size < total,
                credits=credits,
            )
        finally:
            client.close()

    def _fallback_voice(self, job: Job) -> str | None:
        return self.config.dub.configured_voice_id()

    def _state(self, job: Job):
        return get_speech_state(job.work_dir, self._fallback_voice(job))

    def _subtitle_approved(self, job: Job) -> tuple[bool, str]:
        signature = self.subtitle_signature(job)
        latest = self.store.latest_run(job.job_id, lane="pipeline")
        completed = job.status == "done" or (latest is not None and latest.status == "completed")
        return bool(completed and job.approved_signature and job.approved_signature == signature), signature

    def _audio_path(self, job: Job, report: TtsReport | None = None) -> Path:
        snapshot = job.request or {}
        output_dir = Path(snapshot.get("output_dir") or "output").resolve(strict=False)
        if report is not None:
            reported = Path(report.output)
            if not reported.is_absolute():
                reported = Path.cwd() / reported
            reported = reported.resolve(strict=False)
            if reported == output_dir or reported.is_relative_to(output_dir):
                return reported
        return output_dir / f"{Path(job.work_dir).name}.vi.mp3"

    def _report(self, job: Job) -> TtsReport | None:
        path = Path(job.work_dir) / "tts_report.vi.json"
        if not path.is_file():
            return None
        try:
            return read_doc(path, TtsReport)
        except (OSError, ValueError):
            return None

    def _plan(self, job: Job, voice_id: str, calibration: TtsCalibration | None):
        cfg = self.config.model_copy(deep=True)
        cfg.dub.voice_id = voice_id
        if calibration is not None:
            cfg.dub.overhead_sec = calibration.overhead_sec
            cfg.dub.sec_per_syllable = calibration.sec_per_syllable
        return s6_dub.build_plan(job.work_dir, cfg, LANGUAGE, voice_id=voice_id, calibration=calibration)

    def _output(self, job: Job, plan: Any | None, report: TtsReport | None) -> tuple[ArtifactResponse | None, bool]:
        path = self._audio_path(job, report)
        if report is None and not path.exists():
            return None, False
        current = bool(
            plan is not None
            and report is not None
            and report.input_hash == plan.input_hash
            and report.subtitle_approval_signature == self.subtitle_signature(job)
        )
        exists = path.is_file()
        state = "current" if current and exists else "stale" if exists else "missing"
        stat = path.stat() if exists else None
        artifact_id = hashlib.sha256(str(path.resolve(strict=False)).encode("utf-8")).hexdigest()[:20]
        return ArtifactResponse(
            artifact_id=artifact_id,
            name=path.name,
            kind="audio",
            language=LANGUAGE,
            format="mp3",
            size_bytes=stat.st_size if stat else None,
            created_at=_utc_iso(stat.st_mtime) if stat else None,
            state=state,
            download_url=(f"/api/v1/jobs/{job.job_id}/artifacts/{artifact_id}" if exists else None),
        ), state != "current"

    def workspace(self, job: Job) -> TtsWorkspaceResponse:
        self.seed_config_calibration()
        voice_id: str | None = None
        state = None
        artifacts_invalid = False
        try:
            state = self._state(job)
            voice_id = state.voice_id
        except SpeechValidationError:
            pass
        except (OSError, ValueError) as exc:
            artifacts_invalid = True
            log.warning("Could not read TTS state for job %s: %s", job.job_id, exc)

        calibration_record = (
            self.store.get_voice_calibration(PROVIDER, voice_id) if voice_id else None
        )
        calibration = _calibration_model(calibration_record) if calibration_record else None
        plan = None
        cues: list[TtsCueResponse] = []
        uncached = 0
        if voice_id and (Path(job.work_dir) / "segments.json").is_file() and (
            Path(job.work_dir) / "translations.vi.json"
        ).is_file():
            try:
                segments = read_doc(Path(job.work_dir) / "segments.json", SegmentsDoc)
                segment_by_id = {segment.id: segment for segment in segments.segments}
                rows = {row.segment_id: row for row in effective_spoken_items(job.work_dir)}
                clips_dir = Path(job.work_dir) / "dub" / LANGUAGE

                def _override_state(segment_id: int) -> str:
                    evaluation = state and next(
                        (item for item in state.evaluations if item.segment_id == segment_id),
                        None,
                    )
                    return {
                        None: "none",
                        "valid": "manual",
                        "base_changed": "base_changed",
                        "stale": "stale",
                    }.get(evaluation.status if evaluation else None, "none")

                if calibration is not None:
                    plan = self._plan(job, voice_id, calibration)
                    for cue in plan.cues:
                        row = rows[cue.segment_id]
                        segment = segment_by_id[cue.segment_id]
                        calibration_overhead = plan.calibration.overhead_sec
                        calibration_slope = plan.calibration.sec_per_syllable
                        predicted = calibration_overhead + len(cue.text.split()) * calibration_slope
                        preview_state = "current" if cue.clip_path.is_file() and cue.clip_path.stat().st_size > 0 else "missing"
                        if preview_state == "missing" and any(
                            cue.clip_path.parent.glob(f"{cue.segment_id:05d}-*.mp3")
                        ):
                            preview_state = "stale"
                        if preview_state != "current":
                            uncached += 1
                        cues.append(
                            TtsCueResponse(
                                segment_id=cue.segment_id,
                                start=cue.start_sec,
                                end=segment.end,
                                subtitle_text=row.subtitle_text,
                                default_spoken_text=row.base_spoken_text,
                                effective_spoken_text=row.effective_spoken_text,
                                override_state=_override_state(cue.segment_id),  # type: ignore[arg-type]
                                room_seconds=cue.room_sec,
                                predicted_duration=predicted,
                                overflow_seconds=max(0.0, predicted - cue.room_sec),
                                speed=cue.speed,
                                preview_state=preview_state,  # type: ignore[arg-type]
                                preview_url=(
                                    f"/api/v1/jobs/{job.job_id}/tts/vi/previews/{cue.segment_id}"
                                    if preview_state == "current"
                                    else None
                                ),
                            )
                        )
                else:
                    # Chưa hiệu chuẩn thì không có ước tính thời lượng — và không
                    # bịa ra bằng hằng số của giọng khác, đó chính là lỗi cũ. Vẫn
                    # phải liệt kê đủ cue, vì nút "Hiệu chuẩn 8 mẫu" chỉ mở khi
                    # đếm được từ 8 cue trở lên.
                    for draft in s6_dub.build_draft(
                        job.work_dir, self.config, LANGUAGE, voice_id=voice_id
                    ):
                        row = rows[draft.segment_id]
                        segment = segment_by_id[draft.segment_id]
                        uncached += 1
                        cues.append(
                            TtsCueResponse(
                                segment_id=draft.segment_id,
                                start=draft.start_sec,
                                end=segment.end,
                                subtitle_text=row.subtitle_text,
                                default_spoken_text=row.base_spoken_text,
                                effective_spoken_text=row.effective_spoken_text,
                                override_state=_override_state(draft.segment_id),  # type: ignore[arg-type]
                                room_seconds=draft.room_sec,
                                predicted_duration=None,
                                overflow_seconds=None,
                                speed=self.config.dub.base_speed,
                                preview_state=(
                                    "stale"
                                    if any(clips_dir.glob(f"{draft.segment_id:05d}-*.mp3"))
                                    else "missing"
                                ),
                                preview_url=None,
                            )
                        )
            except (OSError, ValueError, SpeechValidationError, KeyError) as exc:
                plan = None
                artifacts_invalid = True
                log.warning("Could not build TTS workspace for job %s: %s", job.job_id, exc)

        subtitle_approved, subtitle_signature = self._subtitle_approved(job)
        report = self._report(job)
        output, output_stale = self._output(job, plan, report)
        latest = self.store.latest_run(job.job_id, lane="tts")
        active_any = self.store.active_run(job.job_id)
        active_tts = self.store.active_run(job.job_id, lane="tts")
        execution_status = latest.status if latest else "not_started"
        approval = self.store.get_tts_approval(job.job_id, LANGUAGE)
        current_audio_signature = None
        if plan is not None and report is not None and not output_stale and output is not None:
            current_audio_signature = sha256_json_canonical(
                {
                    "input_hash": plan.input_hash,
                    "audio": sha256_file(self._audio_path(job, report)),
                }
            )
        quality = "needs_review"
        if current_audio_signature and approval and approval.approved_signature == current_audio_signature:
            quality = "approved"
        elif report is not None and not output_stale and report.warnings:
            quality = "degraded"

        reasons: list[str] = []
        if artifacts_invalid:
            reasons.append("tts_artifacts_invalid")
        if not self.provider_ready:
            reasons.append("tts_provider_unavailable")
        if not voice_id:
            reasons.append("voice_unselected")
        if voice_id and calibration_record is None:
            reasons.append("voice_uncalibrated")
        if state:
            if any(item.status == "stale" for item in state.evaluations):
                reasons.append("stale_spoken_overrides")
            if any(item.status == "base_changed" for item in state.evaluations):
                reasons.append("spoken_overrides_base_changed")
        if not subtitle_approved:
            reasons.append("subtitle_not_approved")
        if output_stale:
            reasons.append("stale_tts_output")
        if report is not None and report.warnings:
            reasons.append("tts_warnings")

        active_locked = active_any is not None and active_any.status in {
            "queued",
            "running",
            "cancelling",
        }
        allowed: list[str] = []
        if active_locked and active_tts is not None:
            allowed.append("cancel")
        elif not active_locked:
            # Nghe thử cần hiệu chuẩn: không có số đo thì không tính được tốc độ,
            # và S6 từ chối trước khi tổng hợp. Nút mờ hơn là một run chắc chắn
            # hỏng — muốn nghe giọng thì thư viện giọng đã có mẫu miễn phí.
            if voice_id and self.provider_ready and cues and calibration_record is not None:
                allowed.append("preview")
            if voice_id and self.provider_ready and len(cues) >= CALIBRATION_SAMPLES:
                allowed.append("calibrate")
            if (
                voice_id
                and self.provider_ready
                and calibration_record is not None
                and subtitle_approved
                and not any(reason in reasons for reason in ("stale_spoken_overrides", "spoken_overrides_base_changed"))
            ):
                allowed.append("render")
            if current_audio_signature and quality != "approved":
                allowed.append("approve")
            if quality == "approved":
                allowed.append("unapprove")

        return TtsWorkspaceResponse(
            revision=state.revision if state else sha256_json_canonical({"voice_id": None}),
            provider_ready=self.provider_ready,
            voice_id=voice_id,
            selected_voice=(
                self._voice_cache.get(voice_id)
                or TtsVoiceResponse(
                    voice_id=voice_id,
                    name=voice_id,
                    calibrated=calibration_record is not None,
                )
                if voice_id
                else None
            ),
            calibration=(
                VoiceCalibrationResponse(
                    provider="vbee",
                    voice_id=calibration_record.voice_id,
                    overhead_sec=calibration_record.overhead_sec,
                    sec_per_syllable=calibration_record.sec_per_syllable,
                    sample_count=calibration_record.sample_count,
                    source_job_id=calibration_record.source_job_id,
                    calibrated_at=_utc_iso(calibration_record.updated_at),
                    revision=_calibration_revision(calibration_record),
                )
                if calibration_record
                else None
            ),
            subtitle_approved=subtitle_approved,
            execution_status=execution_status,  # type: ignore[arg-type]
            quality_status=quality,  # type: ignore[arg-type]
            latest_run=(self.run_response(latest) if latest else None),
            active_run=(self.run_response(active_tts) if active_tts else None),
            attention_reasons=list(dict.fromkeys(reasons)),
            allowed_actions=allowed,  # type: ignore[arg-type]
            cues=cues,
            total_cues=len(cues),
            uncached_cues=uncached,
            output=output,
            output_stale=output_stale,
            editor_locked=active_locked,
        )

    def run_response(self, run: Run) -> RunResponse:
        return RunResponse(
            run_id=run.run_id,
            job_id=run.job_id,
            lane=run.lane,  # type: ignore[arg-type]
            kind=run.kind,
            from_stage=run.from_stage,
            status=run.status,  # type: ignore[arg-type]
            stage=run.stage,
            progress=run.progress,
            message=run.message,
            error=run.error,
            queue_position=self.store.queue_position(run.run_id),
            created_at=_utc_iso(run.created_at),
            started_at=_utc_iso(run.started_at),
            finished_at=_utc_iso(run.ended_at),
            event_seq=run.event_seq,
        )

    def current_audio_signature(self, job: Job) -> str | None:
        state = self.workspace(job)
        if state.output is None or state.output.state != "current":
            return None
        report = self._report(job)
        if report is None:
            return None
        path = self._audio_path(job, report)
        if not path.is_file():
            return None
        return sha256_json_canonical(
            {"input_hash": report.input_hash, "audio": sha256_file(path)}
        )

    def apply_settings(self, job: Job, revision: str, voice_id: str):
        speech_path = Path(job.work_dir) / "speech.vi.json"
        if not speech_path.is_file():
            fallback = self._fallback_voice(job)
            if fallback:
                ensure_speech_doc(job.work_dir, fallback)
            else:
                empty_revision = sha256_json_canonical({"voice_id": None})
                if revision != empty_revision:
                    raise SpeechConflictError(revision, empty_revision)
                ensure_speech_doc(job.work_dir, voice_id)
                self.store.set_tts_approval(job.job_id, LANGUAGE, None)
                return get_speech_state(job.work_dir)
        result = apply_spoken_changes(job.work_dir, voice_id, revision, [])
        self.store.set_tts_approval(job.job_id, LANGUAGE, None)
        return result

    def apply_overrides(self, job: Job, revision: str, changes: list[dict[str, Any]]):
        state = self._state(job)
        result = apply_spoken_changes(job.work_dir, state.voice_id, revision, changes)
        self.store.set_tts_approval(job.job_id, LANGUAGE, None)
        return result

    def preview_path(self, job: Job, segment_id: int) -> Path | None:
        state = self._state(job)
        calibration_record = self.store.get_voice_calibration(PROVIDER, state.voice_id)
        calibration = _calibration_model(calibration_record) if calibration_record else None
        plan = self._plan(job, state.voice_id, calibration)
        cue = next((item for item in plan.cues if item.segment_id == segment_id), None)
        if cue is None or not cue.clip_path.is_file() or cue.clip_path.stat().st_size <= 0:
            return None
        root = (Path(job.work_dir) / "dub" / LANGUAGE).resolve(strict=False)
        path = cue.clip_path.resolve(strict=False)
        if path != root and not path.is_relative_to(root):
            return None
        return path

    def run_tts(
        self,
        job: Job,
        run: Run,
        config: Config,
        on_progress: Callable[[Progress], None],
        cancel,
    ) -> Any:
        payload = run.payload or {}
        voice_id = str(payload.get("voice_id") or "").strip()
        if not voice_id:
            raise ValueError("TTS run has no voice_id")
        state = self._state(job)
        if state.voice_id != voice_id:
            raise RuntimeError("TTS voice changed after the run was queued; retry the action")
        expected_revision = payload.get("speech_revision")
        if expected_revision and state.revision != expected_revision:
            raise RuntimeError("Spoken text changed after the run was queued; reload the workspace")
        calibration_record = self.store.get_voice_calibration(PROVIDER, voice_id)
        calibration = _calibration_model(calibration_record) if calibration_record else None
        expected_calibration = payload.get("calibration_revision")
        current_calibration = (
            _calibration_revision(calibration_record) if calibration_record else None
        )
        if expected_calibration is not None and expected_calibration != current_calibration:
            raise RuntimeError(
                "Voice calibration changed after the run was queued; retry the action"
            )
        # Chặn ngay ở cửa, trước khi vào stage: hỏng ở đây là một câu tiếng Việt
        # đọc được, hỏng sâu bên trong là một ValueError giữa thanh tiến độ.
        if calibration is None and run.kind in {"tts_preview", "tts_render"}:
            raise RuntimeError(
                VOICE_NOT_CALIBRATED.format(voice=voice_id, job=job.job_id)
            )
        if run.kind == "tts_preview":
            ctx = RunContext(job.job_id, on_progress, cancel, stages=["tts_preview"])
            ctx.enter_stage("tts_preview")
            ctx.raise_if_cancelled()
            s6_dub.preview_cue(
                job.work_dir,
                config,
                int(payload["segment_id"]),
                lang=LANGUAGE,
                voice_id=voice_id,
                calibration=calibration,
            )
            ctx.report(1.0, "Nghe thử đã sẵn sàng")
            return
        if run.kind == "tts_calibrate":
            rows = effective_spoken_items(job.work_dir)
            if len(rows) < CALIBRATION_SAMPLES:
                raise ValueError("TTS calibration requires at least 8 subtitle cues")
            ctx = RunContext(job.job_id, on_progress, cancel, stages=["tts_calibrate"])
            ctx.enter_stage("tts_calibrate")

            def progress(fraction: float, message: str) -> None:
                ctx.report(fraction, message)

            result = calibrate_voice(
                job.work_dir,
                config,
                LANGUAGE,
                CALIBRATION_SAMPLES,
                voice_id=voice_id,
                sample_texts=[row.effective_spoken_text for row in rows],
                progress=progress,
                # Đã có số đo mà vẫn bấm hiệu chuẩn nghĩa là muốn ĐO LẠI, nên
                # tổng hợp mới. Lần đo đầu (kể cả thử lại sau lỗi) thì dùng lại
                # probe cũ, khỏi mất thêm lượt.
                force=calibration_record is not None,
            )
            self.store.save_voice_calibration(
                PROVIDER,
                voice_id,
                result.overhead_sec,
                result.sec_per_syllable,
                sample_count=CALIBRATION_SAMPLES,
                source_job_id=job.job_id,
                overwrite=True,
            )
            return
        if run.kind != "tts_render":
            raise ValueError(f"Unsupported TTS run kind: {run.kind}")
        if any(
            item.status in {"stale", "base_changed"}
            for item in state.evaluations
        ):
            raise RuntimeError(
                "Spoken overrides require review before the full TTS render"
            )
        approved, subtitle_signature = self._subtitle_approved(job)
        if not approved or payload.get("subtitle_approval_signature") != subtitle_signature:
            raise RuntimeError("Subtitle approval is no longer current; approve subtitles again")
        ctx = RunContext(job.job_id, on_progress, cancel, stages=["tts_render"])
        ctx.enter_stage("tts_render")
        cfg = config.model_copy(deep=True)
        cfg.dub.voice_id = voice_id
        cfg.dub.overhead_sec = calibration.overhead_sec
        cfg.dub.sec_per_syllable = calibration.sec_per_syllable
        return s6_dub.run(
            job.work_dir,
            cfg,
            LANGUAGE,
            Path((job.request or {}).get("output_dir") or "output"),
            ctx=ctx,
            voice_id=voice_id,
            calibration=calibration,
            subtitle_approval_signature=subtitle_signature,
        )
