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
        lines.append("GLOSSARY — use these renderings exactly:")
        for t in glossary.terms:
            target = t.zh if t.keep_source else (t.vi if lang == "vi" else t.en)
            if not target:
                continue
            note = f"  ({t.note})" if t.note else ""
            lines.append(f"  {t.zh} [{t.pinyin}] -> {target}{note}")

    if lang == "vi":
        style = glossary.style
        if style.speech_register or style.narrator_self_vi or style.audience_vi:
            lines.append("\nREGISTER AND ADDRESS:")
            if style.speech_register:
                lines.append(f"  tone: {style.speech_register}")
            if style.narrator_self_vi:
                lines.append(f"  narrator refers to themselves as: {style.narrator_self_vi}")
            if style.audience_vi:
                lines.append(f"  narrator addresses the audience as: {style.audience_vi}")
        if glossary.address_terms:
            lines.append("\nADDRESS TERMS BETWEEN CHARACTERS (keep consistent for the whole video):")
            for a in glossary.address_terms:
                lines.append(
                    f"  {a.speaker} speaking to {a.addressee}: self '{a.vi_self}', other '{a.vi_other}'"
                )
    return "\n".join(lines)


def build_system_prompt(glossary: GlossaryDoc, lang: str) -> str:
    """System prompt, byte-identical for every batch of a job.

    Stability is deliberate: it is what lets prompt caching make a large glossary
    almost free from the second batch onward.

    Written in English, and so are the JSON keys, even though the product and its
    users are Vietnamese. Measured on qwen2.5-7b via Ollama: a Vietnamese-language
    version of this prompt made the model copy-edit the Chinese instead of
    translating it, on 36 of 36 segments. The same model handed an English prompt
    translated correctly. The phrase that most likely did it was an instruction to
    "keep short sentences short" — read as "keep the text as it is". Small models
    follow English instructions far more reliably, so the instruction language is
    an engineering choice, not a stylistic one.
    """
    lang_name = "VIETNAMESE" if lang == "vi" else "ENGLISH"
    script_hint = (
        "Vietnamese uses the Latin alphabet with diacritics (e.g. \"vượt ngục\")."
        if lang == "vi"
        else "English uses the Latin alphabet."
    )
    return f"""You are a professional subtitle translator. You translate Chinese into {lang_name}.

The user message has two parts: a plain-text SCENE CONTEXT block for you to read,
and a JSON object. Translate every entry in the JSON and nothing else. The scene
context is background only — never translate it, never return it.

ABSOLUTE RULES:
1. Output exactly ONE object per input object, carrying the SAME "id".
   Never merge two entries into one. Never split one entry into two.
   The id set must match 100%. Subtitle timing depends on this.
2. The "translation" value MUST be written in {lang_name}. {script_hint}
   NEVER copy Chinese characters into "translation". If the source is already
   short, the translation is still {lang_name}, not the original text.
3. Translate the meaning naturally, the way a subtitle is written. Do not
   translate word by word, and do not add information that is not in the source.
4. Respect each entry's "max_chars" budget. Subtitles are condensed: drop filler
   words and redundant connectives rather than exceed it. Never drop actual
   meaning to fit.
5. Do not wrap the result in quotes and do not add notes or explanations.

{_glossary_block(glossary, lang)}

Return ONLY this JSON, nothing else:
{{"translations": [{{"id": 0, "translation": "..."}}]}}"""


REVIEW_SUFFIX = """

THIS IS THE REVIEW PASS. Each entry now also carries "draft", your own first
attempt. Inspect it and fix:
- stiff, word-by-word phrasing that no native speaker would write
- terminology that contradicts the glossary above
- pronouns or forms of address that are wrong or inconsistent with nearby lines
- meaning that does not match the Chinese source

Rewrite only what needs it; keep a good draft as it is. Every rule above still
applies, especially: the output must be in the target language, never Chinese,
and exactly one object per input object with the same id."""


def char_budget(segment: Segment, cfg: Config, lang: str) -> int:
    """How many characters this cue can actually display.

    Two limits in the spec disagree and the tighter one has to win: a 7s cue allows
    147 characters at 21 CPS, but only 84 fit in two 42-character lines. Measured on
    a real clip, 24 of 35 Vietnamese translations overflowed because only the CPS
    limit was being considered. Budgeting at generation time is the honest fix —
    trimming afterwards would mean deleting meaning, and splitting the cue would
    break the one-segment-one-cue rule.
    """
    limits = cfg.render.for_lang(lang)
    by_lines = limits.max_chars_per_line * cfg.render.max_lines
    by_rate = int((segment.end - segment.start) * limits.max_cps)
    return max(20, min(by_lines, by_rate))


