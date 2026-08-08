"""S4 — translate segments into Vietnamese / English.

Two invariants hold the timeline together and both are enforced, not hoped for:

* **One segment produces exactly one translation.** No merging two segments into a
  sentence, no splitting one across two. Every cue's timing is inherited from its
  segment, so any change in cardinality desynchronises everything after it.
* **The set of ids that comes back equals the set that went out.** A missing or
  extra id triggers a retry, then a batch split, and finally a hard failure —
  never a blank line quietly filling the gap.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Config
from ..jsonio import read_doc, sha256_json_canonical, write_doc
from ..llm.base import LLMError, LLMProvider
from ..llm.cache import TranslationCache
from ..models import GlossaryDoc, Segment, SegmentsDoc, TranslationItem, TranslationsDoc

log = logging.getLogger(__name__)

# Bump whenever a prompt below changes, or stale cache entries get served silently.
PROMPT_VERSION = 1

LANG_NAMES = {"vi": "tiếng Việt", "en": "tiếng Anh"}


class TranslationFailure(RuntimeError):
    """Raised when a batch cannot be translated with the id set intact."""


def compute_segments_hash(segments_doc: SegmentsDoc) -> str:
    """Hash only what a translation actually depends on: the ids and their text.

    Timings are excluded on purpose — a cue that merely shifted does not need
    re-translating, and including them would throw away the cache on every
    re-render.
    """
    return sha256_json_canonical([[s.id, s.text_zh] for s in segments_doc.segments])


def _glossary_block(glossary: GlossaryDoc, lang: str) -> str:
    lines: list[str] = []
    if glossary.terms:
        lines.append("THUẬT NGỮ BẮT BUỘC DÙNG ĐÚNG:")
        for t in glossary.terms:
            target = t.zh if t.keep_source else (t.vi if lang == "vi" else t.en)
            if not target:
                continue
            note = f"  ({t.note})" if t.note else ""
            lines.append(f"  {t.zh} [{t.pinyin}] -> {target}{note}")

    if lang == "vi":
        style = glossary.style
        if style.speech_register or style.narrator_self_vi or style.audience_vi:
            lines.append("\nGIỌNG ĐIỆU VÀ XƯNG HÔ:")
            if style.speech_register:
                lines.append(f"  giọng điệu: {style.speech_register}")
            if style.narrator_self_vi:
                lines.append(f"  người kể tự xưng: {style.narrator_self_vi}")
            if style.audience_vi:
                lines.append(f"  gọi khán giả: {style.audience_vi}")
        if glossary.address_terms:
            lines.append("\nXƯNG HÔ GIỮA NHÂN VẬT (giữ nhất quán cả video):")
            for a in glossary.address_terms:
                lines.append(f"  {a.speaker} nói với {a.addressee}: tự xưng '{a.vi_self}', gọi '{a.vi_other}'")
    return "\n".join(lines)


def build_system_prompt(glossary: GlossaryDoc, lang: str) -> str:
    """System prompt, byte-identical for every batch of a job.

    Stability is deliberate: it is what lets prompt caching make a large glossary
    almost free from the second batch onward.
    """
    lang_name = LANG_NAMES[lang]
    return f"""Bạn là dịch giả phụ đề chuyên nghiệp, dịch từ tiếng Trung sang {lang_name}.

Người dùng đưa một mảng JSON các câu cần dịch, cùng một ít câu phía trước và phía
sau chỉ để bạn hiểu ngữ cảnh.

QUY TẮC TUYỆT ĐỐI:
1. Mỗi phần tử input phải có ĐÚNG MỘT phần tử output với cùng "id".
2. KHÔNG gộp hai câu thành một. KHÔNG tách một câu thành hai. Số lượng và tập id
   phải khớp 100%. Đây là ràng buộc cứng vì timeline phụ đề phụ thuộc vào nó.
3. Chỉ dịch phần trong "cần dịch". Phần ngữ cảnh chỉ để đọc, KHÔNG dịch.
4. Dịch tự nhiên, đúng nghĩa, đúng văn phong phụ đề. Không dịch máy móc từng chữ.
5. Giữ nguyên câu ngắn là câu ngắn. Không thêm thông tin không có trong bản gốc.
6. Không thêm dấu ngoặc kép bao quanh, không thêm chú thích.

