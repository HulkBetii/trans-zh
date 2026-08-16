"""CLI lồng tiếng: lấy hiệu chuẩn ở đâu, và ghi vào đâu.

CLI trước đây chưa bao giờ đọc bảng `voice_calibrations`, còn `dub-calibrate`
chỉ in hai hằng số ra màn hình. Hai bên vì thế có thể chạy bằng hai bộ số khác
nhau mà không ai biết.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zhsub.cli import _resolve_dub_calibration
from zhsub.config import Config
from zhsub.dub.calibrate import UncalibratedVoiceError
from zhsub.dub.spoken import ensure_speech_doc
from zhsub.jobs import TTS_PROVIDER, JobStore


def _config(tmp_path: Path, voice_id: str) -> Config:
    cfg = Config()
    cfg.paths.jobs_db = str(tmp_path / "jobs.db")
    cfg.dub.voice_id = voice_id
    cfg.dub.overhead_sec = 0.15
    cfg.dub.sec_per_syllable = 0.217
    return cfg


def _job(tmp_path: Path, voice_id: str) -> Path:
    work_dir = tmp_path / "work" / "job-1"
    work_dir.mkdir(parents=True)
    ensure_speech_doc(work_dir, voice_id)
    return work_dir


def test_cli_prefers_the_stored_calibration_over_the_toml_constants(tmp_path):
    """Kho số đo là nguồn sự thật; hằng số TOML chỉ là phương án cuối."""
    cfg = _config(tmp_path, "voice-a")
    work_dir = _job(tmp_path, "voice-a")
    JobStore(cfg.paths.jobs_db).save_voice_calibration(
        TTS_PROVIDER, "voice-a", 0.05, 0.30, sample_count=8, source_job_id="job-1"
    )

    calibration = _resolve_dub_calibration(work_dir, cfg)

    assert (calibration.overhead_sec, calibration.sec_per_syllable) == (0.05, 0.30)


def test_cli_falls_back_to_the_toml_constants_for_the_configured_voice(tmp_path):
    cfg = _config(tmp_path, "voice-a")
    work_dir = _job(tmp_path, "voice-a")

    calibration = _resolve_dub_calibration(work_dir, cfg)

    assert calibration.voice_id == "voice-a"
    assert (calibration.overhead_sec, calibration.sec_per_syllable) == (0.15, 0.217)


def test_cli_refuses_when_the_toml_constants_belong_to_another_voice(tmp_path):
    """Đúng cái bẫy mà zhsub.toml tự cảnh báo, giờ được chặn thật.

    Job dùng voice-b, còn hai hằng số trong [dub] là số đo của voice-a. Trước đây
    S6 lặng lẽ áp số của voice-a cho voice-b.
    """
    cfg = _config(tmp_path, "voice-a")
    work_dir = _job(tmp_path, "voice-b")

    with pytest.raises(UncalibratedVoiceError) as excinfo:
        _resolve_dub_calibration(work_dir, cfg)

    assert "voice-a" in str(excinfo.value) and "voice-b" in str(excinfo.value)


def test_cli_refuses_when_nothing_has_ever_been_measured(tmp_path):
    cfg = _config(tmp_path, "SET_ME")
    work_dir = _job(tmp_path, "voice-b")

    with pytest.raises(UncalibratedVoiceError, match="chưa có số đo"):
        _resolve_dub_calibration(work_dir, cfg)


def test_a_stored_calibration_wins_even_for_a_voice_the_config_never_names(tmp_path):
    """Chọn giọng trong Studio rồi chạy CLI là luồng thường gặp nhất."""
    cfg = _config(tmp_path, "voice-a")
    work_dir = _job(tmp_path, "voice-b")
    JobStore(cfg.paths.jobs_db).save_voice_calibration(
        TTS_PROVIDER, "voice-b", 0.0, 0.2626, sample_count=8, source_job_id="job-1"
    )

    calibration = _resolve_dub_calibration(work_dir, cfg)

    assert calibration.voice_id == "voice-b"
    assert calibration.sec_per_syllable == 0.2626