def _build_user_message(
    batch: list[Segment],
    before: list[Segment],
    after: list[Segment],
    drafts: dict[int, str] | None = None,
    budgets: dict[int, int] | None = None,
) -> str:
    # Context is kept OUTSIDE the JSON on purpose. Measured on qwen2.5-7b: with
    # context as sibling keys inside the same object, 14 of 20 outputs came back as
    # untranslated Chinese — the model could not tell the read-only material from
    # the material to translate, and echoed whichever it saw last. Moving context
    # into a separate plain-text block above the JSON removed the confusion.
    parts: list[str] = []
    if before or after:
        parts.append("SCENE CONTEXT — background only, do NOT translate, do NOT return:")
        for s in before:
            parts.append(f"  ... {s.text_zh}")
        parts.append("  >>> the lines to translate belong here <<<")
        for s in after:
            parts.append(f"  ... {s.text_zh}")
        parts.append("")

    entries: list[dict] = []
    for s in batch:
        entry: dict = {"id": s.id, "text": s.text_zh}
        if budgets:
            entry["max_chars"] = budgets[s.id]
        if drafts:
            entry["draft"] = drafts[s.id]
        entries.append(entry)
    payload = {"to_translate": entries}
    parts.append("TRANSLATE THIS JSON:")
    parts.append(json.dumps(payload, ensure_ascii=False, indent=1))
    return "\n".join(parts)


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


def cjk_ratio(text: str) -> float:
    """Fraction of Han characters in a string."""
    if not text:
        return 0.0
    return sum(1 for ch in text if "一" <= ch <= "鿿") / len(text)


def looks_untranslated(text: str) -> bool:
    """Whether a translation still carries Chinese it should not.

    A ratio alone is too lenient: an otherwise-Vietnamese line ending in
    "hoang诞无比" scores only 0.13 and would slip through, yet it is plainly a
    failure. In a Vietnamese or English subtitle any run of Han characters is wrong
    unless it came from a ``keep_source`` glossary entry, and those are short — so a
    small absolute count catches the leaks a ratio misses.
    """
    han = sum(1 for ch in text if "一" <= ch <= "鿿")
    return han >= 3 or (len(text) > 0 and han / len(text) > 0.3)


def _translate_batch(
    provider: LLMProvider,
    system: str,
    batch: list[Segment],
    before: list[Segment],
    after: list[Segment],
    cfg: Config,
    drafts: dict[int, str] | None = None,
    budgets: dict[int, int] | None = None,
) -> dict[int, str]:
    """One batch through the model, retrying then splitting on id mismatch."""
    expected = {s.id for s in batch}
    last_error: Exception | None = None

    for attempt in range(cfg.translate.max_retries):
        try:
            data = provider.complete_json(
                system, _build_user_message(batch, before, after, drafts, budgets)
            )
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
        left = _translate_batch(
            provider, system, batch[:mid], before, batch[mid:] + after, cfg, drafts, budgets
        )
        right = _translate_batch(
            provider, system, batch[mid:], before + batch[:mid], after, cfg, drafts, budgets
        )
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

        budgets = {s.id: char_budget(s, cfg, lang) for s in batch}
        drafts = _translate_batch(provider, system, batch, before, after, cfg, budgets=budgets)

        # A translation that is still mostly Han characters means the model echoed the
        # source instead of translating it. Retrying just those entries is far cheaper
        # than re-running the batch, and leaving them through would ship Chinese
        # subtitles labelled Vietnamese.
        leaked = [s for s in batch if looks_untranslated(drafts.get(s.id, ""))]
        if leaked:
            log.warning("S4[%s]: %d câu chưa được dịch, gọi lại riêng", lang, len(leaked))
            try:
                redone = _translate_batch(
                    provider, system, leaked, before, after, cfg, budgets=budgets
                )
                drafts.update({k: v for k, v in redone.items() if not looks_untranslated(v)})
            except (TranslationFailure, LLMError) as exc:
                log.warning("S4[%s]: gọi lại không cứu được: %s", lang, exc)

        final = drafts
        if cfg.translate.review_pass:
            try:
                final = _translate_batch(
                    provider, review_system, batch, before, after, cfg, drafts, budgets
                )
                # The review pass can regress a good draft back into Chinese; keep
                # whichever version actually is the target language.
                final = {
                    k: (drafts[k]
                        if looks_untranslated(v) and not looks_untranslated(drafts.get(k, ""))
                        else v)
                    for k, v in final.items()
                }
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
