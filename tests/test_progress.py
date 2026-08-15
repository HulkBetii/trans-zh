"""Progress reporting and cancellation, as an embedding host would use them."""

from __future__ import annotations

import threading

import pytest

from zhsub.progress import (
    STAGE_WEIGHTS,
    Cancelled,
    Progress,
    RunContext,
    ensure_context,
)


def test_fraction_advances_across_stages():
    seen: list[Progress] = []
    ctx = RunContext("job", on_progress=seen.append, stages=list(STAGE_WEIGHTS))

    ctx.enter_stage("ingest")
    ctx.report(1.0)
    ctx.enter_stage("translate")
    ctx.report(0.5)

    assert [round(p.fraction, 3) for p in seen] == [0.0, 0.02, 0.35, 0.65]
    assert seen[-1].stage_label == "Dịch"


def test_fraction_stays_within_bounds():
    seen: list[Progress] = []
    ctx = RunContext("job", on_progress=seen.append)
    ctx.enter_stage("render")
    ctx.report(5.0)   # host code should never do this, but it must not break the bar
    ctx.report(-1.0)

    assert all(0.0 <= p.fraction <= 1.0 for p in seen)
    assert all(0.0 <= p.stage_fraction <= 1.0 for p in seen)


def test_fraction_is_relative_to_the_stages_actually_running():
    """Resuming from `translate` should not start the bar at 85%."""
    seen: list[Progress] = []
    ctx = RunContext("job", on_progress=seen.append, stages=["translate", "render"])

    ctx.enter_stage("translate")
    ctx.report(1.0)

    assert seen[0].fraction == 0.0
    assert round(seen[-1].fraction, 3) == round(0.60 / 0.65, 3)


def test_cancel_raises_at_the_next_report():
    cancel = threading.Event()
    ctx = RunContext("job", cancel=cancel)
    ctx.enter_stage("translate")

    cancel.set()
    with pytest.raises(Cancelled, match="translate"):
        ctx.report(0.5)


def test_reporting_is_also_the_cancellation_check():
    """One call does both, so wiring a progress bar gets a responsive Cancel free."""
    cancel = threading.Event()
    cancel.set()
    ctx = RunContext("job", cancel=cancel)

    with pytest.raises(Cancelled):
        ctx.report(0.0)


def test_context_without_a_callback_still_checks_cancellation():
    cancel = threading.Event()
    cancel.set()
    ctx = RunContext("job", on_progress=None, cancel=cancel)

    with pytest.raises(Cancelled):
        ctx.report(0.5)


def test_each_default_context_gets_its_own_cancel_flag():
    """A shared default would let one cancelled job cancel every other one.

    `batch --concurrency 2` runs jobs as threads in one process, so a module-level
    singleton here would be a cross-job kill switch.
    """
    a, b = ensure_context(None), ensure_context(None)
    a._cancel.set()

    assert a.cancelled()
    assert not b.cancelled()


def test_ensure_context_passes_a_real_context_through():
    ctx = RunContext("job")
    assert ensure_context(ctx) is ctx


def test_pipeline_persists_cancel_before_stage_entry(tmp_path):
    from zhsub.config import Config
    from zhsub.jobs import JobStore
    from zhsub.pipeline import RunRequest, run_job

    config = Config()
    config.paths.work_dir = str(tmp_path / "work")
    store = JobStore(tmp_path / "jobs.db")
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(Cancelled):
        run_job(
            RunRequest("source.mp4", ["vi"], tmp_path / "output"),
            config,
            store,
            job_id="job",
            ctx=RunContext(cancel=cancel),
        )

    assert store.get("job").status == "cancelled"  # type: ignore[union-attr]


def test_pipeline_persists_cancel_after_stage_write(tmp_path, monkeypatch):
    from zhsub.config import Config
    from zhsub.jobs import JobStore
    from zhsub.pipeline import RunRequest, run_job

    config = Config()
    config.paths.work_dir = str(tmp_path / "work")
    store = JobStore(tmp_path / "jobs.db")
    cancel = threading.Event()

    def finish_stage(*_args, **_kwargs):
        cancel.set()

    monkeypatch.setattr("zhsub.pipeline._run_stage", finish_stage)

    with pytest.raises(Cancelled):
        run_job(
            RunRequest(
                "source.mp4",
                ["vi"],
                tmp_path / "output",
                from_stage="render",
            ),
            config,
            store,
            job_id="job",
            ctx=RunContext(cancel=cancel),
        )

    assert store.get("job").status == "cancelled"  # type: ignore[union-attr]
    assert store.latest_stage_runs("job")["render"]["status"] == "cancelled"


def test_cancelling_mid_translation_keeps_what_was_already_paid_for(tmp_path):
    """The claim the Cancel button rests on: stopping wastes no completed work.

    Cancellation is only checked between batches, and the cache is flushed after
    each one, so a cancelled run leaves every finished batch on disk and a later run
    picks them up instead of re-billing them.
    """
    from zhsub.config import Config
    from zhsub.llm.cache import TranslationCache
    from zhsub.models import GlossaryDoc, Segment
    from zhsub.stages.s4_translate import translate_segments

    from .fake_llm import FakeProvider

    segments = [
        Segment(id=i, start=i * 3.0, end=i * 3.0 + 2.0, text_zh=f"第{i}句话内容",
                token_range=(i * 5, (i + 1) * 5))
        for i in range(40)
    ]
    cfg = Config()
    cfg.translate.batch_size = 10
    cfg.translate.review_pass = False
    cache_dir = tmp_path / "cache"

    cancel = threading.Event()
    calls = {"n": 0}

    def on_progress(_p: Progress) -> None:
        calls["n"] += 1
        if calls["n"] == 3:  # part-way through, after some batches finished
            cancel.set()

    ctx = RunContext("job", on_progress=on_progress, cancel=cancel)
    provider = FakeProvider()

    with pytest.raises(Cancelled):
        translate_segments(
            segments, "vi", provider, GlossaryDoc(), cfg,
            TranslationCache(cache_dir), "gh", ctx=ctx,
        )

    persisted = TranslationCache(cache_dir)
    done = sum(
        1 for s in segments
        if persisted.get(
            persisted.make_key(s.text_zh, "vi", provider.model, "gh", cfg.translate.prompt_version)
        )
    )
    assert 0 < done < len(segments), "phải dừng giữa chừng, không phải đầu hay cuối"

    # Resuming: only the untranslated remainder costs anything.
    second = FakeProvider()
    items = translate_segments(
        segments, "vi", second, GlossaryDoc(), cfg, TranslationCache(cache_dir), "gh",
    )

    assert len(items) == len(segments)
    assert sum(1 for i in items if i.cache_hit) == done
    assert len(second.translated_ids) == len(segments) - done
