"""Durable job and run state for CLI and web hosts.

The original ``jobs`` table remains backwards compatible with the CLI. Web runs
live in their own table because one deterministic job can be retried, translated,
or rendered many times without losing the history of earlier attempts.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STAGES = ("ingest", "asr", "segment", "glossary", "translate", "render")
TTS_STAGES = ("tts_preview", "tts_calibrate", "tts_render")
TTS_PROVIDER = "ai33_vbee"
STALE_HEARTBEAT_SEC = 300.0

RUN_STATUSES = (
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "failed",
    "interrupted",
    "completed",
)
ACTIVE_RUN_STATUSES = ("queued", "running", "cancelling")
TERMINAL_RUN_STATUSES = ("cancelled", "failed", "interrupted", "completed")
RUN_LANES = ("pipeline", "tts")
PIPELINE_RUN_KINDS = ("pipeline", "retry", "retranslate", "render")
TTS_RUN_KINDS = TTS_STAGES
RUN_KINDS = PIPELINE_RUN_KINDS + TTS_RUN_KINDS

_RUN_KIND_LANES = {
    **{kind: "pipeline" for kind in PIPELINE_RUN_KINDS},
    **{kind: "tts" for kind in TTS_RUN_KINDS},
}
_RUN_START_STAGES = {
    "pipeline": STAGES,
    "retry": STAGES,
    "retranslate": ("translate",),
    "render": ("render",),
    "tts_preview": ("tts_preview",),
    "tts_calibrate": ("tts_calibrate",),
    "tts_render": ("tts_render",),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    source_uri   TEXT NOT NULL,
    work_dir     TEXT NOT NULL,
    targets      TEXT NOT NULL,
    stage        TEXT,
    status       TEXT NOT NULL,
    error        TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    heartbeat_at REAL,
    request_json TEXT,
    approved_at REAL,
    approved_signature TEXT
);
CREATE TABLE IF NOT EXISTS stage_runs (
    job_id     TEXT NOT NULL,
    stage      TEXT NOT NULL,
    status     TEXT NOT NULL,
    started_at REAL,
    ended_at   REAL,
    error      TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    lane         TEXT NOT NULL DEFAULT 'pipeline',
    kind         TEXT NOT NULL,
    from_stage   TEXT NOT NULL,
    payload_json TEXT,
    force        INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,
    stage        TEXT,
    progress     REAL NOT NULL DEFAULT 0,
    message      TEXT NOT NULL DEFAULT '',
    error        TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    started_at   REAL,
    ended_at     REAL,
    event_seq    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS voice_calibrations (
    provider         TEXT NOT NULL,
    voice_id         TEXT NOT NULL,
    overhead_sec     REAL NOT NULL,
    sec_per_syllable REAL NOT NULL,
    sample_count     INTEGER NOT NULL DEFAULT 8,
    source_job_id    TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    PRIMARY KEY (provider, voice_id)
);
CREATE TABLE IF NOT EXISTS tts_approvals (
    job_id             TEXT NOT NULL,
    lang               TEXT NOT NULL,
    approved_at        REAL,
    approved_signature TEXT,
    PRIMARY KEY (job_id, lang)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_stage_runs_job ON stage_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_runs_job_created ON runs(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_lane_status_created
    ON runs(lane, status, created_at);
CREATE INDEX IF NOT EXISTS idx_run_events_run_seq ON run_events(run_id, seq);
"""


@dataclass(slots=True)
class Job:
    job_id: str
    source_uri: str
    work_dir: str
    targets: list[str]
    stage: str | None
    status: str  # Legacy CLI values: pending | running | done | failed.
    error: str | None
    attempts: int
    created_at: float = 0.0
    updated_at: float = 0.0
    heartbeat_at: float | None = None
    request: dict[str, Any] | None = None
    approved_at: float | None = None
    approved_signature: str | None = None


