"""Persistence and scheduler contract tests for the TTS lane."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from zhsub.config import Config
from zhsub.jobs import JobStore
from zhsub.progress import Progress
from zhsub.web.runtime import RunScheduler


def _request(tmp_path: Path, job_id: str) -> dict:
    return {
        "source": {"kind": "local", "value": str(tmp_path / f"{job_id}.mp4")},
        "targets": ["vi"],
        "formats": ["srt"],
        "bilingual": False,
        "output_dir": str(tmp_path / "output" / job_id),
    }


def _seed(store: JobStore, tmp_path: Path, job_id: str) -> None:
    request = _request(tmp_path, job_id)
    store.upsert(
        job_id,
        request["source"]["value"],
        str(tmp_path / "work" / job_id),
        ["vi"],
        request,
    )


def _wait_for(store: JobStore, run_id: str, status: str, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = store.get_run(run_id)
        if run is not None and run.status == status:
            return
        time.sleep(0.01)
    run = store.get_run(run_id)
    raise AssertionError(f"run {run_id} did not reach {status}: {run}")


def test_old_runs_are_migrated_with_pipeline_defaults(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY, source_uri TEXT NOT NULL, work_dir TEXT NOT NULL,
                targets TEXT NOT NULL, stage TEXT, status TEXT NOT NULL, error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, heartbeat_at REAL,
                request_json TEXT, approved_at REAL, approved_signature TEXT
            );
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL,
                from_stage TEXT NOT NULL, force INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, stage TEXT, progress REAL NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', error TEXT, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, started_at REAL, ended_at REAL,
                event_seq INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO jobs VALUES ('legacy', 'movie.mp4', 'work/legacy', 'vi',
                                     'render', 'done', NULL, 0, 1, 2, NULL,
                                     NULL, NULL, NULL);
            INSERT INTO runs VALUES ('old-run', 'legacy', 'pipeline', 'ingest', 0,
                                     'queued', NULL, 0, '', NULL, 1, 1, NULL, NULL, 0);
            """
        )

    store = JobStore(db_path)
    run = store.get_run("old-run")
    assert run is not None
    assert run.lane == "pipeline"
    assert run.payload is None
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"lane", "payload_json"} <= columns
    assert {"voice_calibrations", "tts_approvals"} <= tables


def test_lane_queue_isolated_but_same_job_is_serialized(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    for job_id in ("p1", "p2", "t1", "t2"):
        _seed(store, tmp_path, job_id)

    p1 = store.create_run("p1", "pipeline", "ingest")
    p2 = store.create_run("p2", "pipeline", "ingest")
    t1 = store.create_run("t1", "tts_render", "tts_render", lane="tts", payload={"lang": "vi"})
    t2 = store.create_run("t2", "tts_render", "tts_render", lane="tts", payload={"lang": "vi"})
    assert store.queue_position(p1.run_id) == 1
    assert store.queue_position(p2.run_id) == 2
    assert store.queue_position(t1.run_id) == 1
    assert store.queue_position(t2.run_id) == 2

    with pytest.raises(ValueError, match="already has active run"):
        store.create_run("p1", "tts_render", "tts_render", lane="tts")

    assert store.claim_next_run("pipeline").run_id == p1.run_id  # type: ignore[union-attr]
    assert store.claim_next_run("pipeline") is None
    assert store.claim_next_run("tts").run_id == t1.run_id  # type: ignore[union-attr]
    assert store.claim_next_run("tts") is None


def test_tts_lifecycle_does_not_mutate_core_job_state_or_approval(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store, tmp_path, "job")
    store.mark_done("job")
    store.set_approval("job", "subtitle-signature")
    run = store.create_run(
        "job",
        "tts_render",
        "tts_render",
        lane="tts",
        payload={"lang": "vi", "voice_id": "voice"},
    )
    assert store.get("job").status == "done"  # type: ignore[union-attr]
    assert store.get("job").approved_signature == "subtitle-signature"  # type: ignore[union-attr]
    store.claim_next_run("tts")
    store.update_run_progress(run.run_id, stage="tts_render", progress=0.5)
    store.finish_run(run.run_id, "completed", stage="tts_render")
    job = store.get("job")
    assert job is not None
    assert job.status == "done"
    assert job.stage is None
    assert job.error is None
    assert job.approved_signature == "subtitle-signature"


def test_recovery_interrupts_tts_run_without_interrupting_core_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store, tmp_path, "job")
    store.mark_done("job")
    run = store.create_run("job", "tts_render", "tts_render", lane="tts")
    assert store.claim_next_run("tts") is not None
    assert store.recover_runs() == [run.run_id]
    recovered = store.get_run(run.run_id)
    job = store.get("job")
    assert recovered is not None and recovered.status == "interrupted"
    assert job is not None and job.status == "done"


