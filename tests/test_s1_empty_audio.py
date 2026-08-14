"""S1 phải dừng khi ASR không nhận ra chữ nào."""

from __future__ import annotations

import pytest

from zhsub.asr.base import AsrOutput
from zhsub.config import Config
from zhsub.jsonio import write_doc
from zhsub.models import IngestDoc, MediaInfo, SourceInfo
from zhsub.stages import s1_asr


class _Engine:
    name, version, device = "fake", "0", "cpu"
    models: dict[str, str] = {}


def _work_dir(tmp_path):
    write_doc(
        tmp_path / "ingest.json",
        IngestDoc(
            job_id="j",
            source=SourceInfo(kind="local", uri="x.mp4"),
            media=MediaInfo(wav_path="audio.wav", duration_sec=1944.7, sample_rate=16000, channels=1),
            created_at="2026-01-01T00:00:00",
        ),
    )
    return tmp_path


def test_silent_audio_stops_the_run_instead_of_producing_an_empty_srt(tmp_path, monkeypatch):
    """Gặp thật: file tải về có track audio đủ 32 phút nhưng bên trong toàn số 0.

    ASR trả 0 token, rồi S2 S3 S4 S5 đều "thành công" với 0 câu và in
    "xong -> output". File .srt rỗng suýt đi thẳng vào khâu dựng.
    """
    monkeypatch.setattr(s1_asr, "build_engine", lambda cfg: _Engine())
    monkeypatch.setattr(s1_asr, "transcribe_file", lambda *a, **k: AsrOutput(sentences=[]))

    with pytest.raises(RuntimeError, match="không nhận ra chữ nào"):
        s1_asr.run(_work_dir(tmp_path), Config())


def test_the_error_says_how_to_check_the_audio(tmp_path, monkeypatch):
    """Thông báo phải đủ để người đọc tự xác minh, không bắt họ đoán."""
    monkeypatch.setattr(s1_asr, "build_engine", lambda cfg: _Engine())
    monkeypatch.setattr(s1_asr, "transcribe_file", lambda *a, **k: AsrOutput(sentences=[]))

    with pytest.raises(RuntimeError) as exc:
        s1_asr.run(_work_dir(tmp_path), Config())

    assert "volumedetect" in str(exc.value)
    assert "-91" in str(exc.value)


def test_nothing_is_written_when_the_run_stops(tmp_path, monkeypatch):
    """Không ghi asr.json rỗng: lần chạy sau sẽ tưởng đã có kết quả và bỏ qua S1."""
    monkeypatch.setattr(s1_asr, "build_engine", lambda cfg: _Engine())
    monkeypatch.setattr(s1_asr, "transcribe_file", lambda *a, **k: AsrOutput(sentences=[]))
    work = _work_dir(tmp_path)

    with pytest.raises(RuntimeError):
        s1_asr.run(work, Config())

    assert not (work / "asr.json").exists()
