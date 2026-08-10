"""Stage runner.

Each stage reads and writes JSON under ``work/<job_id>/`` and re-runs
independently, so ``resume --from <stage>`` is simply "start the loop later".
Stages already holding a valid output file skip themselves, which is what makes a
resume cheap rather than a full redo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .jobs import STAGES, JobStore
from .progress import RunContext, ensure_context
from .stages import s0_ingest, s1_asr, s2_segment, s3_glossary, s4_translate, s5_render

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RunRequest:
    source: str
    targets: list[str]
    out_dir: Path
    bilingual: bool = False
    from_stage: str = "ingest"
    force: bool = False
    formats: tuple[str, ...] = ("srt", "ass")


def stages_from(start: str) -> list[str]:
    if start not in STAGES:
        raise ValueError(f"Stage không hợp lệ: {start!r}. Chọn một trong {list(STAGES)}.")
    return list(STAGES[STAGES.index(start) :])


def run_job(
    req: RunRequest,
    cfg: Config,
    store: JobStore | None = None,
    job_id: str | None = None,
    ctx: RunContext | None = None,
) -> Path:
    """Run the requested stages for one source. Returns the job's work directory."""
    job_id = job_id or s0_ingest.make_job_id(req.source)
    work_dir = Path(cfg.paths.work_dir) / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    if store is not None:
        store.upsert(job_id, req.source, str(work_dir), req.targets)

    todo = stages_from(req.from_stage)
    ctx = ensure_context(ctx)
    ctx.job_id = job_id
    ctx._stages = todo
    log.info("Job %s: chạy %s", job_id, " -> ".join(todo))

    for stage in todo:
        ctx.enter_stage(stage)
        if store is not None:
            store.mark_running(job_id, stage)
        try:
            _run_stage(stage, req, cfg, work_dir, job_id, ctx)
        except Exception as exc:
            if store is not None:
                store.mark_failed(job_id, stage, f"{type(exc).__name__}: {exc}")
            log.error("Job %s hỏng ở stage %s: %s", job_id, stage, exc)
            raise
        ctx.report(1.0)
        if store is not None:
            store.mark_stage_done(job_id, stage)

    if store is not None:
        store.mark_done(job_id)
    return work_dir


def _run_stage(
    stage: str, req: RunRequest, cfg: Config, work_dir: Path, job_id: str,
    ctx: RunContext,
) -> None:
    # `force` applies only to the stage the user resumed from. Propagating it to
    # every later stage would discard the glossary they just hand-edited.
    force_here = req.force and stage == req.from_stage

    if stage == "ingest":
        s0_ingest.run(req.source, work_dir, cfg, job_id=job_id)
    elif stage == "asr":
        s1_asr.run(work_dir, cfg, force=force_here, ctx=ctx)
    elif stage == "segment":
        s2_segment.run(work_dir, cfg, force=force_here, ctx=ctx)
    elif stage == "glossary":
        s3_glossary.run(work_dir, cfg, force=force_here, ctx=ctx)
    elif stage == "translate":
        s4_translate.run(work_dir, cfg, req.targets, force=force_here, ctx=ctx)
    elif stage == "render":
        s5_render.run(
            work_dir, cfg, req.targets, req.out_dir,
            bilingual=req.bilingual, formats=req.formats,
        )
    else:  # pragma: no cover - stages_from already validated
        raise ValueError(f"Stage lạ: {stage}")