def test_progress_event_keeps_cancelling_status(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store, tmp_path, "job")
    run = store.create_run("job", "tts_render", "tts_render", lane="tts")
    assert store.claim_next_run("tts") is not None
    assert store.request_cancel(run.run_id).status == "cancelling"  # type: ignore[union-attr]

    store.update_run_progress(
        run.run_id,
        stage="tts_render",
        progress=0.5,
        message="finishing in-flight cue",
    )

    events = store.events_after(run.run_id, 0)
    assert events[-1].event_type == "progress"
    assert events[-1].payload["status"] == "cancelling"


def test_voice_calibration_and_tts_approval_are_durable(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store, tmp_path, "job")
    calibration = store.save_voice_calibration(
        "ai33",
        "voice",
        0.15,
        0.217,
        source_job_id="job",
    )
    assert store.get_voice_calibration("ai33", "voice") == calibration
    existing = store.save_voice_calibration(
        "ai33", "voice", 0.8, 0.4, overwrite=False
    )
    assert existing.overhead_sec == 0.15
    updated = store.save_voice_calibration("ai33", "voice", 0.2, 0.21)
    assert updated.overhead_sec == 0.2

    approval = store.approve_tts("job", "vi", "tts-signature", expected_signature="tts-signature")
    assert approval.approved_signature == "tts-signature"
    assert store.get_tts_approval("job", "vi") == approval
    store.set_tts_approval("job", "vi", None)
    assert store.get_tts_approval("job", "vi") is None


def test_shared_voice_calibration_requires_exactly_eight_samples(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")

    with pytest.raises(ValueError, match="exactly eight"):
        store.save_voice_calibration(
            "ai33_vbee",
            "voice",
            0.15,
            0.217,
            sample_count=7,
        )


def test_scheduler_runs_one_worker_per_lane_and_allows_cross_lane_overlap(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    for job_id in ("p1", "p2", "t1", "t2"):
        _seed(store, tmp_path, job_id)
    pipeline_runs = [store.create_run(job_id, "pipeline", "ingest") for job_id in ("p1", "p2")]
    tts_runs = [
        store.create_run(job_id, "tts_render", "tts_render", lane="tts")
        for job_id in ("t1", "t2")
    ]

    lock = threading.Lock()
    first_both_lanes = threading.Barrier(2)
    active_total = 0
    active_by_lane = {"pipeline": 0, "tts": 0}
    max_total = 0
    max_by_lane = {"pipeline": 0, "tts": 0}
    first_call = {"pipeline": True, "tts": True}

    def enter(lane: str) -> None:
        nonlocal active_total, max_total
        with lock:
            active_total += 1
            active_by_lane[lane] += 1
            max_total = max(max_total, active_total)
            max_by_lane[lane] = max(max_by_lane[lane], active_by_lane[lane])
            is_first = first_call[lane]
            first_call[lane] = False
        if is_first:
            first_both_lanes.wait(timeout=2.0)

    def leave(lane: str) -> None:
        nonlocal active_total
        with lock:
            active_total -= 1
            active_by_lane[lane] -= 1

    def pipeline_runner(_source: str, **kwargs: object) -> None:
        enter("pipeline")
        try:
            kwargs["on_progress"](Progress("p", "ingest", "ingest", 1.0, 1.0))  # type: ignore[index]
            time.sleep(0.03)
        finally:
            leave("pipeline")

    def tts_runner(job, run, _config, on_progress, cancel) -> None:
        del job, run, cancel
        enter("tts")
        try:
            on_progress(Progress("t", "tts_render", "tts", 1.0, 1.0))
            time.sleep(0.03)
        finally:
            leave("tts")

    scheduler = RunScheduler(
        Config(),
        store,
        runner=pipeline_runner,
        tts_runner=tts_runner,
    )
    scheduler.start()
    try:
        for run in pipeline_runs + tts_runs:
            _wait_for(store, run.run_id, "completed")
    finally:
        scheduler.stop()

    assert max_by_lane == {"pipeline": 1, "tts": 1}
    assert max_total == 2
