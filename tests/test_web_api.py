"""Giao diện web: phần logic, không cần chạy server thật."""

from __future__ import annotations

import json

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
    assert '<script type="module"' in html
    assert 'src="/assets/' in html
