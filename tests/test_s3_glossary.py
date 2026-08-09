"""Glossary extraction must not hand S4 a self-contradictory entry."""

from __future__ import annotations

from zhsub.models import GlossaryTerm
from zhsub.stages.s3_glossary import _merge_terms, _resolve_keep_source


def test_keep_source_is_dropped_when_a_translation_is_present():
    """The flag and a filled-in translation contradict each other; the translation wins.

    Observed with gpt-4o-mini: every extracted term came back keep_source=true *and*
    with a Vietnamese rendering. Believing the flag told S4 to leave 美国空军 in
    Chinese, so a correct glossary produced Chinese fragments in the subtitle.
    """
    term = GlossaryTerm(zh="美国空军", vi="Không quân Hoa Kỳ", en="US Air Force",
                        type="org", keep_source=True)

    assert _resolve_keep_source(term).keep_source is False


def test_keep_source_survives_when_there_is_no_translation():
    term = GlossaryTerm(zh="麻婆豆腐", vi="", en="", type="dish", keep_source=True)

    assert _resolve_keep_source(term).keep_source is True


def test_keep_source_survives_when_the_translation_is_just_the_source():
    term = GlossaryTerm(zh="卡拉OK", vi="卡拉OK", en="卡拉OK", type="term", keep_source=True)

    assert _resolve_keep_source(term).keep_source is True


def test_merge_prefers_the_richest_entry_across_batches():
    merged = _merge_terms([
        [{"zh": "越狱", "vi": "vượt ngục", "type": "term"}],
        [{"zh": "越狱", "vi": "", "en": "prison break", "pinyin": "yuè yù"}],
    ])

    assert len(merged) == 1
    assert merged[0].vi == "vượt ngục"
    assert merged[0].en == "prison break"
    assert merged[0].pinyin == "yuè yù"


def test_malformed_entry_is_skipped_not_fatal():
    merged = _merge_terms([[{"zh": "越狱", "vi": "vượt ngục"}, {"no_zh": "rác"}]])

    assert [t.zh for t in merged] == ["越狱"]
