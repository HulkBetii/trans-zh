"""S2 phải khôi phục được vị trí ngắt ngay cả khi LLM trả về chuỗi không nguyên vẹn."""

from __future__ import annotations

from zhsub.text.align import index_map, recover_breaks, similarity

SENT = "今天我们来聊聊麻婆豆腐这道菜的做法"


def test_llm_tra_ve_nguyen_ven():
    resp = "今天我们来聊聊|麻婆豆腐这道菜的做法"
    breaks, ratio = recover_breaks(SENT, resp)

    assert ratio == 1.0
    assert breaks == [7]
    assert SENT[: breaks[0]] == "今天我们来聊聊"


def test_bo_dau_ngat_o_dau_va_cuoi_chuoi():
    # Ngắt ở vị trí 0 hoặc cuối chuỗi không tạo ra segment nào, phải bị loại.
    breaks, ratio = recover_breaks(SENT, "|" + SENT + "|")

    assert ratio == 1.0
    assert breaks == []


def test_llm_tu_y_sua_chu_van_khoi_phuc_duoc_cho_ngat():
    # LLM đổi 麻婆豆腐 -> 麻辣豆腐; các chỗ ngắt ngoài vùng bị sửa vẫn phải giữ được.
    resp = "今天我们来聊聊|麻辣豆腐|这道菜的做法"
    breaks, ratio = recover_breaks(SENT, resp)

    assert ratio < 1.0
    assert 7 in breaks
    assert SENT[7:11] == "麻婆豆腐"
    assert 11 in breaks, "chỗ ngắt ngay sau vùng bị sửa vẫn phải neo đúng"


def test_cho_ngat_roi_han_vao_vung_khong_khop_thi_bo():
    # Cả đoạn giữa bị viết lại; thà mất chỗ ngắt còn hơn đặt sai rồi kéo lệch timestamp.
    resp = "今天我们来聊聊|完全不同的内容在这里|的做法"
    breaks, _ = recover_breaks(SENT, resp)

    assert all(0 < b < len(SENT) for b in breaks)
    assert breaks == sorted(set(breaks))


def test_index_map_khong_bi_autojunk_pha():
    # Ký tự tiếng Trung phổ biến (的) lặp nhiều lần trong chuỗi dài. Với autojunk
    # mặc định của difflib, chúng bị coi là rác và alignment hỏng hoàn toàn.
    a = ("的是了我" * 60) + "独特标记"
    mapping = index_map(a, a)

    assert len(mapping) == len(a)
    assert all(mapping[i] == i for i in range(len(a)))
    assert similarity(a, a) == 1.0
