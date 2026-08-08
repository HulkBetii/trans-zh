"""Job state in SQLite, so ``batch`` survives a crash or a power cut.

Recovery works on a heartbeat rather than a shutdown hook: a killed process never
gets to run cleanup, so any job still marked ``running`` with a stale heartbeat is
reclaimed at the next batch start. That is the difference between resuming after a
power cut and hanging on a job that no longer exists.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

STAGES = ("ingest", "asr", "segment", "glossary", "translate", "render")
STALE_HEARTBEAT_SEC = 300.0

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
    heartbeat_at REAL
);
CREATE TABLE IF NOT EXISTS stage_runs (
    job_id     TEXT NOT NULL,
    stage      TEXT NOT NULL,
    status     TEXT NOT NULL,
    started_at REAL,
    ended_at   REAL,
    error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_stage_runs_job ON stage_runs(job_id);
"""


@dataclass(slots=True)
class Job:
    job_id: str
    source_uri: str
    work_dir: str
    targets: list[str]
    stage: str | None
    status: str  # pending | running | done | failed
    error: str | None
    attempts: int


class JobStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            # WAL lets concurrent batch workers read while one writes.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, job_id: str, source_uri: str, work_dir: str, targets: list[str]) -> Job:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO jobs (job_id, source_uri, work_dir, targets, stage, status,
                                     created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, 'pending', ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       source_uri = excluded.source_uri,
                       work_dir   = excluded.work_dir,
                       targets    = excluded.targets,
                       updated_at = excluded.updated_at""",
                (job_id, source_uri, work_dir, ",".join(targets), now, now),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _to_job(row) if row else None

    def list(self, status: str | None = None) -> list[Job]:
        query = "SELECT * FROM jobs"
        args: tuple = ()
        if status:
            query += " WHERE status = ?"
            args = (status,)
        query += " ORDER BY created_at"
        with self._conn() as conn:
            return [_to_job(r) for r in conn.execute(query, args).fetchall()]

    def mark_running(self, job_id: str, stage: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='running', stage=?, updated_at=?, heartbeat_at=?, error=NULL"
                " WHERE job_id=?",
                (stage, now, now, job_id),
            )
            conn.execute(
                "INSERT INTO stage_runs (job_id, stage, status, started_at) VALUES (?, ?, 'running', ?)",
                (job_id, stage, now),
            )

    def heartbeat(self, job_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET heartbeat_at=? WHERE job_id=?", (time.time(), job_id))

    def mark_stage_done(self, job_id: str, stage: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE stage_runs SET status='done', ended_at=? WHERE job_id=? AND stage=? AND status='running'",
                (now, job_id, stage),
            )
            conn.execute("UPDATE jobs SET stage=?, updated_at=?, heartbeat_at=? WHERE job_id=?",
                         (stage, now, now, job_id))

    def mark_done(self, job_id: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='done', updated_at=?, heartbeat_at=NULL, error=NULL WHERE job_id=?",
                (now, job_id),
            )

    def mark_failed(self, job_id: str, stage: str, error: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed', stage=?, error=?, updated_at=?,"
                " heartbeat_at=NULL, attempts=attempts+1 WHERE job_id=?",
                (stage, error[:2000], now, job_id),
            )
            conn.execute(
                "UPDATE stage_runs SET status='failed', ended_at=?, error=? "
                "WHERE job_id=? AND stage=? AND status='running'",
                (now, error[:2000], job_id, stage),
            )

    def reclaim_stale(self, stale_after_sec: float = STALE_HEARTBEAT_SEC) -> list[str]:
        """Return ``running`` jobs whose heartbeat died, and reset them to ``pending``.

        A killed process cannot clean up after itself, so without this a crashed job
        stays ``running`` forever and ``batch`` skips it on every subsequent run.
        """
        cutoff = time.time() - stale_after_sec
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE status='running'"
                " AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
                (cutoff,),
            ).fetchall()
            ids = [r["job_id"] for r in rows]
            if ids:
                conn.executemany(
                    "UPDATE jobs SET status='pending', heartbeat_at=NULL WHERE job_id=?",
                    [(i,) for i in ids],
                )
        return ids


def _to_job(row: sqlite3.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        source_uri=row["source_uri"],
        work_dir=row["work_dir"],
        targets=[t for t in (row["targets"] or "").split(",") if t],
        stage=row["stage"],
        status=row["status"],
        error=row["error"],
        attempts=row["attempts"],
    )
