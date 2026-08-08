"""Normalisation must erase exactly the meaningless differences before CER is measured."""

from __future__ import annotations

from zhsub.text.normalize import digits_to_zh, normalize_for_align, normalize_for_cer, strip_punct


def test_traditional_and_simplified_collapse_to_one_form():
    # faster-whisper often returns traditional characters, paraformer returns
    # simplified. Without folding them, CER punishes identical content.
    assert normalize_for_cer("這個問題") == normalize_for_cer("这个问题")


def test_punctuation_does_not_count_towards_cer():
    # ct-punc inserts punctuation; hand-written references vary.
    assert normalize_for_cer("你好，世界。") == normalize_for_cer("你好世界")


def test_arabic_and_chinese_digits_collapse_to_one_form():
    assert digits_to_zh("2024年") == "二零二四年"
    assert normalize_for_cer("2024年") == normalize_for_cer("二零二四年")


def test_fullwidth_folds_to_halfwidth():
    assert normalize_for_cer("ＡＢＣ１２３") == normalize_for_cer("abc123")


def test_align_normalisation_leaves_digits_alone():
    # digits_to_zh lengthens the string and breaks the one-to-one mapping back onto
    # the token array, so the alignment variant must not apply it.
    assert normalize_for_align("2024年") == "2024年"


def test_align_normalisation_preserves_length_without_punctuation():
    text = "今天天气很好"
    assert len(normalize_for_align(text)) == len(text)


def test_strip_punct_removes_both_cjk_and_latin_punctuation():
    assert strip_punct("你好，世界! (test)") == "你好世界 test"
