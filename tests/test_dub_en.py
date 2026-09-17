"""Unit tests for English TTS dubbing with ElevenLabs voices."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zhsub.cli import _resolve_dub_calibration
from zhsub.config import Config
from zhsub.dub.calibrate import calibration_from_config
from zhsub.dub.spoken import ensure_speech_doc, load_speech_doc
from zhsub.dub.text import normalize_for_speech
from zhsub.jobs import JobStore
from zhsub.jsonio import read_doc, write_doc
from zhsub.models import (
    IngestDoc,
    MediaInfo,
    Segment,
    SegmentsDoc,
    SourceInfo,
    TranslationItem,
    TranslationsDoc,
    TtsCalibration,
    TtsReport,
)
from zhsub.stages import s6_dub


def test_english_text_normalization():
    raw_text = "On 12/1/1986, flight 100 landed at 8.000 feet."
    assert normalize_for_speech(raw_text, lang="en") == "On 12/1/1986, flight 100 landed at 8.000 feet."

    acronym_text = "Federal Bureau of Investigation (FBI) conducted the search."
    assert normalize_for_speech(acronym_text, lang="en") == "Federal Bureau of Investigation conducted the search."

    spaced_text = "  Multiple    spaces   between words.  "
    assert normalize_for_speech(spaced_text, lang="en") == "Multiple spaces between words."


def test_english_calibration_from_config():
    cfg = Config()
    cfg.dub.voice_id_en = "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    cfg.dub.overhead_sec_en = 0.12
    cfg.dub.sec_per_syllable_en = 0.280

    cal = calibration_from_config(cfg, lang="en")
    assert cal is not None
    assert cal.voice_id == "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    assert cal.overhead_sec == 0.12
    assert cal.sec_per_syllable == 0.280


def test_cli_resolve_dub_calibration_en(tmp_path: Path):
    cfg = Config()
    cfg.paths.jobs_db = str(tmp_path / "jobs.db")
    cfg.dub.voice_id_en = "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    cfg.dub.overhead_sec_en = 0.12
    cfg.dub.sec_per_syllable_en = 0.280

    work_dir = tmp_path / "job-en"
    work_dir.mkdir(parents=True)
    ensure_speech_doc(work_dir, cfg.dub.voice_id_en, lang="en")

    cal = _resolve_dub_calibration(work_dir, cfg, lang="en")
    assert cal.voice_id == "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    assert cal.overhead_sec == 0.12
    assert cal.sec_per_syllable == 0.280

    JobStore(cfg.paths.jobs_db).save_voice_calibration(
        "elevenlabs",
        "elevenlabs_BcJMy2AClTYRAgDaPpgz",
        0.10,
        0.260,
        sample_count=8,
        source_job_id="job-en",
    )
    cal_stored = _resolve_dub_calibration(work_dir, cfg, lang="en")
    assert cal_stored.overhead_sec == 0.10
    assert cal_stored.sec_per_syllable == 0.260


def _setup_mock_job(work_dir: Path):
    work_dir.mkdir(parents=True, exist_ok=True)
    write_doc(
        work_dir / "ingest.json",
        IngestDoc(
            job_id=work_dir.name,
            source=SourceInfo(kind="local", uri="test.mp3", title="Mock Flight"),
            media=MediaInfo(wav_path="audio.wav", duration_sec=10.0, sample_rate=24000, channels=1),
            created_at="2026-09-15T00:00:00Z",
        ),
    )
    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(
            source_asr_sha256="dummy_asr",
            method="llm",
            segments=[
                Segment(id=0, start=0.0, end=3.0, text_zh="一号", token_range=(0, 1)),
                Segment(id=1, start=3.5, end=7.0, text_zh="二号", token_range=(1, 2)),
            ]
        ),
    )
    write_doc(
        work_dir / "translations.en.json",
        TranslationsDoc(
            lang="en",
            model="mock_model",
            prompt_version=1,
            glossary_hash="dummy_glossary",
            items=[
                TranslationItem(id=0, text_zh="一号", translation="First segment here."),
                TranslationItem(id=1, text_zh="二号", translation="Second segment follows."),
            ]
        ),
    )


def test_s6_dub_build_plan_en(tmp_path: Path):
    work_dir = tmp_path / "job-en"
    _setup_mock_job(work_dir)

    cfg = Config()
    cfg.dub.voice_id_en = "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    cal = calibration_from_config(cfg, lang="en")

    plan = s6_dub.build_plan(work_dir, cfg, lang="en", calibration=cal)
    assert plan.lang == "en"
    assert plan.voice_id == "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    assert len(plan.cues) == 2
    assert plan.cues[0].text == "First segment here."
    assert "en" in str(plan.cues[0].clip_path)


@patch("zhsub.stages.s6_dub.assemble")
@patch("zhsub.stages.s6_dub.decode_pcm")
@patch("zhsub.stages.s6_dub.speech_bounds")
@patch("zhsub.stages.s6_dub.probe_duration")
@patch("zhsub.stages.s6_dub._fetch_clip")
def test_s6_dub_run_en(
    mock_fetch,
    mock_probe,
    mock_bounds,
    mock_decode,
    mock_assemble,
    tmp_path: Path,
):
    work_dir = tmp_path / "job_mock"
    _setup_mock_job(work_dir)
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config()
    cfg.dub.voice_id_en = "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    cal = calibration_from_config(cfg, lang="en")

    def side_effect_fetch(cue, client, voice_id, force=False):
        cue.clip_path.parent.mkdir(parents=True, exist_ok=True)
        cue.clip_path.write_bytes(b"dummy mp3 data")
        return cue.clip_path, True

    mock_fetch.side_effect = side_effect_fetch
    mock_probe.return_value = 2.0
    mock_bounds.return_value = (0.0, 2.0)
    mock_decode.return_value = b"\x00" * 96000

    dst = s6_dub.run(work_dir, cfg, "en", out_dir, calibration=cal)
    assert dst.name == "job_mock.en.mp3"

    report_path = work_dir / "tts_report.en.json"
    assert report_path.is_file()
    report = read_doc(report_path, TtsReport)
    assert report.voice_id == "elevenlabs_BcJMy2AClTYRAgDaPpgz"
    assert len(report.cues) == 2
