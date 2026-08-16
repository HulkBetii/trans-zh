from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zhsub.config import Config
from zhsub.jsonio import read_doc, sha256_json_canonical, write_doc
from zhsub.models import (
    IngestDoc,
    MediaInfo,
    OverrideChange,
    RenderReport,
    Segment,
    SegmentsDoc,
    SourceInfo,
    TranslationItem,
    TranslationsDoc,
    TtsCalibration,
)
from zhsub.overrides import (
    OverrideConflictError,
    apply_override_changes,
    effective_hash,
    effective_text_map,
    get_override_state,
)
from zhsub.stages import s5_render, s6_dub


def _dub_calibration() -> TtsCalibration:
    """S6 từ chối đoán hằng số, nên test phải nói rõ số đo thuộc giọng nào."""
    return TtsCalibration(
        voice_id="voice",
        overhead_sec=0.15,
        sec_per_syllable=0.217,
        samples_hash="test",
        created_at="2026-01-01T00:00:00Z",
    )


def _write_base(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(
            source_asr_sha256="asr",
            method="llm",
            segments=[
                Segment(
                    id=1,
                    start=0.0,
                    end=1.0,
                    text_zh="第一句",
                    token_range=(0, 3),
                ),
                Segment(
                    id=2,
                    start=2.0,
                    end=3.0,
                    text_zh="第二句",
                    token_range=(3, 6),
                ),
            ],
        ),
    )
    write_doc(
        work_dir / "translations.vi.json",
        TranslationsDoc(
            lang="vi",
            model="test-model",
            prompt_version=1,
            glossary_hash="glossary",
            segments_hash="segments",
            items=[
                TranslationItem(id=1, text_zh="第一句", translation="Câu một"),
                TranslationItem(id=2, text_zh="第二句", translation="Câu hai"),
            ],
        ),
    )


def _save(work_dir: Path, changes: list[OverrideChange | dict]) -> str:
    revision = get_override_state(work_dir, "vi").revision
    return apply_override_changes(work_dir, "vi", revision, changes).revision


def test_valid_override_takes_precedence_and_can_be_deleted(tmp_path: Path) -> None:
    _write_base(tmp_path)

    revision = _save(tmp_path, [{"segment_id": 1, "text": "Bản sửa tay"}])

    assert effective_text_map(tmp_path, "vi") == {1: "Bản sửa tay", 2: "Câu hai"}
    state = get_override_state(tmp_path, "vi")
    assert state.evaluations[0].status == "valid"

    apply_override_changes(
        tmp_path,
        "vi",
        revision,
        [OverrideChange(segment_id=1, text=None)],
    )

    assert effective_text_map(tmp_path, "vi") == {1: "Câu một", 2: "Câu hai"}
    assert get_override_state(tmp_path, "vi").items == []


def test_blank_override_is_rejected(tmp_path: Path) -> None:
    _write_base(tmp_path)

    with pytest.raises(ValidationError, match="must not be blank"):
        _save(tmp_path, [{"segment_id": 1, "text": "  \n"}])


def test_source_change_makes_override_stale_and_restores_model_text(tmp_path: Path) -> None:
    _write_base(tmp_path)
    _save(tmp_path, [{"segment_id": 1, "text": "Bản sửa tay"}])
    segments = read_doc(tmp_path / "segments.json", SegmentsDoc)
    segments.segments[0].text_zh = "第一句已改"
    write_doc(tmp_path / "segments.json", segments)

    row = effective_text_map(tmp_path, "vi")
    state = get_override_state(tmp_path, "vi")

    assert row[1] == "Câu một"
    assert state.evaluations[0].status == "stale"


def test_model_change_keeps_override_and_marks_base_changed(tmp_path: Path) -> None:
    _write_base(tmp_path)
    _save(tmp_path, [{"segment_id": 1, "text": "Bản sửa tay"}])
    translations = read_doc(tmp_path / "translations.vi.json", TranslationsDoc)
    translations.items[0].translation = "Bản máy mới"
    write_doc(tmp_path / "translations.vi.json", translations)

    rows = effective_text_map(tmp_path, "vi")
    state = get_override_state(tmp_path, "vi")

    assert rows[1] == "Bản sửa tay"
    assert state.evaluations[0].status == "base_changed"


def test_obsolete_revision_is_rejected_without_losing_first_write(tmp_path: Path) -> None:
    _write_base(tmp_path)
    original_revision = get_override_state(tmp_path, "vi").revision
    apply_override_changes(
        tmp_path,
        "vi",
        original_revision,
        [{"segment_id": 1, "text": "Tab một"}],
    )

    with pytest.raises(OverrideConflictError) as error:
        apply_override_changes(
            tmp_path,
            "vi",
            original_revision,
            [{"segment_id": 1, "text": "Tab hai"}],
        )

    assert error.value.base_revision == original_revision
    assert effective_text_map(tmp_path, "vi")[1] == "Tab một"


