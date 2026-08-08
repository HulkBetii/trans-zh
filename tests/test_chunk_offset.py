"""Timestamp trả về phải **tuyệt đối so với đầu file**, kể cả khi audio bị chia chunk.

Đây là bất biến quan trọng nhất của S1: sai ở đây thì mọi stage sau đều lệch mà
không có cách nào phát hiện.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from zhsub.asr.base import AsrOutput, AsrSentence, AsrToken, merge_outputs, plan_chunks, shift_output
from zhsub.config import Config
from zhsub.stages.s1_asr import build_asr_doc, transcribe_file

from .fake_asr import (
    SENTENCE_LEN,
    SENTENCE_PERIOD,
    FakeEngine,
    expected_relative_starts,
)

FORTY_FIVE_MIN = 45 * 60.0


# ---------------------------------------------------------------------------
# Toán chia cửa sổ và ghép — thuần tuý, không đụng file
# ---------------------------------------------------------------------------


def test_short_file_khong_chia_chunk():
    # Dưới ngưỡng thì để FunASR tự cắt VAD; bọc thêm tầng offset chỉ tổ sinh bug.
    assert plan_chunks(1800.0, 5400.0, 1800.0, 2.0) == [(0.0, 1800.0)]


def test_cua_so_phu_kin_va_chong_lan_dung():
    duration = FORTY_FIVE_MIN
    windows = plan_chunks(duration, 600.0, 600.0, 2.0)

    assert windows[0][0] == 0.0
    assert windows[-1][1] == pytest.approx(duration)
    # Không có lỗ hổng: cửa sổ sau luôn bắt đầu trước khi cửa sổ trước kết thúc.
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert next_start < prev_end
        assert prev_end - next_start == pytest.approx(2.0)


def test_shift_output_doi_ca_token_lan_cau():
    out = AsrOutput(
        sentences=[
            AsrSentence("你好", 1.0, 2.0, [AsrToken("你", 1.0, 1.5), AsrToken("好", 1.5, 2.0)])
        ]
    )
    moved = shift_output(out, 100.0)

    assert moved.sentences[0].start == pytest.approx(101.0)
    assert moved.sentences[0].end == pytest.approx(102.0)
    assert [t.start for t in moved.sentences[0].tokens] == pytest.approx([101.0, 101.5])
    # Không được sửa tại chỗ, nếu không thì chạy lại là cộng offset hai lần.
    assert out.sentences[0].start == pytest.approx(1.0)


def test_merge_khu_phan_lap_o_vung_chong_lan():
    def one(start: float) -> AsrOutput:
        return AsrOutput(
            sentences=[AsrSentence("字", start, start + 1.0, [AsrToken("字", start, start + 1.0)])]
        )

    # Chunk 2 bắt đầu ở giây 98; câu ở giây 99 tuyệt đối đã có trong chunk 1.
    merged = merge_outputs([(0.0, one(99.0)), (98.0, one(1.0))])

    assert len(merged.sentences) == 1
    assert merged.sentences[0].start == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# End-to-end với audio thật 45 phút + engine giả
# ---------------------------------------------------------------------------


def _write_silence(path: Path, duration_sec: float, sample_rate: int = 16000) -> Path:
    """WAV im lặng, ghi theo block để không nuốt hết RAM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(duration_sec * sample_rate)
    block = sample_rate * 60
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        written = 0
        silence = b"\x00\x00" * block
        while written < total:
            n = min(block, total - written)
            w.writeframes(silence if n == block else b"\x00\x00" * n)
            written += n
    return path


@pytest.mark.slow
def test_file_45_phut_timestamp_tuyet_doi(tmp_path: Path):
    wav = _write_silence(tmp_path / "long.wav", FORTY_FIVE_MIN)

    cfg = Config()
    cfg.asr.outer_chunk_threshold_sec = 600.0
    cfg.asr.outer_chunk_sec = 600.0
    cfg.asr.outer_chunk_overlap_sec = 2.0

    engine = FakeEngine()
    out = transcribe_file(wav, FORTY_FIVE_MIN, engine, cfg, scratch_dir=tmp_path / "chunks")

    assert len(engine.calls) > 1, "file 45 phút với ngưỡng 10 phút thì phải chia chunk"

    starts = [s.start for s in out.sentences]
    assert starts == sorted(starts), "timestamp phải tăng dần sau khi ghép"
    assert max(s.end for s in out.sentences) <= FORTY_FIVE_MIN + 1e-6

    # Câu cuối phải nằm gần cuối file, không bị kẹt trong phạm vi chunk đầu tiên
    # (đúng cái xảy ra khi quên cộng offset).
    assert starts[-1] > FORTY_FIVE_MIN - SENTENCE_PERIOD - SENTENCE_LEN

    # Mỗi mốc tuyệt đối phải bằng (điểm đầu của một chunk) + (mốc tương đối mà
    # FakeEngine sinh trong chunk đó). Cộng thiếu, cộng thừa hay cộng dồn offset
    # đều làm mốc rơi ra ngoài tập này.
    windows = plan_chunks(
        FORTY_FIVE_MIN,
        cfg.asr.outer_chunk_threshold_sec,
        cfg.asr.outer_chunk_sec,
        cfg.asr.outer_chunk_overlap_sec,
    )
    valid = {
        round(win_start + rel, 6)
        for win_start, win_end in windows
        for rel in expected_relative_starts(win_end - win_start)
    }
    for start in starts:
        assert round(start, 6) in valid, f"start {start} không khớp offset của chunk nào"

    # Sau khi khử phần lặp thì không cue nào được chồng lên cue kế tiếp.
    for prev, nxt in zip(out.sentences, out.sentences[1:]):
        assert prev.end <= nxt.start + 1e-6

    doc = build_asr_doc(out, engine, FORTY_FIVE_MIN)
    token_starts = [t.start for t in doc.tokens]
    assert token_starts == sorted(token_starts)
    assert doc.tokens[-1].end <= FORTY_FIVE_MIN + 1e-6
    assert len(doc.raw_segments) == len(out.sentences)

    # token_range phải trỏ đúng: text ghép từ token phải khớp text của câu.
    for seg, sent in zip(doc.raw_segments, out.sentences):
        assert doc.display_text(*seg.token_range) == sent.text
