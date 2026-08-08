"""Chuẩn hoá phải xoá đúng những khác biệt vô nghĩa trước khi đo CER."""

from __future__ import annotations

from zhsub.text.normalize import digits_to_zh, normalize_for_align, normalize_for_cer, strip_punct


def test_phon_the_va_gian_the_ve_cung_mot_dang():
    # faster-whisper hay trả phồn thể, paraformer trả giản thể. Không quy về một
    # dạng thì CER bị phạt hàng loạt dù nội dung y hệt.
    assert normalize_for_cer("這個問題") == normalize_for_cer("这个问题")


def test_dau_cau_khong_tinh_vao_cer():
    # ct-punc chèn dấu câu, reference viết tay thì tuỳ người.
    assert normalize_for_cer("你好，世界。") == normalize_for_cer("你好世界")


def test_chu_so_a_rap_va_han_ve_cung_mot_dang():
    assert digits_to_zh("2024年") == "二零二四年"
    assert normalize_for_cer("2024年") == normalize_for_cer("二零二四年")


def test_fullwidth_ve_halfwidth():
    assert normalize_for_cer("ＡＢＣ１２３") == normalize_for_cer("abc123")


def test_normalize_for_align_khong_dung_toi_chu_so():
    # digits_to_zh làm chuỗi dài ra và phá vỡ ánh xạ 1-1 về mảng token, nên bản
    # dùng để align phải giữ nguyên chữ số.
    assert normalize_for_align("2024年") == "2024年"


def test_normalize_for_align_giu_do_dai_khi_khong_co_dau_cau():
    text = "今天天气很好"
    assert len(normalize_for_align(text)) == len(text)


def test_strip_punct_bo_ca_dau_cau_cjk_lan_latin():
    assert strip_punct("你好，世界! (test)") == "你好世界 test"
