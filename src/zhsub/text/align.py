"""Align hai chuỗi ký tự bằng ``difflib``.

Dùng chung ở hai chỗ:

* **S2 repair** — LLM được yêu cầu trả về đúng chuỗi đã gửi kèm dấu ngắt, nhưng
  thực tế nó hay tự ý đổi 全角/半角, đổi chữ số, "sửa lỗi chính tả" nó tưởng là
  sai. So khớp tuyệt đối rồi retry mù không giải quyết được mấy chuyện đó; align
  lại thì các đoạn khớp vẫn khôi phục được vị trí ngắt.
* **bench** — map chuỗi ký tự tham chiếu sang chuỗi ký tự dự đoán để đo sai số
  onset ở cấp ký tự, thay vì so ``start`` của segment (vốn đo cách ngắt câu chứ
  không đo độ chuẩn timestamp).
"""

from __future__ import annotations

from difflib import SequenceMatcher


def _matcher(a: str, b: str) -> SequenceMatcher:
    # autojunk=False là bắt buộc. Mặc định SequenceMatcher coi phần tử xuất hiện
    # trong >1% chuỗi là "junk" khi chuỗi dài >= 200 phần tử. Với tiếng Trung,
    # các ký tự thường gặp (的, 了, 是...) sẽ bị loại và alignment hỏng nặng.
    return SequenceMatcher(None, a, b, autojunk=False)


def index_map(a: str, b: str) -> dict[int, int]:
    """Map index trong ``a`` sang index trong ``b`` trên các block khớp nhau.

    Ký tự nào không nằm trong block khớp thì không có mặt trong kết quả — bên
    gọi tự quyết định xử lý thế nào.
    """
    out: dict[int, int] = {}
    for ai, bi, size in _matcher(a, b).get_matching_blocks():
        for off in range(size):
            out[ai + off] = bi + off
    return out


def similarity(a: str, b: str) -> float:
    return _matcher(a, b).ratio()


def recover_breaks(sent: str, resp: str, sep: str = "|") -> tuple[list[int], float]:
    """Lấy vị trí ngắt câu từ output của LLM, quy về index trong chuỗi gốc.

    Args:
        sent: chuỗi đã gửi cho LLM (không dấu ngắt, không dấu câu).
        resp: chuỗi LLM trả về, có chèn ``sep``.
        sep: ký tự đánh dấu chỗ ngắt.

    Returns:
        ``(breaks, ratio)`` — ``breaks`` là danh sách index tăng dần trong
        ``sent``, mỗi index là vị trí **bắt đầu một segment mới**. ``ratio`` là
        độ khớp giữa chuỗi gửi đi và chuỗi nhận về sau khi bỏ ``sep``; bằng 1.0
        nghĩa là LLM trả về nguyên vẹn.

    Vị trí ngắt rơi vào vùng không khớp thì bị bỏ, không đoán bừa — thà mất một
    chỗ ngắt còn hơn đặt sai chỗ rồi kéo lệch timestamp.
    """
    stripped_chars: list[str] = []
    breaks_in_resp: list[int] = []
    for ch in resp:
        if ch == sep:
            breaks_in_resp.append(len(stripped_chars))
        else:
            stripped_chars.append(ch)
    stripped = "".join(stripped_chars)

    def _clean(xs: list[int]) -> list[int]:
        return sorted({x for x in xs if 0 < x < len(sent)})

    if stripped == sent:
        return _clean(breaks_in_resp), 1.0

    m = index_map(stripped, sent)  # index trong resp-stripped -> index trong sent
    ratio = similarity(sent, stripped)
    mapped: list[int] = []
    for b in breaks_in_resp:
        if b in m:
            mapped.append(m[b])
        elif (b - 1) in m:
            # Chỗ ngắt nằm ngay sau một ký tự khớp được -> đặt sau ký tự đó.
            mapped.append(m[b - 1] + 1)
        # còn lại: rơi vào vùng LLM sửa đổi -> bỏ qua
    return _clean(mapped), ratio
