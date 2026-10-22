"""Durable, single-host indexing queue. No model calls run in API requests.

SQLite transactions claim work; an OS file lock serializes all index writers and
is released on process death. A replacement worker can then recover running jobs.
Delivery is at-least-once, not exactly-once billing across provider/DB crashes.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.config import Settings
from app.services.parsing import PARSER_VERSION


class IndexBusyError(RuntimeError):
    pass


def queue_path(config: Settings) -> Path:
    return config.rag_db_path.with_name("index_jobs.sqlite3")


def queue_scope(config: Settings) -> str:
    values = (PARSER_VERSION, str(config.knowledge_base_dir.resolve()),
              str(config.rag_db_path.resolve()),
              config.rag_embedding_model, config.rag_chunk_chars, config.rag_chunk_overlap,
              config.rag_index_max_embedding_tokens)
    return hashlib.sha256(repr(values).encode()).hexdigest()


@contextmanager
def index_writer(config: Settings):
    # Local Linux/macOS deployment only. All writers must share this volume/path.
    import fcntl

    path = config.rag_db_path.with_suffix(".write.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IndexBusyError("An index writer is already active") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("Index jobs schema is newer than this application")
            if version == 2:
                return
            for statement in """
                CREATE TABLE IF NOT EXISTS index_jobs (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    available_at REAL NOT NULL, error_code TEXT, report TEXT,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS index_requests (
                    scope TEXT NOT NULL, request_key TEXT NOT NULL, job_id TEXT NOT NULL,
                    PRIMARY KEY (scope, request_key)
                );
            """.split(";"):
                if statement.strip():
                    db.execute(statement)
            # A scan already in progress cannot cover a newly uploaded file.
            # Allow a durable follow-up, while still forbidding concurrent workers.
            db.execute("DROP INDEX IF EXISTS one_active_index_job")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_running_index_job "
                       "ON index_jobs(scope) WHERE status='running'")
            db.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row) -> Optional[dict]:
        if row is None:
            return None
        job = dict(row)
        job["report"] = json.loads(job["report"]) if job["report"] else None
        job["engine"] = "hybrid"
        return job

    def enqueue(self, scope: str, request_key: Optional[str] = None,
                *, after_running: bool = False) -> dict:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = None
            if request_key:
                row = db.execute("""SELECT j.* FROM index_jobs j JOIN index_requests r
                    ON j.id=r.job_id WHERE r.scope=? AND r.request_key=?""",
                                 (scope, request_key)).fetchone()
                if row is not None and after_running and row["status"] == "succeeded":
                    # Content-addressed upload keys are not permanent proof that
                    # an index still exists (the file may have been deleted/readded).
                    row = None
            if row is None:
                statuses = "('queued')" if after_running else "('queued','running')"
                row = db.execute("SELECT * FROM index_jobs WHERE scope=? AND status IN "
                                 + statuses + " ORDER BY created_at DESC LIMIT 1",
                                 (scope,)).fetchone()
            if row is None:
                job_id = str(uuid4())
                db.execute("""INSERT INTO index_jobs
                    (id, scope, status, phase, created_at, updated_at, available_at)
                    VALUES (?, ?, 'queued', 'waiting', ?, ?, ?)""",
                           (job_id, scope, now, now, now))
                row = db.execute("SELECT * FROM index_jobs WHERE id=?", (job_id,)).fetchone()
            if request_key:
                db.execute("INSERT INTO index_requests VALUES (?, ?, ?) "
                           "ON CONFLICT(scope, request_key) DO UPDATE SET job_id=excluded.job_id",
                           (scope, request_key, row["id"]))
        return self.decode(row)

    def get(self, job_id: str, scope: str) -> Optional[dict]:
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM index_jobs WHERE id=? AND scope=?",
                                          (job_id, scope)).fetchone())

    def recent(self, scope: str) -> list:
        with self.connect() as db:
            return [self.decode(row) for row in db.execute(
                "SELECT * FROM index_jobs WHERE scope=? ORDER BY created_at DESC LIMIT 20",
                (scope,))]

    def claim(self, scope: str) -> Optional[dict]:
        """Caller MUST hold index_writer, including during recovery and execution."""
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Obtaining the OS lock proves no previous worker still owns this index.
            db.execute("""UPDATE index_jobs SET
                status='failed',
                phase='interrupted', error_code='worker_interrupted', updated_at=?
                WHERE scope=? AND status='running' AND attempts>=max_attempts""", (now, scope))
            row = db.execute("SELECT * FROM index_jobs WHERE scope=? AND status='running'",
                             (scope,)).fetchone()
            if row is None:
                row = db.execute("""SELECT * FROM index_jobs WHERE scope=? AND status='queued'
                AND available_at<=? ORDER BY created_at LIMIT 1""", (scope, now)).fetchone()
            if row is None:
                return None
            db.execute("""UPDATE index_jobs SET status='running', phase='scanning',
                attempts=attempts+1, processed=0, total=0, error_code=NULL, report=NULL,
                updated_at=? WHERE id=?""",
                       (now, row["id"]))
            return self.decode(db.execute("SELECT * FROM index_jobs WHERE id=?",
                                          (row["id"],)).fetchone())

    def progress(self, job_id: str, processed: int, total: int, phase: str):
        with self.connect() as db:
            db.execute("""UPDATE index_jobs SET processed=?, total=?, phase=?, updated_at=?
                WHERE id=? AND status='running'""",
                       (processed, total, phase, time.time(), job_id))

    def finish(self, job: dict, report=None, error_code=None, retry_after=None):
        now = time.time()
        retry = retry_after is not None and job["attempts"] < job["max_attempts"]
        status = "queued" if retry else ("failed" if error_code else "succeeded")
        payload = report.model_dump_json() if report else None
        cost = report.estimated_cost_usd if report else 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE index_jobs SET status=?, phase=?, error_code=?, report=?,
                estimated_cost_usd=estimated_cost_usd+?, updated_at=?, available_at=?
                WHERE id=? AND status='running' AND attempts=?""",
                       (status, "retry_wait" if retry else status, error_code, payload, cost,
                        now, now + (max(1, retry_after) if retry else 0),
                        job["id"], job["attempts"]))
            if retry:
                # A follow-up must not bypass provider Retry-After for this scope.
                db.execute("UPDATE index_jobs SET available_at=MAX(available_at, ?) "
                           "WHERE scope=? AND status='queued'",
                           (now + max(1, retry_after), job["scope"]))
            if error_code in {"provider_quota_exhausted", "provider_authentication",
                              "configuration_missing"}:
                db.execute("UPDATE index_jobs SET status='failed', phase='blocked', "
                           "error_code=?, updated_at=? WHERE scope=? AND status='queued'",
                           (error_code, now, job["scope"]))

    def retry(self, job_id: str, scope: str) -> Optional[dict]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM index_jobs WHERE id=? AND scope=?",
                             (job_id, scope)).fetchone()
            if row is None:
                return None
            if row["status"] != "failed" or row["attempts"] >= row["max_attempts"]:
                raise ValueError("Only failed jobs below the attempt limit can be retried")
            if db.execute("SELECT 1 FROM index_jobs WHERE scope=? "
                          "AND status IN ('queued','running')", (scope,)).fetchone():
                raise IndexBusyError("Another job is already queued")
            try:
                db.execute("""UPDATE index_jobs SET status='queued', phase='waiting',
                    available_at=?, updated_at=? WHERE id=?""", (time.time(), time.time(), job_id))
            except sqlite3.IntegrityError as exc:
                raise IndexBusyError("Another job is already queued") from exc
        return self.get(job_id, scope)