{_glossary_block(glossary, lang)}

CHỈ trả về JSON, không kèm giải thích:
{{"translations": [{{"id": 0, "translation": "..."}}]}}"""


REVIEW_SUFFIX = """

Đây là lượt RÀ SOÁT. Người dùng đưa bản dịch thô của chính bạn. Hãy soi và chỉnh:
- Chỗ nào dịch cứng, dịch word-by-word, đọc lên không giống tiếng Việt tự nhiên?
- Chỗ nào sai thuật ngữ so với danh sách bắt buộc ở trên?
- Chỗ nào xưng hô sai hoặc không nhất quán với các câu xung quanh?
- Chỗ nào sai nghĩa so với bản tiếng Trung?

Sửa những chỗ đó. Câu nào đã tốt thì giữ nguyên. Ràng buộc về id vẫn y nguyên:
đúng một output cho mỗi input, cùng tập id."""


def _build_user_message(
    batch: list[Segment],
    before: list[Segment],
    after: list[Segment],
    drafts: dict[int, str] | None = None,
) -> str:
    payload: dict = {}
    if before:
        payload["ngữ_cảnh_trước"] = [s.text_zh for s in before]
    payload["cần_dịch"] = [
        ({"id": s.id, "text": s.text_zh, "bản_dịch_thô": drafts[s.id]} if drafts else
         {"id": s.id, "text": s.text_zh})
        for s in batch
    ]
    if after:
        payload["ngữ_cảnh_sau"] = [s.text_zh for s in after]
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _parse_translations(data, expected_ids: set[int]) -> dict[int, str]:
    """Extract ``{id: translation}`` and verify the id set matches exactly."""
    items = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise TranslationFailure(f"output không phải mảng: {type(items).__name__}")

    out: dict[int, str] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        try:
            key = int(raw["id"])
        except (KeyError, TypeError, ValueError):
            continue
        text = (raw.get("translation") or "").strip()
        if text:
            out[key] = text

    got = set(out)
    if got != expected_ids:
        missing = sorted(expected_ids - got)
        extra = sorted(got - expected_ids)
        raise TranslationFailure(f"lệch id — thiếu {missing[:8]}, thừa {extra[:8]}")
    return out


def _translate_batch(
    provider: LLMProvider,
    system: str,
    batch: list[Segment],
    before: list[Segment],
    after: list[Segment],
    cfg: Config,
    drafts: dict[int, str] | None = None,
) -> dict[int, str]:
    """One batch through the model, retrying then splitting on id mismatch."""
    expected = {s.id for s in batch}
    last_error: Exception | None = None

    for attempt in range(cfg.translate.max_retries):
        try:
            data = provider.complete_json(system, _build_user_message(batch, before, after, drafts))
            return _parse_translations(data, expected)
        except (TranslationFailure, LLMError) as exc:
            last_error = exc
            log.warning(
                "S4: batch %d câu lỗi (lần %d/%d): %s",
                len(batch), attempt + 1, cfg.translate.max_retries, exc,
            )

    if len(batch) > 1:
        # Halve and retry: a single awkward segment usually poisons the whole batch,
        # and bisecting isolates it instead of failing everything around it.
        mid = len(batch) // 2
        log.warning("S4: chia đôi batch %d câu sau %d lần thất bại", len(batch), cfg.translate.max_retries)
        left = _translate_batch(provider, system, batch[:mid], before, batch[mid:] + after, cfg, drafts)
        right = _translate_batch(provider, system, batch[mid:], before + batch[:mid], after, cfg, drafts)
        return {**left, **right}

    raise TranslationFailure(
        f"Không dịch được segment id={batch[0].id} sau khi đã chia nhỏ tối đa: {last_error}. "
        f"Text: {batch[0].text_zh[:60]!r}"
    )


def translate_segments(
    segments: list[Segment],
    lang: str,
    provider: LLMProvider,
    glossary: GlossaryDoc,
    cfg: Config,
    cache: TranslationCache | None = None,
    glossary_hash: str = "",
) -> list[TranslationItem]:
    system = build_system_prompt(glossary, lang)
    review_system = system + REVIEW_SUFFIX
    by_id = {s.id: s for s in segments}
    results: dict[int, TranslationItem] = {}

    pending: list[Segment] = []
    for seg in segments:
        cached = None
        if cache is not None:
            key = cache.make_key(seg.text_zh, lang, provider.model, glossary_hash, cfg.translate.prompt_version)
            cached = cache.get(key)
        if cached is not None:
            results[seg.id] = TranslationItem(
                id=seg.id, text_zh=seg.text_zh, translation=cached, reviewed=True, cache_hit=True
            )
        else:
            pending.append(seg)

    if cache is not None:
        log.info("S4[%s]: %d/%d câu lấy từ cache", lang, len(results), len(segments))

    size = cfg.translate.batch_size
    for start in range(0, len(pending), size):
        batch = pending[start : start + size]
        first_index = segments.index(batch[0])
        last_index = segments.index(batch[-1])
        before = segments[max(0, first_index - cfg.translate.context_before) : first_index]
        after = segments[last_index + 1 : last_index + 1 + cfg.translate.context_after]

        drafts = _translate_batch(provider, system, batch, before, after, cfg)

        final = drafts
        if cfg.translate.review_pass:
            try:
                final = _translate_batch(provider, review_system, batch, before, after, cfg, drafts)
            except (TranslationFailure, LLMError) as exc:
                # The draft is already valid and id-complete; losing the polish pass
                # is far better than losing the batch.
                log.warning("S4[%s]: lượt rà soát hỏng (%s) — giữ bản thô", lang, exc)
                final = drafts

        for seg in batch:
            results[seg.id] = TranslationItem(
                id=seg.id,
                text_zh=seg.text_zh,
                draft=drafts.get(seg.id, ""),
                translation=final[seg.id],
                reviewed=cfg.translate.review_pass and final is not drafts,
            )
            if cache is not None:
                key = cache.make_key(
                    seg.text_zh, lang, provider.model, glossary_hash, cfg.translate.prompt_version
                )
                cache.put(key, final[seg.id])
        if cache is not None:
            # Flush per batch so a crash keeps everything already paid for.
            cache.flush()
        log.info("S4[%s]: %d/%d câu", lang, len(results), len(segments))

    missing = set(by_id) - set(results)
    if missing:
        raise TranslationFailure(f"Thiếu bản dịch cho id: {sorted(missing)[:10]}")
    return [results[i] for i in sorted(results)]


def run(work_dir, cfg: Config, langs: list[str], force: bool = False) -> dict[str, TranslationsDoc]:
    work_dir = Path(work_dir)
    segments_doc = read_doc(work_dir / "segments.json", SegmentsDoc)
    glossary_path = work_dir / "glossary.json"
    glossary = read_doc(glossary_path, GlossaryDoc) if glossary_path.is_file() else GlossaryDoc()
    glossary_hash = sha256_json_canonical(glossary.model_dump(by_alias=True, mode="json"))

    segments_hash = compute_segments_hash(segments_doc)

    from ..llm.factory import build_provider

    provider = build_provider(cfg.llm.translate, "translate")
    cache = TranslationCache(cfg.paths.cache_dir)

    out: dict[str, TranslationsDoc] = {}
    for lang in langs:
        out_json = work_dir / f"translations.{lang}.json"
        if out_json.is_file() and not force:
            existing = read_doc(out_json, TranslationsDoc)
            # Re-running S2 renumbers and re-splits segments, so anything translated
            # against the old segmentation is stale. Reusing it would break the 1-1
            # relation the timeline depends on. The text cache still absorbs the
            # cost: only genuinely changed segments are paid for again.
            if existing.segments_hash == segments_hash:
                out[lang] = existing
                continue
            log.info("S4[%s]: segments.json đã đổi — dịch lại (cache vẫn dùng được)", lang)

        items = translate_segments(
            segments_doc.segments, lang, provider, glossary, cfg, cache, glossary_hash
        )
        doc = TranslationsDoc(
            lang=lang,
            model=provider.model,
            prompt_version=cfg.translate.prompt_version,
            glossary_hash=glossary_hash,
            segments_hash=segments_hash,
            items=items,
        )
        write_doc(out_json, doc)
        out[lang] = doc
        log.info("S4[%s] xong: %d bản dịch", lang, len(items))

    cache.flush()
    return out
