"""Schema của toàn bộ file JSON trung gian trong ``work/<job_id>/``.

Nguyên tắc xuyên suốt: **timestamp chỉ đến từ ASR**. ``AsrDoc.tokens`` là mảng
neo duy nhất; mọi stage sau chỉ được tham chiếu tới nó bằng index, không bao giờ
tự sinh ra hoặc parse mốc thời gian từ chỗ khác.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 1

Lang = Literal["vi", "en"]


class _Doc(BaseModel):
    """Base cho mọi file JSON được ghi ra đĩa."""

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
    """Một ký tự **có timestamp** từ ASR.

    Dấu câu do ``ct-punc`` chèn không có timestamp riêng nên được treo vào
    ``punct_after`` của token liền trước. Nhờ vậy hai chuỗi tách bạch hoàn toàn:

    * chuỗi để align / gửi cho LLM  = concat(``text``)
    * chuỗi để hiển thị             = concat(``text`` + ``punct_after``)
    """

    i: int
    text: str
    start: float
    end: float
    punct_after: str | None = None


class RawSegment(BaseModel):
    """Câu do ASR tự ngắt (theo khoảng lặng VAD), trước khi S2 ngắt lại."""

    id: int
    start: float
    end: float
    token_range: tuple[int, int]  # nửa mở [lo, hi)


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
    # Các khoảng có tiếng nói do VAD phát hiện, [[start, end], ...]. Cần cho
    # cả việc chia chunk ở S2 lẫn việc kéo dài phụ đề vào khoảng lặng ở S5.
    vad_speech: list[tuple[float, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_monotonic(self) -> AsrDoc:
        prev = -1.0
        for t in self.tokens:
            if t.start < prev - 1e-6:
                raise ValueError(
                    f"token {t.i} có start={t.start} lùi so với token trước ({prev}); "
                    "gần như chắc chắn là lỗi cộng offset khi ghép chunk"
                )
            if t.end < t.start - 1e-6:
                raise ValueError(f"token {t.i} có end < start ({t.end} < {t.start})")
            prev = t.start
        return self

    def char_stream(self) -> str:
        """Chuỗi ký tự liên tục, không dấu câu — dùng để align và gửi cho LLM."""
        return "".join(t.text for t in self.tokens)

    def display_text(self, lo: int, hi: int) -> str:
        """Text có dấu câu cho khoảng token nửa mở ``[lo, hi)``."""
        return "".join(t.text + (t.punct_after or "") for t in self.tokens[lo:hi])

    def char_to_token(self) -> list[int]:
        """Index ký tự trong :meth:`char_stream` -> index token.

        Cần vì token không phải lúc nào cũng dài một ký tự: tiếng Trung thì một
        ký tự một token, nhưng một từ tiếng Anh xen giữa ("OK", "iPhone") là một
        token nhiều ký tự. Bỏ qua chuyện này thì mọi index do LLM trả về sẽ lệch
        đúng ở những video có xen tiếng Anh.
        """
        mapping: list[int] = []
        for t in self.tokens:
            mapping.extend([t.i] * len(t.text))
        return mapping

    def char_times(self) -> list[float]:
        """Thời điểm bắt đầu của từng ký tự trong :meth:`char_stream`.

        Token nhiều ký tự thì nội suy tuyến tính trong khoảng thời gian của token
        — không có thông tin nào mịn hơn để mà dùng.
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
# S3 — glossary.json  (người dùng sửa tay được)
# ---------------------------------------------------------------------------

TermType = Literal["person", "place", "org", "term", "dish", "product", "other"]


class GlossaryTerm(BaseModel):
    zh: str
    pinyin: str = ""
    vi: str = ""
    en: str = ""
    type: TermType = "other"
    keep_source: bool = False  # giữ nguyên dạng tiếng Trung, không dịch
    note: str = ""


class AddressTerm(BaseModel):
    """Xưng hô tiếng Việt là quan hệ **cặp**, không nhét vừa shape của
    :class:`GlossaryTerm`, nên để riêng."""

    speaker: str
    addressee: str
    vi_self: str  # người nói tự xưng
    vi_other: str  # người nói gọi đối phương
    basis: str = ""  # căn cứ suy ra, để người dùng kiểm chứng và sửa


class StyleDecision(BaseModel):
    # tên "register" bị pydantic cảnh báo vì trùng attribute của BaseModel
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
