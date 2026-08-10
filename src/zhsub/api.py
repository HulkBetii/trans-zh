"""Library entry point for embedding hosts.

The CLI is one caller among several. A desktop app wants a single function that
takes a file and gives back paths, reports progress, and can be cancelled —
without knowing that stages, work directories or a job database exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .jobs import JobStore
from .models import GlossaryDoc, RenderReport, SegmentsDoc
from .pipeline import RunRequest, run_job
from .progress import Canceller, ProgressCallback, RunContext


@dataclass(slots=True)
class JobResult:
    """Everything a host needs after a run, without reading the work directory."""

    job_id: str
    work_dir: Path
    outputs: list[str] = field(default_factory=list)
    segments: int = 0
    warnings: int = 0
    #: Path the user can hand-edit before re-running from ``translate``. Surfacing
    #: it here is the point — correcting names is how the translation gets good.
    glossary_path: Path | None = None


def translate_video(
    source: str | Path,
    targets: list[str] | None = None,
    out_dir: str | Path = "output",
    *,
    config: Config | None = None,
    config_path: str | Path | None = None,
    bilingual: bool = False,
    formats: tuple[str, ...] = ("srt", "ass"),
    from_stage: str = "ingest",
    force: bool = False,
    on_progress: ProgressCallback | None = None,
    cancel: Canceller | None = None,
    track_job: bool = True,
) -> JobResult:
    """Run the whole pipeline on one file or URL.

    Args:
        source: media file, or a URL yt-dlp can fetch.
        targets: ``["vi"]``, ``["en"]`` or both. Defaults to Vietnamese.
        out_dir: where the subtitle files are written.
        config: an already-built config; otherwise loaded from ``config_path`` or
            the working directory.
        from_stage: resume point — ``"translate"`` after hand-editing the glossary.
        on_progress: called with a :class:`~zhsub.progress.Progress` on every step.
        cancel: anything with ``is_set()``; a ``threading.Event`` is the obvious
            choice. Raises :class:`~zhsub.progress.Cancelled` when it trips.
        track_job: record state in the jobs database. Turn it off for a one-shot
            run that should leave nothing behind.

    Raises:
        Cancelled: the host asked to stop. Completed stages are kept on disk and a
            later call with the same source resumes from there.
    """
    cfg = config or Config.load(config_path)
    targets = targets or ["vi"]

    req = RunRequest(
        source=str(source),
        targets=targets,
        out_dir=Path(out_dir),
        bilingual=bilingual,
        from_stage=from_stage,
        force=force,
        formats=formats,
    )
    ctx = RunContext(on_progress=on_progress, cancel=cancel)
    store = JobStore(cfg.paths.jobs_db) if track_job else None

    work_dir = run_job(req, cfg, store, ctx=ctx)
    return _collect(work_dir)


def _collect(work_dir: Path) -> JobResult:
    """Read back what the run produced. Missing files mean a partial run, not an error."""
    result = JobResult(job_id=work_dir.name, work_dir=work_dir)

    report_path = work_dir / "render_report.json"
    if report_path.is_file():
        report = RenderReport.model_validate(json.loads(report_path.read_text(encoding="utf-8")))
        result.outputs = report.outputs
        result.warnings = len(report.warnings)

    segments_path = work_dir / "segments.json"
    if segments_path.is_file():
        doc = SegmentsDoc.model_validate(json.loads(segments_path.read_text(encoding="utf-8")))
        result.segments = len(doc.segments)

    glossary_path = work_dir / "glossary.json"
    if glossary_path.is_file():
        result.glossary_path = glossary_path
    return result


def load_glossary(work_dir: str | Path) -> GlossaryDoc:
    """Read a job's glossary so a host can present it for editing."""
    path = Path(work_dir) / "glossary.json"
    return GlossaryDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_glossary(work_dir: str | Path, glossary: GlossaryDoc) -> None:
    """Write an edited glossary back.

    Re-run with ``from_stage="translate"`` afterwards: S4 notices the glossary hash
    changed and re-translates, while the text cache keeps the cost to the segments
    the edit actually affected.
    """
    from .jsonio import write_doc

    write_doc(Path(work_dir) / "glossary.json", glossary)
