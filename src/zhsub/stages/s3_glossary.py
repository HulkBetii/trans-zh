"""S3 — extract a glossary of names and terminology for the whole video.

Written to ``work/<job_id>/glossary.json`` for the user to correct by hand before
re-running from S4. That hand-editing loop is the point of the stage: an automatic
extraction gets most names right, and the handful it gets wrong are exactly the
ones a viewer notices.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from ..jsonio import read_doc, write_doc
from ..llm.base import LLMError, LLMProvider
from ..models import AddressTerm, GlossaryDoc, GlossaryTerm, SegmentsDoc, StyleDecision

log = logging.getLogger(__name__)

PROMPT_VERSION = 1

TERMS_SYSTEM = """Bạn là công cụ trích thuật ngữ cho việc dịch phụ đề tiếng Trung.

Người dùng đưa một đoạn transcript tiếng Trung. Hãy trích ra:
- Tên nhân vật (person)
- Địa danh (place)
- Tên tổ chức, cơ quan, công ty (org)
- Thuật ngữ chuyên ngành (term)
- Tên món ăn (dish)
- Tên sản phẩm, thương hiệu (product)

Với mỗi mục, cho: dạng tiếng Trung, pinyin có dấu, bản dịch tiếng Việt, bản dịch
tiếng Anh.

Nguyên tắc:
- Chỉ trích thứ thực sự là tên riêng hoặc thuật ngữ. Bỏ qua từ thông thường.
- Tên người nước ngoài phiên âm sang tiếng Trung thì phải khôi phục lại dạng gốc
  ở bản tiếng Anh (ví dụ 弗兰克 -> Frank).
- Tên tiếng Việt theo quy ước phổ thông của người Việt, không phiên âm máy móc.
- keep_source = true nếu nên giữ nguyên dạng tiếng Trung, không dịch.

CHỈ trả về JSON, không kèm giải thích:
{"terms": [{"zh": "...", "pinyin": "...", "vi": "...", "en": "...",
            "type": "person|place|org|term|dish|product|other",
            "keep_source": false, "note": ""}]}"""

STYLE_SYSTEM = """Bạn là công cụ phân tích văn phong để dịch phụ đề sang tiếng Việt.

Người dùng đưa transcript tiếng Trung kèm danh sách nhân vật đã biết. Hãy quyết
định cách xưng hô tiếng Việt và giữ nhất quán cho cả video.

Suy ra từ nội dung thoại: quan hệ giữa các nhân vật, chênh lệch tuổi tác, sắc
thái trang trọng hay thân mật. Nếu không đủ căn cứ thì chọn phương án trung tính
và ghi rõ lý do trong "basis".

