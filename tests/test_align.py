"""S2 must recover break positions even when the LLM does not echo the string intact."""

from __future__ import annotations

from zhsub.text.align import index_map, recover_breaks, similarity

SENT = "今天我们来聊聊麻婆豆腐这道菜的做法"


def test_llm_echoes_the_string_intact():
    resp = "今天我们来聊聊|麻婆豆腐这道菜的做法"
    breaks, ratio = recover_breaks(SENT, resp)

    assert ratio == 1.0
    assert breaks == [7]
    assert SENT[: breaks[0]] == "今天我们来聊聊"


def test_breaks_at_the_very_start_or_end_are_dropped():
    # A break at index 0 or at the end produces no segment, so it must be discarded.
    breaks, ratio = recover_breaks(SENT, "|" + SENT + "|")

    assert ratio == 1.0
    assert breaks == []


def test_breaks_survive_the_llm_rewriting_characters():
    # The LLM turns 麻婆豆腐 into 麻辣豆腐; breaks outside the rewritten span must hold.
    resp = "今天我们来聊聊|麻辣豆腐|这道菜的做法"
    breaks, ratio = recover_breaks(SENT, resp)

    assert ratio < 1.0
    assert 7 in breaks
    assert SENT[7:11] == "麻婆豆腐"
    assert 11 in breaks, "a break immediately after a rewritten span must still anchor"


def test_breaks_landing_inside_a_rewritten_span_are_dropped():
    # The middle is rewritten wholesale. Losing a break beats placing one wrongly
    # and dragging a timestamp with it.
    resp = "今天我们来聊聊|完全不同的内容在这里|的做法"
    breaks, _ = recover_breaks(SENT, resp)

    assert all(0 < b < len(SENT) for b in breaks)
    assert breaks == sorted(set(breaks))


def test_index_map_is_not_broken_by_autojunk():
    # Common Chinese characters (的) repeat throughout a long string. With difflib's
    # default autojunk they are treated as noise and alignment collapses entirely.
    a = ("的是了我" * 60) + "独特标记"
    mapping = index_map(a, a)

    assert len(mapping) == len(a)
    assert all(mapping[i] == i for i in range(len(a)))
    assert similarity(a, a) == 1.0
