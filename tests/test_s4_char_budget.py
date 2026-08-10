"""Ngân sách ký tự gửi cho model."""

from __future__ import annotations

from zhsub.config import Config
from zhsub.models import Segment
from zhsub.stages.s4_translate import char_budget


def _seg(duration: float) -> Segment:
    return Segment(id=0, start=0.0, end=duration, text_zh="x", token_range=(0, 1))


def test_the_budget_leaves_room_below_the_real_ceiling():
    """S5 cảnh báo ở đúng CPS mà ngân sách suy ra từ đó, nên gửi trần thật là không
    còn khe hở nào: vượt một ký tự đã thành cảnh báo. Đo trên clip 472 câu, bản dịch
    vượt trung bình 7.1 ký tự trên ngân sách khoảng 86.
    """
    cfg = Config()
    limits = cfg.render.for_lang("vi")
    ceiling = 4.0 * limits.max_cps

    assert char_budget(_seg(4.0), cfg, "vi") < ceiling


def test_the_tighter_of_the_two_limits_wins():
    """Cue 7s cho 147 ký tự theo CPS nhưng chỉ 84 lọt vào 2 dòng x 42."""
    cfg = Config()
    cfg.render.max_lines = 2

    assert char_budget(_seg(7.0), cfg, "vi") < 7.0 * cfg.render.for_lang("vi").max_cps


def test_very_short_cues_keep_a_usable_floor():
    """Cue 0.5s ra ngân sách quá nhỏ để viết nổi một câu tiếng Việt."""
    assert char_budget(_seg(0.5), Config(), "vi") == 20


def test_chinese_lines_get_a_much_tighter_budget():
    """Chuẩn CJK là ~20 ký tự / CPS 9, chặt hơn hẳn 42 / CPS 21 của chữ Latin."""
    cfg = Config()

    assert char_budget(_seg(4.0), cfg, "zh") < char_budget(_seg(4.0), cfg, "vi")
