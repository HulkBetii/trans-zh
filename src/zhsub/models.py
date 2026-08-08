"""Schemas for every intermediate JSON file under ``work/<job_id>/``.

Core invariant: **timestamps only ever come from ASR**. ``AsrDoc.tokens`` is the
single anchor array; later stages reference it by index and must never invent or
parse timing from anywhere else.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    items: list[TranslationItem]


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
