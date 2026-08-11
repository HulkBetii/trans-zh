"""Chuẩn hoá chữ cho TTS. Mọi ca lấy từ bản dịch thật của một video 184 câu."""

from __future__ import annotations

import pytest

from zhsub.dub.text import foreign_names, normalize_for_speech


@pytest.mark.parametrize(
    "raw, spoken",
    [
        # Đã có "Ngày" thì không thêm lần nữa — nếu không thành "Ngày ngày".
        ("Ngày 1/12/1986, khu vực New York", "Ngày 1 tháng 12 năm 1986, khu vực New York"),
        ("Đến ngày 7/9/1989, vụ án", "Đến ngày 7 tháng 9 năm 1989, vụ án"),
        ("không ai còn thấy Helle sau ngày 19/11.", "không ai còn thấy Helle sau ngày 19 tháng 11."),
        # Chưa có thì thêm vào.
        ("tức 18/11, cô ấy", "tức ngày 18 tháng 11, cô ấy"),
        ("Khoảng 3 giờ sáng 19/11,", "Khoảng 3 giờ sáng ngày 19 tháng 11,"),
    ],
)
def test_dates_become_speakable(raw: str, spoken: str):
    assert normalize_for_speech(raw) == spoken


def test_month_year_is_not_read_as_day_month():
    """Ca suýt làm hỏng: "Tháng 5/1988" là tháng/năm, không phải ngày/tháng."""
    assert normalize_for_speech("Tháng 5/1988, phiên tòa") == "Tháng 5 năm 1988, phiên tòa"


def test_thousand_separator_is_removed():
    """"8.000" dễ bị đọc thành "tám phẩy không không không"."""
    assert normalize_for_speech("hơn 8.000 vụ án") == "hơn 8000 vụ án"


def test_a_decimal_is_left_alone():
    """Chỉ đúng ba chữ số sau dấu chấm mới là ngăn hàng nghìn."""
    assert normalize_for_speech("dài 1.5 mét") == "dài 1.5 mét"


def test_an_acronym_in_brackets_is_dropped():
    """Tên đầy đủ vừa đọc xong ngay trước đó; người dẫn không đọc cả hai."""
    assert (
        normalize_for_speech("Cơ quan Tình báo Trung ương Hoa Kỳ (CIA).")
        == "Cơ quan Tình báo Trung ương Hoa Kỳ."
    )


def test_other_brackets_keep_their_words():
    assert (
        normalize_for_speech("nhà tù (nơi hắn trốn) bị điều tra")
        == "nhà tù nơi hắn trốn bị điều tra"
    )


def test_a_ratio_that_is_not_a_date_is_left_alone():
    """Tháng 13 không tồn tại — đừng biến mọi dấu gạch chéo thành ngày."""
    assert normalize_for_speech("tỉ lệ 45/13 người") == "tỉ lệ 45/13 người"


def test_plain_years_are_untouched():
    assert normalize_for_speech("Năm 1986, nơi đây") == "Năm 1986, nơi đây"


def test_names_are_found_by_capitals_in_mid_sentence():
    """Lọc theo "chữ Latin không dấu" thì mọi từ tiếng Việt mở đầu câu đều lọt.

    Tên thật hầu như luôn xuất hiện giữa câu ít nhất một lần, còn "Hung" trong
    "Hung thủ đã..." thì không bao giờ.
    """
    texts = [
        "Hung thủ đã xử lý thi thể rất tinh vi.",
        "Ai cũng biết chuyện đó.",
        "không ai còn thấy Helle sau hôm ấy",
        "Câu chuyện xảy ra ở bang Connecticut.",
    ]

    names, _ = foreign_names(texts)

    assert names == ["Connecticut", "Helle"]


def test_acronyms_are_listed_separately():
    names, acronyms = foreign_names(["làm cho Cơ quan Tình báo Hoa Kỳ CIA và FBI"])

    assert acronyms == ["CIA", "FBI"]
    assert "CIA" not in names
