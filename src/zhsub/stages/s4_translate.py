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
from ..progress import RunContext, ensure_context

log = logging.getLogger(__name__)

# Bump whenever a prompt below changes, or stale cache entries get served silently.
PROMPT_VERSION = 1

LANG_NAMES = {"vi": "tiếng Việt", "en": "tiếng Anh"}


class TranslationFailure(RuntimeError):
    """Raised when a batch cannot be translated with the id set intact."""


def _staleness(
    existing: TranslationsDoc,
    segments_hash: str,
    glossary_hash: str,
    model: str,
    cfg: Config,
) -> list[str]:
    """Name every input that changed since ``existing`` was produced.

    Returning the reasons rather than a bare bool so the log says why a re-run cost
    tokens — silence there is how a stale file goes unnoticed.
    """
    reasons: list[str] = []
    if existing.segments_hash != segments_hash:
        reasons.append("segments.json")
    if existing.glossary_hash != glossary_hash:
        reasons.append("glossary.json")
    if existing.model != model:
        reasons.append("model")
    if existing.prompt_version != cfg.translate.prompt_version:
        reasons.append("prompt_version")
    return reasons


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
        if (
            style.speech_register
            or style.narrator_self_vi
            or style.audience_vi
            or style.subject_third_person_vi
        ):
            lines.append("\nREGISTER AND ADDRESS:")
            if style.speech_register:
                lines.append(f"  tone: {style.speech_register}")
            if style.narrator_self_vi:
                lines.append(f"  narrator refers to themselves as: {style.narrator_self_vi}")
            if style.audience_vi:
                lines.append(f"  narrator addresses the audience as: {style.audience_vi}")
            if style.subject_third_person_vi:
                # Rule 3 lists several acceptable third-person forms, which leaves each
                # batch free to pick again; naming one here is what actually holds it
                # steady across a whole video.
                lines.append(
                    f"  narration calls the main subject: {style.subject_third_person_vi}"
                    f" — use this every time, never switch to another third-person form"
                )
        if glossary.address_terms:
            # Scope this hard. Injected as a blanket rule, the model applies it to
            # narration as well and flips the grammatical person: 他进行了一番伪装
            # ("he disguised himself") came back as "mình đã cải trang" ("I disguised
            # myself"), and 警方 ("the police") became "các anh" ("you"). That is
            # worse than having no address terms at all.
            lines.append(
                "\nADDRESS TERMS — apply ONLY inside direct speech between these two"
                " characters, and keep them consistent for the whole video:"
            )
            for a in glossary.address_terms:
                lines.append(
                    f"  when {a.speaker} speaks TO {a.addressee}: {a.speaker} says"
                    f" '{a.vi_self}' for themselves and '{a.vi_other}' for {a.addressee}"
                )
            lines.append(
                "  NEVER use these in narration. Narration about a character stays in"
                " the third person: 他 is 'anh ta' / 'ông ta', never 'mình' or 'tôi'."
                " Keep the grammatical person of the Chinese source exactly."
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
3. Preserve the grammatical PERSON of the source exactly. Narration about someone
   stays third person: 他/她 is "anh ta" / "cô ta" / "ông ấy", NEVER "mình" or
   "tôi". Only render first person where the Chinese itself is first person
   (我/我们). Getting this wrong turns a documentary into a confession, so check it
   on every line.
4. Translate the meaning naturally, the way a subtitle is written. Do not
   translate word by word, and do not add information that is not in the source.
5. Carry the FORCE of idioms, chengyu and slang, not their dictionary gloss. These
   words are where the writer's voice lives, and a flat paraphrase drains the line:
     扛把子   = the top dog / the kingpin — NOT "a notable figure"
     脑洞大开 = a wild, out-of-nowhere idea — NOT "creative"
     离谱     = ridiculous, off the rails — NOT "unusual"
     心服口服 = won over completely, no argument left
   Prefer a Vietnamese expression with the same punch. If none fits, write plain
   Vietnamese that lands as hard — never a limp encyclopedia phrasing.
6. Match the register of the source. This is spoken commentary: when the Chinese is
   colloquial, the translation is colloquial. Formal wording on casual narration
   reads as stiff and wrong.
7. "max_chars" is a hard limit, not a suggestion. Count the characters of each
   translation before returning it; if it is over, cut filler words and redundant
   connectives and count again. Subtitles are condensed. Never drop actual meaning
   to fit — rephrase shorter instead.
8. Do not wrap the result in quotes and do not add notes or explanations.

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

- any line longer than its own "max_chars"

Rewrite only what needs it; keep a good draft as it is. A revision must never be
longer than the draft it replaces unless the draft was missing meaning: measured,
this pass is where "max_chars" violations grow fastest. Every rule above still
applies, especially: the output must be in the target language, never Chinese,
and exactly one object per input object with the same id."""


# Fraction of the real display limit advertised to the model. 0.9 covers the
# measured ~8% average overshoot; going tighter would start costing meaning, which
# rule 7 forbids trading away.
_BUDGET_SAFETY = 0.9


def char_budget(segment: Segment, cfg: Config, lang: str) -> int:
    """How many characters this cue can actually display.

    Two limits in the spec disagree and the tighter one has to win: a 7s cue allows
    147 characters at 21 CPS, but only 84 fit in two 42-character lines. Measured on
    a real clip, 24 of 35 Vietnamese translations overflowed because only the CPS
    limit was being considered. Budgeting at generation time is the honest fix —
    trimming afterwards would mean deleting meaning, and splitting the cue would
    break the one-segment-one-cue rule.

    The number handed to the model is deliberately below the real ceiling. Sending
    the ceiling itself leaves no slack at all — S5 warns at the same CPS the budget
    is derived from, so overshooting by one character is already a warning. Measured
    on the 472-line clip: translations ran 7.1 characters over on budgets around 86,
    roughly 8%, and the share of over-budget lines climbed 6% -> 15% as the
    translation quality work went in. A margin absorbs that drift without asking the
    model to hit an exact ceiling, which no amount of prompt wording achieved.
    """
    limits = cfg.render.for_lang(lang)
    by_lines = limits.max_chars_per_line * cfg.render.max_lines
    by_rate = int((segment.end - segment.start) * limits.max_cps)
    return max(20, int(min(by_lines, by_rate) * _BUDGET_SAFETY))


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


def looks_untranslated(text: str, keep_source: tuple[str, ...] = ()) -> bool:
    """Whether a translation still carries Chinese it should not.

    In a Vietnamese or English subtitle **any** Han character is wrong unless it
    came from a ``keep_source`` glossary entry, so those are removed first and
    anything left over counts.

    A ratio threshold is far too lenient here. Measured on real qwen2.5-7b output:
    "hoang诞无比" scores 0.13, and the model's most common failure is a single
    character welded into a Vietnamese word — "trốn狱" — which no ratio or
    small-count rule would ever catch.
    """
    for term in keep_source:
        if term:
            text = text.replace(term, "")
    return any("一" <= ch <= "鿿" for ch in text)


def apply_glossary(text: str, glossary: GlossaryDoc, lang: str) -> str:
    """Force any glossary term the model left in Chinese to its defined rendering.

    The glossary is the authority on these strings, so substituting is not a patch
    over a bad translation — it is the definition being applied. Observed with
    gpt-4o-mini: the model produced good Vietnamese but copied 俄克拉荷马州 and
    美国空军 verbatim even though both were defined right there in its own prompt.

    Longest terms first, so a term containing another does not get half-replaced.
    """
    for term in sorted(glossary.terms, key=lambda t: len(t.zh), reverse=True):
        if term.keep_source or not term.zh:
            continue
        target = term.vi if lang == "vi" else term.en
        if not target or term.zh not in text:
            continue
        # A rendering that embeds the source form — FBI -> "Cục Điều tra Liên bang
        # Mỹ (FBI)" — nests inside itself when the model has already applied the
        # glossary line and this function applies it again. Seen on a real run:
        # "Cục Điều tra Liên bang Mỹ (Cục Điều tra Liên bang Mỹ (FBI))", 102
        # characters on a 2.3s cue whose budget was 48. Collapsing any finished
        # rendering back to the source form first makes the substitution idempotent.
        if term.zh in target:
            text = text.replace(target, term.zh)
        text = text.replace(term.zh, target)
    return text


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
    ctx: RunContext | None = None,
) -> list[TranslationItem]:
    ctx = ensure_context(ctx)
    system = build_system_prompt(glossary, lang)
    review_system = system + REVIEW_SUFFIX
    keep_source = tuple(t.zh for t in glossary.terms if t.keep_source)
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
        # Between batches: safe to stop. Everything already translated is in the
        # cache, so cancelling here costs nothing already paid for.
        ctx.report(len(results) / max(len(segments), 1), f"{lang}: {len(results)}/{len(segments)} câu")
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
        leaked = [s for s in batch if looks_untranslated(drafts.get(s.id, ""), keep_source)]
        if leaked:
            log.warning("S4[%s]: %d câu chưa được dịch, gọi lại riêng", lang, len(leaked))
            try:
                redone = _translate_batch(
                    provider, system, leaked, before, after, cfg, budgets=budgets
                )
                drafts.update(
                    {k: v for k, v in redone.items() if not looks_untranslated(v, keep_source)}
                )
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
                        if looks_untranslated(v, keep_source)
                        and not looks_untranslated(drafts.get(k, ""), keep_source)
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

    # Applied here, over both fresh and cached entries, rather than inside the batch
    # loop. The cache holds raw model output, so substituting on read means editing
    # glossary.json changes the result without paying to call the model again — and
    # a cache hit can no longer smuggle an untranslated term past the check.
    for item in results.values():
        item.translation = apply_glossary(item.translation, glossary, lang)

    return [results[i] for i in sorted(results)]


def run(work_dir, cfg: Config, langs: list[str], force: bool = False,
        ctx: RunContext | None = None) -> dict[str, TranslationsDoc]:
    work_dir = Path(work_dir)
    segments_doc = read_doc(work_dir / "segments.json", SegmentsDoc)
    glossary_path = work_dir / "glossary.json"
    glossary = read_doc(glossary_path, GlossaryDoc) if glossary_path.is_file() else GlossaryDoc()
    glossary_hash = sha256_json_canonical(glossary.model_dump(by_alias=True, mode="json"))

    segments_hash = compute_segments_hash(segments_doc)

    from ..llm.factory import build_provider

    provider = build_provider(cfg.llm, "translate")
    cache = TranslationCache(cfg.paths.cache_dir)

    out: dict[str, TranslationsDoc] = {}
    for lang in langs:
        out_json = work_dir / f"translations.{lang}.json"
        if out_json.is_file() and not force:
            existing = read_doc(out_json, TranslationsDoc)
            # Every input that can change a translation has to be compared, not just
            # the segments. Checking segmentation alone meant that hand-editing
            # glossary.json and running `resume --from translate` silently returned
            # the old file — defeating the whole point of the glossary edit loop.
            # The per-segment cache still absorbs the cost: only entries whose text,
            # glossary, model or prompt actually changed are paid for again.
            stale = _staleness(existing, segments_hash, glossary_hash, provider.model, cfg)
            if not stale:
                out[lang] = existing
                continue
            log.info("S4[%s]: %s đã đổi — dịch lại", lang, " và ".join(stale))

        items = translate_segments(
            segments_doc.segments, lang, provider, glossary, cfg, cache, glossary_hash, ctx=ctx
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
