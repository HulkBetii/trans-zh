"""Backend contract and durability tests for the production studio."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from zhsub.config import Config
from zhsub.jobs import JobStore
from zhsub.jsonio import read_doc, sha256_json_canonical, write_doc
from zhsub.models import (
    GlossaryDoc,
    GlossaryTerm,
    IngestDoc,
    MediaInfo,
    RenderReport,
    Segment,
    SegmentsDoc,
    SourceInfo,
    StyleDecision,
    TranslationItem,
    TranslationsDoc,
)
from zhsub.overrides import effective_translations_hash
from zhsub.progress import Cancelled, Progress
from zhsub.stages import s5_render
from zhsub.web.server import create_app, serve


def _config(tmp_path: Path) -> Config:
    config = Config()
    config.paths.jobs_db = str(tmp_path / "jobs.db")
    config.paths.work_dir = str(tmp_path / "work")
    config.paths.cache_dir = str(tmp_path / "cache")
    return config


def _client(tmp_path: Path, *, runner=None, start_scheduler=False):
    return TestClient(
        create_app(
            config=_config(tmp_path),
            runner=runner,
            start_scheduler=start_scheduler,
            enforce_readiness=False,
            file_roots=[tmp_path],
        )
    )


def _create(client: TestClient, source: Path, targets=None, formats=None) -> dict:
    response = client.post(
        "/api/v1/jobs",
        json={
            "source": {"kind": "local", "value": str(source.resolve())},
            "targets": targets or ["vi"],
            "formats": formats or ["srt"],
            "bilingual": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for(client: TestClient, run_id: str, status: str, timeout=3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/runs/{run_id}").json()
        if payload["status"] == status:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {status}: {payload}")


def _write_subtitle_artifacts(work_dir: Path, output_path: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    write_doc(
        work_dir / "ingest.json",
        IngestDoc(
            job_id=work_dir.name,
            source=SourceInfo(kind="local", uri="movie.mp4", title="movie"),
            media=MediaInfo(
                wav_path="audio.wav",
                duration_sec=4.0,
                sample_rate=16000,
                channels=1,
            ),
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(
            source_asr_sha256="source",
            method="llm",
            segments=[
                Segment(id=0, start=0.0, end=2.0, text_zh="你好", token_range=(0, 2)),
                Segment(id=1, start=2.0, end=4.0, text_zh="世界", token_range=(2, 4)),
            ],
        ),
    )
    glossary = GlossaryDoc(
        terms=[GlossaryTerm(zh="世界", vi="thế giới")],
        style=StyleDecision(subject_third_person_vi="anh ấy"),
    )
    write_doc(work_dir / "glossary.json", glossary)
    write_doc(
        work_dir / "translations.vi.json",
        TranslationsDoc(
            lang="vi",
            model="fake",
            prompt_version=1,
            glossary_hash=sha256_json_canonical(
                glossary.model_dump(by_alias=True, mode="json")
            ),
            segments_hash="segments",
            items=[
                TranslationItem(id=0, text_zh="你好", translation="Xin chào"),
                TranslationItem(id=1, text_zh="世界", translation="Thế giới"),
            ],
        ),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("subtitle", encoding="utf-8")
    write_doc(
        work_dir / "render_report.json",
        RenderReport(
            outputs=[str(output_path)],
            effective_translation_hashes={
                "vi": effective_translations_hash(work_dir, "vi")
            },
            segments_render_hash=sha256_json_canonical(
                {
                    "duration_sec": 4.0,
                    "segments": [
                        [0, 0.0, 2.0, "你好"],
                        [1, 2.0, 4.0, "世界"],
                    ],
                }
            ),
            render_request_hash=s5_render.build_render_request_hash(
                ["vi"], ["srt"], False
            ),
        ),
    )


def test_old_database_is_migrated_without_losing_jobs(tmp_path):
    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY, source_uri TEXT NOT NULL, work_dir TEXT NOT NULL,
                targets TEXT NOT NULL, stage TEXT, status TEXT NOT NULL, error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, heartbeat_at REAL
            );
            CREATE TABLE stage_runs (
                job_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
                started_at REAL, ended_at REAL, error TEXT
            );
            INSERT INTO jobs VALUES ('legacy', 'movie.mp4', 'work/legacy', 'vi',
                                     'render', 'done', NULL, 0, 1, 2, NULL);
            """
        )

    store = JobStore(db_path)

    assert store.get("legacy").status == "done"  # type: ignore[union-attr]
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"request_json", "approved_at", "approved_signature"} <= columns
    assert {"runs", "run_events"} <= tables


