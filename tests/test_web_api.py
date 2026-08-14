"""Giao diện web: phần logic, không cần chạy server thật."""

from __future__ import annotations

import json

import pytest

from zhsub.api import job_health
from zhsub.jsonio import write_doc
from zhsub.models import (
    GlossaryDoc,
    GlossaryTerm,
    RenderReport,
    RenderWarning,
    Segment,
    SegmentsDoc,
    StyleDecision,
)


def _job(tmp_path, method="llm", pronoun="anh ta", cps=2):
    write_doc(
        tmp_path / "segments.json",
        SegmentsDoc(
            source_asr_sha256="",
            method=method,
            segments=[
                Segment(id=i, start=i, end=i + 1, text_zh="中", token_range=(i, i + 1))
                for i in range(10)
            ],
        ),
    )
    write_doc(
        tmp_path / "glossary.json",
        GlossaryDoc(
            terms=[GlossaryTerm(zh="A", vi="a")],
            style=StyleDecision(subject_third_person_vi=pronoun),
        ),
    )
    write_doc(
        tmp_path / "render_report.json",
        RenderReport(
            outputs=["x.srt"],
            warnings=[
                RenderWarning(segment_id=i, lang="vi", kind="cps_over", detail="", value=25, limit=21)
                for i in range(cps)
            ]
            + [RenderWarning(segment_id=99, lang="vi", kind="too_many_lines", detail="", value=4, limit=3)],
        ),
    )
    return tmp_path


def test_health_reads_the_four_numbers_that_matter(tmp_path):
    h = job_health(_job(tmp_path))

    assert h.method == "llm"
    assert h.subject_pronoun == "anh ta"
    assert h.segments == 10
    assert h.terms == 1


def test_only_cps_warnings_are_counted(tmp_path):
    """too_many_lines là chuyện khác — gộp vào thì con số CPS mất nghĩa."""
    assert job_health(_job(tmp_path, cps=3)).cps_warnings == 3


def test_a_half_finished_job_reports_blanks_not_errors(tmp_path):
    """Thiếu file nghĩa là chạy dở, không phải hỏng — giao diện vẫn phải hiện được."""
    h = job_health(tmp_path)

    assert h.method == ""
    assert h.subject_pronoun == ""
    assert h.segments == 0


def test_an_unpinned_pronoun_is_visible_as_empty(tmp_path):
    """Khối style rỗng từng lọt qua cả một video mà không ai để ý."""
    assert job_health(_job(tmp_path, pronoun="")).subject_pronoun == ""


def test_rule_fallback_is_reported_as_is(tmp_path):
    """Đây là dấu hiệu duy nhất cho thấy model bị chặn khi ngắt câu."""
    assert job_health(_job(tmp_path, method="rule_fallback")).method == "rule_fallback"


def test_the_page_is_self_contained(tmp_path):
    """Không tải gì từ CDN: máy có thể không có mạng, và trang phải mở được ngay."""
    from zhsub.web import server

    html = server._PAGE.read_text(encoding="utf-8")

    assert "http://" not in html.replace("http://127.0.0.1", "").replace("http://{host}", "")
    assert "<script>" in html


@pytest.mark.parametrize("bad", ["..%2F..%2Fzhsub.toml", "..%5C..%5Cjobs.db"])
def test_download_cannot_escape_the_output_folder(bad, tmp_path, monkeypatch):
    """Tên file từ URL không được dùng để đi ngược thư mục.

    zhsub.toml chứa voice_id và đường dẫn profile; jobs.db chứa lịch sử job. Server
    chỉ nghe ở 127.0.0.1 nên rủi ro thấp, nhưng chặn ở chỗ đọc file vẫn rẻ hơn tin
    vào chuyện đó.
    """
    from fastapi.testclient import TestClient

    from zhsub.web import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "zhsub.toml").write_text("bí mật", encoding="utf-8")
    (tmp_path / "jobs.db").write_text("bí mật", encoding="utf-8")

    resp = TestClient(app).get(f"/api/output/{bad}")

    assert resp.status_code == 404
    assert "bí mật" not in resp.text


def test_outputs_come_from_the_report_not_from_guessing_names(tmp_path, monkeypatch):
    """S5 đặt tên theo phần gốc của nguồn, S6 đặt theo job_id — hai quy ước khác
    nhau, nên so chuỗi với job_id sẽ bỏ sót phụ đề."""
    from zhsub.web.server import _outputs_for

    monkeypatch.chdir(tmp_path)
    work = tmp_path / "work" / "video-6_e0e4a896"
    work.mkdir(parents=True)
    (tmp_path / "output").mkdir()
    for name in ("video-6.vi.srt", "video-6.vi.ass", "video-6_e0e4a896.vi.mp3"):
        (tmp_path / "output" / name).write_text("x", encoding="utf-8")
    write_doc(
        work / "render_report.json",
        RenderReport(outputs=["output/video-6.vi.srt", "output/video-6.vi.ass"]),
    )

    assert _outputs_for(work) == ["video-6.vi.srt", "video-6.vi.ass", "video-6_e0e4a896.vi.mp3"]


def test_a_listed_output_that_was_deleted_is_not_offered(tmp_path, monkeypatch):
    """Báo cáo ghi lại lần chạy trước; file có thể đã bị xoá từ lúc đó."""
    from zhsub.web.server import _outputs_for

    monkeypatch.chdir(tmp_path)
    work = tmp_path / "work" / "j"
    work.mkdir(parents=True)
    (tmp_path / "output").mkdir()
    write_doc(work / "render_report.json", RenderReport(outputs=["output/mất-rồi.srt"]))

    assert _outputs_for(work) == []


def test_a_real_output_file_downloads(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from zhsub.web import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "phim.vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")

    resp = TestClient(app).get("/api/output/phim.vi.srt")

    assert resp.status_code == 200
    assert "xin chào" in resp.text
