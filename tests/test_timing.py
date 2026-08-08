"""Cue timing rules — the shared mechanism behind S2's minimum duration and S5's CPS relief."""

from __future__ import annotations

import pytest

from zhsub.timing import (
    NEIGHBOUR_GUARD_SEC,
    Span,
    apply_min_duration,
    cps,
    extend_into_silence,
    merge_adjacent,
    relieve_cps,
    wrap_lines,
)


def test_short_cue_borrows_from_the_silence_after_it():
    # A 0.3s utterance cannot be stretched to 0.8s of speech, but its subtitle can
    # stay up for 0.8s by running into the silence that follows.
    spans = [Span(1.0, 1.3), Span(5.0, 6.0)]
    apply_min_duration(spans, 0.8, audio_duration_sec=10.0)

    assert spans[0].end == pytest.approx(1.8)
    assert spans[0].duration == pytest.approx(0.8)


def test_extension_never_crosses_into_the_next_cue():
    """The one thing that must never happen: a line outliving the next line's speech."""
    spans = [Span(1.0, 1.3), Span(1.5, 2.5)]
    apply_min_duration(spans, 0.8, audio_duration_sec=10.0)

    assert spans[0].end <= spans[1].start - NEIGHBOUR_GUARD_SEC + 1e-9
    assert spans[0].duration < 0.8, "không đủ khoảng lặng thì chấp nhận ngắn hơn"


def test_extension_stops_at_the_end_of_the_audio():
    spans = [Span(9.5, 9.7)]
    apply_min_duration(spans, 5.0, audio_duration_sec=10.0)

    assert spans[0].end == pytest.approx(10.0)


def test_extension_flags_only_the_cues_it_changed():
    spans = [Span(0.0, 2.0), Span(5.0, 5.2)]
    flags = apply_min_duration(spans, 0.8, audio_duration_sec=10.0)

    assert flags == [False, True]


def test_merge_joins_cues_separated_by_a_short_gap():
    spans = [Span(0.0, 1.0), Span(1.1, 2.0), Span(6.0, 7.0)]
    groups = merge_adjacent(spans, max_gap_sec=0.25, max_duration_sec=7.0)

    assert groups == [[0, 1], [2]]


def test_merge_respects_the_maximum_duration():
    # Gap is small enough, but merging would exceed the 7s ceiling.
    spans = [Span(0.0, 5.0), Span(5.1, 10.0)]
    groups = merge_adjacent(spans, max_gap_sec=0.25, max_duration_sec=7.0)

    assert groups == [[0], [1]]


def test_cps_relief_uses_available_silence():
    spans = [Span(0.0, 1.0), Span(9.0, 10.0)]
    text = "x" * 42  # 42 CPS in one second, way over the limit

    new_end = relieve_cps(spans, 0, text, max_cps=21.0, audio_duration_sec=12.0)

    assert new_end == pytest.approx(2.0)
    assert cps(text, new_end - spans[0].start) == pytest.approx(21.0)


def test_cps_relief_gives_up_rather_than_stealing_time():
    spans = [Span(0.0, 1.0), Span(1.2, 2.0)]
    text = "x" * 42

    new_end = relieve_cps(spans, 0, text, max_cps=21.0, audio_duration_sec=12.0)

    assert new_end <= spans[1].start - NEIGHBOUR_GUARD_SEC + 1e-9
    assert cps(text, new_end - spans[0].start) > 21.0, "vượt ngưỡng thì để bên gọi cảnh báo"


def test_cps_of_a_zero_length_cue_is_infinite_not_a_crash():
    assert cps("abc", 0.0) == float("inf")


def test_wrap_splits_latin_on_spaces():
    lines = wrap_lines("anh ta la trum so cua gioi vuot nguc o my", 20, 2)

    assert all(len(line) <= 20 for line in lines)
    assert " ".join(lines) == "anh ta la trum so cua gioi vuot nguc o my"


def test_wrap_splits_chinese_anywhere():
    # Chinese has no spaces, so wrapping cannot depend on them.
    text = "他是美国越狱史上的扛把子被称为美国最会逃的男人"
    lines = wrap_lines(text, 20, 2)

    assert all(len(line) <= 20 for line in lines)
    assert "".join(lines) == text


def test_wrap_may_exceed_max_lines_so_the_caller_can_warn():
    # Acceptance criterion #2 requires one cue per segment and forbids trimming the
    # translation, so overflow is reported rather than silently truncated.
    lines = wrap_lines("x" * 200, 42, 2)

    assert len(lines) > 2
    assert "".join(lines) == "x" * 200
