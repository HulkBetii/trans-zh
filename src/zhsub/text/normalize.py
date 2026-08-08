"""Chuẩn hoá text tiếng Trung trước khi đo CER.

Không làm mấy bước này thì CER đo nhiễu chứ không đo chất lượng nhận dạng:

* ``faster-whisper`` large-v3 hay trả **phồn thể**, ``paraformer-zh`` trả giản thể
  → chênh nhau hàng loạt ký tự dù nội dung y hệt.
* ``ct-punc`` chèn dấu câu, còn reference viết tay thì tuỳ người
  → dấu câu chiếm phần lớn sai số nếu không strip.
* Một engine viết "2024", engine kia viết "二零二四"
  → lệch hệ thống, engine nào cũng bị phạt oan tuỳ cách người viết ref.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_ZH_DIGITS = "零一二三四五六七八九"

# Dấu câu CJK + Latin. Dùng ranh giới Unicode category cho phần Latin nên chỉ
# cần liệt kê tường minh các ký tự CJK.
_CJK_PUNCT = "。，、；：？！“”‘’（）《》〈〉【】「」『』…—～·﹏､｡"


@lru_cache(maxsize=1)
def _opencc_t2s():
    try:
        from opencc import OpenCC

        return OpenCC("t2s")
    except Exception:  # pragma: no cover - chỉ chạy khi thiếu opencc
        return None


def to_simplified(text: str) -> str:
    """Phồn thể -> giản thể. Thiếu opencc thì trả nguyên văn."""
    cc = _opencc_t2s()
    return cc.convert(text) if cc is not None else text


def to_halfwidth(text: str) -> str:
    """Fullwidth ASCII -> halfwidth. ``NFKC`` cũng gộp luôn vài dạng tương đương."""
    return unicodedata.normalize("NFKC", text)


def strip_punct(text: str) -> str:
    out = []
    for ch in text:
        if ch in _CJK_PUNCT:
            continue
        # P* = punctuation, S* = symbol
        if unicodedata.category(ch)[0] in ("P", "S"):
            continue
        out.append(ch)
    return "".join(out)


def digits_to_zh(text: str) -> str:
    """Chuỗi chữ số Ả Rập -> đọc từng chữ số bằng chữ Hán ("2024" -> "二零二四").

    Chuẩn hoá một chiều như vậy để hai engine viết khác kiểu vẫn so được với nhau.

    Hạn chế đã biết: chữ số Hán dạng *giá trị* thì không đụng tới, nên nếu ref
    viết "二十五" mà ASR trả "25" (-> "二五") vẫn tính là sai. Đây là trường hợp
    hiếm và sai số nhỏ; đảo chiều (Hán -> Ả Rập) sẽ phải suy luận giá trị và dễ
    sai hơn nhiều.
    """
    return re.sub(r"\d+", lambda m: "".join(_ZH_DIGITS[int(d)] for d in m.group()), text)


def collapse_space(text: str) -> str:
    return re.sub(r"\s+", "", text)


def normalize_for_cer(text: str) -> str:
    """Toàn bộ chuỗi chuẩn hoá, áp dụng y hệt cho cả reference lẫn hypothesis."""
    text = to_halfwidth(text)
    text = to_simplified(text)
    text = strip_punct(text)
    text = digits_to_zh(text)
    text = collapse_space(text)
    return text.lower()


def normalize_for_align(text: str) -> str:
    """Bản nhẹ hơn, dùng khi align chuỗi để tra ngược timestamp.

    Giữ nguyên số lượng ký tự càng nhiều càng tốt nên **không** đụng tới chữ số:
    ``digits_to_zh`` làm chuỗi dài ra và phá vỡ ánh xạ 1-1 về mảng token.
    """
    text = to_halfwidth(text)
    text = to_simplified(text)
    text = strip_punct(text)
    text = collapse_space(text)
    return text.lower()
