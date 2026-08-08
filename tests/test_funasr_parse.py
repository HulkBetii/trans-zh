"""Pairing FunASR's ``timestamp`` array with its already-punctuated text.

The most misalignment-prone spot in S1: ``ct-punc`` inserts punctuation into the
text, but punctuation carries no timestamp, so zipping the two arrays directly is
wrong from the very first character.
"""

from __future__ import annotations

import pytest

from zhsub.asr.funasr_paraformer import parse_funasr_result, split_tokens


def test_split_tokens_attaches_punctuation_to_the_preceding_token():
    assert split_tokens("你好，世界。") == [
        ["你", ""], ["好", "，"], ["世", ""], ["界", "。"],
    ]


def test_an_english_word_is_a_single_token():
    # FunASR emits one timestamp for a whole Latin word, not one per letter.
    assert split_tokens("我用 iPhone 拍的") == [
        ["我", ""], ["用", ""], ["iPhone", ""], ["拍", ""], ["的", ""],
    ]


def test_token_count_matches_timestamp_count_on_real_output():
    """The invariant the whole parser rests on, taken from real FunASR output.

    Sampled from the bundled 70s clip: 369 characters of punctuated text reduce to
    333 tokens, and FunASR returns exactly 333 timestamps.
    """
    text = "试错的过程很简单，而且特别是今天报名唱学卡的同学，"
    assert len(split_tokens(text)) == 8 + 15


def test_timestamps_are_not_shifted_by_punctuation():
    res = [{
        "text": "你好，世界。",
        "timestamp": [[1000, 1400], [1400, 1800], [2200, 2600], [2600, 3000]],
    }]
    out = parse_funasr_result(res)

    tokens = [t for s in out.sentences for t in s.tokens]
    assert [t.text for t in tokens] == ["你", "好", "世", "界"]
    assert [t.punct_after for t in tokens] == [None, "，", None, "。"]
    # Milliseconds to seconds, and "世" must start at 2.2s rather than 1.8s —
    # 1.8s is precisely the answer you get if punctuation is counted as a token.
    assert [t.start for t in tokens] == pytest.approx([1.0, 1.4, 2.2, 2.6])


def test_sentences_split_on_punctuation():
    res = [{
        "text": "你好，世界。",
        "timestamp": [[1000, 1400], [1400, 1800], [2200, 2600], [2600, 3000]],
    }]
    out = parse_funasr_result(res)

    assert [s.text for s in out.sentences] == ["你好，", "世界。"]
    assert out.sentences[0].start == pytest.approx(1.0)
    assert out.sentences[0].end == pytest.approx(1.8)
    assert out.sentences[1].start == pytest.approx(2.2)


def test_sentences_also_split_on_a_silent_gap():
    # ct-punc regularly misses a pause; without a gap break the result is one long
    # cue straddling an obvious silence.
    res = [{
        "text": "你好世界",
        "timestamp": [[0, 400], [400, 800], [5000, 5400], [5400, 5800]],
    }]
    out = parse_funasr_result(res, gap_sec=0.5)

    assert [s.text for s in out.sentences] == ["你好", "世界"]


def test_sentence_info_is_ignored_even_when_present():
    """FunASR 1.4.1 corrupts ``sentence_info``; the top-level pair is authoritative.

    On the bundled 70s sample its text diverges from the top-level text at
    character 297 and its per-sentence timestamps drift by up to 1.7s. Here the
    embedded ``sentence_info`` deliberately disagrees with the top-level text, and
    the parser must follow the top level.
    """
    res = [{
        "text": "你好，世界。",
        "timestamp": [[1000, 1400], [1400, 1800], [2200, 2600], [2600, 3000]],
        "sentence_info": [{"text": "你好世，界。", "start": 9999, "end": 99999,
                           "timestamp": [[9999, 9999]]}],
    }]
    out = parse_funasr_result(res)

    assert "".join(s.text for s in out.sentences) == "你好，世界。"
    assert out.sentences[0].start == pytest.approx(1.0)


def test_apostrophe_stays_inside_an_english_word():
    """"'" has Unicode category Po, so a naive punctuation-first check splits
    "don't" into "don" + "t" and invents a token FunASR never emitted.

    On a 35-minute clip with English speech that was exactly 19 phantom tokens,
    which broke the token==timestamp invariant and cost the whole timeline.
    """
    assert split_tokens("don't") == [["don't", ""]]
    assert split_tokens("i'm ok") == [["i'm", ""], ["ok", ""]]
    # A leading quote is still punctuation, not the start of a word.
    assert split_tokens("他说'好") == [["他", ""], ["说", "'"], ["好", ""]]


def test_count_mismatch_raises_instead_of_inventing_a_timeline():
    """A mismatch must fail loudly rather than smear tokens across the file.

    Spreading evenly produces a plausible-looking, wholly fabricated timeline: a
    0.2% discrepancy on a 35-minute clip once became a uniform 259
    tokens-per-minute smear measuring 28 seconds of onset error.
    """
    res = [{
        "text": "你好世界",
        "timestamp": [[0, 1000], [1000, 4000]],  # two timestamps missing
    }]
    with pytest.raises(ValueError, match="không khớp"):
        parse_funasr_result(res)


def test_missing_timestamps_raise_rather_than_inventing_a_timeline():
    with pytest.raises(ValueError, match="không khớp"):
        parse_funasr_result([{"text": "你好", "timestamp": []}])


def test_empty_result():
    assert parse_funasr_result([]).sentences == []
    assert parse_funasr_result([{"text": ""}]).sentences == []
