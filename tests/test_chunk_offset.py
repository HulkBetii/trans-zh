"""Returned timestamps must be **absolute to the start of the file**, even when chunked.

This is S1's most important invariant: get it wrong and every later stage is
skewed with no way to detect it.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from zhsub.asr.base import (
    AsrOutput,
    AsrSentence,
    AsrToken,
    merge_outputs,
    plan_chunks,
    shift_output,
)
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
# Window planning and merging — pure, no file I/O
# ---------------------------------------------------------------------------


def test_short_file_is_not_chunked():
    # Below the threshold FunASR handles VAD splitting itself; wrapping another
    # offset layer around it only creates bugs.
    assert plan_chunks(1800.0, 5400.0, 1800.0, 2.0) == [(0.0, 1800.0)]


def test_windows_cover_the_file_with_the_configured_overlap():
    duration = FORTY_FIVE_MIN
    windows = plan_chunks(duration, 600.0, 600.0, 2.0)

    assert windows[0][0] == 0.0
    assert windows[-1][1] == pytest.approx(duration)
    # No gaps: each window starts before the previous one ends.
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert next_start < prev_end
        assert prev_end - next_start == pytest.approx(2.0)


def test_shift_output_moves_sentences_and_tokens():
    out = AsrOutput(
        sentences=[
            AsrSentence("你好", 1.0, 2.0, [AsrToken("你", 1.0, 1.5), AsrToken("好", 1.5, 2.0)])
        ]
    )
    moved = shift_output(out, 100.0)

    assert moved.sentences[0].start == pytest.approx(101.0)
    assert moved.sentences[0].end == pytest.approx(102.0)
    assert [t.start for t in moved.sentences[0].tokens] == pytest.approx([101.0, 101.5])
    # Must not mutate in place, or a re-run would add the offset twice.
    assert out.sentences[0].start == pytest.approx(1.0)


def test_merge_drops_duplicates_from_the_overlap_region():
    def one(start: float) -> AsrOutput:
        return AsrOutput(
            sentences=[AsrSentence("字", start, start + 1.0, [AsrToken("字", start, start + 1.0)])]
        )

    # Chunk 2 starts at second 98; its sentence at absolute 99 is already in chunk 1.
    merged = merge_outputs([(0.0, one(99.0)), (98.0, one(1.0))])

    assert len(merged.sentences) == 1
    assert merged.sentences[0].start == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# End-to-end over real 45-minute audio with a fake engine
# ---------------------------------------------------------------------------


def _write_silence(path: Path, duration_sec: float, sample_rate: int = 16000) -> Path:
    """Silent WAV, written in blocks so it does not eat all the memory."""
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
def test_45_minute_file_yields_absolute_timestamps(tmp_path: Path):
    wav = _write_silence(tmp_path / "long.wav", FORTY_FIVE_MIN)

    cfg = Config()
    cfg.asr.outer_chunk_threshold_sec = 600.0
    cfg.asr.outer_chunk_sec = 600.0
    cfg.asr.outer_chunk_overlap_sec = 2.0

    engine = FakeEngine()
    out = transcribe_file(wav, FORTY_FIVE_MIN, engine, cfg, scratch_dir=tmp_path / "chunks")

    assert len(engine.calls) > 1, "a 45-minute file with a 10-minute threshold must be chunked"

    starts = [s.start for s in out.sentences]
    assert starts == sorted(starts), "timestamps must be ascending after merging"
    assert max(s.end for s in out.sentences) <= FORTY_FIVE_MIN + 1e-6

    # The last sentence must land near the end of the file, not be stuck inside the
    # first chunk's range — which is exactly what happens if the offset is dropped.
    assert starts[-1] > FORTY_FIVE_MIN - SENTENCE_PERIOD - SENTENCE_LEN

    # Every absolute start must equal (some chunk's start) + (a relative offset the
    # fake engine emits inside that chunk). Missing, doubled or accumulated offsets
    # all push a start outside this set.
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
        assert round(start, 6) in valid, f"start {start} matches no chunk offset"

    # After duplicate removal no cue may overlap the next one.
    for prev, nxt in zip(out.sentences, out.sentences[1:]):
        assert prev.end <= nxt.start + 1e-6

    doc = build_asr_doc(out, engine, FORTY_FIVE_MIN)
    token_starts = [t.start for t in doc.tokens]
    assert token_starts == sorted(token_starts)
    assert doc.tokens[-1].end <= FORTY_FIVE_MIN + 1e-6
    assert len(doc.raw_segments) == len(out.sentences)

    # token_range must point at the right span: text rebuilt from tokens should
    # reproduce the sentence text exactly.
    for seg, sent in zip(doc.raw_segments, out.sentences):
        assert doc.display_text(*seg.token_range) == sent.text
