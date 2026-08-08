"""S2 — re-split sentences semantically, keeping the original timestamps.

The LLM never sees or emits a timestamp. It receives a bare character stream and
returns the same stream with ``|`` inserted; every time value comes from indexing
back into ``asr.json``. Any code here that parsed a time out of a model response
would be a bug by construction.
"""

from __future__ import annotations

import logging
import re

from ..config import Config
from ..jsonio import read_doc, sha256_file, write_doc
from ..llm.base import LLMError, LLMProvider
from ..models import AsrDoc, Segment, SegmentsDoc
from ..text.align import recover_breaks
from ..timing import Span, apply_min_duration, merge_adjacent

log = logging.getLogger(__name__)

SEP = "|"

# English, for the same measured reason as the S4 prompt: small local models follow
# English instructions far more reliably than Vietnamese ones.
SYSTEM_PROMPT = """You segment Chinese transcripts into subtitle lines.

The user sends one continuous Chinese character stream with NO punctuation.
Insert the character "|" at every point where a new subtitle line should start.

ABSOLUTE RULES:
1. You may ONLY insert "|". Do not change, add, remove or reorder any other
   character. Removing every "|" from your answer must reproduce the input exactly.
2. Add no punctuation, no spaces, no explanation.
3. Break on meaning: each piece should be one complete thought that reads naturally.
4. Aim for 8-25 characters per piece. Never exceed 30 characters.
5. NEVER break inside a number, a proper name, or an English word.
6. NEVER leave a connective at the end of a piece (而且, 但是, 因为, 所以, 然后,
   虽然, 如果, 就是, 这个, 那个 ...). They must start the next piece instead.

Return only the string with "|" inserted. Nothing else."""

# Conjunctions that must not be left dangling at the end of a cue.
_TRAILING_CONNECTORS = (
    "而且", "但是", "因为", "所以", "然后", "虽然", "如果", "就是", "还有",
    "并且", "不过", "可是", "于是", "由于", "为了", "以及", "或者", "这个",
    "那个", "的话", "的时候",
)

_DIGIT_RUN = re.compile(r"[0-9]")


def _chunk_at_silences(doc: AsrDoc, min_silence: float, max_chars: int) -> list[tuple[int, int]]:
    """Split the token stream into LLM-sized pieces at confident silences.

    A 45-minute transcript is 25-35k Han characters and cannot go in one call.
    Cutting at a VAD silence guarantees the split never lands mid-sentence, so each
    chunk can be re-segmented independently without affecting its neighbours.
    """
    tokens = doc.tokens
    if not tokens:
        return []

    # Candidate cut points: token indices preceded by a long enough pause.
    cuts: list[int] = []
    for i in range(1, len(tokens)):
        if tokens[i].start - tokens[i - 1].end >= min_silence:
            cuts.append(i)

    chunks: list[tuple[int, int]] = []
    start = 0
    while start < len(tokens):
        limit = start + max_chars
        if limit >= len(tokens):
            chunks.append((start, len(tokens)))
            break
        # Prefer the last silence before the character budget runs out.
        candidates = [c for c in cuts if start < c <= limit]
        end = candidates[-1] if candidates else limit
        chunks.append((start, end))
        start = end
    return chunks


def _rule_based_breaks(doc: AsrDoc, lo: int, hi: int, cfg: Config) -> list[int]:
    """Fallback splitter: punctuation, then silence, then hard length.

    Used when the LLM cannot return a usable string. Cruder than semantic
    splitting, but it never invents or misplaces a timestamp, which matters more.
    """
    breaks: list[int] = []
    last = lo
    for i in range(lo, hi):
        tok = doc.tokens[i]
        long_enough = (i + 1 - last) >= 6
        ends_clause = tok.punct_after and any(p in tok.punct_after for p in "。？！，、；：")
        silence_follows = (
            i + 1 < hi and doc.tokens[i + 1].start - tok.end >= cfg.segment.chunk_at_silence_sec
        )
        too_long = (i + 1 - last) >= 28
        if (ends_clause and long_enough) or silence_follows or too_long:
            if i + 1 < hi:
                breaks.append(i + 1)
                last = i + 1
    return breaks


