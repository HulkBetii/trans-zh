"""HTTP contract tests for the Vietnamese TTS workspace."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zhsub.config import Config
from zhsub.dub.calibrate import calibration_hash
from zhsub.jsonio import sha256_json_canonical, sha256_text, write_doc
from zhsub.jobs import JobStore, TTS_PROVIDER
from zhsub.models import (
    GlossaryDoc,
    IngestDoc,
    MediaInfo,
    RenderReport,
    Segment,
    SegmentsDoc,
    SourceInfo,
    StyleDecision,
    TranslationItem,
    TranslationsDoc,
    TtsCalibration,
    TtsReport,
)
from zhsub.overrides import effective_translations_hash
from zhsub.stages import s5_render, s6_dub
from zhsub.web.server import create_app


VOICE_ID = "voice-vi-a"
API_KEY_ENV = "ZHSUB_TEST_TTS_KEY"


def _config(tmp_path: Path) -> Config:
    config = Config()
    config.paths.jobs_db = str(tmp_path / "jobs.db")
    config.paths.work_dir = str(tmp_path / "work")
    config.paths.cache_dir = str(tmp_path / "cache")
    config.dub.api_key_env = API_KEY_ENV
    config.dub.voice_id = "SET_ME"
    return config


def _client(tmp_path: Path, config: Config | None = None) -> TestClient:
    return TestClient(
        create_app(
            config=config or _config(tmp_path),
            start_scheduler=False,
            enforce_readiness=False,
            file_roots=[tmp_path],
        )
    )


def _create_completed_job(
    client: TestClient,
    tmp_path: Path,
    *,
    cue_count: int = 8,
) -> tuple[dict, Path, JobStore]:
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"media")
    created_response = client.post(
        "/api/v1/jobs",
        json={
            "source": {"kind": "local", "value": str(source.resolve())},
            "targets": ["vi"],
            "formats": ["srt"],
            "bilingual": False,
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()

    store = JobStore(tmp_path / "jobs.db")
    job = store.get(created["job_id"])
    assert job is not None and job.request is not None
    work_dir = Path(job.work_dir)
    output_path = Path(job.request["output_dir"]) / "movie.vi.srt"
    _write_subtitle_artifacts(work_dir, output_path, cue_count=cue_count)

    claimed = store.claim_next_run("pipeline")
    assert claimed is not None and claimed.run_id == created["run_id"]
    store.finish_run(created["run_id"], "completed", stage="render")
    return created, work_dir, store


def _write_subtitle_artifacts(
    work_dir: Path,
    output_path: Path,
    *,
    cue_count: int,
) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = float(cue_count * 2)
    segments = [
        Segment(
            id=index,
            start=float(index * 2),
            end=float(index * 2) + 1.25,
            text_zh=f"句{index}",
            token_range=(index, index + 1),
        )
        for index in range(cue_count)
    ]
    write_doc(
        work_dir / "ingest.json",
        IngestDoc(
            job_id=work_dir.name,
            source=SourceInfo(kind="local", uri="movie.mp4", title="movie"),
            media=MediaInfo(
                wav_path="audio.wav",
                duration_sec=duration,
                sample_rate=16000,
                channels=1,
            ),
            created_at="2026-01-01T00:00:00Z",
        ),
    )
    write_doc(
        work_dir / "segments.json",
        SegmentsDoc(source_asr_sha256="source", method="llm", segments=segments),
    )
    glossary = GlossaryDoc(
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
                TranslationItem(
                    id=segment.id,
                    text_zh=segment.text_zh,
                    translation=f"Câu dịch số {segment.id}",
                )
                for segment in segments
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
            segments_render_hash=s5_render.build_segments_render_hash(
                segments, duration
            ),
            render_request_hash=s5_render.build_render_request_hash(
                ["vi"], ["srt"], False
            ),
        ),
    )


def _select_voice(client: TestClient, job_id: str) -> dict:
    workspace = client.get(f"/api/v1/jobs/{job_id}/tts/vi")
    assert workspace.status_code == 200, workspace.text
    response = client.put(
        f"/api/v1/jobs/{job_id}/tts/vi/settings",
        json={"revision": workspace.json()["revision"], "voice_id": VOICE_ID},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve_subtitles(client: TestClient, job_id: str) -> dict:
    response = client.post(f"/api/v1/jobs/{job_id}/approve")
    assert response.status_code == 200, response.text
    assert response.json()["quality_status"] == "approved"
    return response.json()


def _save_calibration(store: JobStore, job_id: str) -> None:
    store.save_voice_calibration(
        TTS_PROVIDER,
        VOICE_ID,
        0.15,
        0.217,
        sample_count=8,
        source_job_id=job_id,
    )


class _FakeSpeechClient:
    #: Tham số của lần gọi gần nhất, để test kiểm nguồn giọng được truyền xuống.
    last_params: dict = {}

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def voice_page(self, provider: str = "vbee", **kwargs):
        _FakeSpeechClient.last_params = {"provider": provider, **kwargs}
        rows = self.voices()
        return rows, len(rows)

    def voices(self, **_kwargs):
        return [
            {
                "voice_id": VOICE_ID,
                "name": "Mai Vietnamese",
                "language": "Vietnamese",
                "gender": "female",
                "preview_url": "https://example.test/mai.mp3",
                "descriptives": ["PREMIUM"],
            },
            {
                "voice_id": "voice-en",
                "name": "English voice",
                "language": "English",
            },
        ]

    def credits(self) -> int:
        return 37

    def close(self) -> None:
        pass


def test_tts_capability_and_vietnamese_voice_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr("zhsub.web.tts_service.SpeechClient", _FakeSpeechClient)
    store = JobStore(tmp_path / "jobs.db")
    store.save_voice_calibration(TTS_PROVIDER, VOICE_ID, 0.15, 0.217)

    with _client(tmp_path) as client:
        meta = client.get("/api/v1/meta")
        voices = client.get(
            "/api/v1/tts/voices",
            params={"search": "Mai", "page": 1, "page_size": 1},
        )

    assert meta.status_code == 200
    assert "tts" in meta.json()["capabilities"]
    assert voices.status_code == 200, voices.text
    assert voices.json() == {
        "items": [
            {
                "voice_id": VOICE_ID,
                "name": "Mai Vietnamese",
                "description": None,
                "locale": None,
                "gender": "female",
                "age": None,
                "category": None,
                "tier": "PREMIUM",
                "preview_url": "https://example.test/mai.mp3",
                "avatar_url": None,
                "calibrated": True,
            }
        ],
        "page": 1,
        "page_size": 1,
        "total": 1,
        "has_more": False,
        "credits": 37,
    }


def test_job_summary_ignores_inactive_tts_until_the_lane_is_started(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        created, _, _ = _create_completed_job(client, tmp_path)
        _approve_subtitles(client, created["job_id"])

        inactive = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        inactive_attention = client.get(
            "/api/v1/jobs",
            params={"lane": "any", "quality": "needs_review,degraded"},
        ).json()
        tts_only = client.get("/api/v1/jobs", params={"lane": "tts"}).json()

        _select_voice(client, created["job_id"])
        started = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        started_attention = client.get(
            "/api/v1/jobs",
            params={"lane": "any", "quality": "needs_review,degraded"},
        ).json()

    inactive_tts = next(item for item in inactive["lanes"] if item["id"] == "tts")
    started_tts = next(item for item in started["lanes"] if item["id"] == "tts")
    assert inactive_tts["started"] is False
    assert inactive_tts["execution_status"] == "not_started"
    assert inactive_attention["total"] == 0
    assert tts_only["total"] == 0
    assert started_tts["started"] is True
    assert started_tts["quality_status"] == "needs_review"
    assert started_attention["items"][0]["job_id"] == created["job_id"]


def test_configured_default_voice_does_not_start_tts_until_user_saves_it(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.dub.voice_id = VOICE_ID
    with _client(tmp_path, config) as client:
        created, work_dir, _ = _create_completed_job(client, tmp_path)

        initial = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        workspace = client.get(f"/api/v1/jobs/{created['job_id']}/tts/vi").json()
        after_workspace = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        speech_created_before_selection = (work_dir / "speech.vi.json").exists()
        selected = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/settings",
            json={"revision": workspace["revision"], "voice_id": VOICE_ID},
        )
        started = client.get(f"/api/v1/jobs/{created['job_id']}").json()

    assert workspace["voice_id"] == VOICE_ID
    assert next(item for item in initial["lanes"] if item["id"] == "tts")["started"] is False
    assert next(item for item in after_workspace["lanes"] if item["id"] == "tts")["started"] is False
    assert speech_created_before_selection is False
    assert selected.status_code == 200, selected.text
    assert (work_dir / "speech.vi.json").is_file()
    assert next(item for item in started["lanes"] if item["id"] == "tts")["started"] is True


def test_tts_lane_is_filterable_and_cancellable_without_mutating_pipeline_aliases(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        created, _, store = _create_completed_job(client, tmp_path)
        _select_voice(client, created["job_id"])
        run = store.create_run(
            created["job_id"],
            "tts_preview",
            "tts_preview",
            lane="tts",
            payload={"lang": "vi", "segment_id": 0},
        )

        active = client.get(
            "/api/v1/jobs",
            params={"lane": "any", "execution": "queued,running,cancelling"},
        ).json()
        detail = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        cancelled = client.post(f"/api/v1/runs/{run.run_id}/cancel")

    tts_lane = next(item for item in detail["lanes"] if item["id"] == "tts")
    assert active["items"][0]["job_id"] == created["job_id"]
    assert detail["execution_status"] == "completed"
    assert detail["active_run"] is None
    assert tts_lane["active_run"]["run_id"] == run.run_id
    assert tts_lane["allowed_actions"] == ["cancel"]
    assert cancelled.status_code == 200
    assert store.get_run(run.run_id).status == "cancelled"  # type: ignore[union-attr]


def test_tts_activity_controls_job_sort_order(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first, _, _ = _create_completed_job(client, tmp_path)
        second_source = tmp_path / "movie-second.mp4"
        second_source.write_bytes(b"media")
        second = client.post(
            "/api/v1/jobs",
            json={
                "source": {"kind": "local", "value": str(second_source.resolve())},
                "targets": ["vi"],
                "formats": ["srt"],
                "bilingual": False,
            },
        ).json()
        time.sleep(0.01)
        _select_voice(client, first["job_id"])

        jobs = client.get("/api/v1/jobs", params={"lane": "any"}).json()

    assert jobs["items"][0]["job_id"] == first["job_id"]
    assert {item["job_id"] for item in jobs["items"]} >= {
        first["job_id"],
        second["job_id"],
    }


def test_tts_workspace_settings_and_spoken_override_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    with _client(tmp_path) as client:
        created, _, _ = _create_completed_job(client, tmp_path)
        initial = client.get(f"/api/v1/jobs/{created['job_id']}/tts/vi").json()
        assert initial["voice_id"] is None
        assert "voice_unselected" in initial["attention_reasons"]

        selected = _select_voice(client, created["job_id"])
        assert selected["voice_id"] == VOICE_ID
        assert selected["total_cues"] == 8
        assert selected["cues"][0]["end"] == pytest.approx(1.25)

        stale_settings = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/settings",
            json={"revision": initial["revision"], "voice_id": "voice-vi-b"},
        )
        saved = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/spoken-overrides",
            json={
                "revision": selected["revision"],
                "changes": [{"segment_id": 0, "text": "Cách đọc riêng"}],
            },
        )
        stale_override = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/spoken-overrides",
            json={
                "revision": selected["revision"],
                "changes": [{"segment_id": 1, "text": "Không được ghi đè"}],
            },
        )
        blank_override = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/spoken-overrides",
            json={
                "revision": saved.json()["revision"],
                "changes": [{"segment_id": 0, "text": "  "}],
            },
        )

    assert stale_settings.status_code == 409
    assert stale_settings.json()["detail"]["code"] == "revision_conflict"
    assert saved.status_code == 200, saved.text
    assert saved.json()["cues"][0]["effective_spoken_text"] == "Cách đọc riêng"
    assert saved.json()["cues"][0]["override_state"] == "manual"
    assert stale_override.status_code == 409
    assert stale_override.json()["detail"]["code"] == "revision_conflict"
    assert blank_override.status_code == 422


def test_preview_and_calibration_are_allowed_before_subtitle_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    with _client(tmp_path) as client:
        created, _, store = _create_completed_job(client, tmp_path)
        selected = _select_voice(client, created["job_id"])
        assert selected["subtitle_approved"] is False
        assert {"preview", "calibrate"} <= set(selected["allowed_actions"])
        assert "render" not in selected["allowed_actions"]

        preview = client.post(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/preview",
            json={"segment_id": 0},
        )
        assert preview.status_code == 200, preview.text
        preview_run = store.get_run(preview.json()["run_id"])
        assert preview.json()["lane"] == "tts"
        assert preview.json()["kind"] == "tts_preview"
        assert preview_run is not None
        assert preview_run.payload == {
            "lang": "vi",
            "voice_id": VOICE_ID,
            "speech_revision": selected["revision"],
            "segment_id": 0,
        }
        client.post(f"/api/v1/runs/{preview.json()['run_id']}/cancel")

        calibration = client.post(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/calibrate"
        )
        assert calibration.status_code == 200, calibration.text
        assert calibration.json()["lane"] == "tts"
        assert calibration.json()["kind"] == "tts_calibrate"
        client.post(f"/api/v1/runs/{calibration.json()['run_id']}/cancel")

        render = client.post(f"/api/v1/jobs/{created['job_id']}/tts/vi/render")

    assert render.status_code == 409


def test_tts_calibration_requires_eight_cues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    with _client(tmp_path) as client:
        created, _, _ = _create_completed_job(client, tmp_path, cue_count=7)
        selected = _select_voice(client, created["job_id"])
        response = client.post(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/calibrate"
        )

    assert "calibrate" not in selected["allowed_actions"]
    assert response.status_code == 409


def test_tts_render_is_not_advertised_when_provider_is_unavailable(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        created, _, store = _create_completed_job(client, tmp_path)
        _select_voice(client, created["job_id"])
        _save_calibration(store, created["job_id"])
        _approve_subtitles(client, created["job_id"])

        workspace = client.get(f"/api/v1/jobs/{created['job_id']}/tts/vi")
        render = client.post(f"/api/v1/jobs/{created['job_id']}/tts/vi/render")

    assert workspace.json()["provider_ready"] is False
    assert "render" not in workspace.json()["allowed_actions"]
    assert render.status_code == 409
    assert render.json()["detail"]["code"] == "action_not_allowed"


def test_tts_render_snapshots_gate_state_and_does_not_replace_core_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    with _client(tmp_path) as client:
        created, _, store = _create_completed_job(client, tmp_path)
        selected = _select_voice(client, created["job_id"])
        _save_calibration(store, created["job_id"])
        _approve_subtitles(client, created["job_id"])

        response = client.post(f"/api/v1/jobs/{created['job_id']}/tts/vi/render")
        assert response.status_code == 200, response.text
        queued = store.get_run(response.json()["run_id"])
        core_detail = client.get(f"/api/v1/jobs/{created['job_id']}").json()
        run_detail = client.get(f"/api/v1/runs/{response.json()['run_id']}").json()
        blocked_settings = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/settings",
            json={"revision": selected["revision"], "voice_id": "voice-vi-b"},
        )

    assert response.json()["lane"] == "tts"
    assert response.json()["kind"] == "tts_render"
    assert run_detail["lane"] == "tts"
    assert queued is not None and queued.payload is not None
    assert queued.payload["lang"] == "vi"
    assert queued.payload["voice_id"] == VOICE_ID
    assert queued.payload["speech_revision"] == selected["revision"]
    assert queued.payload["calibration_revision"]
    assert queued.payload["subtitle_approval_signature"]
    assert core_detail["execution_status"] == "completed"
    assert core_detail["active_run"] is None
    assert blocked_settings.status_code == 409


def test_preview_download_resolves_only_the_current_cue_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    config = _config(tmp_path)
    with _client(tmp_path, config) as client:
        created, work_dir, _ = _create_completed_job(client, tmp_path)
        selected = _select_voice(client, created["job_id"])
        plan = s6_dub.build_plan(work_dir, config, voice_id=VOICE_ID)
        plan.cues[0].clip_path.write_bytes(b"preview-audio")

        current = client.get(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/previews/0"
        )
        unknown = client.get(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/previews/999"
        )
        changed = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/spoken-overrides",
            json={
                "revision": selected["revision"],
                "changes": [{"segment_id": 0, "text": "Nội dung cache mới"}],
            },
        )
        assert changed.status_code == 200, changed.text
        stale = client.get(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/previews/0"
        )

    assert current.status_code == 200
    assert current.content == b"preview-audio"
    assert current.headers["content-type"].startswith("audio/mpeg")
    assert unknown.status_code == 404
    assert stale.status_code == 404


def test_tts_approval_is_separate_and_stale_audio_is_not_a_subtitle_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    config = _config(tmp_path)
    with _client(tmp_path, config) as client:
        created, work_dir, store = _create_completed_job(client, tmp_path)
        selected = _select_voice(client, created["job_id"])
        _save_calibration(store, created["job_id"])
        _approve_subtitles(client, created["job_id"])
        core_approval = store.get(created["job_id"]).approved_signature  # type: ignore[union-attr]

        calibration = TtsCalibration(
            voice_id=VOICE_ID,
            overhead_sec=0.15,
            sec_per_syllable=0.217,
            samples_hash="shared-calibration",
            created_at="2026-01-01T00:00:00Z",
            points=[],
        )
        plan = s6_dub.build_plan(
            work_dir,
            config,
            voice_id=VOICE_ID,
            calibration=calibration,
        )
        job = store.get(created["job_id"])
        assert job is not None and job.request is not None
        output = Path(job.request["output_dir"]) / f"{work_dir.name}.vi.mp3"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ID3-current-audio")
        write_doc(
            work_dir / "tts_report.vi.json",
            TtsReport(
                output=str(output),
                voice_id=VOICE_ID,
                voice_hash=sha256_text(f"ai33_vbee:{VOICE_ID}"),
                calibration_hash=calibration_hash(calibration),
                input_hash=plan.input_hash,
                speech_revision=plan.speech_revision,
                subtitle_approval_signature=core_approval or "",
                duration_sec=plan.total_duration_sec,
            ),
        )

        before = client.get(f"/api/v1/jobs/{created['job_id']}/tts/vi").json()
        approved = client.post(f"/api/v1/jobs/{created['job_id']}/tts/vi/approve")
        unapproved = client.post(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/unapprove"
        )
        changed = client.put(
            f"/api/v1/jobs/{created['job_id']}/tts/vi/spoken-overrides",
            json={
                "revision": selected["revision"],
                "changes": [{"segment_id": 0, "text": "Bản đọc thay đổi"}],
            },
        )
        core_after = client.get(f"/api/v1/jobs/{created['job_id']}").json()

    assert before["output"]["state"] == "current"
    assert "approve" in before["allowed_actions"]
    assert approved.status_code == 200, approved.text
    assert approved.json()["quality_status"] == "approved"
    assert unapproved.status_code == 200, unapproved.text
    assert unapproved.json()["quality_status"] == "needs_review"
    assert changed.status_code == 200, changed.text
    assert changed.json()["output_stale"] is True
    assert store.get_tts_approval(created["job_id"], "vi") is None
    assert store.get(created["job_id"]).approved_signature == core_approval  # type: ignore[union-attr]
    assert core_after["quality_status"] == "approved"
    assert "stale_outputs" not in core_after["attention_reasons"]
