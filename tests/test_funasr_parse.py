"""Ghép mảng ``timestamp`` của FunASR với text đã có dấu câu.

Đây là chỗ dễ lệch nhất trong S1: ``ct-punc`` chèn dấu câu vào text nhưng dấu câu
không có timestamp riêng, nên zip thẳng hai mảng là sai ngay từ ký tự đầu tiên.
"""

from __future__ import annotations

import pytest

from zhsub.asr.funasr_paraformer import parse_funasr_result, split_tokens


def test_split_tokens_treo_dau_cau_vao_token_truoc():
    assert split_tokens("你好，世界。") == [
        ["你", ""], ["好", "，"], ["世", ""], ["界", "。"],
    ]


def test_tu_tieng_anh_la_mot_token():
    # FunASR sinh một timestamp cho cả từ Latin, không phải từng chữ cái.
    assert split_tokens("我用 iPhone 拍的") == [
        ["我", ""], ["用", ""], ["iPhone", ""], ["拍", ""], ["的", ""],
    ]


def test_timestamp_khop_token_khong_bi_lech_boi_dau_cau():
    res = [{
        "sentence_info": [{
            "text": "你好，世界。",
            "start": 1000,
            "end": 3000,
            "timestamp": [[1000, 1400], [1400, 1800], [2200, 2600], [2600, 3000]],
        }]
    }]
    out = parse_funasr_result(res)
    sent = out.sentences[0]

    assert out.degraded_sentences == 0
    assert [t.text for t in sent.tokens] == ["你", "好", "世", "界"]
    assert [t.punct_after for t in sent.tokens] == [None, "，", None, "。"]
    # Milli giây -> giây, và mốc của "世" phải là 2.2s chứ không phải 1.8s
    # (đúng cái sai nếu dấu câu bị tính là một token).
    assert [t.start for t in sent.tokens] == pytest.approx([1.0, 1.4, 2.2, 2.6])
    assert sent.start == pytest.approx(1.0)
    assert sent.end == pytest.approx(3.0)


def test_lech_so_luong_thi_chia_deu_va_danh_dau_degraded():
    res = [{
        "sentence_info": [{
            "text": "你好世界",
            "start": 0,
            "end": 4000,
            "timestamp": [[0, 1000], [1000, 2000]],  # thiếu 2 timestamp
        }]
    }]
    out = parse_funasr_result(res)

    assert out.degraded_sentences == 1
    assert [t.start for t in out.sentences[0].tokens] == pytest.approx([0.0, 1.0, 2.0, 3.0])


def test_khong_co_sentence_info_thi_dung_text_o_cap_tren():
    res = [{"text": "你好", "timestamp": [[500, 900], [900, 1300]]}]
    out = parse_funasr_result(res)

    assert len(out.sentences) == 1
    assert out.sentences[0].start == pytest.approx(0.5)
    assert out.degraded_sentences == 0


def test_ket_qua_rong():
    assert parse_funasr_result([]).sentences == []
    assert parse_funasr_result([{"sentence_info": []}]).sentences == []