@dataclass(slots=True)
class Run:
    run_id: str
    job_id: str
    lane: str
    kind: str
    from_stage: str
    payload: dict[str, Any] | None
    force: bool
    status: str
    stage: str | None
    progress: float
    message: str
    error: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    ended_at: float | None
    event_seq: int


@dataclass(slots=True)
class RunEvent:
    run_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: float


@dataclass(slots=True)
class VoiceCalibration:
    provider: str
    voice_id: str
    overhead_sec: float
    sec_per_syllable: float
    sample_count: int
    source_job_id: str | None
    created_at: float
    updated_at: float


@dataclass(slots=True)
class TtsApproval:
    job_id: str
    lang: str
    approved_at: float | None
    approved_signature: str | None


def _validate_run_spec(lane: str, kind: str, from_stage: str) -> None:
    if lane not in RUN_LANES:
        raise ValueError(f"Unknown run lane: {lane}")
    if kind not in RUN_KINDS:
        raise ValueError(f"Unknown run kind: {kind}")
    expected_lane = _RUN_KIND_LANES[kind]
    if lane != expected_lane:
        raise ValueError(f"Run kind {kind!r} belongs to lane {expected_lane!r}")
    if from_stage not in _RUN_START_STAGES[kind]:
        raise ValueError(f"Invalid start stage {from_stage!r} for run kind {kind!r}")


class JobStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            self._migrate(conn)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Apply additive migrations without rebuilding an existing database."""
        jobs_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        if jobs_table is not None:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "request_json" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN request_json TEXT")
            if "approved_at" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN approved_at REAL")
            if "approved_signature" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN approved_signature TEXT")
        runs_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
        if runs_table is not None:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            if "lane" not in columns:
                conn.execute(
                    "ALTER TABLE runs ADD COLUMN lane TEXT NOT NULL DEFAULT 'pipeline'"
                )
            if "payload_json" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN payload_json TEXT")
            conn.execute("UPDATE runs SET lane='pipeline' WHERE lane IS NULL OR lane=''")
        conn.executescript(_SCHEMA)

    # -- legacy job state -------------------------------------------------

    def upsert(
        self,
        job_id: str,
        source_uri: str,
        work_dir: str,
        targets: list[str],
        request: dict[str, Any] | None = None,
    ) -> Job:
        now = time.time()
        request_json = _dump_json(request) if request is not None else None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO jobs (
                       job_id, source_uri, work_dir, targets, stage, status,
                       created_at, updated_at, request_json
                   ) VALUES (?, ?, ?, ?, NULL, 'pending', ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       source_uri = excluded.source_uri,
                       work_dir   = excluded.work_dir,
                       targets    = excluded.targets,
                       request_json = COALESCE(excluded.request_json, jobs.request_json),
                       updated_at = excluded.updated_at""",
                (
                    job_id,
                    source_uri,
                    work_dir,
                    ",".join(targets),
                    now,
                    now,
                    request_json,
                ),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def set_request(self, job_id: str, request: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET request_json=?, updated_at=? WHERE job_id=?",
                (_dump_json(request), time.time(), job_id),
            )

    def set_approval(self, job_id: str, signature: str | None) -> None:
        approved_at = time.time() if signature is not None else None
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET approved_at=?, approved_signature=?, updated_at=? "
                "WHERE job_id=?",
                (approved_at, signature, time.time(), job_id),
            )

    def approve(self, job_id: str, signature: str) -> None:
        """Approve only while no run can mutate the job artifacts."""
        now = time.time()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT run_id FROM runs WHERE job_id=? "
                "AND status IN ('queued','running','cancelling') LIMIT 1",
                (job_id,),
            ).fetchone()
            if active is not None:
                raise ValueError(f"Job {job_id} has active run {active['run_id']}")
            changed = conn.execute(
                "UPDATE jobs SET approved_at=?, approved_signature=?, updated_at=? "
                "WHERE job_id=? AND status='done'",
                (now, signature, now, job_id),
            ).rowcount
            if not changed:
                raise ValueError(f"Job {job_id} is not completed")

    # -- TTS configuration and approval ----------------------------------

    def get_voice_calibration(
        self,
        provider: str,
        voice_id: str,
    ) -> VoiceCalibration | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM voice_calibrations WHERE provider=? AND voice_id=?",
                (provider, voice_id),
            ).fetchone()
        return _to_voice_calibration(row) if row else None

    def save_voice_calibration(
        self,
        provider: str,
        voice_id: str,
        overhead_sec: float,
        sec_per_syllable: float,
        *,
        sample_count: int = 8,
        source_job_id: str | None = None,
        overwrite: bool = True,
    ) -> VoiceCalibration:
        """Persist one shared calibration for a provider/voice pair.

        A calibration is deliberately keyed by voice rather than by job.  The
        measured coefficients can therefore be reused by every job while the
        source job remains available for provenance in the UI.
        """
        provider = provider.strip()
        voice_id = voice_id.strip()
        if not provider or not voice_id:
            raise ValueError("provider and voice_id must not be blank")
        if (
            not math.isfinite(overhead_sec)
            or not math.isfinite(sec_per_syllable)
            or overhead_sec < 0
            or sec_per_syllable <= 0
        ):
            raise ValueError("calibration coefficients must be non-negative/positive")
        if sample_count != 8:
            raise ValueError("shared TTS calibration requires exactly eight samples")
        now = time.time()
        with self._conn() as conn:
            if overwrite:
                conn.execute(
                    """INSERT INTO voice_calibrations (
                           provider, voice_id, overhead_sec, sec_per_syllable,
                           sample_count, source_job_id, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(provider, voice_id) DO UPDATE SET
                           overhead_sec=excluded.overhead_sec,
                           sec_per_syllable=excluded.sec_per_syllable,
                           sample_count=excluded.sample_count,
                           source_job_id=excluded.source_job_id,
                           updated_at=excluded.updated_at""",
                    (
                        provider,
                        voice_id,
                        float(overhead_sec),
                        float(sec_per_syllable),
                        int(sample_count),
                        source_job_id,
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO voice_calibrations (
                           provider, voice_id, overhead_sec, sec_per_syllable,
                           sample_count, source_job_id, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        provider,
                        voice_id,
                        float(overhead_sec),
                        float(sec_per_syllable),
                        int(sample_count),
                        source_job_id,
                        now,
                        now,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM voice_calibrations WHERE provider=? AND voice_id=?",
                (provider, voice_id),
            ).fetchone()
        assert row is not None
        return _to_voice_calibration(row)

    def get_tts_approval(self, job_id: str, lang: str) -> TtsApproval | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tts_approvals WHERE job_id=? AND lang=?",
                (job_id, lang),
            ).fetchone()
        return _to_tts_approval(row) if row else None

    def set_tts_approval(
        self,
        job_id: str,
        lang: str,
        signature: str | None,
    ) -> TtsApproval | None:
        """Set or clear TTS approval without touching core job approval."""
        now = time.time()
        with self._conn() as conn:
            if signature is None:
                conn.execute(
                    "DELETE FROM tts_approvals WHERE job_id=? AND lang=?",
                    (job_id, lang),
                )
                return None
            conn.execute(
                """INSERT INTO tts_approvals (
                       job_id, lang, approved_at, approved_signature
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id, lang) DO UPDATE SET
                       approved_at=excluded.approved_at,
                       approved_signature=excluded.approved_signature""",
                (job_id, lang, now, signature),
            )
            row = conn.execute(
                "SELECT * FROM tts_approvals WHERE job_id=? AND lang=?",
                (job_id, lang),
            ).fetchone()
        return _to_tts_approval(row) if row else None

    def approve_tts(
        self,
        job_id: str,
        lang: str,
        signature: str,
        expected_signature: str | None = None,
    ) -> TtsApproval:
        """Approve a TTS output after an atomic revision and run check."""
        if not signature.strip():
            raise ValueError("TTS approval signature must not be blank")
        now = time.time()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if expected_signature is not None and expected_signature != signature:
                raise ValueError("TTS approval revision is stale")
            active = conn.execute(
                "SELECT run_id FROM runs WHERE job_id=? "
                "AND status IN ('queued','running','cancelling') LIMIT 1",
                (job_id,),
            ).fetchone()
            if active is not None:
                raise ValueError(f"Job {job_id} has active run {active['run_id']}")
            exists = conn.execute(
                "SELECT 1 FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"Unknown job: {job_id}")
            conn.execute(
                """INSERT INTO tts_approvals (
                       job_id, lang, approved_at, approved_signature
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id, lang) DO UPDATE SET
                       approved_at=excluded.approved_at,
                       approved_signature=excluded.approved_signature""",
                (job_id, lang, now, signature),
            )
            row = conn.execute(
                "SELECT * FROM tts_approvals WHERE job_id=? AND lang=?",
                (job_id, lang),
            ).fetchone()
        assert row is not None
        return _to_tts_approval(row)

    def get(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _to_job(row) if row else None

    def list(self, status: str | None = None) -> list[Job]:
        query = "SELECT * FROM jobs"
        args: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            args = (status,)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            return [_to_job(row) for row in conn.execute(query, args).fetchall()]

    def mark_running(self, job_id: str, stage: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='running', stage=?, updated_at=?, "
                "heartbeat_at=?, error=NULL WHERE job_id=?",
                (stage, now, now, job_id),
            )
            conn.execute(
                "INSERT INTO stage_runs (job_id, stage, status, started_at) "
                "VALUES (?, ?, 'running', ?)",
                (job_id, stage, now),
            )

    def heartbeat(self, job_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET heartbeat_at=? WHERE job_id=?", (time.time(), job_id))

    def mark_stage_done(self, job_id: str, stage: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE stage_runs SET status='done', ended_at=? "
                "WHERE rowid=(SELECT rowid FROM stage_runs WHERE job_id=? AND stage=? "
                "AND status='running' ORDER BY started_at DESC LIMIT 1)",
                (now, job_id, stage),
            )
            conn.execute(
                "UPDATE jobs SET stage=?, updated_at=?, heartbeat_at=? WHERE job_id=?",
                (stage, now, now, job_id),
            )

    def mark_done(self, job_id: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='done', updated_at=?, heartbeat_at=NULL, "
                "error=NULL WHERE job_id=?",
                (now, job_id),
            )

    def mark_failed(self, job_id: str, stage: str, error: str) -> None:
        now = time.time()
        short_error = error[:2000]
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed', stage=?, error=?, updated_at=?, "
                "heartbeat_at=NULL, attempts=attempts+1 WHERE job_id=?",
                (stage, short_error, now, job_id),
            )
            conn.execute(
                "UPDATE stage_runs SET status='failed', ended_at=?, error=? "
                "WHERE rowid=(SELECT rowid FROM stage_runs WHERE job_id=? AND stage=? "
                "AND status='running' ORDER BY started_at DESC LIMIT 1)",
                (now, short_error, job_id, stage),
            )

    def mark_job_cancelled(self, job_id: str, stage: str | None = None) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='cancelled', stage=COALESCE(?, stage), error=NULL, "
                "updated_at=?, heartbeat_at=NULL WHERE job_id=?",
                (stage, now, job_id),
            )
            conn.execute(
                "UPDATE stage_runs SET status='cancelled', ended_at=?, error=NULL "
                "WHERE rowid=(SELECT rowid FROM stage_runs WHERE job_id=? AND status='running' "
                "ORDER BY started_at DESC LIMIT 1)",
                (now, job_id),
            )

    def reclaim_stale(self, stale_after_sec: float = STALE_HEARTBEAT_SEC) -> list[str]:
        """Reset stale legacy CLI jobs so ``batch`` can resume them."""
        cutoff = time.time() - stale_after_sec
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE status='running' "
                "AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
                (cutoff,),
            ).fetchall()
            ids = [row["job_id"] for row in rows]
            if ids:
                conn.executemany(
                    "UPDATE jobs SET status='pending', heartbeat_at=NULL WHERE job_id=?",
                    [(job_id,) for job_id in ids],
                )
        return ids

    def latest_stage_runs(self, job_id: str) -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.* FROM stage_runs s
                   WHERE s.job_id=? AND s.rowid=(
                       SELECT s2.rowid FROM stage_runs s2
                       WHERE s2.job_id=s.job_id AND s2.stage=s.stage
                       ORDER BY COALESCE(s2.started_at, 0) DESC, s2.rowid DESC LIMIT 1
                   )""",
                (job_id,),
            ).fetchall()
        return {row["stage"]: dict(row) for row in rows}

    # -- durable web runs -------------------------------------------------

    def upsert_and_create_run(
        self,
        job_id: str,
        source_uri: str,
        work_dir: str,
        targets: list[str],
        request: dict[str, Any],
        *,
        lane: str = "pipeline",
        kind: str = "pipeline",
        from_stage: str = "ingest",
        force: bool = False,
        payload: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> tuple[Job, Run]:
        """Atomically persist a request snapshot and enqueue its first run."""
        _validate_run_spec(lane, kind, from_stage)
        if lane != "pipeline":
            raise ValueError("upsert_and_create_run only supports the pipeline lane")
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        now = time.time()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT run_id FROM runs WHERE job_id=? "
                "AND status IN ('queued','running','cancelling')",
                (job_id,),
            ).fetchone()
            if active is not None:
                raise ValueError(f"Job {job_id} already has active run {active['run_id']}")
            conn.execute(
                """INSERT INTO jobs (
                       job_id, source_uri, work_dir, targets, stage, status,
                       error, created_at, updated_at, request_json,
                       approved_at, approved_signature
                   ) VALUES (?, ?, ?, ?, NULL, 'pending', NULL, ?, ?, ?, NULL, NULL)
                   ON CONFLICT(job_id) DO UPDATE SET
                       source_uri=excluded.source_uri,
                       work_dir=excluded.work_dir,
                       targets=excluded.targets,
                       status='pending',
                       error=NULL,
                       request_json=excluded.request_json,
                       approved_at=NULL,
                       approved_signature=NULL,
                       heartbeat_at=NULL,
                       updated_at=excluded.updated_at""",
                (
                    job_id,
                    source_uri,
                    work_dir,
                    ",".join(targets),
                    now,
                    now,
                    _dump_json(request),
                ),
            )
            conn.execute(
                """INSERT INTO runs (
                       run_id, job_id, lane, kind, from_stage, payload_json,
                       force, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    run_id,
                    job_id,
                    lane,
                    kind,
                    from_stage,
                    _dump_json(payload) if payload is not None else None,
                    int(force),
                    now,
                    now,
                ),
            )
            self._append_event(conn, run_id, "state", {"status": "queued"}, now)
        job = self.get(job_id)
        run = self.get_run(run_id)
        assert job is not None and run is not None
        return job, run

    def create_run(
        self,
        job_id: str,
        kind: str,
        from_stage: str,
        *,
        lane: str = "pipeline",
        force: bool = False,
        payload: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        _validate_run_spec(lane, kind, from_stage)
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        now = time.time()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT run_id FROM runs WHERE job_id=? AND status IN ('queued','running','cancelling')",
                (job_id,),
            ).fetchone()
            if active is not None:
                raise ValueError(f"Job {job_id} already has active run {active['run_id']}")
            conn.execute(
                """INSERT INTO runs (
                       run_id, job_id, lane, kind, from_stage, payload_json,
                       force, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    run_id,
                    job_id,
                    lane,
                    kind,
                    from_stage,
                    _dump_json(payload) if payload is not None else None,
                    int(force),
                    now,
                    now,
                ),
            )
            if lane == "pipeline":
                conn.execute(
                    "UPDATE jobs SET status='pending', error=NULL, approved_at=NULL, "
                    "approved_signature=NULL, updated_at=? WHERE job_id=?",
                    (now, job_id),
                )
            self._append_event(conn, run_id, "state", {"status": "queued"}, now)
        run = self.get_run(run_id)
        assert run is not None
        return run

    def get_run(self, run_id: str) -> Run | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _to_run(row) if row else None

    def latest_run(self, job_id: str, lane: str | None = None) -> Run | None:
        with self._conn() as conn:
            if lane is None:
                row = conn.execute(
                    "SELECT * FROM runs WHERE job_id=? "
                    "ORDER BY created_at DESC, run_id DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
            else:
                if lane not in RUN_LANES:
                    raise ValueError(f"Unknown run lane: {lane}")
                row = conn.execute(
                    "SELECT * FROM runs WHERE job_id=? AND lane=? "
                    "ORDER BY created_at DESC, run_id DESC LIMIT 1",
                    (job_id, lane),
                ).fetchone()
        return _to_run(row) if row else None

    def active_run(self, job_id: str, lane: str | None = None) -> Run | None:
        with self._conn() as conn:
            args: tuple[Any, ...] = (job_id,)
            lane_clause = ""
            if lane is not None:
                if lane not in RUN_LANES:
                    raise ValueError(f"Unknown run lane: {lane}")
                lane_clause = " AND lane=?"
                args += (lane,)
            row = conn.execute(
                "SELECT * FROM runs WHERE job_id=?" + lane_clause + " "
                "AND status IN ('queued','running','cancelling') "
                "ORDER BY created_at DESC, run_id DESC LIMIT 1",
                args,
            ).fetchone()
        return _to_run(row) if row else None

    def list_runs(
        self,
        job_id: str | None = None,
        lane: str | None = None,
    ) -> list[Run]:
        query = "SELECT * FROM runs"
        clauses: list[str] = []
        args: list[Any] = []
        if job_id is not None:
            clauses.append("job_id=?")
            args.append(job_id)
        if lane is not None:
            if lane not in RUN_LANES:
                raise ValueError(f"Unknown run lane: {lane}")
            clauses.append("lane=?")
            args.append(lane)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, run_id DESC"
        with self._conn() as conn:
            return [_to_run(row) for row in conn.execute(query, tuple(args)).fetchall()]

    def queue_position(self, run_id: str) -> int | None:
        run = self.get_run(run_id)
        if run is None or run.status != "queued":
            return None
        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE lane=? AND status='queued' "
                "AND (created_at < ? OR (created_at = ? AND run_id <= ?))",
                (run.lane, run.created_at, run.created_at, run.run_id),
            ).fetchone()["n"]
        return int(count)

    def claim_next_run(self, lane: str = "pipeline") -> Run | None:
        """Atomically claim the oldest queued run for one scheduler lane."""
        if lane not in RUN_LANES:
            raise ValueError(f"Unknown run lane: {lane}")
        now = time.time()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            busy = conn.execute(
                "SELECT run_id FROM runs WHERE lane=? "
                "AND status IN ('running','cancelling') LIMIT 1",
                (lane,),
            ).fetchone()
            if busy is not None:
                return None
            row = conn.execute(
                "SELECT run_id FROM runs WHERE lane=? AND status='queued' "
                "ORDER BY created_at, run_id LIMIT 1",
                (lane,),
            ).fetchone()
            if row is None:
                return None
            run_id = row["run_id"]
            changed = conn.execute(
                "UPDATE runs SET status='running', started_at=COALESCE(started_at, ?), "
                "updated_at=? WHERE run_id=? AND status='queued'",
                (now, now, run_id),
            ).rowcount
            if not changed:
                return None
            job_id = conn.execute(
                "SELECT job_id FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()["job_id"]
            if lane == "pipeline":
                conn.execute(
                    "UPDATE jobs SET status='running', updated_at=?, error=NULL WHERE job_id=?",
                    (now, job_id),
                )
            self._append_event(conn, run_id, "state", {"status": "running"}, now)
        return self.get_run(run_id)

    def update_run_progress(
        self,
        run_id: str,
        *,
        stage: str,
        progress: float,
        message: str = "",
        stage_label: str = "",
        stage_progress: float | None = None,
    ) -> Run | None:
        now = time.time()
        progress = min(max(float(progress), 0.0), 1.0)
        message = message[:2000]
        payload: dict[str, Any] = {
            "status": "running",
            "stage": stage,
            "progress": progress,
            "message": message,
        }
        if stage_label:
            payload["stage_label"] = stage_label
        if stage_progress is not None:
            payload["stage_progress"] = min(max(float(stage_progress), 0.0), 1.0)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            if row["status"] not in {"running", "cancelling"}:
                return _to_run(row)
            payload["status"] = row["status"]
            conn.execute(
                "UPDATE runs SET stage=?, progress=?, message=?, updated_at=? "
                "WHERE run_id=? AND status IN ('running','cancelling')",
                (stage, progress, message, now, run_id),
            )
            self._append_event(conn, run_id, "progress", payload, now)
        return self.get_run(run_id)

    def request_cancel(self, run_id: str) -> Run | None:
        """Cancel a queued run or request safe cancellation of a running run."""
        now = time.time()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            status = row["status"]
            if status == "queued":
                conn.execute(
                    "UPDATE runs SET status='cancelled', updated_at=?, ended_at=? WHERE run_id=?",
                    (now, now, run_id),
                )
                if row["lane"] == "pipeline":
                    conn.execute(
                        "UPDATE jobs SET status='cancelled', updated_at=?, heartbeat_at=NULL "
                        "WHERE job_id=?",
                        (now, row["job_id"]),
                    )
                self._append_event(conn, run_id, "state", {"status": "cancelled"}, now)
            elif status == "running":
                conn.execute(
                    "UPDATE runs SET status='cancelling', updated_at=? WHERE run_id=?",
                    (now, run_id),
                )
                self._append_event(conn, run_id, "state", {"status": "cancelling"}, now)
        return self.get_run(run_id)

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
        stage: str | None = None,
    ) -> Run | None:
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"Not a terminal run status: {status}")
        now = time.time()
        short_error = error[:2000] if error else None
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            if row["status"] in TERMINAL_RUN_STATUSES:
                return _to_run(row)
            if row["status"] == "cancelling" and status in {"completed", "failed"}:
                status = "cancelled"
                short_error = None
            final_stage = stage or row["stage"]
            progress = 1.0 if status == "completed" else float(row["progress"])
            conn.execute(
                "UPDATE runs SET status=?, stage=?, progress=?, error=?, updated_at=?, "
                "ended_at=? WHERE run_id=?",
                (status, final_stage, progress, short_error, now, now, run_id),
            )
            if row["lane"] == "pipeline":
                legacy_status = {
                    "completed": "done",
                    "cancelled": "cancelled",
                    "failed": "failed",
                    "interrupted": "interrupted",
                }[status]
                conn.execute(
                    "UPDATE jobs SET status=?, stage=COALESCE(?, stage), error=?, "
                    "updated_at=?, heartbeat_at=NULL WHERE job_id=?",
                    (legacy_status, final_stage, short_error, now, row["job_id"]),
                )
            self._append_event(
                conn,
                run_id,
                "state",
                {
                    "status": status,
                    "stage": final_stage,
                    "progress": progress,
                    "error": short_error,
                },
                now,
            )
        return self.get_run(run_id)

    def recover_runs(self) -> list[str]:
        """Mark process-owned runs interrupted while leaving queued work intact."""
        now = time.time()
        recovered: list[str] = []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id, job_id, lane, stage, progress FROM runs "
                "WHERE status IN ('running','cancelling')"
            ).fetchall()
            for row in rows:
                recovered.append(row["run_id"])
                conn.execute(
                    "UPDATE runs SET status='interrupted', updated_at=?, ended_at=?, "
                    "error=COALESCE(error, 'Server stopped before this run finished') "
                    "WHERE run_id=?",
                    (now, now, row["run_id"]),
                )
                if row["lane"] == "pipeline":
                    conn.execute(
                        "UPDATE jobs SET status='interrupted', stage=COALESCE(?, stage), "
                        "updated_at=?, heartbeat_at=NULL WHERE job_id=?",
                        (row["stage"], now, row["job_id"]),
                    )
                    conn.execute(
                        "UPDATE stage_runs SET status='interrupted', ended_at=?, "
                        "error=COALESCE(error, 'Server stopped before this stage finished') "
                        "WHERE rowid=(SELECT rowid FROM stage_runs WHERE job_id=? "
                        "AND status='running' ORDER BY started_at DESC LIMIT 1)",
                        (now, row["job_id"]),
                    )
                self._append_event(
                    conn,
                    row["run_id"],
                    "state",
                    {
                        "status": "interrupted",
                        "stage": row["stage"],
                        "progress": row["progress"],
                    },
                    now,
                )
        return recovered

    def events_after(self, run_id: str, seq: int) -> list[RunEvent]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, seq),
            ).fetchall()
        return [_to_event(row) for row in rows]

    def _append_event(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: float,
    ) -> int:
        conn.execute("UPDATE runs SET event_seq=event_seq+1 WHERE run_id=?", (run_id,))
        seq_row = conn.execute("SELECT event_seq FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if seq_row is None:
            raise ValueError(f"Unknown run: {run_id}")
        seq = int(seq_row["event_seq"])
        conn.execute(
            "INSERT INTO run_events (run_id, seq, event_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event_type, _dump_json(payload), created_at),
        )
        return seq


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _to_job(row: sqlite3.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        source_uri=row["source_uri"],
        work_dir=row["work_dir"],
        targets=[target for target in (row["targets"] or "").split(",") if target],
        stage=row["stage"],
        status=row["status"],
        error=row["error"],
        attempts=row["attempts"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        heartbeat_at=row["heartbeat_at"],
        request=_load_json(row["request_json"]),
        approved_at=row["approved_at"],
        approved_signature=row["approved_signature"],
    )


def _to_run(row: sqlite3.Row) -> Run:
    return Run(
        run_id=row["run_id"],
        job_id=row["job_id"],
        lane=row["lane"] or "pipeline",
        kind=row["kind"],
        from_stage=row["from_stage"],
        payload=_load_json(row["payload_json"]),
        force=bool(row["force"]),
        status=row["status"],
        stage=row["stage"],
        progress=float(row["progress"]),
        message=row["message"] or "",
        error=row["error"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        event_seq=int(row["event_seq"]),
    )


def _to_voice_calibration(row: sqlite3.Row) -> VoiceCalibration:
    return VoiceCalibration(
        provider=row["provider"],
        voice_id=row["voice_id"],
        overhead_sec=float(row["overhead_sec"]),
        sec_per_syllable=float(row["sec_per_syllable"]),
        sample_count=int(row["sample_count"]),
        source_job_id=row["source_job_id"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _to_tts_approval(row: sqlite3.Row) -> TtsApproval:
    return TtsApproval(
        job_id=row["job_id"],
        lang=row["lang"],
        approved_at=float(row["approved_at"]) if row["approved_at"] is not None else None,
        approved_signature=row["approved_signature"],
    )


def _to_event(row: sqlite3.Row) -> RunEvent:
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        payload = {}
    return RunEvent(
        run_id=row["run_id"],
        seq=int(row["seq"]),
        event_type=row["event_type"],
        payload=payload if isinstance(payload, dict) else {},
        created_at=float(row["created_at"]),
    )
