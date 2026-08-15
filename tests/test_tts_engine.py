from __future__ import annotations

import threading
from pathlib import Path

import pytest

from zhsub.config import Config
from zhsub.dub.audio import AudioPlacement
from zhsub.dub.spoken import apply_spoken_changes, get_speech_state
from zhsub.jsonio import write_doc
from zhsub.models import (
    IngestDoc,
    MediaInfo,
    Segment,
    SegmentsDoc,
    SourceInfo,
    TranslationItem,
    TranslationsDoc,
)
from zhsub.progress import Cancelled, RunContext
from zhsub.stages import s6_dub


def _write_base(work_dir: Path) -> None:
    write_doc(
        work_dir / "ingest.json",
        IngestDoc(
            job_id="job",
            source=SourceInfo(kind="local", uri="movie.mp4", title="movie"),
            media=MediaInfo(
                wav_path="audio.wav",
                duration_sec=12.0,
                sample_rate=16000,
                channels=1,
            ),
            created_at="now",
        ),
    )
    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(
            source_asr_sha256="asr",
            method="llm",
            segments=[
                Segment(id=1, start=0.0, end=1.0, text_zh="一", token_range=(0, 1)),
                Segment(id=2, start=2.0, end=3.0, text_zh="二", token_range=(1, 2)),
                Segment(id=3, start=4.0, end=5.0, text_zh="三", token_range=(2, 3)),
                Segment(id=4, start=6.0, end=7.0, text_zh="四", token_range=(3, 4)),
            ],
        ),
    )
    write_doc(
        work_dir / "translations.vi.json",
        TranslationsDoc(
            lang="vi",
            model="test",
            prompt_version=1,
            glossary_hash="glossary",
            items=[
                TranslationItem(id=1, text_zh="一", translation="Câu một"),
                TranslationItem(id=2, text_zh="二", translation="Câu hai"),
                TranslationItem(id=3, text_zh="三", translation="Câu ba"),
                TranslationItem(id=4, text_zh="四", translation="Câu bốn"),
            ],
        ),
    )