def test_revision_conflicts_when_machine_translation_changes(tmp_path: Path) -> None:
    _write_base(tmp_path)
    original_revision = get_override_state(tmp_path, "vi").revision
    translations = read_doc(tmp_path / "translations.vi.json", TranslationsDoc)
    translations.items[0].translation = "Bản máy mới"
    write_doc(tmp_path / "translations.vi.json", translations)

    with pytest.raises(OverrideConflictError):
        apply_override_changes(
            tmp_path,
            "vi",
            original_revision,
            [{"segment_id": 1, "text": "Bản sửa cho bản máy cũ"}],
        )

    assert get_override_state(tmp_path, "vi").items == []


def test_revision_conflicts_when_source_segment_changes(tmp_path: Path) -> None:
    _write_base(tmp_path)
    original_revision = get_override_state(tmp_path, "vi").revision
    segments = read_doc(tmp_path / "segments.json", SegmentsDoc)
    segments.segments[0].text_zh = "第一句已改"
    write_doc(tmp_path / "segments.json", segments)

    with pytest.raises(OverrideConflictError):
        apply_override_changes(
            tmp_path,
            "vi",
            original_revision,
            [{"segment_id": 1, "text": "Bản sửa cho source cũ"}],
        )

    assert get_override_state(tmp_path, "vi").items == []


def test_render_uses_effective_text_and_records_its_hash(tmp_path: Path) -> None:
    work_dir = tmp_path / "work" / "job"
    output_dir = tmp_path / "output"
    _write_base(work_dir)
    write_doc(
        work_dir / "ingest.json",
        IngestDoc(
            job_id="job",
            source=SourceInfo(kind="local", uri="video.mp4", title="video"),
            media=MediaInfo(
                wav_path="audio.wav",
                duration_sec=4.0,
                sample_rate=16000,
                channels=1,
            ),
            created_at="2026-08-15T00:00:00Z",
        ),
    )
    _save(work_dir, [{"segment_id": 1, "text": "Bản sửa tay"}])

    report = s5_render.run(
        work_dir,
        Config(),
        ["vi"],
        output_dir,
        formats=("srt",),
    )

    subtitle = Path(report.outputs[0]).read_text(encoding="utf-8-sig")
    assert "Bản sửa tay" in subtitle
    assert "Câu một" not in subtitle
    assert report.effective_translation_hashes == {"vi": effective_hash(work_dir, "vi")}
    assert report.segments_render_hash == sha256_json_canonical(
        {
            "duration_sec": 4.0,
            "segments": [
                [1, 0.0, 1.0, "第一句"],
                [2, 2.0, 3.0, "第二句"],
            ],
        }
    )
    assert report.render_request_hash == sha256_json_canonical(
        {"targets": ["vi"], "formats": ["srt"], "bilingual": False}
    )


def test_legacy_render_report_without_hashes_remains_readable() -> None:
    report = RenderReport.model_validate({"schema": 1, "outputs": [], "warnings": []})

    assert report.effective_translation_hashes == {}
    assert report.segments_render_hash == ""
    assert report.render_request_hash == ""


def test_dub_cache_invalidates_only_the_edited_cue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "work" / "job"
    _write_base(work_dir)
    synthesized: list[str] = []

    class FakeSpeechClient:
        def __init__(self, base_url: str, api_key: str) -> None:
            pass

        def credits(self) -> int:
            return 100

        def synthesize(self, text: str, voice_id: str, speed: float) -> str:
            synthesized.append(text)
            return f"task-{len(synthesized)}"

        def wait(self, task_id: str) -> dict[str, str]:
            return {"audio_url": task_id}

        def download(self, url: str, destination: Path) -> Path:
            destination.write_bytes(b"audio")
            return destination

        def close(self) -> None:
            pass

    monkeypatch.setattr(s6_dub, "SpeechClient", FakeSpeechClient)
    monkeypatch.setattr(s6_dub, "probe_duration", lambda path: 0.5)
    monkeypatch.setattr(s6_dub, "speech_bounds", lambda path, duration: (0.0, duration))
    monkeypatch.setattr(s6_dub, "decode_pcm", lambda *args: b"pcm")
    monkeypatch.setattr(s6_dub, "assemble", lambda *args: None)
    monkeypatch.setenv("TEST_DUB_API_KEY", "secret")
    config = Config()
    config.dub.api_key_env = "TEST_DUB_API_KEY"
    config.dub.voice_id = "voice"
    config.dub.concurrency = 1

    s6_dub.run(work_dir, config, "vi", tmp_path / "output", calibration=_dub_calibration())
    _save(work_dir, [{"segment_id": 1, "text": "Bản sửa tay"}])
    s6_dub.run(work_dir, config, "vi", tmp_path / "output", calibration=_dub_calibration())

    assert synthesized == ["Câu một", "Câu hai", "Bản sửa tay"]
