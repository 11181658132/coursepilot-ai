"""Public demo namespace isolation and transactional PostgreSQL adapter.

Only a trusted signed gateway supplies the tenant/quota keys, never client JSON.
The canonical StudyStore state machine is shared with the local SQLite app.
"""

import math
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from app.services.study_store import StudyBudgetExceeded, StudyStore


class PublicStudyStore(StudyStore):
    def __init__(self, dsn, tenant, quota_key, *, initialize=False):
        if not re.fullmatch(r"[a-f0-9]{64}", tenant + ""):
            raise ValueError("Invalid server tenant")
        if not re.fullmatch(r"[a-f0-9]{64}", quota_key + ""):
            raise ValueError("Invalid server quota key")
        self.dsn, self.prefix, self.quota_key = dsn, "v1" + tenant + "_", quota_key
        if initialize:
            self.initialize()
            with self.connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS public_demo_limits "
                           "(key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, "
                           "PRIMARY KEY (key, day))")

    @contextmanager
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        # URL is server-only. Never log connection exceptions/DSNs or enable driver DEBUG logs.
        with psycopg.connect(self.dsn, sslmode="require", connect_timeout=5,
                             row_factory=dict_row, prepare_threshold=None) as connection:
            connection.execute("SET LOCAL statement_timeout = '5000ms'")
            yield PgConnection(connection)

    def request_key(self, request_id):
        return str(uuid.uuid5(uuid.NAMESPACE_OID, self.prefix + str(request_id)))

    def new_id(self):
        return self.prefix + uuid.uuid4().hex

    def check_id(self, identifier):
        if (not isinstance(identifier, str) or not identifier.startswith(self.prefix)
                or not re.fullmatch(r"[a-f0-9]{32}", identifier[len(self.prefix):])):
            raise KeyError("Plan not found")

    def scope(self):
        return " AND id LIKE ?", (self.prefix + "%",)

    def allow_start(self, db):
        day = datetime.now(timezone.utc).date().isoformat()
        for key, limit in ((self.quota_key, 2), ("global", 20)):
            row = db.execute("SELECT count FROM public_demo_limits WHERE key=? AND day=?",
                             (key, day)).fetchone()
            if row and row["count"] >= limit:
                raise StudyBudgetExceeded("今日 Agent 体验次数已用完。")
            db.execute("INSERT INTO public_demo_limits (key, day, count) VALUES (?, ?, 1) "
                       "ON CONFLICT (key, day) DO UPDATE SET count=public_demo_limits.count+1",
                       (key, day))

    def allow_charge(self, db, amount, day):
        if not math.isfinite(amount) or amount <= 0:
            raise StudyBudgetExceeded("Invalid reservation")
        month = day[:8] + "01"
        used = db.execute("SELECT COALESCE(SUM(amount),0) FROM study_charges WHERE day>=?",
                          (month,)).fetchone()[0]
        if used + amount > 0.50:
            raise StudyBudgetExceeded("本月 Agent 估算费用预留已达上限。")


class PgRow(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class PgCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return None if row is None else PgRow(row)

    def fetchall(self):
        return [PgRow(row) for row in self.cursor.fetchall()]


class PgConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        if sql == "BEGIN IMMEDIATE":
            # Serialize short mutation/budget transactions across all serverless instances.
            # The lock is released at commit/rollback, never held during an LLM request.
            sql = "SELECT pg_advisory_xact_lock(7350101)"
        else:
            sql = (sql.replace("study_runs", "cp_demo_study_runs")
                   .replace("study_charges", "cp_demo_study_charges")
                   .replace("public_demo_limits", "cp_demo_limits")
                   .replace("amount REAL", "amount DOUBLE PRECISION").replace("?", "%s"))
        return PgCursor(self.connection.execute(sql, params))

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)
