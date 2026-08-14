"""Schemas for every intermediate JSON file under ``work/<job_id>/``.

Core invariant: **timestamps only ever come from ASR**. ``AsrDoc.tokens`` is the
single anchor array; later stages reference it by index and must never invent or
parse timing from anywhere else.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = 1

Lang = Literal["vi", "en"]


class _Doc(BaseModel):
    """Base class for every JSON document written to disk."""

    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# S0 — ingest.json
# ---------------------------------------------------------------------------


class SourceInfo(BaseModel):
    kind: Literal["local", "url"]
    uri: str
    sha256: str | None = None
    title: str | None = None


class MediaInfo(BaseModel):
    wav_path: str
    duration_sec: float
    sample_rate: int
    channels: int


class IngestDoc(_Doc):
    job_id: str
    source: SourceInfo
    media: MediaInfo
    created_at: str


# ---------------------------------------------------------------------------
# S1 — asr.json
# ---------------------------------------------------------------------------


class Token(BaseModel):
    """A single **timestamped** unit from ASR.

    Punctuation inserted by ``ct-punc`` carries no timestamp of its own, so it
    hangs off the preceding token as ``punct_after``. That keeps two streams
    cleanly separated:

    * stream used for alignment / sent to the LLM = concat(``text``)
    * stream used for display                     = concat(``text`` + ``punct_after``)
    """

    i: int
    text: str
    start: float
    end: float
    punct_after: str | None = None


class RawSegment(BaseModel):
    """A sentence as split by ASR itself (on VAD silence), before S2 re-splits."""

    id: int
    start: float
    end: float
    token_range: tuple[int, int]  # half-open [lo, hi)


class EngineInfo(BaseModel):
    name: str
    version: str
    models: dict[str, str] = Field(default_factory=dict)
    device: str


class AsrDoc(_Doc):
    engine: EngineInfo
    audio_duration_sec: float
    tokens: list[Token]
    raw_segments: list[RawSegment]
    # Speech intervals detected by VAD, [[start, end], ...]. Needed both for
    # chunking the LLM input in S2 and for extending subtitles into silence in S5.
    vad_speech: list[tuple[float, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_monotonic(self) -> AsrDoc:
        prev = -1.0
        for t in self.tokens:
            if t.start < prev - 1e-6:
                raise ValueError(
                    f"token {t.i} starts at {t.start}, behind the previous token ({prev}); "
                    "almost certainly a chunk-offset arithmetic bug"
                )
            if t.end < t.start - 1e-6:
                raise ValueError(f"token {t.i} has end < start ({t.end} < {t.start})")
            prev = t.start
        return self

    def char_stream(self) -> str:
        """Continuous character stream, no punctuation — for alignment and the LLM."""
        return "".join(t.text for t in self.tokens)

    def display_text(self, lo: int, hi: int) -> str:
        """Punctuated text for the half-open token range ``[lo, hi)``."""
        return "".join(t.text + (t.punct_after or "") for t in self.tokens[lo:hi])

    def char_to_token(self) -> list[int]:
        """Map character index in :meth:`char_stream` to token index.

        Necessary because tokens are not always one character wide: Chinese is
        one character per token, but an embedded English word ("OK", "iPhone")
        is a single multi-character token. Ignoring this shifts every index the
        LLM returns, on exactly those videos that mix in English.
        """
        mapping: list[int] = []
        for t in self.tokens:
            mapping.extend([t.i] * len(t.text))
        return mapping

    def char_times(self) -> list[float]:
        """Start time of each character in :meth:`char_stream`.

        Multi-character tokens are interpolated linearly across the token's
        duration — there is no finer information available.
        """
        times: list[float] = []
        for t in self.tokens:
            n = len(t.text)
            if n <= 1:
                times.append(t.start)
                continue
            step = (t.end - t.start) / n
            times.extend(t.start + k * step for k in range(n))
        return times


# ---------------------------------------------------------------------------
# S2 — segments.json
# ---------------------------------------------------------------------------

SegmentMethod = Literal["llm", "llm+repair", "rule_fallback"]


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text_zh: str
    token_range: tuple[int, int]
    flags: list[str] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


class SegmentsDoc(_Doc):
    source_asr_sha256: str
    method: SegmentMethod
    segments: list[Segment]


# ---------------------------------------------------------------------------
# S3 — glossary.json  (hand-editable by the user)
# ---------------------------------------------------------------------------

TermType = Literal["person", "place", "org", "term", "dish", "product", "other"]


class GlossaryTerm(BaseModel):
    zh: str
    pinyin: str = ""
    vi: str = ""
    en: str = ""
    type: TermType = "other"
    keep_source: bool = False  # leave in Chinese, do not translate
    note: str = ""


class AddressTerm(BaseModel):
    """Vietnamese address terms are a **pairwise** relation and do not fit the
    ``{zh, pinyin, vi, en}`` shape of :class:`GlossaryTerm`, hence a separate
    section."""

    speaker: str
    addressee: str
    vi_self: str  # how the speaker refers to themselves
    vi_other: str  # how the speaker addresses the other party
    basis: str = ""  # the reasoning, so the user can check and correct it


class StyleDecision(BaseModel):
    # named ``speech_register`` because plain ``register`` shadows a BaseModel attribute
    speech_register: str = ""
    narrator_self_vi: str = ""
    audience_vi: str = ""
    # How narration refers to the main subject in the third person. Without it the
    # S4 prompt only offers a menu — "anh ta" / "cô ta" / "ông ấy" — and every batch
    # picks again: measured on one 472-line video, the dominant form covered only
    # 77-90% of occurrences depending on how the conversation was threaded.
    # ``address_terms`` cannot fill this role; it is scoped to direct speech and is
    # explicitly forbidden in narration.
    # One value per video, not per character: a second protagonist would need its
    # own, but the common case is a single subject and the file is user-editable.
    subject_third_person_vi: str = ""


class GlossaryDoc(_Doc):
    version: int = 1
    terms: list[GlossaryTerm] = Field(default_factory=list)
    address_terms: list[AddressTerm] = Field(default_factory=list)
    style: StyleDecision = Field(default_factory=StyleDecision)


# ---------------------------------------------------------------------------
# S4 — translations.<lang>.json
# ---------------------------------------------------------------------------


class TranslationItem(BaseModel):
    id: int
    text_zh: str
    draft: str = ""
    translation: str
    reviewed: bool = False
    cache_hit: bool = False


class TranslationsDoc(_Doc):
    lang: Lang
    model: str
    prompt_version: int
    glossary_hash: str
    # Hash of the (id, text_zh) pairs this file was produced from. Re-running S2
    # changes the segmentation, which silently invalidates every translation below
    # it; without this the stale file is reused and the 1-1 relation breaks.
    # Deliberately excludes timings: a cue that only moved needs no re-translation.
    segments_hash: str = ""
    items: list[TranslationItem]


# ---------------------------------------------------------------------------
# Manual subtitle overrides — overrides.<lang>.json
# ---------------------------------------------------------------------------


class SubtitleOverride(BaseModel):
    segment_id: int
    source_text_hash: str
    base_translation_hash: str
    text: str
    updated_at: str

    @field_validator("text")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("override text must not be blank")
        return value


class OverridesDoc(_Doc):
    lang: Lang
    items: list[SubtitleOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_segment_ids(self) -> OverridesDoc:
        segment_ids = [item.segment_id for item in self.items]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("override segment_id values must be unique")
        return self


class OverrideChange(BaseModel):
    segment_id: int
    text: str | None

    @field_validator("text")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("override text must not be blank")
        return value


OverrideStatus = Literal["valid", "base_changed", "stale"]


class OverrideEvaluation(BaseModel):
    segment_id: int
    text: str
    status: OverrideStatus


class OverrideState(BaseModel):
    lang: Lang
    revision: str
    items: list[SubtitleOverride] = Field(default_factory=list)
    evaluations: list[OverrideEvaluation] = Field(default_factory=list)


class EffectiveTranslationItem(BaseModel):
    segment_id: int
    source_text: str
    model_text: str
    effective_text: str
    overridden: bool = False
    stale: bool = False
    base_changed: bool = False


# ---------------------------------------------------------------------------
# S6 speech preparation — speech.vi.json
# ---------------------------------------------------------------------------


SpeechOverrideStatus = Literal["valid", "base_changed", "stale"]


class SpeechOverride(BaseModel):
    segment_id: int
    source_text_hash: str
    base_spoken_hash: str
    text: str
    updated_at: str

    @field_validator("text")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("spoken override text must not be blank")
        return value


class SpeechDoc(_Doc):
    lang: Literal["vi"] = "vi"
    voice_id: str
    overrides: list[SpeechOverride] = Field(default_factory=list)

    @field_validator("voice_id")
    @classmethod
    def _reject_blank_voice(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("voice_id must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _check_unique_segment_ids(self) -> SpeechDoc:
        segment_ids = [item.segment_id for item in self.overrides]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("spoken override segment_id values must be unique")
        return self


class SpeechChange(BaseModel):
    segment_id: int
    text: str | None

    @field_validator("text")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("spoken override text must not be blank")
        return value


class SpeechEvaluation(BaseModel):
    segment_id: int
    text: str
    status: SpeechOverrideStatus


class SpeechState(BaseModel):
    lang: Literal["vi"] = "vi"
    voice_id: str
    revision: str
    overrides: list[SpeechOverride] = Field(default_factory=list)
    evaluations: list[SpeechEvaluation] = Field(default_factory=list)


class EffectiveSpokenItem(BaseModel):
    segment_id: int
    source_text: str
    subtitle_text: str
    base_spoken_text: str
    effective_spoken_text: str
    overridden: bool = False
    stale: bool = False
    base_changed: bool = False


class TtsCalibrationPoint(BaseModel):
    syllables: int
    duration_sec: float


class TtsCalibration(BaseModel):
    provider: Literal["ai33_vbee"] = "ai33_vbee"
    voice_id: str
    sample_count: Literal[8] = 8
    overhead_sec: float
    sec_per_syllable: float
    samples_hash: str
    created_at: str
    points: list[TtsCalibrationPoint] = Field(default_factory=list)


class TtsCueReport(BaseModel):
    segment_id: int
    spoken_text_hash: str
    speed: float
    cache_hit: bool
    requested_start_sec: float
    actual_start_sec: float
    duration_sec: float
    room_sec: float
    drift_sec: float


class TtsWarning(BaseModel):
    segment_id: int
    kind: Literal["duration_overrun", "start_drift"]
    detail: str
    value: float
    limit: float


class TtsReport(_Doc):
    lang: Literal["vi"] = "vi"
    output: str
    voice_id: str
    voice_hash: str
    calibration_hash: str
    input_hash: str
    speech_revision: str
    subtitle_approval_signature: str = ""
    duration_sec: float
    cues: list[TtsCueReport] = Field(default_factory=list)
    warnings: list[TtsWarning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# S5 — render_report.json
# ---------------------------------------------------------------------------


class RenderWarning(BaseModel):
    segment_id: int
    lang: str
    kind: Literal["cps_over", "line_over", "too_many_lines"]
    detail: str
    value: float
    limit: float


class RenderReport(_Doc):
    outputs: list[str] = Field(default_factory=list)
    warnings: list[RenderWarning] = Field(default_factory=list)
    # Missing in legacy reports. A default keeps those artifacts readable while
    # allowing the web API to detect outputs rendered from older effective text.
    effective_translation_hashes: dict[str, str] = Field(default_factory=dict)
    # Timing, source text, and media duration also affect rendered cues even when
    # the effective translations stay unchanged.
    segments_render_hash: str = ""
    # A reused job can be submitted with different targets, formats, or bilingual
    # mode; old artifacts must not be presented as current for the new request.
    render_request_hash: str = ""
