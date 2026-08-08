"""Đo CER và sai số onset.

**Sai số onset đo ở cấp ký tự, không so ``start`` của segment.** paraformer ngắt
câu theo khoảng lặng VAD còn faster-whisper ngắt theo cửa sổ 30 giây; so mốc bắt
đầu của segment giữa hai engine là đang đo *chính sách ngắt câu* chứ không đo *độ
chuẩn của timestamp*. Cách làm ở đây: align chuỗi ký tự tham chiếu với chuỗi ký
tự dự đoán, rồi lấy thời điểm của **ký tự đầu tiên** của mỗi cue tham chiếu.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from zhsub.subtitle import Cue
from zhsub.text.align import index_map
from zhsub.text.normalize import normalize_for_align, normalize_for_cer


@dataclass(slots=True)
class OnsetResult:
    median_ms: float
    p90_ms: float
    within_200ms: float  # tỉ lệ %
    within_500ms: float
    coverage: float  # % cue tham chiếu match được
    n_matched: int
    n_ref: int


def expand_tokens_to_chars(
    tokens: list[tuple[str, float, float]],
) -> tuple[list[str], list[float]]:
    """``[(text, start, end)]`` -> ``(danh sách ký tự, thời điểm từng ký tự)``.

    Token nhiều ký tự (từ tiếng Anh xen giữa, hoặc "từ" của whisper vốn hay gộp
    vài chữ Hán) được nội suy tuyến tính — không có thông tin nào mịn hơn.
    """
    chars: list[str] = []
    times: list[float] = []
    for text, start, end in tokens:
        n = len(text)
        if n == 0:
            continue
        if n == 1:
            chars.append(text)
            times.append(start)
            continue
        step = (end - start) / n
        for k, ch in enumerate(text):
            chars.append(ch)
            times.append(start + k * step)
    return chars, times


def _normalize_keeping_times(
    chars: list[str], times: list[float]
) -> tuple[str, list[float]]:
    """Chuẩn hoá từng ký tự một để giữ nguyên ánh xạ ký tự <-> thời gian.

    Chuẩn hoá cả chuỗi một lượt sẽ làm độ dài thay đổi (dấu câu bị bỏ, NFKC gộp
    ký tự) và ``times`` lệch ngay. Ký tự nào chuẩn hoá xong ra rỗng thì bỏ luôn
    cả thời gian tương ứng.
    """
    out_chars: list[str] = []
    out_times: list[float] = []
    for ch, t in zip(chars, times):
        norm = normalize_for_align(ch)
        if not norm:
            continue
        # t2s trên một ký tự Hán gần như luôn ra một ký tự; lấy ký tự đầu để
        # đảm bảo bất biến 1-1 giữa chuỗi và mảng thời gian.
        out_chars.append(norm[0])
        out_times.append(t)
    return "".join(out_chars), out_times


def compute_cer(ref_cues: list[Cue], hyp_text: str) -> float:
    """CER sau khi chuẩn hoá **giống hệt nhau** ở cả hai phía."""
    import jiwer

    ref = normalize_for_cer("".join(c.text for c in ref_cues))
    hyp = normalize_for_cer(hyp_text)
    if not ref:
        raise ValueError("Reference rỗng sau khi chuẩn hoá")
    return float(jiwer.cer(ref, hyp))


def compute_onset(
    ref_cues: list[Cue],
    pred_chars: list[str],
    pred_times: list[float],
) -> OnsetResult:
    """Sai số onset giữa reference và dự đoán, đo ở cấp ký tự."""
    ref_chars: list[str] = []
    cue_first_index: list[tuple[int, float]] = []  # (index ký tự đầu, thời điểm ref)
    for cue in ref_cues:
        norm = normalize_for_align(cue.text)
        if not norm:
            continue
        cue_first_index.append((len(ref_chars), cue.start))
        ref_chars.extend(norm)

    ref_stream = "".join(ref_chars)
    pred_stream, pred_time_list = _normalize_keeping_times(pred_chars, pred_times)

    mapping = index_map(ref_stream, pred_stream)

    errors_ms: list[float] = []
    for first_idx, ref_start in cue_first_index:
        pred_idx = mapping.get(first_idx)
        if pred_idx is None or pred_idx >= len(pred_time_list):
            # Ký tự đầu của cue không khớp được -> bỏ, không đoán bừa. Nội suy
            # sang ký tự lân cận sẽ thêm nhiễu cỡ vài trăm ms, đúng bằng độ phân
            # giải mà metric này đang cố đo.
            continue
        errors_ms.append(abs(pred_time_list[pred_idx] - ref_start) * 1000.0)

    n_ref = len(cue_first_index)
    n_matched = len(errors_ms)
    if n_matched == 0:
        return OnsetResult(float("nan"), float("nan"), 0.0, 0.0, 0.0, 0, n_ref)

    ordered = sorted(errors_ms)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    return OnsetResult(
        median_ms=statistics.median(ordered),
        p90_ms=p90,
        within_200ms=100.0 * sum(e <= 200 for e in ordered) / n_matched,
        within_500ms=100.0 * sum(e <= 500 for e in ordered) / n_matched,
        coverage=100.0 * n_matched / n_ref,
        n_matched=n_matched,
        n_ref=n_ref,
    )