def test_recovery_interrupts_owned_runs_but_keeps_queue(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    request = {
        "source": {"kind": "local", "value": str(tmp_path / "one.mp4")},
        "targets": ["vi"], "formats": ["srt"], "bilingual": False,
        "output_dir": str(tmp_path / "output"),
    }
    for job_id in ("one", "two"):
        store.upsert(job_id, request["source"]["value"], str(tmp_path / job_id), ["vi"], request)
    running = store.create_run("one", "pipeline", "ingest")
    queued = store.create_run("two", "pipeline", "ingest")
    assert store.claim_next_run().run_id == running.run_id  # type: ignore[union-attr]
    store.mark_running("one", "ingest")
    assert JobStore(tmp_path / "jobs.db").claim_next_run() is None

    assert store.recover_runs() == [running.run_id]
    assert store.get_run(running.run_id).status == "interrupted"  # type: ignore[union-attr]
    assert store.get_run(queued.run_id).status == "queued"  # type: ignore[union-attr]
    assert store.latest_stage_runs("one")["ingest"]["status"] == "interrupted"


def test_only_one_active_run_can_be_created_for_a_job(tmp_path):
    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    store.upsert("job", "movie.mp4", str(tmp_path / "work"), ["vi"])
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def create() -> None:
        barrier.wait()
        try:
            JobStore(db_path).create_run("job", "pipeline", "ingest")
        except ValueError:
            outcomes.append("conflict")
        else:
            outcomes.append("created")

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["conflict", "created"]


def test_rejected_create_does_not_overwrite_the_active_run_snapshot(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    original = {
        "source": {"kind": "local", "value": "movie.mp4"},
        "targets": ["vi"], "formats": ["srt"], "bilingual": False,
        "output_dir": str(tmp_path / "output" / "job"),
    }
    replacement = {
        **original,
        "targets": ["en"],
        "formats": ["ass"],
    }
    store.upsert_and_create_run(
        "job", "movie.mp4", str(tmp_path / "work" / "job"), ["vi"], original
    )

    try:
        store.upsert_and_create_run(
            "job", "movie.mp4", str(tmp_path / "work" / "job"), ["en"], replacement
        )
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a second active run was accepted")

    job = store.get("job")
    assert job.targets == ["vi"]  # type: ignore[union-attr]
    assert job.request == original  # type: ignore[union-attr]


def test_finish_cannot_overwrite_a_cancellation_request(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.upsert("job", "movie.mp4", str(tmp_path / "work"), ["vi"])
    run = store.create_run("job", "pipeline", "ingest")
    store.claim_next_run()

    assert store.request_cancel(run.run_id).status == "cancelling"  # type: ignore[union-attr]
    assert store.finish_run(run.run_id, "completed").status == "cancelled"  # type: ignore[union-attr]
    assert store.request_cancel(run.run_id).status == "cancelled"  # type: ignore[union-attr]


def test_scheduler_runs_only_one_pipeline_at_a_time(tmp_path):
    active = 0
    maximum = 0
    lock = threading.Lock()

    def runner(source, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        kwargs["on_progress"](
            Progress("", "ingest", "ingest", 0.5, 0.1, Path(source).name)
        )
        time.sleep(0.06)
        with lock:
            active -= 1

    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    with _client(tmp_path, runner=runner, start_scheduler=True) as client:
        first_run = _create(client, first)["run_id"]
        second_run = _create(client, second)["run_id"]
        _wait_for(client, first_run, "completed")
        _wait_for(client, second_run, "completed")

    assert maximum == 1


def test_cancel_is_idempotent_and_never_becomes_failed(tmp_path):
    started = threading.Event()

    def runner(_source, **kwargs):
        started.set()
        while not kwargs["cancel"].is_set():
            time.sleep(0.005)
        raise Cancelled("cancelled in test")

    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path, runner=runner, start_scheduler=True) as client:
        created = _create(client, source)
        assert started.wait(1)
        first = client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        second = client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        final = _wait_for(client, created["run_id"], "cancelled")
        detail = client.get(f"/api/v1/jobs/{created['job_id']}").json()

    assert first.status_code == second.status_code == 200
    assert final["error"] is None
    assert detail["execution_status"] == "cancelled"


def test_sse_snapshot_is_repeatable_for_multiple_subscribers(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        first = client.get(f"/api/v1/runs/{created['run_id']}/events")
        second = client.get(
            f"/api/v1/runs/{created['run_id']}/events",
            headers={"Last-Event-ID": "1"},
        )

    assert first.status_code == second.status_code == 200
    assert '"type": "snapshot"' in first.text
    assert '"status": "cancelled"' in first.text
    assert first.text == second.text


def test_create_validates_source_and_persists_output_snapshot(tmp_path):
    relative = _client(tmp_path).post(
        "/api/v1/jobs",
        json={
            "source": {"kind": "local", "value": "movie.mp4"},
            "targets": ["vi"], "formats": ["srt"], "bilingual": False,
        },
    )
    assert relative.status_code == 400

    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        detail = client.get(f"/api/v1/jobs/{created['job_id']}").json()
    assert Path(detail["request"]["output_dir"]).name == created["job_id"]


def test_corrupt_job_artifact_does_not_break_job_list(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        store = JobStore(tmp_path / "jobs.db")
        job = store.get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        work_dir.mkdir(parents=True)
        (work_dir / "segments.json").write_text("{broken", encoding="utf-8")
        response = client.get("/api/v1/jobs")

    assert response.status_code == 200
    assert response.json()["items"][0]["job_id"] == created["job_id"]


def test_job_filters_accept_csv_groups_used_by_the_navigator(tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    with _client(tmp_path) as client:
        _create(client, first)
        _create(client, second)
        active = client.get(
            "/api/v1/jobs",
            params={"execution": "queued,running,cancelling"},
        )
        attention = client.get(
            "/api/v1/jobs",
            params={"quality": "needs_review,degraded"},
        )
        invalid = client.get("/api/v1/jobs", params={"execution": "unknown"})

    assert active.status_code == attention.status_code == 200
    assert active.json()["total"] == attention.json()["total"] == 2
    assert invalid.status_code == 422


def test_glossary_revision_conflict_does_not_overwrite_newer_data(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        job = JobStore(tmp_path / "jobs.db").get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        work_dir.mkdir(parents=True)
        write_doc(work_dir / "glossary.json", GlossaryDoc())
        initial = client.get(f"/api/v1/jobs/{created['job_id']}/glossary").json()
        changed = initial["document"]
        changed["style"]["speech_register"] = "trang trọng"
        saved = client.put(
            f"/api/v1/jobs/{created['job_id']}/glossary",
            json={"revision": initial["revision"], "document": changed},
        )
        conflict = client.put(
            f"/api/v1/jobs/{created['job_id']}/glossary",
            json={"revision": initial["revision"], "document": initial["document"]},
        )

    assert saved.status_code == 200
    assert conflict.status_code == 409
    assert json.loads((work_dir / "glossary.json").read_text(encoding="utf-8"))["style"]["speech_register"] == "trang trọng"


def test_glossary_lock_contract_is_shared_by_get_and_put(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        store = JobStore(tmp_path / "jobs.db")
        job = store.get(created["job_id"])
        assert job is not None
        work_dir = Path(job.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        write_doc(work_dir / "glossary.json", GlossaryDoc())
        store.request_cancel(created["run_id"])
        store.create_run(
            created["job_id"],
            "tts_preview",
            "tts_preview",
            lane="tts",
            payload={"lang": "vi", "segment_id": 0},
        )

        locked = client.get(f"/api/v1/jobs/{created['job_id']}/glossary")
        update = client.put(
            f"/api/v1/jobs/{created['job_id']}/glossary",
            json={
                "revision": locked.json()["revision"],
                "document": locked.json()["document"],
            },
        )

    assert locked.json()["editor_locked"] is True
    assert locked.json()["lock_reason"] == "tts_run_active"
    assert update.status_code == 409
    assert update.json()["detail"]["code"] == "artifact_locked"


def test_glossary_put_rejects_invalid_user_terms(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        job = JobStore(tmp_path / "jobs.db").get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        work_dir.mkdir(parents=True)
        write_doc(work_dir / "glossary.json", GlossaryDoc())
        initial = client.get(f"/api/v1/jobs/{created['job_id']}/glossary").json()
        invalid_terms = [
            [{"zh": ""}],
            [{"zh": "A"}, {"zh": "A"}],
            [{"zh": "A", "vi": "a", "keep_source": True}],
        ]
        responses = []
        for terms in invalid_terms:
            document = {**initial["document"], "terms": terms}
            responses.append(
                client.put(
                    f"/api/v1/jobs/{created['job_id']}/glossary",
                    json={"revision": initial["revision"], "document": document},
                )
            )

    assert [response.status_code for response in responses] == [422, 422, 422]
    assert json.loads((work_dir / "glossary.json").read_text(encoding="utf-8"))["terms"] == []


def test_override_marks_output_stale_and_rejects_old_revision(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        job = JobStore(tmp_path / "jobs.db").get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        output = Path(job.request["output_dir"]) / "movie.vi.srt"  # type: ignore[index,union-attr]
        _write_subtitle_artifacts(work_dir, output)
        initial = client.get(f"/api/v1/jobs/{created['job_id']}/subtitles/vi").json()
        saved = client.put(
            f"/api/v1/jobs/{created['job_id']}/subtitle-overrides/vi",
            json={
                "revision": initial["revision"],
                "changes": [{"segment_id": 0, "text": "Chào bạn"}],
            },
        )
        conflict = client.put(
            f"/api/v1/jobs/{created['job_id']}/subtitle-overrides/vi",
            json={
                "revision": initial["revision"],
                "changes": [{"segment_id": 1, "text": "Địa cầu"}],
            },
        )

    assert saved.status_code == 200
    assert saved.json()["cues"][0]["effective_text"] == "Chào bạn"
    assert saved.json()["output_stale"] is True
    assert conflict.status_code == 409


def test_retranslate_forces_the_translate_stage(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        job = JobStore(tmp_path / "jobs.db").get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        output = Path(job.request["output_dir"]) / "movie.vi.srt"  # type: ignore[index,union-attr]
        _write_subtitle_artifacts(work_dir, output)
        response = client.post(f"/api/v1/jobs/{created['job_id']}/retranslate")
        queued = JobStore(tmp_path / "jobs.db").get_run(response.json()["run_id"])

    assert response.status_code == 200
    assert queued.from_stage == "translate"  # type: ignore[union-attr]
    assert queued.force is True  # type: ignore[union-attr]


def test_media_range_and_artifact_ownership(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"0123456789")
    secret = tmp_path / "secret.srt"
    secret.write_text("secret", encoding="utf-8")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        job = JobStore(tmp_path / "jobs.db").get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        output = Path(job.request["output_dir"]) / "movie.vi.srt"  # type: ignore[index,union-attr]
        _write_subtitle_artifacts(work_dir, output)
        ranged = client.get(
            f"/api/v1/jobs/{created['job_id']}/media",
            headers={"Range": "bytes=2-5"},
        )
        detail = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        artifact = detail["artifacts"][0]
        download = client.get(artifact["download_url"])
        denied = client.get(f"/api/v1/jobs/{created['job_id']}/artifacts/deadbeef")

    assert ranged.status_code == 206
    assert ranged.content == b"2345"
    assert ranged.headers["content-range"] == "bytes 2-5/10"
    assert download.text == "subtitle"
    assert denied.status_code == 404
    assert "secret" not in denied.text


def test_report_cannot_claim_another_new_jobs_output(tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    with _client(tmp_path) as client:
        first_job = _create(client, first)
        second_job = _create(client, second)
        client.post(f"/api/v1/runs/{first_job['run_id']}/cancel")
        client.post(f"/api/v1/runs/{second_job['run_id']}/cancel")
        store = JobStore(tmp_path / "jobs.db")
        owner = store.get(second_job["job_id"])
        intruder = store.get(first_job["job_id"])
        owned_file = Path(owner.request["output_dir"]) / "second.vi.srt"  # type: ignore[index,union-attr]
        owned_file.parent.mkdir(parents=True)
        owned_file.write_text("owned by second", encoding="utf-8")
        intruder_work = Path(intruder.work_dir)  # type: ignore[union-attr]
        intruder_work.mkdir(parents=True)
        write_doc(intruder_work / "render_report.json", RenderReport(outputs=[str(owned_file)]))
        detail = client.get(f"/api/v1/jobs/{first_job['job_id']}").json()
        guessed_id = hashlib.sha256(str(owned_file.resolve()).encode("utf-8")).hexdigest()[:20]
        download = client.get(
            f"/api/v1/jobs/{first_job['job_id']}/artifacts/{guessed_id}"
        )

    assert [(item["name"], item["state"]) for item in detail["artifacts"]] == [
        ("first.vi.srt", "missing")
    ]
    assert download.status_code == 404


def test_unreported_expected_file_is_stale_not_missing(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        job = JobStore(tmp_path / "jobs.db").get(created["job_id"])
        output = Path(job.request["output_dir"]) / "movie.vi.srt"  # type: ignore[index,union-attr]
        output.parent.mkdir(parents=True)
        output.write_text("old output", encoding="utf-8")
        detail = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        artifact = detail["artifacts"][0]
        downloaded = client.get(artifact["download_url"])

    assert artifact["state"] == "stale"
    assert downloaded.text == "old output"


def test_timing_change_marks_rendered_output_stale(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        client.post(f"/api/v1/runs/{created['run_id']}/cancel")
        job = JobStore(tmp_path / "jobs.db").get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        output = Path(job.request["output_dir"]) / "movie.vi.srt"  # type: ignore[index,union-attr]
        _write_subtitle_artifacts(work_dir, output)
        current = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        segments = read_doc(work_dir / "segments.json", SegmentsDoc)
        segments.segments[0].end = 1.5
        write_doc(work_dir / "segments.json", segments)
        stale = client.get(f"/api/v1/jobs/{created['job_id']}").json()

    assert current["artifacts"][0]["state"] == "current"
    assert stale["artifacts"][0]["state"] == "stale"
    assert "stale_outputs" in stale["attention_reasons"]


def test_new_render_options_stale_old_files_and_retry_from_ingest(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        first = _create(client, source)
        client.post(f"/api/v1/runs/{first['run_id']}/cancel")
        store = JobStore(tmp_path / "jobs.db")
        job = store.get(first["job_id"])
        output = Path(job.request["output_dir"]) / "movie.vi.srt"  # type: ignore[index,union-attr]
        _write_subtitle_artifacts(Path(job.work_dir), output)  # type: ignore[union-attr]
        store.mark_running(first["job_id"], "render")
        store.mark_stage_done(first["job_id"], "render")
        store.mark_done(first["job_id"])

        second_response = client.post(
            "/api/v1/jobs",
            json={
                "source": {"kind": "local", "value": str(source.resolve())},
                "targets": ["vi", "en"],
                "formats": ["ass"],
                "bilingual": True,
            },
        )
        second = second_response.json()
        detail = client.get(f"/api/v1/jobs/{first['job_id']}").json()
        client.post(f"/api/v1/runs/{second['run_id']}/cancel")
        retry = client.post(f"/api/v1/jobs/{first['job_id']}/retry").json()

    states = {(item["name"], item["state"]) for item in detail["artifacts"]}
    assert ("movie.vi.srt", "stale") in states
    assert ("movie.vi.bilingual.ass", "missing") in states
    assert ("movie.en.bilingual.ass", "missing") in states
    assert retry["from_stage"] == "s0"


def test_file_browser_lists_only_directories_and_media(tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "movie.mkv").write_bytes(b"media")
    (tmp_path / "notes.txt").write_text("hidden", encoding="utf-8")
    with _client(tmp_path) as client:
        listing = client.get("/api/v1/files", params={"path": str(tmp_path)}).json()
    assert [(item["name"], item["kind"]) for item in listing["entries"]] == [
        ("folder", "directory"),
        ("movie.mkv", "file"),
    ]


def test_approval_is_persisted_and_invalidated_by_edits(tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    with _client(tmp_path) as client:
        created = _create(client, source)
        store = JobStore(tmp_path / "jobs.db")
        store.claim_next_run()
        store.finish_run(created["run_id"], "completed", stage="render")
        job = store.get(created["job_id"])
        work_dir = Path(job.work_dir)  # type: ignore[union-attr]
        output = Path(job.request["output_dir"]) / "movie.vi.srt"  # type: ignore[index,union-attr]
        _write_subtitle_artifacts(work_dir, output)
        approved = client.post(f"/api/v1/jobs/{created['job_id']}/approve")
        glossary = client.get(f"/api/v1/jobs/{created['job_id']}/glossary").json()
        glossary["document"]["style"]["speech_register"] = "tự nhiên"
        client.put(
            f"/api/v1/jobs/{created['job_id']}/glossary",
            json={"revision": glossary["revision"], "document": glossary["document"]},
        )
        after_edit = client.get(f"/api/v1/jobs/{created['job_id']}").json()

    assert approved.status_code == 200
    assert approved.json()["quality_status"] == "approved"
    assert after_edit["quality_status"] != "approved"


def test_serve_rejects_non_loopback_before_starting_uvicorn():
    try:
        serve("0.0.0.0", 8756)
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("serve accepted a non-loopback host")
