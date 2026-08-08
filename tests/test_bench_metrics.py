"""The benchmark's credibility rests entirely on these definitions being right."""

from __future__ import annotations

import pytest

from bench.metrics import (
    compute_cer,
    compute_onset,
    cues_after_a_real_pause,
    expand_tokens_to_chars,
)
from zhsub.subtitle import Cue


def test_expand_tokens_interpolates_multi_character_tokens():
    # whisper routinely bundles several Han characters into one "word", and English
    # words inside Chinese arrive as one token too.
    chars, times = expand_tokens_to_chars([("你好世界", 0.0, 4.0)])

    assert chars == ["你", "好", "世", "界"]
    assert times == pytest.approx([0.0, 1.0, 2.0, 3.0])


def test_expand_tokens_uses_start_directly_for_single_characters():
    chars, times = expand_tokens_to_chars([("你", 1.0, 1.4), ("好", 2.0, 2.4)])

    assert chars == ["你", "好"]
    assert times == pytest.approx([1.0, 2.0])


def test_contiguous_cc_track_yields_only_one_usable_onset():
    """An auto-CC track is wall-to-wall, so almost no cue start is a real onset."""
    cues = [
        Cue(0.0, 2.0, "他是美国越狱史上的扛把子"),
        Cue(2.0, 4.0, "被称为美国最会逃的男人"),
        Cue(4.0, 6.0, "他的人生信条很简单"),
    ]
    assert cues_after_a_real_pause(cues) == [True, False, False]


def test_cue_following_a_silence_is_flagged():
    cues = [
        Cue(0.0, 2.0, "第一句"),
        Cue(2.0, 4.0, "紧接着"),   # contiguous -> just a text break
        Cue(5.0, 7.0, "停顿之后"),  # 1s of silence -> a real onset
    ]
    assert cues_after_a_real_pause(cues) == [True, False, True]


def test_perfect_prediction_has_zero_onset_error():
    cues = [Cue(1.0, 2.0, "你好"), Cue(5.0, 6.0, "世界")]
    chars = ["你", "好", "世", "界"]
    times = [1.0, 1.5, 5.0, 5.5]

    result = compute_onset(cues, chars, times)

    assert result.n_matched == 2
    assert result.median_ms == pytest.approx(0.0)
    assert result.coverage == pytest.approx(100.0)


def test_onset_error_is_measured_in_milliseconds():
    cues = [Cue(1.0, 2.0, "你好")]
    result = compute_onset(cues, ["你", "好"], [1.3, 1.8])

    assert result.median_ms == pytest.approx(300.0)
    assert result.within_500ms == pytest.approx(100.0)
    assert result.within_200ms == pytest.approx(0.0)


def test_only_after_pause_restricts_to_real_onsets():
    # Second cue is contiguous with the first, so its start carries no timing
    # information and must be excluded.
    cues = [Cue(1.0, 2.0, "你好"), Cue(2.0, 3.0, "世界")]
    chars = ["你", "好", "世", "界"]
    times = [1.0, 1.5, 2.9, 2.95]  # second cue predicted 900ms late

    everything = compute_onset(cues, chars, times)
    restricted = compute_onset(cues, chars, times, only_after_pause=True)

    assert everything.n_ref == 2
    assert restricted.n_ref == 1
    assert restricted.median_ms == pytest.approx(0.0)


def test_unmatched_cues_lower_coverage_rather_than_being_guessed():
    cues = [Cue(1.0, 2.0, "你好"), Cue(5.0, 6.0, "完全不同的内容")]
    # Prediction only contains the first cue's text.
    result = compute_onset(cues, ["你", "好"], [1.0, 1.5])

    assert result.n_ref == 2
    assert result.n_matched == 1
    assert result.coverage == pytest.approx(50.0)


def test_cer_ignores_punctuation_and_script_variant():
    cues = [Cue(0.0, 1.0, "這個問題")]

    assert compute_cer(cues, "这个问题。") == pytest.approx(0.0)


def test_cer_counts_real_substitutions():
    cues = [Cue(0.0, 1.0, "越狱天才")]
    # The homophone error an ASR system actually makes here.
    assert compute_cer(cues, "粤语天才") == pytest.approx(0.5)
