"""Chuẩn hoá chữ trước khi đưa cho TTS đọc.

Không phải sửa bản dịch — file phụ đề giữ nguyên. Chỉ đổi những dạng viết mà mắt
đọc hiểu ngay nhưng miệng thì mơ hồ: "19/11" nên đọc là "ngày mười chín tháng mười
một", còn để nguyên thì máy có thể đọc "mười chín gạch chéo mười một".

Mọi luật ở đây rút ra từ chính bản dịch thật, kèm ca phản ví dụ đã suýt làm hỏng:

* ``Tháng 5/1988`` là tháng/năm chứ không phải ngày/tháng.
* ``Ngày 1/12/1986`` đã có sẵn chữ "Ngày" — thêm lần nữa thành "Ngày ngày".
"""

from __future__ import annotations

import re

# Ngày/tháng/năm. Bắt luôn chữ "ngày" đứng trước để không nhân đôi nó.
_DMY = re.compile(r"\b(ngày\s+)?(\d{1,2})/(\d{1,2})/(\d{4})\b", re.IGNORECASE)
# Tháng/năm: phần sau bốn chữ số nên không lẫn với ngày/tháng.
_MY = re.compile(r"\b(tháng\s+)?(\d{1,2})/(\d{4})\b", re.IGNORECASE)
# Ngày/tháng không có năm.
_DM = re.compile(r"\b(ngày\s+)?(\d{1,2})/(\d{1,2})\b", re.IGNORECASE)

# Dấu chấm ngăn hàng nghìn: "8.000" dễ bị đọc thành "tám phẩy không không không".
# Đúng ba chữ số phía sau nên "1.5" không dính.
_THOUSANDS = re.compile(r"\b(\d{1,3})\.(\d{3})\b")

# Ngoặc chứa mỗi một từ viết tắt IN HOA, ngay sau tên đầy đủ vừa đọc xong.
_ACRONYM_PAREN = re.compile(r"\s*\(([A-Z]{2,6})\)")
# Ngoặc khác: bỏ dấu ngoặc, giữ chữ bên trong.
_PAREN = re.compile(r"\s*\(([^)]*)\)")


def _dmy(m: re.Match) -> str:
    prefix, day, month, year = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    if month > 12 or day > 31:
        return m.group(0)
    return f"{prefix or 'ngày '}{day} tháng {month} năm {year}"


def _my(m: re.Match) -> str:
    prefix, month, year = m.group(1), int(m.group(2)), m.group(3)
    if month > 12:
        return m.group(0)
    return f"{prefix or 'tháng '}{month} năm {year}"


def _dm(m: re.Match) -> str:
    prefix, day, month = m.group(1), int(m.group(2)), int(m.group(3))
    if month > 12 or day > 31:
        return m.group(0)
    return f"{prefix or 'ngày '}{day} tháng {month}"


_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")
# Chữ hoa GIỮA câu — đứng sau một từ thường hoặc dấu phẩy. Đây mới là dấu hiệu của
# danh từ riêng. Lọc theo "chữ Latin không dấu" thì mọi từ tiếng Việt mở đầu câu đều
# lọt: Ai, Ban, Hung, Kinh... còn tên thật thì hầu như luôn xuất hiện giữa câu ít
# nhất một lần.
_MIDSENTENCE_CAP = re.compile(r"[a-zà-ỹ,]\s+([A-Z][a-zA-Z]*)")


def foreign_names(texts: list[str]) -> tuple[list[str], list[str]]:
    """Tên riêng chữ Latin và viết tắt xuất hiện trong bản dịch.

    Chỉ để LIỆT KÊ cho người đọc quyết định cách phát âm, không tự phiên âm: "Helle"
    đọc là "Hen-lơ" hay "Hên-lê" là lựa chọn của người làm kênh.
    """
    blob = " ".join(texts)
    acronyms = sorted(set(_ACRONYM.findall(blob)))
    names = sorted(
        {w for w in _MIDSENTENCE_CAP.findall(blob) if w not in acronyms and len(w) > 1}
    )
    return names, acronyms


def normalize_for_speech(text: str) -> str:
    """Đổi các dạng viết tắt bằng số/ký hiệu thành chữ đọc được.

    Thứ tự có ý nghĩa: ngày/tháng/năm phải xử lý trước ngày/tháng, nếu không
    "1/12/1986" bị luật ngày/tháng cắn mất phần đầu.
    """
    text = _DMY.sub(_dmy, text)
    text = _MY.sub(_my, text)
    text = _DM.sub(_dm, text)
    text = _THOUSANDS.sub(r"\1\2", text)
    # Bỏ hẳn viết tắt trong ngoặc: tên đầy đủ vừa được đọc ngay trước đó, người dẫn
    # sẽ không đọc cả hai. "Cơ quan Tình báo Trung ương Hoa Kỳ (CIA)" -> bỏ "(CIA)".
    text = _ACRONYM_PAREN.sub("", text)
    text = _PAREN.sub(r" \1", text)
    return text
