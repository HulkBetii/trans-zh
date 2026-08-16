"""Local FastAPI host for the Subtitle Production Studio."""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..api import JobHealth, job_health
from ..config import Config
from ..credentials import CredentialStore, get_default_credential_store
from ..dub.calibrate import calibration_hash
from ..dub.client import DubError
from ..dub.spoken import SpeechConflictError, SpeechValidationError, get_speech_state
from ..jobs import (
    ACTIVE_RUN_STATUSES,
    STAGES,
    TERMINAL_RUN_STATUSES,
    TTS_PROVIDER,
    Job,
    JobStore,
    Run,
)
from ..jsonio import read_doc, sha256_file, sha256_json_canonical, write_doc
from ..models import (
    GlossaryDoc,
    IngestDoc,
    RenderReport,
    SegmentsDoc,
    TranslationsDoc,
    TtsCalibration,
    TtsReport,
)
from ..overrides import (
    OverrideConflictError,
    OverrideValidationError,
    apply_override_changes,
    effective_items,
    effective_translations_hash,
    get_override_state,
)
from ..progress import STAGE_LABELS
from ..stages import s0_ingest, s5_render
from ..timing import cps
from .runtime import RunEventBroker, RunScheduler, Runner, TtsRunner
from .schemas import (
    ApiMessage,
    ArtifactResponse,
    CreateJobRequest,
    CreateJobResponse,
    CredentialId,
    CredentialTestRequest,
    CredentialTestResponse,
    CredentialUpdateRequest,
    ExecutionStatus,
    FileEntryResponse,
    FileListResponse,
    FileRootResponse,
    FileRootsResponse,
    GlossaryResponse,
    GlossaryUpdateRequest,
    HealthResponse,
    JobDetail,
    JobLaneSummary,
    JobPage,
    JobRequestSnapshot,
    JobSummary,
    MetaResponse,
    OverrideUpdateRequest,
    QualityStatus,
    ReadinessItem,
    RunResponse,
    SettingsCredentialResponse,
    SettingsProviderId,
    SettingsProviderResponse,
    SettingsResponse,
    SettingsRevisionRequest,
    SourceRequest,
    StageDescriptor,
    StageState,
    SubtitleCue,
    SubtitleResponse,
    SubtitleWarningResponse,
    SpokenOverrideUpdateRequest,
    TtsPreviewRequest,
    TtsSettingsUpdateRequest,
    TtsVoicePage,
    TtsWorkspaceResponse,
)
from .tts_service import LANGUAGE as TTS_LANGUAGE
from .tts_service import TtsService

_STATIC = Path(__file__).with_name("static")
_PAGE = _STATIC / "index.html"
_MEDIA_SUFFIXES = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
}
_PUBLIC_STAGE = {
    "ingest": "s0",
    "asr": "s1",
    "segment": "s2",
    "glossary": "s3",
    "translate": "s4",
    "render": "s5",
}
_STAGE_DESCRIPTION = {
    "ingest": "Download when needed and normalize the source to 16 kHz mono WAV.",
    "asr": "Recognize speech and establish the only authoritative timeline.",
    "segment": "Insert sentence boundaries without generating timestamps.",
    "glossary": "Extract terminology, register, and forms of address.",
    "translate": "Produce exactly one translation for every segment ID.",
    "render": "Write SRT and ASS artifacts without changing cue cardinality.",
}
_DEGRADED_REASONS = {"segmentation_fallback", "render_warnings"}
_APPROVAL_BLOCKERS = {"stale_overrides", "stale_outputs", "translations_stale"}
_MASKED_CREDENTIAL = "********"
_CREDENTIAL_SPECS = {
    CredentialId.openai: ("OpenAI", "OPENAI_API_KEY"),
    CredentialId.anthropic: ("Anthropic", "ANTHROPIC_API_KEY"),
    CredentialId.ai33: ("AI33 / Vbee", "AI33_API_KEY"),
}


def _conflict(
    code: Literal["revision_conflict", "artifact_locked", "action_not_allowed"],
    message: str,
    **context: Any,
) -> HTTPException:
    return HTTPException(
        409,
        {"code": code, "message": message, **context},
    )
_ENV_CREDENTIAL_IDS = {
    env_name: credential_id
    for credential_id, (_, env_name) in _CREDENTIAL_SPECS.items()
}
log = logging.getLogger(__name__)


