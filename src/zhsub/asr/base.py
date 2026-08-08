"""Interface ASR + phần toán ghép chunk.

``FunASRParaformer`` là implementation đầu tiên và duy nhất ở v1. Thêm
``SenseVoice`` / ``FasterWhisper`` sau chỉ cần implement :class:`ASREngine`,
không đụng tới stage nào khác.

Toàn bộ phép cộng offset nằm ở đây dưới dạng **hàm thuần**, tách khỏi engine, để
test được bằng fake engine — không cần GPU, không cần audio thật. Đây là chỗ dễ
sinh bug lệch timestamp nhất nên nó phải là phần dễ test nhất.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class AsrToken:
    """Một token có timestamp. Tiếng Trung thường là 1 ký tự, tiếng Anh là 1 từ."""

    text: str
    start: float
    end: float
    punct_after: str | None = None


@dataclass(slots=True)
class AsrSentence:
    text: str  # có dấu câu, để hiển thị
    start: float
    end: float
    tokens: list[AsrToken] = field(default_factory=list)


@dataclass(slots=True)
class AsrOutput:
    sentences: list[AsrSentence] = field(default_factory=list)
    # Số câu mà số lượng timestamp không khớp số lượng token, phải chia đều thời
    # gian. Con số này được ghi vào asr.json để biết kết quả có đáng tin không.
    degraded_sentences: int = 0


@runtime_checkable
class ASREngine(Protocol):
    name: str
    version: str
    models: dict[str, str]
    device: str

    def transcribe(self, wav_path: str | Path) -> AsrOutput:
        """Nhận dạng toàn bộ file. Timestamp tính từ đầu **file được truyền vào**."""
        ...


# ---------------------------------------------------------------------------
# Ghép chunk — hàm thuần
# ---------------------------------------------------------------------------


def plan_chunks(
    duration_sec: float,
    threshold_sec: float,
    chunk_sec: float,
    overlap_sec: float,
) -> list[tuple[float, float]]:
    """Chia file thành các cửa sổ ``[start, end)`` tính bằng giây.

    Dưới ``threshold_sec`` thì trả về đúng một cửa sổ phủ cả file: FunASR đã tự
    cắt theo VAD và trả timestamp tuyệt đối rồi, bọc thêm một tầng offset nữa chỉ
    tổ tạo bug.

    Các cửa sổ chồng nhau ``overlap_sec`` để câu nằm vắt qua ranh giới không bị
    mất; phần trùng do :func:`merge_outputs` khử.
    """
    if duration_sec <= 0:
        return [(0.0, 0.0)]
    if duration_sec <= threshold_sec:
        return [(0.0, duration_sec)]
    if chunk_sec <= overlap_sec:
        raise ValueError(f"chunk_sec ({chunk_sec}) phải lớn hơn overlap_sec ({overlap_sec})")

    step = chunk_sec - overlap_sec
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_sec:
        end = min(start + chunk_sec, duration_sec)
        windows.append((start, end))
        if end >= duration_sec:
            break
        start += step
    return windows


def shift_output(out: AsrOutput, offset: float) -> AsrOutput:
    """Dời mọi timestamp đi ``offset`` giây. Không sửa tại chỗ."""
    if offset == 0.0:
        return out
    return AsrOutput(
        sentences=[
            AsrSentence(
                text=s.text,
                start=s.start + offset,
                end=s.end + offset,
                tokens=[replace(t, start=t.start + offset, end=t.end + offset) for t in s.tokens],
            )
            for s in out.sentences
        ],
        degraded_sentences=out.degraded_sentences,
    )


def merge_outputs(parts: list[tuple[float, AsrOutput]]) -> AsrOutput:
    """Ghép kết quả từng chunk lại, timestamp quy về **tuyệt đối so với đầu file**.

    Args:
        parts: danh sách ``(offset_giây, kết_quả_chunk)``, kết quả mang timestamp
            tương đối so với đầu chunk.

    Câu nào bắt đầu trước điểm kết thúc của câu đã nhận trước đó thì bị loại —
    đó là phần lặp ở vùng chồng lấn. Cắt theo mốc thời gian chứ không so text, vì
    hai chunk nhận dạng cùng một đoạn audio thường ra chữ hơi khác nhau.
    """
    merged: list[AsrSentence] = []
    degraded = 0
    frontier = float("-inf")

    for offset, out in parts:
        degraded += out.degraded_sentences
        for sent in shift_output(out, offset).sentences:
            if sent.start < frontier - 1e-6:
                continue
            merged.append(sent)
            frontier = max(frontier, sent.end)

    merged.sort(key=lambda s: (s.start, s.end))
    return AsrOutput(sentences=merged, degraded_sentences=degraded)