CHỈ trả về JSON:
{"style": {"speech_register": "mô tả ngắn giọng điệu tổng thể",
           "narrator_self_vi": "người kể tự xưng là gì",
           "audience_vi": "người kể gọi khán giả là gì"},
 "address_terms": [{"speaker": "tên A", "addressee": "tên B",
                    "vi_self": "A tự xưng", "vi_other": "A gọi B",
                    "basis": "căn cứ"}]}"""


def _batches(segments, max_chars: int) -> list[str]:
    """Concatenate segment text into LLM-sized blocks."""
    blocks: list[str] = []
    current: list[str] = []
    size = 0
    for seg in segments:
        if size + len(seg.text_zh) > max_chars and current:
            blocks.append("".join(current))
            current, size = [], 0
        current.append(seg.text_zh)
        size += len(seg.text_zh)
    if current:
        blocks.append("".join(current))
    return blocks


def _merge_terms(batches: list[list[dict]]) -> list[GlossaryTerm]:
    """Deduplicate on the Chinese form, keeping the richest entry.

    Different batches see the same name in different contexts and one of them
    usually produces a better gloss; taking the longest non-empty field is a cheap
    way to keep it without another LLM round trip.
    """
    merged: dict[str, dict] = {}
    for batch in batches:
        for raw in batch:
            zh = (raw.get("zh") or "").strip()
            if not zh:
                continue
            existing = merged.setdefault(zh, {"zh": zh})
            for field in ("pinyin", "vi", "en", "note"):
                value = (raw.get(field) or "").strip()
                if len(value) > len(existing.get(field, "")):
                    existing[field] = value
            if raw.get("type"):
                existing.setdefault("type", raw["type"])
            if raw.get("keep_source"):
                existing["keep_source"] = True

    out: list[GlossaryTerm] = []
    for data in merged.values():
        try:
            out.append(GlossaryTerm.model_validate(data))
        except Exception:  # noqa: BLE001 - a malformed entry must not sink the job
            log.warning("S3: bỏ qua entry hỏng: %r", data)
    out.sort(key=lambda t: (t.type, t.zh))
    return out


def build_glossary(
    segments_doc: SegmentsDoc, provider: LLMProvider | None, max_chars: int = 3000
) -> GlossaryDoc:
    if provider is None:
        log.warning("S3: không có LLM — sinh glossary rỗng, người dùng tự điền")
        return GlossaryDoc()

    blocks = _batches(segments_doc.segments, max_chars)
    log.info("S3: quét %d đoạn transcript", len(blocks))

    collected: list[list[dict]] = []
    for i, block in enumerate(blocks):
        try:
            data = provider.complete_json(TERMS_SYSTEM, block)
        except LLMError as exc:
            log.warning("S3: đoạn %d/%d lỗi: %s — bỏ qua", i + 1, len(blocks), exc)
            continue
        terms = data.get("terms") if isinstance(data, dict) else data
        if isinstance(terms, list):
            collected.append(terms)

    terms = _merge_terms(collected)
    log.info("S3: %d thuật ngữ sau khi hợp nhất", len(terms))

    style, address = _extract_style(segments_doc, terms, provider)
    return GlossaryDoc(terms=terms, address_terms=address, style=style)


def _extract_style(
    segments_doc: SegmentsDoc, terms: list[GlossaryTerm], provider: LLMProvider
) -> tuple[StyleDecision, list[AddressTerm]]:
    """Decide Vietnamese address terms once, for the whole video.

    Only a sample of the transcript is sent: register and relationships are
    established early and stay put, so paying for the full text buys nothing.
    """
    people = [t.zh for t in terms if t.type == "person"][:20]
    sample = "".join(s.text_zh for s in segments_doc.segments[:120])[:3000]
    user = f"Nhân vật đã biết: {', '.join(people) if people else '(chưa có)'}\n\nTranscript:\n{sample}"

    try:
        data = provider.complete_json(STYLE_SYSTEM, user)
    except LLMError as exc:
        log.warning("S3: không phân tích được văn phong: %s", exc)
        return StyleDecision(), []

    style = StyleDecision()
    if isinstance(data, dict) and isinstance(data.get("style"), dict):
        try:
            style = StyleDecision.model_validate(data["style"])
        except Exception:  # noqa: BLE001
            log.warning("S3: style hỏng: %r", data.get("style"))

    address: list[AddressTerm] = []
    if isinstance(data, dict):
        for raw in data.get("address_terms") or []:
            try:
                address.append(AddressTerm.model_validate(raw))
            except Exception:  # noqa: BLE001
                log.warning("S3: bỏ qua xưng hô hỏng: %r", raw)
    return style, address


def run(work_dir, cfg: Config, force: bool = False) -> GlossaryDoc:
    work_dir = Path(work_dir)
    out_json = work_dir / "glossary.json"
    # Never overwrite without being asked: this file is the user's edit surface, and
    # silently regenerating it would throw away their corrections.
    if out_json.is_file() and not force:
        log.info("S3: đã có glossary.json, giữ nguyên (dùng --force để sinh lại)")
        return read_doc(out_json, GlossaryDoc)

    segments_doc = read_doc(work_dir / "segments.json", SegmentsDoc)

    provider = None
    try:
        from ..llm.factory import build_provider

        provider = build_provider(cfg.llm.translate, "translate")
    except Exception as exc:
        log.warning("S3: không dựng được LLM (%s)", exc)

    doc = build_glossary(segments_doc, provider)
    write_doc(out_json, doc)
    log.info("S3 xong: %d thuật ngữ, %d cặp xưng hô", len(doc.terms), len(doc.address_terms))
    return doc