def _utc_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _is_inside(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve(strict=False)
    return any(resolved == root or resolved.is_relative_to(root) for root in roots)


def _safe_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _legacy_execution(status: str) -> ExecutionStatus:
    return ExecutionStatus(
        {
            "pending": "interrupted",
            "running": "interrupted",
            "done": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "interrupted": "interrupted",
        }.get(status, "interrupted")
    )


def _source_title(source: SourceRequest, job_id: str) -> str:
    if source.kind == "local":
        return Path(source.value).stem or job_id
    parsed = urlparse(source.value)
    tail = Path(parsed.path).stem
    return tail or parsed.hostname or job_id


def _snapshot_for(job: Job) -> JobRequestSnapshot:
    if job.request is not None:
        try:
            return JobRequestSnapshot.model_validate(job.request)
        except ValueError:
            pass
    source = SourceRequest(
        kind="url" if s0_ingest.is_url(job.source_uri) else "local",
        value=job.source_uri,
    )
    return JobRequestSnapshot(
        source=source,
        targets=[target for target in job.targets if target in {"vi", "en"}] or ["vi"],
        formats=["srt", "ass"],
        bilingual=False,
        output_dir=str(Path("output").resolve()),
    )


def _run_response(store: JobStore, run: Run | None) -> RunResponse | None:
    if run is None:
        return None
    stage = _PUBLIC_STAGE.get(run.stage or "", run.stage)
    return RunResponse(
        run_id=run.run_id,
        job_id=run.job_id,
        lane=run.lane,  # type: ignore[arg-type]
        kind=run.kind,
        from_stage=_PUBLIC_STAGE.get(run.from_stage, run.from_stage),
        status=ExecutionStatus(run.status),
        stage=stage,
        progress=run.progress,
        message=run.message,
        error=run.error,
        queue_position=store.queue_position(run.run_id),
        created_at=_utc_iso(run.created_at),
        started_at=_utc_iso(run.started_at),
        finished_at=_utc_iso(run.ended_at),
        event_seq=run.event_seq,
    )


def _execution_for(job: Job, latest_run: Run | None) -> ExecutionStatus:
    return ExecutionStatus(latest_run.status) if latest_run is not None else _legacy_execution(job.status)


def _safe_health(work_dir: Path) -> JobHealth:
    try:
        return job_health(work_dir)
    except (OSError, ValueError, json.JSONDecodeError):
        return JobHealth()


def _glossary_stale(work_dir: Path, targets: list[str]) -> bool:
    glossary_path = work_dir / "glossary.json"
    raw = _safe_json(glossary_path)
    if raw is None:
        return False
    glossary_hash = sha256_json_canonical(raw)
    for lang in targets:
        path = work_dir / f"translations.{lang}.json"
        if not path.is_file():
            continue
        try:
            if read_doc(path, TranslationsDoc).glossary_hash != glossary_hash:
                return True
        except (OSError, ValueError, json.JSONDecodeError):
            return True
    return False


def _override_flags(work_dir: Path, targets: list[str]) -> tuple[bool, bool]:
    stale = False
    base_changed = False
    for lang in targets:
        if not (work_dir / f"translations.{lang}.json").is_file():
            continue
        try:
            state = get_override_state(work_dir, lang)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        stale = stale or any(item.status == "stale" for item in state.evaluations)
        base_changed = base_changed or any(item.status == "base_changed" for item in state.evaluations)
    return stale, base_changed


def _artifact_language(name: str) -> str | None:
    match = re.search(r"\.(vi|en)(?:\.|$)", name, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _allowed_output_roots(job: Job, snapshot: JobRequestSnapshot) -> list[Path]:
    if job.request is not None:
        return [Path(snapshot.output_dir).resolve(strict=False)]
    return [Path("output").resolve(strict=False)]


def _render_report(work_dir: Path) -> RenderReport | None:
    path = work_dir / "render_report.json"
    if not path.is_file():
        return None
    try:
        return read_doc(path, RenderReport)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _tts_report(work_dir: Path) -> TtsReport | None:
    path = work_dir / "tts_report.vi.json"
    if not path.is_file():
        return None
    try:
        return read_doc(path, TtsReport)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _tts_output_stale(job: Job, store: JobStore, report: TtsReport) -> bool:
    try:
        speech = get_speech_state(job.work_dir, report.voice_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return True
    calibration_record = store.get_voice_calibration(TTS_PROVIDER, report.voice_id)
    if calibration_record is None:
        return True
    calibration = TtsCalibration(
        voice_id=calibration_record.voice_id,
        overhead_sec=calibration_record.overhead_sec,
        sec_per_syllable=calibration_record.sec_per_syllable,
        samples_hash="persisted",
        created_at="persisted",
        points=[],
    )
    return bool(
        report.voice_id != speech.voice_id
        or report.speech_revision != speech.revision
        or report.calibration_hash != calibration_hash(calibration)
        or report.subtitle_approval_signature != _approval_signature(job)
    )


def _render_request_stale(report: RenderReport, snapshot: JobRequestSnapshot) -> bool:
    return bool(
        report.render_request_hash
        and report.render_request_hash
        != s5_render.build_render_request_hash(
            snapshot.targets,
            snapshot.formats,
            snapshot.bilingual,
        )
    )


def _lang_output_stale(
    work_dir: Path,
    report: RenderReport,
    lang: str,
    snapshot: JobRequestSnapshot,
) -> bool:
    if _render_request_stale(report, snapshot):
        return True
    rendered_segments = getattr(report, "segments_render_hash", "")
    if rendered_segments:
        try:
            segments = read_doc(work_dir / "segments.json", SegmentsDoc)
            ingest = read_doc(work_dir / "ingest.json", IngestDoc)
            current_segments = s5_render.build_segments_render_hash(
                segments.segments,
                ingest.media.duration_sec,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return True
        if current_segments != rendered_segments:
            return True
    expected = report.effective_translation_hashes.get(lang)
    if expected:
        try:
            return expected != effective_translations_hash(work_dir, lang)
        except (OSError, ValueError, json.JSONDecodeError):
            return True
    override_raw = _safe_json(work_dir / f"overrides.{lang}.json")
    return bool(override_raw and override_raw.get("items"))


def _expected_subtitle_paths(
    job: Job,
    snapshot: JobRequestSnapshot,
) -> dict[Path, tuple[str, str]]:
    if job.request is None:
        return {}
    work_dir = Path(job.work_dir)
    ingest_path = work_dir / "ingest.json"
    try:
        ingest = read_doc(ingest_path, IngestDoc)
        stem = ingest.source.title or work_dir.name
    except (OSError, ValueError, json.JSONDecodeError):
        stem = _source_title(snapshot.source, job.job_id)
    output_dir = Path(snapshot.output_dir).resolve(strict=False)
    expected: dict[Path, tuple[str, str]] = {}
    for lang in snapshot.targets:
        suffix = f".{lang}.bilingual" if snapshot.bilingual else f".{lang}"
        for output_format in snapshot.formats:
            path = (output_dir / f"{stem}{suffix}.{output_format}").resolve(strict=False)
            if _is_inside(path, [output_dir]):
                expected[path] = (lang, output_format)
    return expected


def _artifacts_for(job: Job, store: JobStore | None = None) -> list[ArtifactResponse]:
    work_dir = Path(job.work_dir)
    snapshot = _snapshot_for(job)
    render_report = _render_report(work_dir)
    tts_report = _tts_report(work_dir)
    roots = _allowed_output_roots(job, snapshot)
    expected_paths = _expected_subtitle_paths(job, snapshot)
    candidates: list[tuple[Path, str]] = []
    if render_report is not None:
        for raw_path in render_report.outputs:
            path = Path(raw_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            path = path.resolve(strict=False)
            if _is_inside(path, roots):
                candidates.append((path, "subtitle"))
    tts_output_path: Path | None = None
    if tts_report is not None:
        tts_output_path = Path(tts_report.output)
        if not tts_output_path.is_absolute():
            tts_output_path = Path.cwd() / tts_output_path
        tts_output_path = tts_output_path.resolve(strict=False)
        if _is_inside(tts_output_path, roots):
            candidates.append((tts_output_path, "audio"))

    for root in roots:
        if not root.is_dir():
            continue
        pattern = "*.mp3" if job.request is not None else f"{job.job_id}.*.mp3"
        for path in root.glob(pattern):
            candidates.append((path.resolve(strict=False), "audio"))

    artifacts: list[ArtifactResponse] = []
    seen: set[Path] = set()
    for path, kind in candidates:
        if path in seen:
            continue
        seen.add(path)
        lang = _artifact_language(path.name)
        exists = path.is_file()
        stale = bool(
            render_report
            and lang
            and kind == "subtitle"
            and (
                _lang_output_stale(work_dir, render_report, lang, snapshot)
                or (expected_paths and path not in expected_paths)
            )
        )
        if kind == "audio":
            stale = bool(
                tts_report is None
                or tts_output_path != path
                or store is None
                or _tts_output_stale(job, store, tts_report)
            )
        state: Literal["current", "stale", "missing"] = (
            "missing" if not exists else "stale" if stale else "current"
        )
        stat = path.stat() if exists else None
        artifact_id = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:20]
        artifacts.append(
            ArtifactResponse(
                artifact_id=artifact_id,
                name=path.name,
                kind=kind,  # type: ignore[arg-type]
                language=lang,
                format=path.suffix.lower().lstrip(".") or None,
                size_bytes=stat.st_size if stat else None,
                created_at=_utc_iso(stat.st_mtime) if stat else None,
                state=state,
                download_url=(
                    f"/api/v1/jobs/{job.job_id}/artifacts/{artifact_id}" if exists else None
                ),
            )
        )
    for path, (lang, output_format) in expected_paths.items():
        if path in seen:
            continue
        exists = path.is_file()
        stat = path.stat() if exists else None
        artifact_id = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:20]
        artifacts.append(
            ArtifactResponse(
                artifact_id=artifact_id,
                name=path.name,
                kind="subtitle",
                language=lang,
                format=output_format,
                size_bytes=stat.st_size if stat else None,
                created_at=_utc_iso(stat.st_mtime) if stat else None,
                state="stale" if exists else "missing",
                download_url=(
                    f"/api/v1/jobs/{job.job_id}/artifacts/{artifact_id}"
                    if exists
                    else None
                ),
            )
        )
    return artifacts


def _attention_reasons(
    job: Job,
    store: JobStore,
) -> tuple[list[str], bool, list[ArtifactResponse]]:
    work_dir = Path(job.work_dir)
    snapshot = _snapshot_for(job)
    reasons: list[str] = []
    health = _safe_health(work_dir)
    if health.method == "rule_fallback":
        reasons.append("segmentation_fallback")
    if (work_dir / "glossary.json").is_file() and not health.subject_pronoun:
        reasons.append("style_unpinned")
    report = _render_report(work_dir)
    if report is not None and report.warnings:
        reasons.append("render_warnings")
    glossary_stale = _glossary_stale(work_dir, snapshot.targets)
    if glossary_stale:
        reasons.append("translations_stale")
    stale_overrides, base_changed = _override_flags(work_dir, snapshot.targets)
    if stale_overrides:
        reasons.append("stale_overrides")
    if base_changed:
        reasons.append("override_base_changed")
    artifacts = _artifacts_for(job, store)
    report_exists = (work_dir / "render_report.json").is_file()
    if (report_exists or job.status == "done") and any(
        item.kind == "subtitle" and item.state in {"stale", "missing"}
        for item in artifacts
    ):
        reasons.append("stale_outputs")
    return list(dict.fromkeys(reasons)), glossary_stale, artifacts


def _approval_signature(job: Job) -> str:
    work_dir = Path(job.work_dir)
    snapshot = _snapshot_for(job)
    names = ["segments.json", "glossary.json", "render_report.json"]
    for lang in snapshot.targets:
        names.extend([f"translations.{lang}.json", f"overrides.{lang}.json"])
    digest = hashlib.sha256()
    for name in names:
        path = work_dir / name
        digest.update(name.encode("utf-8"))
        digest.update(sha256_file(path).encode("ascii") if path.is_file() else b"missing")
    return digest.hexdigest()


def _quality_for(job: Job, execution: ExecutionStatus, reasons: list[str]) -> QualityStatus:
    current_signature = _approval_signature(job)
    if (
        job.approved_signature == current_signature
        and not _APPROVAL_BLOCKERS.intersection(reasons)
        and execution == ExecutionStatus.completed
    ):
        return QualityStatus.approved
    if _DEGRADED_REASONS.intersection(reasons):
        return QualityStatus.degraded
    return QualityStatus.needs_review


def _job_summary(store: JobStore, job: Job, tts_service: TtsService) -> JobSummary:
    latest = store.latest_run(job.job_id, lane="pipeline")
    active = latest if latest is not None and latest.status in ACTIVE_RUN_STATUSES else None
    execution = _execution_for(job, latest)
    reasons, _, _ = _attention_reasons(job, store)
    snapshot = _snapshot_for(job)
    pipeline_quality = _quality_for(job, execution, reasons)
    pipeline_latest = _run_response(store, latest)
    pipeline_active = _run_response(store, active)
    pipeline_lane = JobLaneSummary(
        id="pipeline",
        started=True,
        execution_status=execution,
        quality_status=pipeline_quality,
        stage=_PUBLIC_STAGE.get((active or latest).stage or "", (active or latest).stage)
        if (active or latest)
        else _PUBLIC_STAGE.get(job.stage or "", job.stage),
        progress=(active or latest).progress
        if (active or latest)
        else (1.0 if execution == ExecutionStatus.completed else 0.0),
        message=(active or latest).message if (active or latest) else "",
        error=(active or latest).error if (active or latest) else job.error,
        attention_reasons=reasons,
        active_run=pipeline_active,
        latest_run=pipeline_latest,
        allowed_actions=["cancel"] if active is not None else [],
    )
    tts_started = "vi" in snapshot.targets and tts_service.started(job)
    tts_activity = tts_service.activity_timestamp(job) if tts_started else None
    if tts_started:
        workspace = tts_service.workspace(job)
        tts_latest = workspace.latest_run
        tts_active = workspace.active_run
        tts_lane = JobLaneSummary(
            id="tts",
            started=True,
            execution_status=workspace.execution_status,
            quality_status=workspace.quality_status,
            stage=(tts_active or tts_latest).stage if (tts_active or tts_latest) else None,
            progress=(tts_active or tts_latest).progress if (tts_active or tts_latest) else 0.0,
            message=(tts_active or tts_latest).message if (tts_active or tts_latest) else "",
            error=(tts_active or tts_latest).error if (tts_active or tts_latest) else None,
            attention_reasons=workspace.attention_reasons,
            active_run=tts_active,
            latest_run=tts_latest,
            allowed_actions=["cancel"] if tts_active is not None else [],
        )
    else:
        tts_lane = JobLaneSummary(
            id="tts",
            started=False,
            execution_status="not_started",
            quality_status=QualityStatus.needs_review,
        )
    updated_at = max(
        timestamp
        for timestamp in (
            job.updated_at,
            latest.updated_at if latest else None,
            tts_activity,
        )
        if timestamp is not None
    )
    return JobSummary(
        job_id=job.job_id,
        title=_source_title(snapshot.source, job.job_id),
        source=snapshot.source,
        targets=snapshot.targets,
        execution_status=execution,
        quality_status=pipeline_quality,
        stage=pipeline_lane.stage,
        progress=pipeline_lane.progress,
        message=pipeline_lane.message,
        error=pipeline_lane.error,
        attention_reasons=reasons,
        active_run=pipeline_active,
        lanes=[pipeline_lane, tts_lane],
        created_at=_utc_iso(job.created_at),
        updated_at=_utc_iso(updated_at),
    )


def _stage_states(store: JobStore, job: Job, active: Run | None) -> list[StageState]:
    latest_stages = store.latest_stage_runs(job.job_id)
    work_dir = Path(job.work_dir)
    snapshot = _snapshot_for(job)
    artifact_exists = {
        "ingest": (work_dir / "ingest.json").is_file(),
        "asr": (work_dir / "asr.json").is_file(),
        "segment": (work_dir / "segments.json").is_file(),
        "glossary": (work_dir / "glossary.json").is_file(),
        "translate": all((work_dir / f"translations.{lang}.json").is_file() for lang in snapshot.targets),
        "render": (work_dir / "render_report.json").is_file(),
    }
    result: list[StageState] = []
    for stage in STAGES:
        row = latest_stages.get(stage)
        status = "completed" if artifact_exists[stage] else "pending"
        message: str | None = None
        error: str | None = None
        started_at = row.get("started_at") if row else None
        finished_at = row.get("ended_at") if row else None
        if row:
            status = {
                "done": "completed",
                "interrupted": "failed",
                "cancelled": "pending",
            }.get(row["status"], row["status"])
            error = row.get("error")
            if row["status"] == "cancelled":
                message = "Cancelled safely; completed stage artifacts were preserved."
        if active and active.stage == stage:
            status = "running" if active.status == "running" else active.status
            message = active.message
        if stage == "segment" and _safe_health(work_dir).method == "rule_fallback" and status == "completed":
            status = "degraded"
        duration = (
            max(0.0, float(finished_at) - float(started_at))
            if started_at is not None and finished_at is not None
            else None
        )
        result.append(
            StageState(
                id=_PUBLIC_STAGE[stage],
                status=status,
                started_at=_utc_iso(started_at),
                finished_at=_utc_iso(finished_at),
                duration_seconds=duration,
                message=message,
                error=error,
            )
        )
    return result


def _job_detail(store: JobStore, job: Job, tts_service: TtsService) -> JobDetail:
    summary = _job_summary(store, job, tts_service)
    latest = store.latest_run(job.job_id, lane="pipeline")
    active = latest if latest is not None and latest.status in ACTIVE_RUN_STATUSES else None
    reasons, glossary_stale, artifacts = _attention_reasons(job, store)
    work_dir = Path(job.work_dir)
    snapshot = _snapshot_for(job)
    actions: list[str] = []
    if active is not None:
        actions.append("cancel")
    else:
        if summary.execution_status in {ExecutionStatus.failed, ExecutionStatus.interrupted, ExecutionStatus.cancelled}:
            actions.append("retry")
        if (work_dir / "segments.json").is_file() and (work_dir / "glossary.json").is_file():
            actions.append("retranslate")
        if all((work_dir / f"translations.{lang}.json").is_file() for lang in snapshot.targets):
            actions.append("render")
        if summary.execution_status == ExecutionStatus.completed:
            if summary.quality_status == QualityStatus.approved:
                actions.append("unapprove")
            elif not _APPROVAL_BLOCKERS.intersection(reasons):
                actions.append("approve")
    return JobDetail(
        **summary.model_dump(exclude={"attention_reasons"}),
        attention_reasons=reasons,
        request=snapshot,
        stages=_stage_states(store, job, active),
        health=HealthResponse.model_validate(asdict(_safe_health(work_dir))),
        artifacts=artifacts,
        allowed_actions=actions,
        glossary_stale=glossary_stale,
    )


def _readiness(config: Config) -> list[ReadinessItem]:
    checks: list[ReadinessItem] = []

    def add(identifier: str, label: str, ready: bool, detail: str) -> None:
        checks.append(
            ReadinessItem(
                id=identifier,
                label=label,
                status="ready" if ready else "blocked",
                detail=detail,
            )
        )

    add("ffmpeg", "FFmpeg", shutil.which("ffmpeg") is not None, "Required to normalize media.")
    add("ffprobe", "FFprobe", shutil.which("ffprobe") is not None, "Required to inspect duration.")
    add(
        "funasr",
        "FunASR",
        importlib.util.find_spec("funasr") is not None,
        "Required for timestamped Chinese speech recognition.",
    )
    for role, profile in (("segment", config.llm.segment), ("translate", config.llm.translate)):
        configured = bool(profile.model and profile.model != "SET_ME")
        if profile.provider == "chatgpt_web":
            configured = configured and importlib.util.find_spec("playwright") is not None
        else:
            configured = configured and profile.credential_available()
        add(
            f"llm_{role}",
            f"LLM {role}",
            configured,
            f"Provider: {profile.provider}; model: {profile.model or 'not configured'}.",
        )
    tts_ready = config.dub.credential_available()
    checks.append(
        ReadinessItem(
            id="tts_ai33",
            label="AI33 / Vbee TTS",
            status="ready" if tts_ready else "warning",
            detail=(
                "Vietnamese cue preview, calibration, and MP3 timeline rendering."
                if tts_ready
                else f"Optional: set {config.dub.api_key_env} to enable Vietnamese TTS."
            ),
        )
    )
    return checks


def _credential_store_available(store: CredentialStore) -> bool:
    try:
        return bool(store.available())
    except Exception as exc:  # pragma: no cover - depends on the OS credential backend
        log.warning("Credential store availability check failed (%s)", type(exc).__name__)
        return False


def _credential_id_for_env(env_name: str) -> CredentialId | None:
    return _ENV_CREDENTIAL_IDS.get(env_name.strip())


def _configured_value(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned if cleaned and cleaned != "SET_ME" else None


def _settings_providers(config: Config) -> list[SettingsProviderResponse]:
    providers: list[SettingsProviderResponse] = []
    for provider_id, label, profile in (
        (SettingsProviderId.segment, "Phân đoạn (S2)", config.llm.segment),
        (SettingsProviderId.translate, "Dịch phụ đề (S4)", config.llm.translate),
    ):
        providers.append(
            SettingsProviderResponse(
                id=provider_id,
                label=label,
                provider=profile.provider,
                model=_configured_value(profile.model),
                base_url=profile.base_url if profile.provider != "chatgpt_web" else None,
                credential_id=(
                    None
                    if profile.provider == "chatgpt_web"
                    else _credential_id_for_env(profile.api_key_env)
                ),
            )
        )
    providers.append(
        SettingsProviderResponse(
            id=SettingsProviderId.tts,
            label="Lồng tiếng (TTS)",
            provider="ai33_vbee",
            model=_configured_value(config.dub.voice_id),
            base_url=config.dub.base_url,
            credential_id=_credential_id_for_env(config.dub.api_key_env),
        )
    )
    return providers


def _stored_credential(store: CredentialStore, env_name: str) -> str | None:
    try:
        value = store.get_secret(env_name)
    except Exception as exc:
        log.warning("Credential store read failed for %s (%s)", env_name, type(exc).__name__)
        raise RuntimeError("Credential store is unavailable") from exc
    cleaned = value.strip() if value else ""
    return cleaned or None


def _credential_snapshot(
    store: CredentialStore,
) -> tuple[bool, list[SettingsCredentialResponse], list[dict[str, str | None]]]:
    store_available = _credential_store_available(store)
    credentials: list[SettingsCredentialResponse] = []
    revision_items: list[dict[str, str | None]] = []
    for credential_id, (label, env_name) in _CREDENTIAL_SPECS.items():
        environment_secret = os.environ.get(env_name, "").strip() or None
        stored_secret = None
        if environment_secret is None and store_available:
            stored_secret = _stored_credential(store, env_name)
        secret = environment_secret or stored_secret
        source: Literal["environment", "credential_store", "none"]
        if environment_secret is not None:
            source = "environment"
        elif stored_secret is not None:
            source = "credential_store"
        else:
            source = "none"
        credentials.append(
            SettingsCredentialResponse(
                id=credential_id,
                label=label,
                configured=secret is not None,
                source=source,
                editable=source != "environment" and store_available,
                masked_value=_MASKED_CREDENTIAL if secret is not None else None,
                env_name=env_name,
            )
        )
        revision_items.append(
            {
                "id": credential_id.value,
                "source": source,
                "fingerprint": (
                    hashlib.sha256(secret.encode("utf-8")).hexdigest()
                    if secret is not None
                    else None
                ),
            }
        )
    return store_available, credentials, revision_items


def _effective_credential_secret(
    store: CredentialStore,
    credential_id: CredentialId,
) -> str | None:
    _, env_name = _CREDENTIAL_SPECS[credential_id]
    environment_secret = os.environ.get(env_name, "").strip() or None
    if environment_secret is not None:
        return environment_secret
    if not _credential_store_available(store):
        return None
    return _stored_credential(store, env_name)


def _settings_response(config: Config, store: CredentialStore) -> SettingsResponse:
    store_available, credentials, revision_credentials = _credential_snapshot(store)
    providers = _settings_providers(config)
    revision = sha256_json_canonical(
        {
            "credential_store_available": store_available,
            "providers": [provider.model_dump(mode="json") for provider in providers],
            "credentials": revision_credentials,
        }
    )
    return SettingsResponse(
        revision=revision,
        credential_store_available=store_available,
        providers=providers,
        credentials=credentials,
    )


def _is_loopback_host(value: str) -> bool:
    hostname = urlparse(f"//{value}").hostname
    if hostname is None:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_synthetic_test_client(request: Request) -> bool:
    client = request.client
    hostname = urlparse(f"//{request.headers.get('host', '')}").hostname
    return bool(client and client.host == "testclient" and hostname == "testserver")


def _guard_api_request(request: Request) -> None:
    if _is_synthetic_test_client(request):
        return
    if not _is_loopback_host(request.headers.get("host", "")):
        raise HTTPException(
            403,
            {"code": "invalid_host", "message": "API access requires a loopback host"},
        )
    client = request.client
    try:
        is_loopback_client = bool(client and ipaddress.ip_address(client.host).is_loopback)
    except ValueError:
        is_loopback_client = False
    if not is_loopback_client:
        raise HTTPException(
            403,
            {"code": "invalid_client", "message": "API access requires a loopback client"},
        )


def _guard_settings_request(request: Request) -> None:
    _guard_api_request(request)
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None or not _is_loopback_host(parsed.netloc):
            raise HTTPException(
                403,
                {"code": "invalid_origin", "message": "Settings require a loopback origin"},
            )


def _validate_secret_value(value: str) -> str:
    secret = value.strip()
    if not secret or len(secret) > 4096 or any(character.isspace() for character in secret):
        raise HTTPException(
            422,
            {"code": "invalid_secret", "message": "Credential must be a non-empty single-line value"},
        )
    return secret


def _validation_base_url(config: Config, credential_id: CredentialId) -> str:
    for profile in (config.llm.segment, config.llm.translate):
        if _credential_id_for_env(profile.api_key_env) == credential_id:
            return profile.base_url.rstrip("/")
    if credential_id == CredentialId.openai:
        return "https://api.openai.com/v1"
    if credential_id == CredentialId.anthropic:
        return "https://api.anthropic.com"
    return config.dub.base_url.rstrip("/")


def _credential_validation_request(
    config: Config,
    credential_id: CredentialId,
    secret: str,
) -> CredentialTestResponse:
    base_url = _validation_base_url(config, credential_id)
    params: dict[str, str | int] | None = None
    if credential_id == CredentialId.openai:
        url = f"{base_url}/models"
        headers = {"Authorization": f"Bearer {secret}"}
    elif credential_id == CredentialId.anthropic:
        url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
        headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
    else:
        url = f"{base_url}/v3/voices"
        headers = {"xi-api-key": secret}
        params = {"provider": "vbee", "language": "Vietnamese", "page_size": 1}

    started = time.perf_counter()
    try:
        response = httpx.get(url, headers=headers, params=params, timeout=10.0)
    except Exception as exc:
        log.warning(
            "Credential validation failed for %s (%s)",
            credential_id.value,
            type(exc).__name__,
        )
        return CredentialTestResponse(
            ok=False,
            code="provider_unavailable",
            message="Không thể xác minh credential lúc này.",
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )

    latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    if 200 <= response.status_code < 300:
        return CredentialTestResponse(
            ok=True,
            code="ok",
            message="Kết nối thành công.",
            latency_ms=latency_ms,
        )
    if response.status_code in {401, 403}:
        return CredentialTestResponse(
            ok=False,
            code="invalid_credentials",
            message="Credential bị nhà cung cấp từ chối.",
            latency_ms=latency_ms,
        )
    return CredentialTestResponse(
        ok=False,
        code="provider_unavailable",
        message="Không thể xác minh credential lúc này.",
        latency_ms=latency_ms,
    )


def _system_file_roots() -> list[Path]:
    if os.name == "nt":
        roots = [Path(f"{letter}:\\") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
        return [root.resolve(strict=False) for root in roots if root.exists()]
    return [Path("/").resolve()]


def _validate_source(source: SourceRequest) -> str:
    if source.kind == "url":
        parsed = urlparse(source.value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(400, "Source URL must use http or https")
        return source.value

    raw_path = Path(source.value)
    if not raw_path.is_absolute():
        raise HTTPException(400, "Local source path must be absolute")
    try:
        path = raw_path.resolve(strict=True)
    except OSError:
        raise HTTPException(400, f"Source file does not exist: {source.value}") from None
    if not path.is_file() or path.suffix.lower() not in _MEDIA_SUFFIXES:
        raise HTTPException(400, "Local source must be a supported media file")
    return str(path)


def _media_path(job: Job) -> Path | None:
    snapshot = _snapshot_for(job)
    if snapshot.source.kind == "local":
        try:
            source = Path(snapshot.source.value).resolve(strict=True)
        except OSError:
            source = None
        if source is not None and source.is_file():
            return source

    work_dir = Path(job.work_dir).resolve(strict=False)
    ingest = _safe_json(work_dir / "ingest.json") or {}
    media = ingest.get("media") if isinstance(ingest.get("media"), dict) else {}
    wav_name = media.get("wav_path") if isinstance(media, dict) else None
    if isinstance(wav_name, str):
        candidate = (work_dir / wav_name).resolve(strict=False)
        if _is_inside(candidate, [work_dir]) and candidate.is_file():
            return candidate
    fallback = work_dir / "audio.wav"
    return fallback if fallback.is_file() else None


def _parse_range(value: str, size: int) -> tuple[int, int]:
    if not value.startswith("bytes=") or "," in value:
        raise HTTPException(416, "Only one byte range is supported", headers={"Content-Range": f"bytes */{size}"})
    raw_start, separator, raw_end = value[6:].partition("-")
    if not separator:
        raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
    try:
        if raw_start:
            start = int(raw_start)
            end = int(raw_end) if raw_end else size - 1
        else:
            suffix = int(raw_end)
            if suffix <= 0:
                raise ValueError
            start = max(size - suffix, 0)
            end = size - 1
    except ValueError:
        raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"}) from None
    if start < 0 or start >= size or end < start:
        raise HTTPException(416, "Byte range is outside the file", headers={"Content-Range": f"bytes */{size}"})
    return start, min(end, size - 1)


def _file_chunks(path: Path, start: int, end: int, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            block = handle.read(min(chunk_size, remaining))
            if not block:
                return
            remaining -= len(block)
            yield block


_document_lock_guard = threading.Lock()
_document_locks: dict[Path, threading.RLock] = {}


def _document_lock(path: Path) -> threading.RLock:
    key = path.resolve(strict=False)
    with _document_lock_guard:
        return _document_locks.setdefault(key, threading.RLock())


def create_app(
    *,
    config: Config | None = None,
    credential_store: CredentialStore | None = None,
    runner: Runner | None = None,
    tts_runner: TtsRunner | None = None,
    start_scheduler: bool = True,
    enforce_readiness: bool = True,
    file_roots: list[Path] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(api: FastAPI):
        runtime = get_runtime(api)
        if api.state.start_scheduler:
            runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    api = FastAPI(title="zhsub Subtitle Production Studio", version="1", lifespan=lifespan)
    api.state.config_override = config
    api.state.credential_store_override = credential_store
    api.state.credential_store = None
    api.state.runner = runner
    api.state.tts_runner = tts_runner
    api.state.start_scheduler = start_scheduler
    api.state.enforce_readiness = enforce_readiness
    api.state.file_roots = [root.resolve(strict=False) for root in file_roots] if file_roots else None
    api.state.runtime = None
    api.state.tts_service = None
    api.state.runtime_lock = threading.Lock()

    @api.middleware("http")
    async def protect_local_api(request: Request, call_next):
        api_path = request.url.path == "/api/v1" or request.url.path.startswith(
            "/api/v1/"
        )
        if api_path:
            try:
                _guard_api_request(request)
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers=exc.headers,
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        if request.url.path.startswith("/api/v1/settings"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def get_runtime(target: FastAPI = api) -> RunScheduler:
        with target.state.runtime_lock:
            if target.state.runtime is None:
                secret_store = (
                    target.state.credential_store_override
                    if target.state.credential_store_override is not None
                    else get_default_credential_store()
                )
                if target.state.config_override is not None:
                    cfg = target.state.config_override.model_copy(
                        deep=True
                    ).bind_credential_store(secret_store)
                else:
                    cfg = Config.load(credential_store=secret_store)
                store = JobStore(cfg.paths.jobs_db)
                tts_service = TtsService(
                    cfg,
                    store,
                    subtitle_signature=_approval_signature,
                )
                try:
                    tts_service.seed_config_calibration()
                except (OSError, ValueError) as exc:
                    log.warning("Could not seed the configured TTS calibration: %s", exc)
                target.state.runtime = RunScheduler(
                    cfg,
                    store,
                    runner=target.state.runner,
                    tts_runner=target.state.tts_runner or tts_service.run_tts,
                    broker=RunEventBroker(),
                )
                target.state.tts_service = tts_service
                target.state.credential_store = secret_store
            runtime: RunScheduler = target.state.runtime
        if target.state.start_scheduler:
            runtime.start()
        return runtime

    def get_credential_store() -> CredentialStore:
        get_runtime()
        secret_store: CredentialStore | None = api.state.credential_store
        assert secret_store is not None
        return secret_store

    def get_tts_service() -> TtsService:
        get_runtime()
        service: TtsService | None = api.state.tts_service
        assert service is not None
        return service

    def require_job(job_id: str) -> tuple[RunScheduler, Job]:
        runtime = get_runtime()
        job = runtime.store.get(job_id)
        if job is None:
            raise HTTPException(404, f"Unknown job: {job_id}")
        return runtime, job

    def require_tts_job(job_id: str) -> tuple[RunScheduler, Job, TtsService]:
        runtime, job = require_job(job_id)
        if TTS_LANGUAGE not in _snapshot_for(job).targets:
            raise HTTPException(404, "Vietnamese TTS is not available for this job")
        return runtime, job, get_tts_service()

    def glossary_lock_state(
        runtime: RunScheduler,
        job_id: str,
    ) -> tuple[bool, str | None]:
        active = runtime.store.active_run(job_id)
        if active is None:
            return False, None
        if active.lane == "tts":
            return True, "tts_run_active"
        if active.status in {"running", "cancelling"} and active.stage in {
            "glossary",
            "translate",
        }:
            return True, "pipeline_regenerating_glossary"
        return False, None

    def reject_active_tts_edit(runtime: RunScheduler, job_id: str) -> None:
        active = runtime.store.active_run(job_id)
        if active is not None:
            raise HTTPException(
                409,
                {
                    "code": "artifact_locked",
                    "message": "TTS artifacts are locked by an active run",
                    "run_id": active.run_id,
                },
            )

    def enqueue_tts(
        runtime: RunScheduler,
        job: Job,
        kind: str,
        payload: dict[str, Any],
    ) -> RunResponse:
        try:
            run = runtime.enqueue(
                job.job_id,
                kind,
                kind,
                lane="tts",
                payload=payload,
            )
        except ValueError as exc:
            raise HTTPException(
                409,
                {"code": "action_not_allowed", "message": str(exc)},
            ) from exc
        response = _run_response(runtime.store, run)
        assert response is not None
        return response

    @api.get("/api/v1/meta", response_model=MetaResponse)
    def meta() -> MetaResponse:
        runtime = get_runtime()
        readiness = _readiness(runtime.config)
        return MetaResponse(
            stages=[
                StageDescriptor(
                    id=_PUBLIC_STAGE[stage],
                    label=STAGE_LABELS.get(stage, stage),
                    short_label=_PUBLIC_STAGE[stage].upper(),
                    description=_STAGE_DESCRIPTION[stage],
                )
                for stage in STAGES
            ],
            targets=["vi", "en"],
            formats=["srt", "ass"],
            capabilities=[
                "core_studio",
                "glossary",
                "subtitle_overrides",
                "media_range",
                "tts",
                "settings",
            ],
            readiness=readiness,
        )

    def settings_workspace() -> SettingsResponse:
        runtime = get_runtime()
        try:
            return _settings_response(runtime.config, get_credential_store())
        except RuntimeError as exc:
            raise HTTPException(
                503,
                {"code": "credential_store_unavailable", "message": str(exc)},
            ) from exc

    def reject_settings_mutation(
        request: Request,
        revision: str,
        runtime: RunScheduler,
    ) -> SettingsResponse:
        _guard_settings_request(request)
        active = next(
            (run for run in runtime.store.list_runs() if run.status in ACTIVE_RUN_STATUSES),
            None,
        )
        if active is not None:
            raise _conflict(
                "action_not_allowed",
                "Settings cannot change while a run is active",
                reason="active_run",
                run_id=active.run_id,
            )
        current = settings_workspace()
        if revision != current.revision:
            raise _conflict(
                "revision_conflict",
                "Settings changed in another client",
                current_revision=current.revision,
            )
        return current

    def require_editable_credential(
        credential_id: CredentialId,
        current: SettingsResponse,
    ) -> tuple[CredentialStore, str]:
        credential = next(item for item in current.credentials if item.id == credential_id)
        if credential.source == "environment":
            raise _conflict(
                "action_not_allowed",
                f"{credential.env_name} is managed by the environment",
                reason="managed_by_environment",
            )
        if not current.credential_store_available or not credential.editable:
            raise HTTPException(
                503,
                {
                    "code": "credential_store_unavailable",
                    "message": "Secure credential storage is unavailable",
                },
            )
        return get_credential_store(), credential.env_name

    @api.get("/api/v1/settings", response_model=SettingsResponse)
    def get_settings(request: Request) -> SettingsResponse:
        _guard_api_request(request)
        return settings_workspace()

    @api.put(
        "/api/v1/settings/credentials/{credential_id}",
        response_model=SettingsResponse,
    )
    def put_credential(
        credential_id: CredentialId,
        request_body: CredentialUpdateRequest,
        request: Request,
    ) -> SettingsResponse:
        secret = _validate_secret_value(request_body.secret.get_secret_value())
        runtime = get_runtime()
        with runtime.control_lock:
            current = reject_settings_mutation(request, request_body.revision, runtime)
            store, env_name = require_editable_credential(credential_id, current)
            try:
                store.set_secret(env_name, secret)
            except Exception as exc:
                log.warning(
                    "Credential store write failed for %s (%s)",
                    env_name,
                    type(exc).__name__,
                )
                raise HTTPException(
                    503,
                    {
                        "code": "credential_store_unavailable",
                        "message": "Secure credential storage is unavailable",
                    },
                ) from exc
            return settings_workspace()

    @api.delete(
        "/api/v1/settings/credentials/{credential_id}",
        response_model=SettingsResponse,
    )
    def delete_credential(
        credential_id: CredentialId,
        request_body: SettingsRevisionRequest,
        request: Request,
    ) -> SettingsResponse:
        runtime = get_runtime()
        with runtime.control_lock:
            current = reject_settings_mutation(request, request_body.revision, runtime)
            store, env_name = require_editable_credential(credential_id, current)
            try:
                store.delete_secret(env_name)
            except Exception as exc:
                log.warning(
                    "Credential store delete failed for %s (%s)",
                    env_name,
                    type(exc).__name__,
                )
                raise HTTPException(
                    503,
                    {
                        "code": "credential_store_unavailable",
                        "message": "Secure credential storage is unavailable",
                    },
                ) from exc
            return settings_workspace()

    @api.post(
        "/api/v1/settings/credentials/{credential_id}/test",
        response_model=CredentialTestResponse,
    )
    def test_credential(
        credential_id: CredentialId,
        request_body: CredentialTestRequest,
        request: Request,
    ) -> CredentialTestResponse:
        _guard_settings_request(request)
        store = get_credential_store()
        if request_body.secret is not None:
            secret = _validate_secret_value(request_body.secret.get_secret_value())
        else:
            try:
                secret = _effective_credential_secret(store, credential_id)
            except RuntimeError as exc:
                raise HTTPException(
                    503,
                    {"code": "credential_store_unavailable", "message": str(exc)},
                ) from exc
            if secret is None:
                raise HTTPException(
                    422,
                    {
                        "code": "credential_not_configured",
                        "message": "No draft or configured credential is available",
                    },
                )
        return _credential_validation_request(get_runtime().config, credential_id, secret)

    @api.get("/api/v1/tts/voices", response_model=TtsVoicePage)
    def list_tts_voices(
        search: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        ownership: Literal["all", "vbee", "community"] = "all",
    ) -> TtsVoicePage:
        service = get_tts_service()
        try:
            return service.voices(
                search=search, page=page, page_size=page_size, ownership=ownership
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except DubError as exc:
            raise HTTPException(502, str(exc)) from exc

    @api.get("/api/v1/jobs", response_model=JobPage)
    def list_jobs_v1(
        search: str = "",
        execution: str | None = None,
        quality: str | None = None,
        lane: Literal["pipeline", "tts", "any"] = "pipeline",
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> JobPage:
        runtime = get_runtime()
        tts_service = get_tts_service()
        summaries = [
            _job_summary(runtime.store, job, tts_service)
            for job in runtime.store.list()
        ]
        summaries.sort(key=lambda item: item.updated_at or "", reverse=True)

        def selected_lanes(item: JobSummary) -> list[JobLaneSummary]:
            lanes = [summary for summary in item.lanes if summary.started]
            if lane == "any":
                return lanes
            return [summary for summary in lanes if summary.id == lane]

        if lane == "tts":
            summaries = [item for item in summaries if selected_lanes(item)]
        query = search.strip().casefold()
        if query:
            summaries = [
                item for item in summaries
                if query in item.job_id.casefold()
                or query in item.title.casefold()
                or (item.source is not None and query in item.source.value.casefold())
            ]
        if execution:
            try:
                execution_values = {ExecutionStatus(value.strip()) for value in execution.split(",") if value.strip()}
            except ValueError as exc:
                raise HTTPException(422, f"Unknown execution status: {exc}") from exc
            summaries = [
                item
                for item in summaries
                if any(
                    summary.execution_status in execution_values
                    for summary in selected_lanes(item)
                )
            ]
        if quality:
            try:
                quality_values = {QualityStatus(value.strip()) for value in quality.split(",") if value.strip()}
            except ValueError as exc:
                raise HTTPException(422, f"Unknown quality status: {exc}") from exc
            summaries = [
                item
                for item in summaries
                if any(
                    summary.quality_status in quality_values
                    for summary in selected_lanes(item)
                )
            ]
        total = len(summaries)
        offset = (page - 1) * page_size
        return JobPage(items=summaries[offset: offset + page_size], page=page, page_size=page_size, total=total)

    @api.post("/api/v1/jobs", response_model=CreateJobResponse, status_code=201)
    def create_job(request_body: CreateJobRequest) -> CreateJobResponse:
        runtime = get_runtime()
        if api.state.enforce_readiness:
            blocked = [item for item in _readiness(runtime.config) if item.status == "blocked"]
            if blocked:
                raise HTTPException(
                    503,
                    {"code": "not_ready", "checks": [item.model_dump(mode="json") for item in blocked]},
                )
        source_value = _validate_source(request_body.source)
        source = SourceRequest(kind=request_body.source.kind, value=source_value)
        job_id = s0_ingest.make_job_id(source_value)
        work_dir = Path(runtime.config.paths.work_dir).resolve(strict=False) / job_id
        output_dir = Path(runtime.config.paths.output_dir).resolve(strict=False) / job_id
        snapshot = JobRequestSnapshot(
            source=source,
            targets=request_body.targets,
            formats=request_body.formats,
            bilingual=request_body.bilingual,
            output_dir=str(output_dir),
        )
        try:
            run = runtime.create_job_run(
                job_id,
                source_value,
                str(work_dir),
                list(request_body.targets),
                snapshot.model_dump(mode="json"),
            )
        except ValueError as exc:
            raise _conflict("action_not_allowed", str(exc)) from exc
        return CreateJobResponse(job_id=job_id, run_id=run.run_id)

    @api.get("/api/v1/jobs/{job_id}", response_model=JobDetail)
    def get_job(job_id: str) -> JobDetail:
        runtime, job = require_job(job_id)
        return _job_detail(runtime.store, job, get_tts_service())

    def enqueue_action(job_id: str, kind: str, from_stage: str, *, force: bool = False) -> RunResponse:
        runtime, job = require_job(job_id)
        if job.request is None:
            runtime.store.set_request(
                job_id,
                _snapshot_for(job).model_dump(mode="json"),
            )
        try:
            run = runtime.enqueue(job_id, kind, from_stage, force=force)
        except ValueError as exc:
            raise _conflict("action_not_allowed", str(exc)) from exc
        response = _run_response(runtime.store, run)
        assert response is not None
        return response

    @api.post("/api/v1/jobs/{job_id}/retry", response_model=RunResponse)
    def retry_job(job_id: str) -> RunResponse:
        runtime, job = require_job(job_id)
        latest = runtime.store.latest_run(job_id, lane="pipeline")
        execution = _execution_for(job, latest)
        if execution not in {ExecutionStatus.failed, ExecutionStatus.interrupted, ExecutionStatus.cancelled}:
            raise _conflict(
                "action_not_allowed",
                "Only failed, interrupted, or cancelled jobs can be retried",
            )
        from_stage = (
            (latest.stage or latest.from_stage) if latest else None
        ) or job.stage or "ingest"
        return enqueue_action(job_id, "retry", from_stage)

    @api.post("/api/v1/jobs/{job_id}/retranslate", response_model=RunResponse)
    def retranslate_job(job_id: str) -> RunResponse:
        _, job = require_job(job_id)
        work_dir = Path(job.work_dir)
        if not (work_dir / "segments.json").is_file() or not (work_dir / "glossary.json").is_file():
            raise _conflict(
                "action_not_allowed",
                "Segments and glossary must exist before retranslation",
            )
        return enqueue_action(job_id, "retranslate", "translate", force=True)

    @api.post("/api/v1/jobs/{job_id}/render", response_model=RunResponse)
    def render_job(job_id: str) -> RunResponse:
        _, job = require_job(job_id)
        snapshot = _snapshot_for(job)
        if not all((Path(job.work_dir) / f"translations.{lang}.json").is_file() for lang in snapshot.targets):
            raise _conflict(
                "action_not_allowed",
                "All requested translations must exist before render",
            )
        return enqueue_action(job_id, "render", "render")

    @api.post("/api/v1/jobs/{job_id}/approve", response_model=JobDetail)
    def approve_job(job_id: str) -> JobDetail:
        runtime, job = require_job(job_id)
        detail = _job_detail(runtime.store, job, get_tts_service())
        if detail.execution_status != ExecutionStatus.completed:
            raise _conflict(
                "action_not_allowed",
                "Only completed jobs can be approved",
            )
        blockers = _APPROVAL_BLOCKERS.intersection(detail.attention_reasons)
        if blockers:
            raise _conflict(
                "action_not_allowed",
                "Job quality blockers must be resolved before approval",
                reasons=sorted(blockers),
            )
        try:
            runtime.store.approve(job_id, _approval_signature(job))
        except ValueError as exc:
            raise _conflict("action_not_allowed", str(exc)) from exc
        updated = runtime.store.get(job_id)
        assert updated is not None
        return _job_detail(runtime.store, updated, get_tts_service())

    @api.post("/api/v1/jobs/{job_id}/unapprove", response_model=JobDetail)
    def unapprove_job(job_id: str) -> JobDetail:
        runtime, _ = require_job(job_id)
        runtime.store.set_approval(job_id, None)
        updated = runtime.store.get(job_id)
        assert updated is not None
        return _job_detail(runtime.store, updated, get_tts_service())

    @api.get("/api/v1/jobs/{job_id}/glossary", response_model=GlossaryResponse)
    def get_glossary(job_id: str) -> GlossaryResponse:
        runtime, job = require_job(job_id)
        path = Path(job.work_dir) / "glossary.json"
        raw = _safe_json(path)
        if raw is None:
            raise HTTPException(404, "Glossary is not available yet")
        try:
            document = GlossaryDoc.model_validate(raw)
        except ValueError as exc:
            raise HTTPException(422, f"Invalid glossary artifact: {exc}") from exc
        editor_locked, lock_reason = glossary_lock_state(runtime, job_id)
        return GlossaryResponse(
            revision=sha256_json_canonical(raw),
            document=document,
            editor_locked=editor_locked,
            lock_reason=lock_reason,
        )

    @api.put("/api/v1/jobs/{job_id}/glossary", response_model=GlossaryResponse)
    def put_glossary(job_id: str, request_body: GlossaryUpdateRequest) -> GlossaryResponse:
        runtime, job = require_job(job_id)
        editor_locked, lock_reason = glossary_lock_state(runtime, job_id)
        if editor_locked:
            raise HTTPException(
                409,
                {
                    "code": "artifact_locked",
                    "message": "Glossary is locked by an active run",
                    "lock_reason": lock_reason,
                },
            )
        path = Path(job.work_dir) / "glossary.json"
        with _document_lock(path):
            current_raw = _safe_json(path)
            if current_raw is None:
                raise HTTPException(404, "Glossary is not available yet")
            current_revision = sha256_json_canonical(current_raw)
            if request_body.revision != current_revision:
                raise HTTPException(
                    409,
                    {
                        "code": "revision_conflict",
                        "current_revision": current_revision,
                    },
                )
            write_doc(path, request_body.document)
            runtime.store.set_approval(job_id, None)
            written = request_body.document.model_dump(by_alias=True, mode="json")
            return GlossaryResponse(
                revision=sha256_json_canonical(written),
                document=request_body.document,
                editor_locked=False,
                lock_reason=None,
            )

    def subtitle_workspace(runtime: RunScheduler, job: Job, lang: str) -> SubtitleResponse:
        if lang not in {"vi", "en"}:
            raise HTTPException(404, "Unsupported subtitle language")
        work_dir = Path(job.work_dir)
        if not (work_dir / "segments.json").is_file() or not (work_dir / f"translations.{lang}.json").is_file():
            raise HTTPException(404, f"Subtitles for {lang} are not available yet")
        try:
            segments = read_doc(work_dir / "segments.json", SegmentsDoc)
            translations = read_doc(work_dir / f"translations.{lang}.json", TranslationsDoc)
            rows = effective_items(work_dir, lang)
            state = get_override_state(work_dir, lang)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"Invalid subtitle artifacts: {exc}") from exc

        report = _render_report(work_dir)
        warnings: dict[int, list[SubtitleWarningResponse]] = {}
        if report is not None:
            for warning in report.warnings:
                if warning.lang != lang:
                    continue
                warnings.setdefault(warning.segment_id, []).append(
                    SubtitleWarningResponse(
                        kind=warning.kind,
                        detail=warning.detail,
                        value=warning.value,
                        limit=warning.limit,
                    )
                )
        segment_by_id = {segment.id: segment for segment in segments.segments}
        reviewed_by_id = {item.id: item.reviewed for item in translations.items}
        cues: list[SubtitleCue] = []
        for row in rows:
            segment = segment_by_id[row.segment_id]
            row_warnings = warnings.get(row.segment_id, [])
            override_state: Literal["none", "manual", "base_changed", "stale"] = "none"
            if row.stale:
                override_state = "stale"
            elif row.base_changed:
                override_state = "base_changed"
            elif row.overridden:
                override_state = "manual"
            cues.append(
                SubtitleCue(
                    segment_id=row.segment_id,
                    start=segment.start,
                    end=segment.end,
                    source_text=row.source_text,
                    model_text=row.model_text,
                    effective_text=row.effective_text,
                    reviewed=reviewed_by_id.get(row.segment_id, False),
                    cps=round(cps(row.effective_text, segment.duration), 2),
                    line_count=row.effective_text.count("\n") + 1,
                    warnings=row_warnings,
                    override_state=override_state,
                )
            )
        active = runtime.store.active_run(job.job_id)
        editor_locked = bool(
            active
            and (
                active.lane == "tts"
                or (
                    active.status in {"running", "cancelling"}
                    and active.stage in {"segment", "glossary", "translate"}
                )
            )
        )
        output_stale = bool(
            report
            and any(
                artifact.kind == "subtitle"
                and artifact.language == lang
                and artifact.state != "current"
                for artifact in _artifacts_for(job, runtime.store)
            )
        )
        return SubtitleResponse(
            revision=state.revision,
            language=lang,  # type: ignore[arg-type]
            cues=cues,
            editor_locked=editor_locked,
            output_stale=output_stale,
        )

    @api.get("/api/v1/jobs/{job_id}/subtitles/{lang}", response_model=SubtitleResponse)
    def get_subtitles(job_id: str, lang: str) -> SubtitleResponse:
        runtime, job = require_job(job_id)
        return subtitle_workspace(runtime, job, lang)

    @api.put("/api/v1/jobs/{job_id}/subtitle-overrides/{lang}", response_model=SubtitleResponse)
    def put_subtitle_overrides(
        job_id: str,
        lang: str,
        request_body: OverrideUpdateRequest,
    ) -> SubtitleResponse:
        runtime, job = require_job(job_id)
        active = runtime.store.active_run(job_id)
        if active and (
            active.lane == "tts"
            or (
                active.status in {"running", "cancelling"}
                and active.stage in {"segment", "glossary", "translate"}
            )
        ):
            raise HTTPException(
                409,
                {
                    "code": "artifact_locked",
                    "message": "Subtitle artifacts are locked by an active run",
                    "run_id": active.run_id,
                },
            )
        try:
            apply_override_changes(
                job.work_dir,
                lang,
                request_body.revision,
                request_body.changes,
            )
        except OverrideConflictError as exc:
            raise HTTPException(
                409,
                {
                    "code": "revision_conflict",
                    "current_revision": exc.current_revision,
                },
            ) from exc
        except OverrideValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        runtime.store.set_approval(job_id, None)
        return subtitle_workspace(runtime, job, lang)

    @api.get(
        "/api/v1/jobs/{job_id}/tts/vi",
        response_model=TtsWorkspaceResponse,
    )
    def get_tts_workspace(job_id: str) -> TtsWorkspaceResponse:
        _, job, service = require_tts_job(job_id)
        return service.workspace(job)

    @api.put(
        "/api/v1/jobs/{job_id}/tts/vi/settings",
        response_model=TtsWorkspaceResponse,
    )
    def put_tts_settings(
        job_id: str,
        request_body: TtsSettingsUpdateRequest,
    ) -> TtsWorkspaceResponse:
        runtime, job, service = require_tts_job(job_id)
        reject_active_tts_edit(runtime, job_id)
        try:
            service.apply_settings(job, request_body.revision, request_body.voice_id)
        except SpeechConflictError as exc:
            raise HTTPException(
                409,
                {
                    "code": "revision_conflict",
                    "current_revision": exc.current_revision,
                },
            ) from exc
        except SpeechValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        return service.workspace(job)

    @api.put(
        "/api/v1/jobs/{job_id}/tts/vi/spoken-overrides",
        response_model=TtsWorkspaceResponse,
    )
    def put_tts_spoken_overrides(
        job_id: str,
        request_body: SpokenOverrideUpdateRequest,
    ) -> TtsWorkspaceResponse:
        runtime, job, service = require_tts_job(job_id)
        reject_active_tts_edit(runtime, job_id)
        try:
            service.apply_overrides(
                job,
                request_body.revision,
                [item.model_dump(mode="json") for item in request_body.changes],
            )
        except SpeechConflictError as exc:
            raise HTTPException(
                409,
                {
                    "code": "revision_conflict",
                    "current_revision": exc.current_revision,
                },
            ) from exc
        except SpeechValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        return service.workspace(job)

    @api.post(
        "/api/v1/jobs/{job_id}/tts/vi/preview",
        response_model=RunResponse,
    )
    def preview_tts(job_id: str, request_body: TtsPreviewRequest) -> RunResponse:
        runtime, job, service = require_tts_job(job_id)
        workspace = service.workspace(job)
        if "preview" not in workspace.allowed_actions:
            raise _conflict(
                "action_not_allowed",
                "TTS preview is not currently allowed",
            )
        if not any(cue.segment_id == request_body.segment_id for cue in workspace.cues):
            raise HTTPException(404, "Unknown Vietnamese subtitle segment")
        assert workspace.voice_id is not None
        payload: dict[str, Any] = {
            "lang": TTS_LANGUAGE,
            "voice_id": workspace.voice_id,
            "speech_revision": workspace.revision,
            "segment_id": request_body.segment_id,
        }
        if workspace.calibration is not None:
            payload["calibration_revision"] = workspace.calibration.revision
        return enqueue_tts(runtime, job, "tts_preview", payload)

    @api.post(
        "/api/v1/jobs/{job_id}/tts/vi/calibrate",
        response_model=RunResponse,
    )
    def calibrate_tts(job_id: str) -> RunResponse:
        runtime, job, service = require_tts_job(job_id)
        workspace = service.workspace(job)
        if "calibrate" not in workspace.allowed_actions:
            raise _conflict(
                "action_not_allowed",
                "TTS calibration requires a ready provider, a voice, and eight cues",
            )
        assert workspace.voice_id is not None
        return enqueue_tts(
            runtime,
            job,
            "tts_calibrate",
            {
                "lang": TTS_LANGUAGE,
                "voice_id": workspace.voice_id,
                "speech_revision": workspace.revision,
            },
        )

    @api.post(
        "/api/v1/jobs/{job_id}/tts/vi/render",
        response_model=RunResponse,
    )
    def render_tts(job_id: str) -> RunResponse:
        runtime, job, service = require_tts_job(job_id)
        workspace = service.workspace(job)
        if "render" not in workspace.allowed_actions:
            raise _conflict(
                "action_not_allowed",
                "TTS render requires approved subtitles, a calibrated voice, and clean spoken overrides",
            )
        assert workspace.voice_id is not None
        assert workspace.calibration is not None
        return enqueue_tts(
            runtime,
            job,
            "tts_render",
            {
                "lang": TTS_LANGUAGE,
                "voice_id": workspace.voice_id,
                "speech_revision": workspace.revision,
                "calibration_revision": workspace.calibration.revision,
                "subtitle_approval_signature": _approval_signature(job),
            },
        )

    @api.post(
        "/api/v1/jobs/{job_id}/tts/vi/approve",
        response_model=TtsWorkspaceResponse,
    )
    def approve_tts(job_id: str) -> TtsWorkspaceResponse:
        runtime, job, service = require_tts_job(job_id)
        reject_active_tts_edit(runtime, job_id)
        signature = service.current_audio_signature(job)
        if signature is None:
            raise _conflict(
                "action_not_allowed",
                "Only the current TTS output can be approved",
            )
        try:
            runtime.store.approve_tts(
                job_id,
                TTS_LANGUAGE,
                signature,
                expected_signature=signature,
            )
        except ValueError as exc:
            raise _conflict("action_not_allowed", str(exc)) from exc
        return service.workspace(job)

    @api.post(
        "/api/v1/jobs/{job_id}/tts/vi/unapprove",
        response_model=TtsWorkspaceResponse,
    )
    def unapprove_tts(job_id: str) -> TtsWorkspaceResponse:
        runtime, job, service = require_tts_job(job_id)
        reject_active_tts_edit(runtime, job_id)
        runtime.store.set_tts_approval(job_id, TTS_LANGUAGE, None)
        return service.workspace(job)

    @api.get("/api/v1/jobs/{job_id}/tts/vi/previews/{segment_id}")
    def download_tts_preview(job_id: str, segment_id: int) -> FileResponse:
        _, job, service = require_tts_job(job_id)
        try:
            path = service.preview_path(job, segment_id)
        except (OSError, ValueError, SpeechValidationError):
            path = None
        if path is None:
            raise HTTPException(404, "Current TTS preview is not available")
        return FileResponse(path, media_type="audio/mpeg", filename=path.name)

    @api.get("/api/v1/runs/{run_id}", response_model=RunResponse)
    def get_run(run_id: str) -> RunResponse:
        runtime = get_runtime()
        run = runtime.store.get_run(run_id)
        response = _run_response(runtime.store, run)
        if response is None:
            raise HTTPException(404, f"Unknown run: {run_id}")
        return response

    @api.post("/api/v1/runs/{run_id}/cancel", response_model=ApiMessage)
    def cancel_run(run_id: str) -> ApiMessage:
        runtime = get_runtime()
        run = runtime.cancel(run_id)
        if run is None:
            raise HTTPException(404, f"Unknown run: {run_id}")
        return ApiMessage(ok=True, detail=run.status)

    @api.get("/api/v1/runs/{run_id}/events")
    def run_events(run_id: str, request: Request) -> StreamingResponse:
        runtime = get_runtime()
        initial = runtime.store.get_run(run_id)
        if initial is None:
            raise HTTPException(404, f"Unknown run: {run_id}")

        def encode(run: Run, event_type: str, event_id: int) -> str:
            response = _run_response(runtime.store, run)
            assert response is not None
            payload = {
                "id": event_id,
                "type": event_type,
                "run": response.model_dump(mode="json"),
            }
            return f"id: {event_id}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        def stream() -> Iterator[str]:
            current = runtime.store.get_run(run_id)
            if current is None:
                return
            # A fresh snapshot is always first, even when Last-Event-ID is present.
            last_seq = current.event_seq
            yield encode(current, "snapshot", last_seq)
            if current.status in TERMINAL_RUN_STATUSES:
                return
            generation = runtime.broker.generation(run_id)
            while True:
                events = runtime.store.events_after(run_id, last_seq)
                for event in events:
                    current = runtime.store.get_run(run_id)
                    if current is None:
                        return
                    if current.status == "completed":
                        event_type = "completed"
                    elif current.status in {"failed", "interrupted"}:
                        event_type = "error"
                    elif current.status == "cancelled":
                        event_type = "completed"
                    else:
                        event_type = "progress"
                    last_seq = event.seq
                    yield encode(current, event_type, event.seq)
                current = runtime.store.get_run(run_id)
                if current is None or current.status in TERMINAL_RUN_STATUSES:
                    return
                next_generation = runtime.broker.wait(run_id, generation, 15.0)
                if next_generation == generation:
                    yield ": keepalive\n\n"
                generation = next_generation

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @api.get("/api/v1/files/roots", response_model=FileRootsResponse)
    def file_roots_endpoint() -> FileRootsResponse:
        roots = api.state.file_roots or _system_file_roots()
        return FileRootsResponse(
            roots=[FileRootResponse(label=root.drive or root.name or str(root), path=str(root)) for root in roots]
        )

    @api.get("/api/v1/files", response_model=FileListResponse)
    def list_files(path: str) -> FileListResponse:
        roots: list[Path] = api.state.file_roots or _system_file_roots()
        raw = Path(path)
        if not raw.is_absolute():
            raise HTTPException(400, "Directory path must be absolute")
        try:
            directory = raw.resolve(strict=True)
        except OSError:
            raise HTTPException(404, "Directory does not exist") from None
        if not directory.is_dir() or not _is_inside(directory, roots):
            raise HTTPException(403, "Directory is outside the allowed roots")
        entries: list[FileEntryResponse] = []
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            raise HTTPException(403, f"Cannot list directory: {exc}") from exc
        for child in children:
            try:
                resolved = child.resolve(strict=True)
                if not _is_inside(resolved, roots):
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            if resolved.is_dir():
                entries.append(FileEntryResponse(name=child.name, path=str(resolved), kind="directory"))
            elif resolved.is_file() and resolved.suffix.lower() in _MEDIA_SUFFIXES:
                entries.append(
                    FileEntryResponse(
                        name=child.name,
                        path=str(resolved),
                        kind="file",
                        size_bytes=stat.st_size,
                    )
                )
        entries.sort(key=lambda item: (item.kind != "directory", item.name.casefold()))
        parent_path = directory.parent
        parent = str(parent_path) if parent_path != directory and _is_inside(parent_path, roots) else None
        return FileListResponse(path=str(directory), parent=parent, entries=entries)

    @api.get("/api/v1/jobs/{job_id}/media")
    def job_media(job_id: str, request: Request):
        _, job = require_job(job_id)
        path = _media_path(job)
        if path is None:
            raise HTTPException(404, "Job media is not available")
        size = path.stat().st_size
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        range_header = request.headers.get("range")
        if not range_header:
            return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes"})
        start, end = _parse_range(range_header, size)
        return StreamingResponse(
            _file_chunks(path, start, end),
            status_code=206,
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(end - start + 1),
            },
        )

    @api.get("/api/v1/jobs/{job_id}/artifacts/{artifact_id}")
    def download_artifact(job_id: str, artifact_id: str) -> FileResponse:
        _, job = require_job(job_id)
        snapshot = _snapshot_for(job)
        roots = _allowed_output_roots(job, snapshot)
        report = _render_report(Path(job.work_dir))
        candidates: list[Path] = []
        if report:
            for raw_path in report.outputs:
                path = Path(raw_path)
                if not path.is_absolute():
                    path = Path.cwd() / path
                resolved = path.resolve(strict=False)
                if _is_inside(resolved, roots):
                    candidates.append(resolved)
        candidates.extend(_expected_subtitle_paths(job, snapshot))
        for root in roots:
            if root.is_dir():
                pattern = "*.mp3" if job.request is not None else f"{job_id}.*.mp3"
                candidates.extend(path.resolve(strict=False) for path in root.glob(pattern))
        for path in candidates:
            candidate_id = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:20]
            if candidate_id == artifact_id and path.is_file() and _is_inside(path, roots):
                return FileResponse(path, filename=path.name)
        raise HTTPException(404, "Artifact does not exist or does not belong to this job")

    if _STATIC.exists():
        api.mount("/assets", StaticFiles(directory=_STATIC / "assets", check_dir=False), name="assets")

    @api.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        if not _PAGE.is_file():
            return HTMLResponse("Frontend build is not installed", status_code=503)
        return FileResponse(_PAGE)

    @api.get("/{spa_path:path}", include_in_schema=False)
    def spa_fallback(spa_path: str):
        if spa_path.startswith("api/"):
            raise HTTPException(404, "API route does not exist")
        if not _PAGE.is_file():
            return HTMLResponse("Frontend build is not installed", status_code=503)
        return FileResponse(_PAGE)

    return api


app = create_app()


def serve(host: str = "127.0.0.1", port: int = 8756) -> None:
    normalized = host.strip().lower()
    if normalized != "localhost":
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            raise ValueError("Web UI host must be localhost or a loopback IP") from None
        if not address.is_loopback:
            raise ValueError("Web UI can only bind to a loopback address without authentication")

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
