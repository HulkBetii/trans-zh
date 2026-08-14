"""Nhịp hỏi trạng thái task."""

from __future__ import annotations

from zhsub.dub.client import POLL_MAX_ATTEMPTS, POLL_SCHEDULE_SEC


def _delay(attempt: int) -> float:
    return POLL_SCHEDULE_SEC[min(attempt, len(POLL_SCHEDULE_SEC) - 1)]


def test_the_first_question_comes_quickly():
    """Phần lớn câu tổng hợp xong trong 2-5 giây; chờ cứng 8 giây là mỗi cue mất
    vài giây vô ích, trên 342 cue thành cả chục phút."""
    assert _delay(0) <= 2.0


def test_the_gap_widens_and_then_holds():
    """Câu lâu mà hỏi dồn thì dội vào đúng endpoint hay quá tải nhất."""
    delays = [_delay(i) for i in range(len(POLL_SCHEDULE_SEC) + 3)]

    assert delays == sorted(delays)
    assert delays[-1] == POLL_SCHEDULE_SEC[-1]


def test_a_task_is_given_several_minutes_before_giving_up():
    total = sum(_delay(i) for i in range(POLL_MAX_ATTEMPTS))

    assert total > 300