class _FakeSpeechClient:
    calls: list[str] = []

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url

    def synthesize(self, text: str, voice_id: str, speed: float) -> str:
        self.calls.append(text)
        return text

    def wait(self, task_id: str) -> dict[str, str]:
        return {"audio_url": task_id}

    def download(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")
        return destination

    def close(self) -> None:
        return None


@pytest.fixture
def fake_tts(monkeypatch: pytest.MonkeyPatch):
    _FakeSpeechClient.calls = []
    monkeypatch.setattr(s6_dub, "SpeechClient", _FakeSpeechClient)
    monkeypatch.setattr(s6_dub, "probe_duration", lambda path: 0.5)
    monkeypatch.setattr(s6_dub, "speech_bounds", lambda path, duration: (0.0, duration))
    monkeypatch.setattr(s6_dub, "decode_pcm", lambda *args: b"\x01\x02" * 250)
    monkeypatch.setenv("TEST_DUB_API_KEY", "secret")
    return _FakeSpeechClient


def _config() -> Config:
    config = Config()
    config.dub.api_key_env = "TEST_DUB_API_KEY"
    config.dub.voice_id = "voice-a"
    config.dub.concurrency = 2
    return config


def test_full_tts_uses_source_media_duration_and_writes_report(tmp_path, fake_tts, monkeypatch):
    work_dir = tmp_path / "work" / "job"
    _write_base(work_dir)
    observed: dict[str, float] = {}

    def fake_assemble(clips, sample_rate, destination, total_sec, min_gap, placements):
        observed["total_sec"] = total_sec
        for start, pcm in clips:
            duration = len(pcm) / (sample_rate * 2)
            placements.append(AudioPlacement(start, start, duration, 0.0))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mp3")

    monkeypatch.setattr(s6_dub, "assemble", fake_assemble)
    destination = s6_dub.run(work_dir, _config(), "vi", tmp_path / "output")

    report = (work_dir / "tts_report.vi.json").read_text(encoding="utf-8")
    assert destination.name == "job.vi.mp3"
    assert observed["total_sec"] == 12.0
    assert '"duration_sec": 12.0' in report
    assert '"voice_id": "voice-a"' in report
    assert len(fake_tts.calls) == 4


def test_preview_and_full_render_share_the_same_cue_cache(
    tmp_path, fake_tts, monkeypatch
):
    work_dir = tmp_path / "work" / "job"
    _write_base(work_dir)
    monkeypatch.setattr(s6_dub, "assemble", lambda *args: None)

    preview = s6_dub.preview_cue(work_dir, _config(), 1)
    s6_dub.run(work_dir, _config(), "vi", tmp_path / "output")

    assert preview.is_file()
    assert len(fake_tts.calls) == 4


def test_editing_one_spoken_cue_invalidates_only_that_cache_entry(
    tmp_path, fake_tts, monkeypatch
):
    work_dir = tmp_path / "work" / "job"
    _write_base(work_dir)
    monkeypatch.setattr(s6_dub, "assemble", lambda *args: None)

    s6_dub.run(work_dir, _config(), "vi", tmp_path / "output")
    revision = get_speech_state(work_dir).revision
    apply_spoken_changes(
        work_dir,
        "voice-a",
        revision,
        [{"segment_id": 2, "text": "Câu hai đọc khác"}],
    )
    s6_dub.run(work_dir, _config(), "vi", tmp_path / "output")

    assert fake_tts.calls.count("Câu hai đọc khác") == 1
    assert len(fake_tts.calls) == 5


def test_cancel_stops_dispatching_new_paid_cues_and_keeps_in_flight_cache(
    tmp_path, fake_tts, monkeypatch
):
    work_dir = tmp_path / "work" / "job"
    _write_base(work_dir)
    monkeypatch.setattr(s6_dub, "assemble", lambda *args: None)
    cancel = threading.Event()

    def on_progress(progress):
        if progress.stage_fraction > 0:
            cancel.set()

    ctx = RunContext(on_progress=on_progress, cancel=cancel)

    with pytest.raises(Cancelled):
        s6_dub.run(work_dir, _config(), "vi", tmp_path / "output", ctx=ctx)

    assert 1 <= len(fake_tts.calls) <= 2
    assert list((work_dir / "dub" / "vi").glob("*.mp3"))
    assert not (work_dir / "tts_report.vi.json").exists()


def test_cancel_during_pcm_decode_skips_assembly_and_report(
    tmp_path, fake_tts, monkeypatch
):
    work_dir = tmp_path / "work" / "job"
    _write_base(work_dir)
    cancel = threading.Event()

    def cancel_after_first_decode(*args):
        cancel.set()
        return b"\x01\x02" * 250

    monkeypatch.setattr(s6_dub, "decode_pcm", cancel_after_first_decode)
    monkeypatch.setattr(
        s6_dub,
        "assemble",
        lambda *args: pytest.fail("assembly must not start after cancellation"),
    )
    ctx = RunContext(cancel=cancel)

    with pytest.raises(Cancelled):
        s6_dub.run(work_dir, _config(), "vi", tmp_path / "output", ctx=ctx)

    assert not (work_dir / "tts_report.vi.json").exists()


def test_first_terminal_provider_failure_stops_new_paid_dispatch(
    tmp_path, fake_tts, monkeypatch
):
    work_dir = tmp_path / "work" / "job"
    _write_base(work_dir)
    config = _config()
    config.dub.concurrency = 1

    def fail_fetch(cue, client, voice_id, *, force=False):
        del client, voice_id, force
        fake_tts.calls.append(cue.text)
        raise RuntimeError("provider rejected request")

    monkeypatch.setattr(s6_dub, "_fetch_clip", fail_fetch)

    with pytest.raises(RuntimeError, match="1/4 cue failed"):
        s6_dub.run(work_dir, config, "vi", tmp_path / "output")

    assert len(fake_tts.calls) == 1
