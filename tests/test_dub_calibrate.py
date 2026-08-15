"""Đo tốc độ đọc: chọn mẫu và khớp tuyến tính."""

from __future__ import annotations

import pytest

from zhsub.config import Config
from zhsub.dub import calibrate
from zhsub.dub.calibrate import calibration_hash, fit, pick_samples
from zhsub.models import TtsCalibration, TtsCalibrationPoint


def test_samples_span_short_to_long():
    """Lấy toàn câu dài thì ước lượng overhead sai bét — overhead chỉ lộ ra ở câu
    ngắn, nơi nó chiếm phần lớn thời lượng."""
    texts = [" ".join(["x"] * n) for n in range(1, 41)]

    picked = pick_samples(texts, 8)

    assert len(picked) == 8
    assert len(picked[0].split()) == 1
    assert len(picked[-1].split()) == 40


def test_every_sample_is_used_when_there_are_few():
    texts = ["a", "a b", "a b c"]

    assert pick_samples(texts, 8) == texts


def test_blank_lines_are_skipped():
    assert pick_samples(["", "   ", "a b"], 8) == ["a b"]


def test_fit_recovers_the_two_constants():
    # thời lượng = 0.15 + âm_tiết x 0.217
    points = [(n, 0.15 + n * 0.217) for n in (3, 10, 20, 28)]

    overhead, per_syllable = fit(points)

    assert round(overhead, 3) == 0.15
    assert round(per_syllable, 3) == 0.217


def test_fit_needs_samples_of_differing_length():
    """Mọi mẫu cùng số âm tiết thì hệ vô định — báo lỗi thay vì trả số bịa."""
    with pytest.raises(ValueError, match="cùng số âm tiết"):
        fit([(10, 2.3), (10, 2.4), (10, 2.2)])


def test_calibration_hash_ignores_provenance_not_persisted_in_sqlite():
    original = TtsCalibration(
        voice_id="voice-a",
        overhead_sec=0.15,
        sec_per_syllable=0.217,
        samples_hash="original-samples",
        created_at="2026-01-01T00:00:00Z",
        points=[TtsCalibrationPoint(syllables=4, duration_sec=1.1)],
    )
    reloaded = original.model_copy(
        update={
            "samples_hash": "database-revision",
            "created_at": "2026-02-01T00:00:00Z",
            "points": [],
        }
    )

    assert calibration_hash(original) == calibration_hash(reloaded)


def test_calibration_accepts_a_voice_override_without_changing_job_voice(
    tmp_path, monkeypatch
):
    calls: list[tuple[str, str]] = []

    class FakeSpeechClient:
        def __init__(self, base_url: str, api_key: str) -> None:
            pass

        def synthesize(self, text: str, voice_id: str, speed: float) -> str:
            calls.append((text, voice_id))
            return text

        def wait(self, task_id: str) -> dict[str, str]:
            return {"audio_url": task_id}

        def download(self, url, destination):
            destination.write_bytes(b"audio")
            return destination

        def close(self) -> None:
            pass

    monkeypatch.setattr(calibrate, "SpeechClient", FakeSpeechClient)
    monkeypatch.setattr(calibrate, "probe_duration", lambda path: 1.0)
    monkeypatch.setattr(calibrate, "speech_bounds", lambda path, duration: (0.0, duration))
    monkeypatch.setenv("TEST_DUB_API_KEY", "secret")
    cfg = Config()
    cfg.dub.api_key_env = "TEST_DUB_API_KEY"
    cfg.dub.voice_id = "job-voice"
    texts = [" ".join(["x"] * size) for size in range(1, 9)]

    result = calibrate.calibrate_voice(
        tmp_path,
        cfg,
        voice_id="calibration-voice",
        sample_texts=texts,
    )

    assert result.voice_id == "calibration-voice"
    assert len(calls) == 8
    assert {voice for _, voice in calls} == {"calibration-voice"}
