from __future__ import annotations

from pathlib import Path

from zhsub.config import Config
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
from zhsub.stages.s5_render import run as run_s5
from zhsub.subtitle import Cue
from zhsub.youtube_kit import (
    _remove_vietnamese_accents,
    build_youtube_upload_kit,
    extract_chapters,
    format_timestamp,
    generate_youtube_kit,
)


def test_format_timestamp():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3665) == "01:01:05"


def test_remove_vietnamese_accents():
    assert _remove_vietnamese_accents("Kỳ Án 15 Năm Mất Tích") == "Ky An 15 Nam Mat Tich"
    assert _remove_vietnamese_accents("điều tra") == "dieu tra"


def test_extract_chapters_empty():
    chaps = extract_chapters([], 100.0)
    assert len(chaps) == 4
    assert chaps[0][0] == "00:00"


def test_extract_chapters_with_cues():
    cues = [
        Cue(start=0.0, end=5.0, text="Khởi đầu câu chuyện bí ẩn"),
        Cue(start=50.0, end=55.0, text="Dấu vết tại vách đá"),
        Cue(start=100.0, end=105.0, text="Đội cứu hộ bắt đầu tìm kiếm"),
        Cue(start=200.0, end=205.0, text="Mẩu giấy để lại"),
        Cue(start=300.0, end=305.0, text="Những giả thuyết cuối cùng"),
    ]
    chaps = extract_chapters(cues, 305.0)
    assert len(chaps) >= 4
    assert chaps[0][0] == "00:00"
    assert "Mở đầu" in chaps[0][1]


def test_build_youtube_upload_kit_content():
    cues = [
        Cue(start=0.0, end=5.0, text="Khởi đầu câu chuyện bí ẩn"),
        Cue(start=30.0, end=35.0, text="Đoạn đường núi hiểm trở"),
    ]
    glossary = {
        "terms": [
            {"zh": "任铁生", "vi": "Nhậm Thiết Sinh", "type": "person"},
            {"zh": "北京", "vi": "Bắc Kinh", "type": "org"},
        ],
        "style": {"subject_third_person_vi": "ông ấy"},
    }
    kit = build_youtube_upload_kit(
        title="Vụ Án Nhậm Thiết Sinh",
        duration_sec=35.0,
        cues=cues,
        glossary=glossary,
    )

    # 1. Check title section
    assert "DANH SÁCH TIÊU ĐỀ CLICKBAIT" in kit
    assert "Nhậm Thiết Sinh" in kit

    # 2. Check description & timestamps
    assert "NỘI DUNG MÔ TẢ & DÒNG THỜI GIAN" in kit
    assert "00:00 - Mở đầu" in kit

    # 3. Check tags
    assert "nhậm thiết sinh" in kit
    assert "nham thiet sinh" in kit

    # 4. Check master prompts with @image and text overlays
    assert "@image" in kit
    assert "[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]" in kit
    assert "15 NĂM BỐC HƠI!" in kit
    assert "MẨU GIẤY BÍ ẨN!" in kit
    assert "TÌM KIẾM VÔ VỌNG!" in kit

    # 5. Check pinned comment
    assert "GHIM BÌNH LUẬN" in kit


def test_s5_render_generates_youtube_kit(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    write_doc(
        work_dir / "ingest.json",
        IngestDoc(
            job_id="test_job",
            source=SourceInfo(kind="local", uri="test.mp3", title="Nhậm Thiết Sinh Mất Tích"),
            media=MediaInfo(wav_path="audio.wav", duration_sec=20.0, sample_rate=16000, channels=1),
            created_at="2026-01-01T00:00:00Z",
        ),
    )

    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(
            source_asr_sha256="asr",
            method="llm",
            segments=[
                Segment(id=0, start=0.0, end=5.0, text_zh="第一句话", token_range=(0, 2)),
                Segment(id=1, start=6.0, end=12.0, text_zh="第二句话", token_range=(2, 4)),
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
                TranslationItem(id=0, text_zh="第一句话", translation="Câu nói thứ nhất."),
                TranslationItem(id=1, text_zh="第二句话", translation="Câu nói thứ hai diễn biến tiếp theo."),
            ],
        ),
    )

    cfg = Config()
    cfg.render.render_youtube_kit = True

    report = run_s5(work_dir, cfg, ["vi"], out_dir)

    kit_files = [p for p in report.outputs if p.endswith(".youtube_upload_kit.txt")]
    assert len(kit_files) == 1
    kit_file = Path(kit_files[0])
    assert kit_file.is_file()

    content = kit_file.read_text(encoding="utf-8")
    assert "BỘ KIT UPLOAD YOUTUBE 100% TIẾNG VIỆT" in content
    assert "@image" in content


