from __future__ import annotations

from pathlib import Path

import pytest

from zhsub.dub.spoken import (
    SpeechConflictError,
    apply_spoken_changes,
    effective_spoken_items,
    get_speech_state,
)
from zhsub.jsonio import read_doc, write_doc
from zhsub.models import Segment, SegmentsDoc, TranslationItem, TranslationsDoc


def _write_base(work_dir: Path) -> None:
    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(
            source_asr_sha256="asr",
            method="llm",
            segments=[
                Segment(id=1, start=0, end=1, text_zh="第一句", token_range=(0, 1)),
                Segment(id=2, start=1, end=2, text_zh="第二句", token_range=(1, 2)),
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
                TranslationItem(id=1, text_zh="第一句", translation="Ngày 1/12/1986."),
                TranslationItem(id=2, text_zh="第二句", translation="Câu thứ hai."),
            ],
        ),
    )


def test_default_spoken_text_is_normalized_effective_subtitle(tmp_path: Path) -> None:
    _write_base(tmp_path)

    rows = effective_spoken_items(tmp_path, "voice-a")

    assert rows[0].effective_spoken_text == "Ngày 1 tháng 12 năm 1986."
    assert rows[0].overridden is False


def test_manual_spoken_text_is_sent_exactly_without_normalizing_again(tmp_path: Path) -> None:
    _write_base(tmp_path)
    state = get_speech_state(tmp_path, "voice-a")

    apply_spoken_changes(
        tmp_path,
        "voice-a",
        state.revision,
        [{"segment_id": 1, "text": "Đọc đúng 1/12/1986"}],
    )

    row = effective_spoken_items(tmp_path)[0]
    assert row.effective_spoken_text == "Đọc đúng 1/12/1986"
    assert row.overridden is True


def test_manual_spoken_text_survives_a_changed_subtitle_base(tmp_path: Path) -> None:
    _write_base(tmp_path)
    state = get_speech_state(tmp_path, "voice-a")
    apply_spoken_changes(
        tmp_path,
        "voice-a",
        state.revision,
        [{"segment_id": 1, "text": "Cách đọc tay"}],
    )
    translations = read_doc(tmp_path / "translations.vi.json", TranslationsDoc)
    translations.items[0].translation = "Bản phụ đề mới"
    write_doc(tmp_path / "translations.vi.json", translations)

    row = effective_spoken_items(tmp_path)[0]
    evaluation = get_speech_state(tmp_path).evaluations[0]

    assert row.effective_spoken_text == "Cách đọc tay"
    assert row.base_changed is True
    assert evaluation.status == "base_changed"


def test_stale_source_override_does_not_apply(tmp_path: Path) -> None:
    _write_base(tmp_path)
    state = get_speech_state(tmp_path, "voice-a")
    apply_spoken_changes(
        tmp_path,
        "voice-a",
        state.revision,
        [{"segment_id": 1, "text": "Cách đọc tay"}],
    )
    segments = read_doc(tmp_path / "segments.json", SegmentsDoc)
    segments.segments[0].text_zh = "第一句改"
    write_doc(tmp_path / "segments.json", segments)

    row = effective_spoken_items(tmp_path)[0]

    assert row.effective_spoken_text == "Ngày 1 tháng 12 năm 1986."
    assert row.stale is True


def test_speech_update_rejects_an_obsolete_revision(tmp_path: Path) -> None:
    _write_base(tmp_path)
    revision = get_speech_state(tmp_path, "voice-a").revision
    apply_spoken_changes(
        tmp_path,
        "voice-a",
        revision,
        [{"segment_id": 1, "text": "Tab một"}],
    )

    with pytest.raises(SpeechConflictError):
        apply_spoken_changes(
            tmp_path,
            "voice-a",
            revision,
            [{"segment_id": 1, "text": "Tab hai"}],
        )
