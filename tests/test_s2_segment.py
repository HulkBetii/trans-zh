"""S2 must never let a timestamp originate anywhere but the ASR token array."""

from __future__ import annotations

import pytest

from zhsub.config import Config
from zhsub.models import AsrDoc, EngineInfo, RawSegment, Token
from zhsub.stages.s2_segment import build_segments

from .fake_llm import SegmentingProvider


_PUNCT = "。，、；：？！"


def _asr_doc(text: str, start: float = 1.0, step: float = 0.25, gap_at: int | None = None) -> AsrDoc:
    """Build an AsrDoc the way S1 really does.

    Punctuation must hang off the previous token's ``punct_after`` rather than
    becoming a token of its own — that is what ct-punc produces, and any splitting
    heuristic that reads punctuation depends on it.
    """
    tokens: list[Token] = []
    t = start
    for ch in text:
        if ch in _PUNCT:
            if tokens:
                tokens[-1].punct_after = (tokens[-1].punct_after or "") + ch
            continue
        if gap_at is not None and len(tokens) == gap_at:
            t += 2.0  # a clear silence
        tokens.append(Token(i=len(tokens), text=ch, start=round(t, 3), end=round(t + step, 3)))
        t += step
    return AsrDoc(
        engine=EngineInfo(name="fake", version="0", models={}, device="cpu"),
        audio_duration_sec=round(tokens[-1].end + 5.0, 3),
        tokens=tokens,
        raw_segments=[RawSegment(id=0, start=tokens[0].start, end=tokens[-1].end,
                                 token_range=(0, len(tokens)))],
        vad_speech=[(tokens[0].start, tokens[-1].end)],
    )


def test_every_boundary_time_comes_from_a_token():
    doc = _asr_doc("他是美国越狱史上的扛把子被称为美国最会逃的男人")
    cfg = Config()

    result = build_segments(doc, SegmentingProvider(every=8), cfg)

    token_starts = {t.start for t in doc.tokens}
    token_ends = {t.end for t in doc.tokens}
    for seg in result.segments:
        assert seg.start in token_starts, "start phải là mốc của một token có thật"
        assert seg.end in token_ends or "extended_into_silence" in seg.flags


def test_segments_cover_every_token_exactly_once():
    doc = _asr_doc("他是美国越狱史上的扛把子被称为美国最会逃的男人")
    result = build_segments(doc, SegmentingProvider(every=8), Config())

    covered: list[int] = []
    for seg in result.segments:
        lo, hi = seg.token_range
        covered.extend(range(lo, hi))

    assert covered == sorted(covered)
    assert covered == list(range(len(doc.tokens))), "không được sót hay lặp token"


def test_text_is_reassembled_from_the_token_array():
    doc = _asr_doc("他是美国越狱史上的扛把子")
    result = build_segments(doc, SegmentingProvider(every=5), Config())

    assert "".join(s.text_zh for s in result.segments) == doc.display_text(0, len(doc.tokens))


def test_corrupted_llm_reply_still_produces_a_valid_timeline():
    """A model that rewrites a character must not be able to shift any timestamp."""
    doc = _asr_doc("他是美国越狱史上的扛把子被称为美国最会逃的男人")
    result = build_segments(doc, SegmentingProvider(every=8, corrupt=True), Config())

    token_starts = {t.start for t in doc.tokens}
    assert all(seg.start in token_starts for seg in result.segments)
    assert "".join(s.text_zh for s in result.segments) == doc.display_text(0, len(doc.tokens))


def test_no_llm_falls_back_to_rules_without_losing_tokens():
    doc = _asr_doc("他是美国越狱史上的扛把子。被称为美国最会逃的男人。")
    result = build_segments(doc, None, Config())

    assert result.method == "rule_fallback"
    assert result.segments
    covered = [i for s in result.segments for i in range(*s.token_range)]
    assert covered == list(range(len(doc.tokens)))


def test_segments_are_monotonic_and_non_overlapping():
    doc = _asr_doc("他是美国越狱史上的扛把子被称为美国最会逃的男人", gap_at=10)
    result = build_segments(doc, SegmentingProvider(every=6), Config())

    for a, b in zip(result.segments, result.segments[1:]):
        assert a.end <= b.start + 1e-6
        assert a.start < b.start


def test_max_duration_is_respected():
    doc = _asr_doc("他是美国越狱史上的扛把子被称为美国最会逃的男人", step=0.5)
    cfg = Config()
    result = build_segments(doc, SegmentingProvider(every=6), cfg)

    for seg in result.segments:
        assert seg.end - seg.start <= cfg.segment.max_duration_sec + 1e-6


def test_max_duration_holds_when_the_llm_returns_no_breaks_at_all():
    """The ceiling must not depend on the model cooperating.

    Observed on a real 3-minute clip: qwen2.5-7b returned almost no break markers
    for one chunk, producing a single 26.5s cue against a 7s limit, which then blew
    through every line-length rule downstream. `max_duration_sec` was only guarding
    merges, never forcing a split.
    """

    class _NoBreaks(SegmentingProvider):
        def _call(self, system, user, cache_system, json_mode=False):
            return user  # echoes the stream back untouched

    doc = _asr_doc("他是美国越狱史上的扛把子，被称为美国最会逃的男人。" * 3, step=0.35)
    cfg = Config()
    result = build_segments(doc, _NoBreaks(), cfg)

    longest = max(s.end - s.start for s in result.segments)
    assert longest <= cfg.segment.max_duration_sec + 1e-6, f"còn cue dài {longest:.2f}s"
    covered = [i for s in result.segments for i in range(*s.token_range)]
    assert covered == list(range(len(doc.tokens))), "cắt xong vẫn phải phủ đủ token"


def test_long_range_is_split_at_punctuation_not_arbitrarily():
    doc = _asr_doc("他是美国越狱史上的扛把子。被称为美国最会逃的男人", step=0.4)

    class _NoBreaks(SegmentingProvider):
        def _call(self, system, user, cache_system, json_mode=False):
            return user

    result = build_segments(doc, _NoBreaks(), Config())

    # The full stop sits after 12 characters; a cut there should be preferred over
    # a blind midpoint split.
    assert any(s.text_zh.endswith("。") for s in result.segments)