def test_build_youtube_upload_kit_en_aviation():
    cues = [
        Cue(start=0.0, end=5.0, text="Hello, this is News Watch covering United 232."),
        Cue(start=50.0, end=55.0, text="The DC-10 experienced catastrophic hydraulic failure."),
        Cue(start=100.0, end=105.0, text="Captain Al Haynes and Dennis Fitch coordinated throttle control."),
    ]
    glossary = {
        "terms": [
            {"zh": "联合航空", "en": "United Airlines", "type": "org"},
            {"zh": "海恩斯", "en": "Alfred C. Al Haynes", "type": "person"},
            {"zh": "麦克唐纳道格拉斯", "en": "McDonnell Douglas", "type": "org"},
        ],
    }
    kit = build_youtube_upload_kit(
        title="Airframe_Evidence1",
        duration_sec=1688.0,
        cues=cues,
        glossary=glossary,
        lang="en",
    )

    # 1. Check title section
    assert "YOUTUBE UPLOAD KIT (US / GLOBAL ENGLISH)" in kit
    assert "TOP 5 HIGH-CTR TITLE FORMULAS" in kit
    assert "United Airlines Flight 232" in kit or "DC-10" in kit

    # 2. Check description & factsheet
    assert "VIDEO DESCRIPTION & CHAPTER TIMESTAMPS" in kit
    assert "FLIGHT & INCIDENT FACTSHEET" in kit
    assert "00:00 Introduction" in kit
    assert "NTSB" in kit

    # 3. Check tags (<500 chars)
    assert "SEO TAGS LIST" in kit
    assert "united airlines flight 232" in kit

    # 4. Check AI thumbnail prompts
    assert "CINEMATIC AI THUMBNAIL CONCEPTS" in kit
    assert "NO CONTROLS LEFT!" in kit
    assert "ALL 3 LINES GONE!" in kit
    assert "184 SURVIVED?!" in kit

    # 5. Check pinned comment & Fair Use
    assert "PINNED ENGAGEMENT COMMENT" in kit
    assert "Fair Use Notice" in kit


def test_s5_render_generates_youtube_kit_en(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    write_doc(
        work_dir / "ingest.json",
        IngestDoc(
            job_id="test_job_en",
            source=SourceInfo(kind="local", uri="test_aviation.mp3", title="United Airlines 232"),
            media=MediaInfo(wav_path="audio.wav", duration_sec=120.0, sample_rate=16000, channels=1),
            created_at="2026-01-01T00:00:00Z",
        ),
    )

    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(
            source_asr_sha256="asr",
            method="llm",
            segments=[
                Segment(id=0, start=0.0, end=5.0, text_zh="第一句话", token_range=(0, 2)),
                Segment(id=1, start=6.0, end=12.0, text_zh="第二句话", token_range=(2, 4)),
            ],
        ),
    )

    write_doc(
        work_dir / "translations.en.json",
        TranslationsDoc(
            lang="en",
            model="test-model",
            prompt_version=1,
            glossary_hash="glossary",
            segments_hash="segments",
            items=[
                TranslationItem(id=0, text_zh="第一句话", translation="United Airlines flight experienced engine failure."),
                TranslationItem(id=1, text_zh="第二句话", translation="All three hydraulic systems were severed."),
            ],
        ),
    )

    cfg = Config()
    cfg.render.render_youtube_kit = True

    report = run_s5(work_dir, cfg, ["en"], out_dir)

    kit_files = [p for p in report.outputs if "youtube_upload_kit" in p]
    assert len(kit_files) >= 1
    kit_file = Path(kit_files[0])
    assert kit_file.is_file()

    content = kit_file.read_text(encoding="utf-8")
    assert "YOUTUBE UPLOAD KIT (US / GLOBAL ENGLISH)" in content