def _fix_break_positions(doc: AsrDoc, breaks: list[int], lo: int, hi: int) -> list[int]:
    """Nudge breaks off positions that would split a number, a word, or a connector."""
    fixed: list[int] = []
    for b in breaks:
        if not (lo < b < hi):
            continue
        prev_tok = doc.tokens[b - 1]
        cur_tok = doc.tokens[b]

        # Never cut between two digits (e.g. a year split across two cues).
        if _DIGIT_RUN.search(prev_tok.text) and _DIGIT_RUN.search(cur_tok.text):
            continue

        # Never leave a connector dangling: push the break back before it.
        tail = "".join(t.text for t in doc.tokens[max(lo, b - 3) : b])
        moved = b
        for conn in _TRAILING_CONNECTORS:
            if tail.endswith(conn):
                moved = b - len(conn)
                break
        if lo < moved < hi:
            fixed.append(moved)
    return sorted(set(fixed))


def _best_split_point(doc: AsrDoc, lo: int, hi: int) -> int:
    """Pick the least damaging place to cut ``[lo, hi)``, biased toward the middle.

    Preference order mirrors how a human would break the line: a sentence end, then
    a clause mark, then an audible pause. Ties go to whichever candidate sits
    closest to the midpoint so the two halves come out balanced.
    """
    mid = (lo + hi) // 2
    best: tuple[int, int, int] | None = None
    for i in range(lo + 1, hi):
        prev = doc.tokens[i - 1]
        score = 0
        if prev.punct_after:
            if any(p in prev.punct_after for p in "。？！"):
                score += 4
            elif any(p in prev.punct_after for p in "，、；："):
                score += 2
        gap = doc.tokens[i].start - prev.end
        if gap >= 0.3:
            score += 3
        elif gap >= 0.15:
            score += 1
        if score == 0:
            continue
        candidate = (score, -abs(i - mid), i)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best is not None else mid


def _enforce_max_duration(
    doc: AsrDoc, ranges: list[tuple[int, int]], max_duration_sec: float
) -> list[tuple[int, int]]:
    """Split any range longer than the ceiling, recursively.

    Without this, ``max_duration_sec`` only ever acts as a guard against *merging*
    too far, and an under-segmenting model sails straight past it. Observed on a
    real 3-minute clip: a single cue of 129 tokens spanning 26.5s against a 7s
    limit, which then overflowed every line-length rule downstream.
    """
    out: list[tuple[int, int]] = []
    stack = list(reversed(ranges))
    while stack:
        lo, hi = stack.pop()
        duration = doc.tokens[hi - 1].end - doc.tokens[lo].start
        # Below four tokens there is nothing meaningful left to cut.
        if duration <= max_duration_sec or hi - lo < 4:
            out.append((lo, hi))
            continue
        split = _best_split_point(doc, lo, hi)
        if not (lo < split < hi):
            out.append((lo, hi))
            continue
        stack.append((split, hi))
        stack.append((lo, split))
    return out


def _segment_chunk(
    doc: AsrDoc, lo: int, hi: int, provider: LLMProvider | None, cfg: Config
) -> tuple[list[int], str]:
    """Break positions (token indices) for one chunk, plus the method that produced them."""
    stream = "".join(t.text for t in doc.tokens[lo:hi])
    if provider is None or len(stream) < 12:
        return _rule_based_breaks(doc, lo, hi, cfg), "rule_fallback"

    char_to_token = []
    for i in range(lo, hi):
        char_to_token.extend([i] * len(doc.tokens[i].text))

    best_method = "rule_fallback"
    for attempt in range(cfg.segment.max_retries + 1):
        try:
            reply = provider.complete(SYSTEM_PROMPT, stream)
        except LLMError as exc:
            log.warning("S2: LLM lỗi ở chunk [%d:%d]: %s", lo, hi, exc)
            break

        char_breaks, ratio = recover_breaks(stream, reply, SEP)
        if ratio >= cfg.segment.repair_min_ratio:
            method = "llm" if ratio == 1.0 else "llm+repair"
            token_breaks = sorted({char_to_token[c] for c in char_breaks if c < len(char_to_token)})
            if token_breaks:
                return token_breaks, method
            best_method = method
            # Distinct failure from a corrupted echo: the model returned the string
            # intact but inserted no break at all. Saying "khớp 1.000 < 0.95" here
            # would be nonsense, so name what actually went wrong.
            log.warning(
                "S2: chunk [%d:%d] LLM trả về nguyên vẹn nhưng không chèn chỗ ngắt nào "
                "(lần %d) — thử lại", lo, hi, attempt + 1,
            )
        else:
            log.warning(
                "S2: chunk [%d:%d] khớp %.3f < %.2f (lần %d) — thử lại",
                lo, hi, ratio, cfg.segment.repair_min_ratio, attempt + 1,
            )

    # Report what actually produced the breaks. Carrying the LLM's method label
    # through a rule fallback makes segments.json claim a provenance it does not have.
    log.warning("S2: chunk [%d:%d] rơi xuống ngắt theo rule", lo, hi)
    return _rule_based_breaks(doc, lo, hi, cfg), "rule_fallback"


