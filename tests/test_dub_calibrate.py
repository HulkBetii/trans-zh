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


# Số đo thật của giọng "Ngân Kể Chuyện" (vbee), 8 mẫu đã cắt lặng. Đường thẳng
# khớp nhất cho overhead = -0.426, và lớp lưu chặn lại bằng ValueError — sau khi
# đã tiêu 8 lượt TTS.
NGAN_KE_CHUYEN = [
    (6, 1.34), (9, 2.24), (11, 2.51), (14, 3.55),
    (16, 4.52), (19, 4.64), (22, 5.64), (31, 8.56),
]


def test_fit_never_returns_a_negative_overhead():
    overhead, per_syllable = fit(NGAN_KE_CHUYEN)

    assert overhead >= 0
    assert per_syllable > 0


def test_a_pinned_overhead_refits_the_slope_instead_of_keeping_it():
    """Ép overhead về 0 mà giữ slope cũ thì sai số gấp gần ba lần."""
    overhead, per_syllable = fit(NGAN_KE_CHUYEN)
    naive_slope = 0.2844  # slope của phép khớp không ràng buộc

    def rss(slope: float) -> float:
        return sum((y - (overhead + slope * x)) ** 2 for x, y in NGAN_KE_CHUYEN)

    assert rss(per_syllable) < rss(naive_slope) / 2


def test_short_cues_get_a_positive_predicted_duration():
    """Lý do overhead âm bị cấm: cue một âm tiết dự đoán ra thời lượng âm."""
    overhead, per_syllable = fit(NGAN_KE_CHUYEN)

    assert overhead + per_syllable * 1 > 0


def test_fit_rejects_samples_whose_duration_does_not_grow():
    with pytest.raises(ValueError, match="không tăng theo số âm tiết"):
        fit([(5, 4.0), (10, 3.0), (20, 1.0)])


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
            # url == task_id == chính câu đã tổng hợp, nên độ dài clip giả lập
            # được theo số âm tiết.
            destination.write_bytes(b"a" * len(url.split()))
            return destination

        def close(self) -> None:
            pass

    monkeypatch.setattr(calibrate, "SpeechClient", FakeSpeechClient)
    # Thời lượng phải tăng theo số âm tiết. Stub cũ trả cứng 1.0 cho mọi mẫu,
    # tức một giọng đọc câu 8 âm tiết đúng bằng câu 1 âm tiết — phép khớp trên
    # dữ liệu đó cho slope bằng 0, thứ mà lớp lưu vẫn luôn từ chối.
    monkeypatch.setattr(
        calibrate, "probe_duration", lambda path: 0.15 + 0.217 * len(path.read_bytes())
    )
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
    # Hệ số phải qua được đúng cửa mà lớp lưu sẽ dựng lên.
    assert result.overhead_sec >= 0
    assert result.sec_per_syllable > 0
