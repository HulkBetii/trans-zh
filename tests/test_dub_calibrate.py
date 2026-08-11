"""Đo tốc độ đọc: chọn mẫu và khớp tuyến tính."""

from __future__ import annotations

import pytest

from zhsub.dub.calibrate import fit, pick_samples


def test_samples_span_short_to_long():
    """Lấy toàn câu dài thì ước lượng overhead sai bét — overhead chỉ lộ ra ở câu
    ngắn, nơi nó chiếm phần lớn thời lượng."""
    texts = [" ".join(["x"] * n) for n in range(1, 41)]

    picked = pick_samples(texts, 8)

    assert len(picked) == 8
    assert len(picked[0].split()) == 1
    assert len(picked[-1].split()) == 40


def test_every_sample_is_used_when_there_are_few():
    texts = ["a", "a b", "a b c"]

    assert pick_samples(texts, 8) == texts


def test_blank_lines_are_skipped():
    assert pick_samples(["", "   ", "a b"], 8) == ["a b"]


def test_fit_recovers_the_two_constants():
    # thời lượng = 0.15 + âm_tiết x 0.217
    points = [(n, 0.15 + n * 0.217) for n in (3, 10, 20, 28)]

    overhead, per_syllable = fit(points)

    assert round(overhead, 3) == 0.15
    assert round(per_syllable, 3) == 0.217


def test_fit_needs_samples_of_differing_length():
    """Mọi mẫu cùng số âm tiết thì hệ vô định — báo lỗi thay vì trả số bịa."""
    with pytest.raises(ValueError, match="cùng số âm tiết"):
        fit([(10, 2.3), (10, 2.4), (10, 2.2)])
