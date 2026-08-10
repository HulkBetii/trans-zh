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

TERMS_SYSTEM = """You extract a translation glossary from a Chinese transcript.

Extract only:
- character names (person)
- place names (place)
- organisations, agencies, companies (org)
- specialist terminology a general audience would not know (term)
- dish names (dish)
- product and brand names (product)

For each entry give the Chinese form, pinyin with tone marks, a Vietnamese
rendering and an English rendering.

THE INCLUSION TEST — apply it to every candidate before adding it:

  "Would a competent translator, with no glossary at all, plausibly render this
   differently from one line to the next in a way that would confuse a viewer?"

  If no, LEAVE IT OUT. Only entries that pass belong in the glossary.

A telling sign a word fails the test: it is listed in an ordinary dictionary and
its translation varies with grammar. 监狱 (prison), 越狱 (to escape prison), 美国
(America / American / the US), 警察 (police), 逃跑 (to flee) all fail — they are
everyday vocabulary. Forcing one fixed rendering on them makes the translation
WORSE, because the natural wording differs by sentence. A specific named prison
passes. A generic "prison" does not.

Aim for a short, high-value list. Twenty precise entries beat eighty padded ones.

OTHER RULES:
- A foreign name transliterated into Chinese must be restored to its original form
  in "en" (弗兰克 -> Frank, not "Fulanke"). Get real people and places right.
- "vi" must be VIETNAMESE, not a copy of the English. Bare foreign proper nouns do
  stay as they are (North Dakota, Richard McNair), but a descriptive name has to be
  translated: 弗罗伦斯联邦监狱 -> "Nhà tù liên bang Florence", NOT "Florence Federal
  Correctional Institution"; 魔鬼岛 -> "Đảo Quỷ", NOT "Devil's Island". If "vi" and
  "en" come out identical, the entry must be a bare proper noun — otherwise you got
  it wrong.
- "keep_source" must be false for almost everything. Set it true ONLY when the
  term has to appear on screen in Chinese characters, and then leave both "vi" and
  "en" empty. If you provide a translation, keep_source MUST be false. Names,
  places and organisations are essentially always false.

Return ONLY this JSON, no explanation:
{"terms": [{"zh": "...", "pinyin": "...", "vi": "...", "en": "...",
            "type": "person|place|org|term|dish|product|other",
            "keep_source": false, "note": ""}]}"""

# English, same measured reason as the S2 and S4 prompts. The Vietnamese version of
# this prompt returned an entirely empty style block on a 35-minute narration that
# plainly has a register and addresses its audience directly.
STYLE_SYSTEM = """You decide Vietnamese forms of address for subtitling a Chinese video.

The user sends a Chinese transcript plus any character names already identified.
Vietnamese has no neutral "you" or "I": every line commits to a relationship, so
these choices must be made once and held for the whole video.

Decide, and answer in VIETNAMESE inside the JSON values:

1. "speech_register" — the overall tone in a few words (formal documentary,
   casual storytelling, comedic, ...).
2. "narrator_self_vi" — what the narrator calls themselves. Fill this in for ANY
   video with a narrator or presenter, including documentaries. Common choices:
   "mình", "tôi", "chúng ta", "chúng mình".
3. "audience_vi" — how the narrator addresses the viewer. Almost every online
   video has one. Common choices: "các bạn", "mọi người", "anh em".
4. "address_terms" — one entry per pair of characters who speak TO EACH OTHER.
   Leave this empty ONLY if the video is pure narration with no dialogue between
   named people. Infer from the dialogue: relative age, closeness, formality. When
   the evidence is thin, choose a neutral pairing and say so in "basis".

Never leave "speech_register", "narrator_self_vi" or "audience_vi" empty — infer
the most likely value from the transcript instead.

Return ONLY this JSON:
{"style": {"speech_register": "...", "narrator_self_vi": "...", "audience_vi": "..."},
 "address_terms": [{"speaker": "A", "addressee": "B",
                    "vi_self": "how A refers to themselves",
                    "vi_other": "how A addresses B",
                    "basis": "the evidence, in Vietnamese"}]}"""


# Everyday words that must never become glossary entries. Pinning them to one
# rendering makes the translation worse, because the natural wording changes with
# grammar: 美国 is "America", "American" or "the US" depending on the sentence.
#
# This is a backstop for an instruction the model does not reliably follow. The
# prompt names 监狱, 越狱, 美国, 警察 and 逃跑 as explicit examples of what to leave
# out, and a measured run still returned all five. A deterministic filter is the
# only thing that actually holds.
_COMMON_WORDS = frozenset("""
美国 中国 英国 法国 德国 日本 韩国 加拿大
监狱 越狱 逃跑 警察 警方 法官 律师 犯人 囚犯 罪犯 案件 法院
男人 女人 孩子 父母 家庭 朋友 老板 医生 学生 老师
今天 明天 昨天 早上 晚上 时间 地方 东西 事情 问题 办法
汽车 飞机 火车 电话 电脑 手机 网络 视频 照片
""".split())


def _is_common_word(zh: str) -> bool:
    return zh in _COMMON_WORDS


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
    dropped: list[str] = []
    for batch in batches:
        for raw in batch:
            zh = (raw.get("zh") or "").strip()
            if not zh:
                continue
            if _is_common_word(zh):
                dropped.append(zh)
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

    if dropped:
        log.info("S3: loại %d mục là từ thông thường: %s",
                 len(set(dropped)), ", ".join(sorted(set(dropped))))

    out: list[GlossaryTerm] = []
    for data in merged.values():
        try:
            term = GlossaryTerm.model_validate(data)
        except Exception:  # noqa: BLE001 - a malformed entry must not sink the job
            log.warning("S3: bỏ qua entry hỏng: %r", data)
            continue
        out.append(_resolve_keep_source(term))
    out.sort(key=lambda t: (t.type, t.zh))
    return out


def _resolve_keep_source(term: GlossaryTerm) -> GlossaryTerm:
    """Drop ``keep_source`` when the entry also carries a real translation.

    The two contradict each other, and models set the flag far too eagerly.
    Observed with gpt-4o-mini: every extracted term came back ``keep_source: true``
    *and* with a filled-in Vietnamese rendering. Believing the flag made S4 instruct
    the model to leave 美国空军 in Chinese and made the substitution step skip it, so
    correct glossary content produced Chinese fragments in a Vietnamese subtitle.

    A supplied translation is the stronger signal of intent, so it wins.
    """
    if not term.keep_source:
        return term
    translated = [x for x in (term.vi, term.en) if x and x != term.zh]
    if translated:
        log.debug("S3: bỏ keep_source cho %r vì đã có bản dịch %r", term.zh, translated[0])
        return term.model_copy(update={"keep_source": False})
    return term


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
