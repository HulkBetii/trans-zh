"""Typed HTTP contract for the local production studio."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from ..models import GlossaryDoc, OverrideChange


class ExecutionStatus(str, Enum):
    queued = "queued"
    running = "running"
    cancelling = "cancelling"
    cancelled = "cancelled"
    failed = "failed"
    interrupted = "interrupted"
    completed = "completed"


class QualityStatus(str, Enum):
    needs_review = "needs_review"
    degraded = "degraded"
    approved = "approved"


class SourceRequest(BaseModel):
    kind: Literal["local", "url"]
    value: str = Field(min_length=1, max_length=8192)

    @field_validator("value")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        value = value.strip().strip('"')
        if not value:
            raise ValueError("source value must not be blank")
        return value


class JobRequestSnapshot(BaseModel):
    source: SourceRequest
    targets: list[Literal["vi", "en"]]
    formats: list[Literal["srt", "ass"]]
    bilingual: bool = False
    output_dir: str


class CreateJobRequest(BaseModel):
    source: SourceRequest
    targets: list[Literal["vi", "en"]] = Field(default_factory=lambda: ["vi"])
    formats: list[Literal["srt", "ass"]] = Field(default_factory=lambda: ["srt", "ass"])
    bilingual: bool = False

    @field_validator("targets", "formats")
    @classmethod
    def _unique_non_empty(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("at least one value is required")
        if len(values) != len(set(values)):
            raise ValueError("duplicate values are not allowed")
        return values


class StageDescriptor(BaseModel):
    id: str
    label: str
    short_label: str | None = None
    description: str | None = None


class ReadinessItem(BaseModel):
    id: str
    label: str
    status: Literal["ready", "warning", "blocked"]
    detail: str | None = None


class MetaResponse(BaseModel):
    stages: list[StageDescriptor]
    targets: list[str]
    formats: list[str]
    capabilities: list[str]
    readiness: list[ReadinessItem]


class CredentialId(str, Enum):
    openai = "openai"
    anthropic = "anthropic"
    ai33 = "ai33"


class SettingsProviderId(str, Enum):
    segment = "segment"
    translate = "translate"
    tts = "tts"


class SettingsCredentialResponse(BaseModel):
    id: CredentialId
    label: str
    configured: bool
    source: Literal["environment", "credential_store", "none"]
    editable: bool
    masked_value: Literal["********"] | None = None
    env_name: str


class SettingsProviderResponse(BaseModel):
    id: SettingsProviderId
    label: str
    provider: str
    model: str | None = None
    base_url: str | None = None
    credential_id: CredentialId | None = None


class SettingsResponse(BaseModel):
    revision: str
    credential_store_available: bool
    providers: list[SettingsProviderResponse]
    credentials: list[SettingsCredentialResponse]


class CredentialUpdateRequest(BaseModel):
    revision: str
    secret: SecretStr


class SettingsRevisionRequest(BaseModel):
    revision: str


class CredentialTestRequest(BaseModel):
    secret: SecretStr | None = None


class CredentialTestResponse(BaseModel):
    ok: bool
    code: str
    message: str
    latency_ms: int


class RunResponse(BaseModel):
    run_id: str
    job_id: str
    lane: Literal["pipeline", "tts"] = "pipeline"
    kind: str
    from_stage: str
    status: ExecutionStatus
    stage: str | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    queue_position: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    event_seq: int = 0


class HealthResponse(BaseModel):
    method: str = ""
    subject_pronoun: str = ""
    cps_warnings: int = 0
    address_terms: int = 0
    segments: int = 0
    terms: int = 0


class ArtifactResponse(BaseModel):
    artifact_id: str
    name: str
    kind: Literal["subtitle", "audio", "report", "other"]
    language: str | None = None
    format: str | None = None
    size_bytes: int | None = None
    created_at: str | None = None
    state: Literal["current", "stale", "missing"]
    download_url: str | None = None


class JobLaneSummary(BaseModel):
    id: Literal["pipeline", "tts"]
    started: bool = True
    execution_status: ExecutionStatus | Literal["not_started"]
    quality_status: QualityStatus
    stage: str | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    attention_reasons: list[str] = Field(default_factory=list)
    active_run: RunResponse | None = None
    latest_run: RunResponse | None = None
    allowed_actions: list[Literal["cancel"]] = Field(default_factory=list)


class JobSummary(BaseModel):
    job_id: str
    title: str
    source: SourceRequest | None = None
    targets: list[str]
    execution_status: ExecutionStatus
    quality_status: QualityStatus
    stage: str | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    attention_reasons: list[str] = Field(default_factory=list)
    active_run: RunResponse | None = None
    lanes: list[JobLaneSummary] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class JobPage(BaseModel):
    items: list[JobSummary]
    page: int
    page_size: int
    total: int


class StageState(BaseModel):
    id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    message: str | None = None
    error: str | None = None
    cache_hit: bool = False


class JobDetail(JobSummary):
    request: JobRequestSnapshot
    stages: list[StageState]
    health: HealthResponse | None = None
    artifacts: list[ArtifactResponse]
    allowed_actions: list[
        Literal["cancel", "retry", "retranslate", "render", "approve", "unapprove"]
    ]
    glossary_stale: bool = False


class CreateJobResponse(BaseModel):
    job_id: str
    run_id: str


class GlossaryResponse(BaseModel):
    revision: str
    document: GlossaryDoc
    editor_locked: bool = False
    lock_reason: str | None = None


class GlossaryUpdateRequest(BaseModel):
    revision: str
    document: GlossaryDoc

    @model_validator(mode="after")
    def _validate_terms(self) -> GlossaryUpdateRequest:
        seen: set[str] = set()
        for term in self.document.terms:
            key = term.zh.strip()
            if not key:
                raise ValueError("glossary term zh must not be blank")
            if key in seen:
                raise ValueError(f"duplicate glossary term: {key}")
            seen.add(key)
            if term.keep_source and (term.vi.strip() or term.en.strip()):
                raise ValueError(
                    f"term {key!r} cannot keep source and define a translation"
                )
        return self


class SubtitleWarningResponse(BaseModel):
    kind: str
    detail: str
    value: float
    limit: float


class SubtitleCue(BaseModel):
    segment_id: int
    start: float
    end: float
    source_text: str
    model_text: str
    effective_text: str
    overridden: bool = False
    stale: bool = False
    base_changed: bool = False
    override_state: Literal["none", "manual", "base_changed", "stale"] = "none"
    warnings: list[SubtitleWarningResponse] = Field(default_factory=list)
    reviewed: bool = False
    cps: float | None = None
    line_count: int | None = None


class SubtitleResponse(BaseModel):
    revision: str
    language: Literal["vi", "en"]
    cues: list[SubtitleCue]
    editor_locked: bool = False
    output_stale: bool = False


class OverrideUpdateRequest(BaseModel):
    revision: str
    changes: list[OverrideChange]

    @field_validator("changes")
    @classmethod
    def _unique_segments(cls, values: list[OverrideChange]) -> list[OverrideChange]:
        ids = [item.segment_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("each segment may be updated once per request")
        return values


class TtsVoiceResponse(BaseModel):
    voice_id: str
    name: str
    description: str | None = None
    locale: str | None = None
    gender: str | None = None
    age: str | None = None
    category: str | None = None
    tier: str | None = None
    preview_url: str | None = None
    avatar_url: str | None = None
    calibrated: bool = False


class TtsVoicePage(BaseModel):
    items: list[TtsVoiceResponse]
    page: int
    page_size: int
    total: int
    has_more: bool = False
    credits: int | None = None


class VoiceCalibrationResponse(BaseModel):
    provider: Literal["vbee"] = "vbee"
    voice_id: str
    overhead_sec: float
    sec_per_syllable: float
    sample_count: int
    source_job_id: str | None = None
    calibrated_at: str | None = None
    revision: str


class TtsCueResponse(BaseModel):
    segment_id: int
    start: float
    end: float
    subtitle_text: str
    default_spoken_text: str
    effective_spoken_text: str
    override_state: Literal["none", "manual", "base_changed", "stale"] = "none"
    room_seconds: float
    predicted_duration: float | None = None
    overflow_seconds: float | None = None
    speed: float = 1.0
    preview_state: Literal["missing", "current", "stale"] = "missing"
    preview_url: str | None = None


TtsExecutionStatus = Literal[
    "not_started",
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "failed",
    "interrupted",
    "completed",
]

TtsAllowedAction = Literal[
    "preview",
    "calibrate",
    "render",
    "cancel",
    "approve",
    "unapprove",
]


class TtsWorkspaceResponse(BaseModel):
    revision: str
    language: Literal["vi"] = "vi"
    provider: Literal["vbee"] = "vbee"
    provider_ready: bool = False
    voice_id: str | None = None
    selected_voice: TtsVoiceResponse | None = None
    calibration: VoiceCalibrationResponse | None = None
    subtitle_approved: bool = False
    execution_status: TtsExecutionStatus = "not_started"
    quality_status: QualityStatus = QualityStatus.needs_review
    latest_run: RunResponse | None = None
    active_run: RunResponse | None = None
    attention_reasons: list[str] = Field(default_factory=list)
    allowed_actions: list[TtsAllowedAction] = Field(default_factory=list)
    cues: list[TtsCueResponse] = Field(default_factory=list)
    total_cues: int = 0
    uncached_cues: int = 0
    output: ArtifactResponse | None = None
    output_stale: bool = False
    editor_locked: bool = False


class TtsSettingsUpdateRequest(BaseModel):
    revision: str
    voice_id: str = Field(min_length=1, max_length=512)

    @field_validator("voice_id")
    @classmethod
    def _strip_voice_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("voice_id must not be blank")
        return value


class SpokenOverrideUpdateRequest(BaseModel):
    revision: str
    changes: list[OverrideChange]

    @field_validator("changes")
    @classmethod
    def _unique_spoken_segments(cls, values: list[OverrideChange]) -> list[OverrideChange]:
        ids = [item.segment_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("each segment may be updated once per request")
        return values


class TtsPreviewRequest(BaseModel):
    segment_id: int = Field(ge=0)


class FileRootResponse(BaseModel):
    label: str
    path: str


class FileRootsResponse(BaseModel):
    roots: list[FileRootResponse]


class FileEntryResponse(BaseModel):
    name: str
    path: str
    kind: Literal["directory", "file"]
    size_bytes: int | None = None


class FileListResponse(BaseModel):
    path: str
    parent: str | None = None
    entries: list[FileEntryResponse]


class ApiMessage(BaseModel):
    ok: bool = True
    detail: str = ""