def build_segments(doc: AsrDoc, provider: LLMProvider | None, cfg: Config) -> SegmentsDoc:
    """Re-segment ``doc`` and produce ``segments.json``. Times come only from tokens."""
    chunks = _chunk_at_silences(
        doc, cfg.segment.chunk_at_silence_sec, cfg.segment.max_chars_per_llm_call
    )

    all_breaks: list[int] = []
    methods: set[str] = set()
    for lo, hi in chunks:
        breaks, method = _segment_chunk(doc, lo, hi, provider, cfg)
        methods.add(method)
        all_breaks.extend(_fix_break_positions(doc, breaks, lo, hi))
        all_breaks.append(hi)  # a chunk boundary is always a cue boundary

    bounds = sorted({0, *[b for b in all_breaks if 0 < b <= len(doc.tokens)], len(doc.tokens)})
    ranges = [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]

    # The ceiling has to be enforced here, not merely respected while merging: a
    # model that under-segments produces ranges already far past it.
    ranges = _enforce_max_duration(doc, ranges, cfg.segment.max_duration_sec)

    spans = [Span(doc.tokens[a].start, doc.tokens[b - 1].end) for a, b in ranges]
    groups = merge_adjacent(spans, cfg.segment.merge_gap_sec, cfg.segment.max_duration_sec)

    merged_ranges = [(ranges[g[0]][0], ranges[g[-1]][1]) for g in groups]
    merged_spans = [Span(doc.tokens[a].start, doc.tokens[b - 1].end) for a, b in merged_ranges]
    extended = apply_min_duration(merged_spans, cfg.segment.min_duration_sec, doc.audio_duration_sec)

    segments = [
        Segment(
            id=i,
            start=round(span.start, 3),
            end=round(span.end, 3),
            text_zh=doc.display_text(a, b),
            token_range=(a, b),
            flags=["extended_into_silence"] if was_extended else [],
        )
        for i, ((a, b), span, was_extended) in enumerate(
            zip(merged_ranges, merged_spans, extended)
        )
    ]

    method = "llm" if methods == {"llm"} else ("rule_fallback" if methods == {"rule_fallback"} else "llm+repair")
    return SegmentsDoc(source_asr_sha256="", method=method, segments=segments)


def run(work_dir, cfg: Config, force: bool = False) -> SegmentsDoc:
    from pathlib import Path

    work_dir = Path(work_dir)
    out_json = work_dir / "segments.json"
    if out_json.is_file() and not force:
        return read_doc(out_json, SegmentsDoc)

    asr_path = work_dir / "asr.json"
    doc = read_doc(asr_path, AsrDoc)

    provider = None
    try:
        from ..llm.factory import build_provider

        provider = build_provider(cfg.llm.segment, "segment")
    except Exception as exc:
        log.warning("S2: không dựng được LLM (%s) — dùng ngắt theo rule", exc)

    result = build_segments(doc, provider, cfg)
    result.source_asr_sha256 = sha256_file(asr_path)
    write_doc(out_json, result)

    durations = [s.end - s.start for s in result.segments]
    log.info(
        "S2 xong: %d segment (%s), dài trung bình %.2fs, dài nhất %.2fs",
        len(result.segments), result.method,
        sum(durations) / max(len(durations), 1), max(durations, default=0.0),
    )
    return result
