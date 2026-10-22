"""Local-first Agent state, confirmation boundary and atomic cost reservations."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class StudyConflict(ValueError):
    pass


class StudyBudgetExceeded(ValueError):
    pass


class StudyStore:
    # Longer than the configured 120s execution deadline + 20s provider timeout.
    # Expiry fences stale workers; recovery never replays a model/tool request.
    RUN_LEASE_SECONDS = 180

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS study_runs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, course TEXT NOT NULL,
                    status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS study_charges (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, day TEXT NOT NULL,
                    amount REAL NOT NULL, settled INTEGER NOT NULL DEFAULT 0
                );
            """)

    def request_key(self, request_id):
        return str(request_id)

    def new_id(self):
        return uuid.uuid4().hex

    def check_id(self, identifier):
        pass

    def scope(self):
        return "", ()

    def allow_start(self, db):
        pass

    def allow_charge(self, db, amount, day):
        pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, request):
        body = request.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM study_runs WHERE request_id = ?",
                             (self.request_key(request.request_id),)).fetchone()
            if row:
                if row["status"] == "deleted":
                    raise StudyConflict("该请求的内容已删除，旧请求不会重新执行。")
                if row["fingerprint"] != fingerprint:
                    raise StudyConflict("请求标识已用于其他参数，请创建新请求。")
                if row["status"] == "running":
                    raise StudyConflict("此请求正在运行；请稍后刷新，不要重复生成。")
                return self.decode(row), False
            self.allow_start(db)
            run_id = self.new_id()
            now = datetime.now(timezone.utc).isoformat()
            payload = {"request": body, "trace": [], "tasks": [], **self.lease()}
            db.execute("INSERT INTO study_runs VALUES (?, ?, ?, ?, 'running', 0, ?, ?)",
                       (run_id, self.request_key(request.request_id), fingerprint, request.course,
                        now, json.dumps(payload)))
            return self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                          (run_id,)).fetchone()), True

    def decode(self, row):
        if row is None:
            raise KeyError("Plan not found")
        return {"id": row["id"], "course": row["course"], "status": row["status"],
                "version": row["version"], "created_at": row["created_at"],
                **json.loads(row["payload"])}

    def get(self, run_id):
        self.check_id(run_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM study_runs WHERE id=?", (run_id,)).fetchone()
            result = self.decode(row)
            if result["status"] == "deleted":
                raise KeyError("Plan not found")
            charges = db.execute("SELECT COALESCE(SUM(amount),0) AS cost, "
                                 "COALESCE(SUM(1-settled),0) AS uncertain, COUNT(*) AS calls "
                                 "FROM study_charges WHERE run_id=?",
                                 (run_id,)).fetchone()
        if result.get("kind") != "feedback":
            result["feedback"] = [self.get(key) for key in result.get("feedback_ids", [])]
        result["recovery_available"] = self.expired(result) or any(
            item["recovery_available"] for item in result.get("feedback", []))
        return {**result, "accounted_cost_usd": round(charges[0], 8),
                "uncertain_calls": charges[1], "api_calls": charges[2]}

    def list(self, course):
        clause, params = self.scope()
        with self.connect() as db:
            rows = db.execute("SELECT id FROM study_runs WHERE course=? AND status<>'deleted' "
                              + clause +
                              " ORDER BY created_at DESC LIMIT 20", (course, *params)).fetchall()
        return [self.get(row["id"]) for row in rows]

    def finish(self, run_id, status, payload):
        self.check_id(run_id)
        assert status in {"draft", "failed"}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self.running(db, run_id)
            payload = {**self.payload(payload), "lease_expires_at": state.get("lease_expires_at"),
                       "checkpoint_at": state.get("checkpoint_at"),
                       "finished_at": datetime.now(timezone.utc).isoformat()}
            if status == "failed":
                for event in payload.get("trace", []):
                    if event["status"] == "running":
                        event["status"] = "failed"
            result = db.execute("UPDATE study_runs SET status=?, payload=?, version=version+1 "
                                "WHERE id=? AND status='running'",
                                (status, json.dumps(payload), run_id))
            if result.rowcount != 1:
                raise StudyConflict("运行状态已改变。")
        return self.get(run_id)

    @classmethod
    def lease(cls):
        return {"lease_expires_at": time.time() + cls.RUN_LEASE_SECONDS,
                "checkpoint_at": datetime.now(timezone.utc).isoformat()}

    @classmethod
    def expired(cls, state):
        if state["status"] != "running":
            return False
        expires = state.get("lease_expires_at")
        if expires is None:  # Existing pre-lease runs are recoverable without a migration.
            expires = (datetime.fromisoformat(state["created_at"]).timestamp()
                       + cls.RUN_LEASE_SECONDS)
        return time.time() >= expires

    def running(self, db, run_id):
        state = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                       (run_id,)).fetchone())
        if state["status"] != "running" or self.expired(state):
            raise StudyConflict("运行已结束或检查点已过期，请核对已保存状态。")
        return state

    def checkpoint(self, run_id, payload):
        self.check_id(run_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.running(db, run_id)
            db.execute("UPDATE study_runs SET payload=? WHERE id=? AND status='running'",
                       (json.dumps({**self.payload(payload), **self.lease()}), run_id))

    def recover(self, run_id, version):
        """Mark expired plan/feedback operations interrupted, never resume execution."""
        self.check_id(run_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                            (run_id,)).fetchone())
            if parent.get("kind") == "feedback":
                raise KeyError("Plan not found")
            states = [parent]
            for identifier in parent.get("feedback_ids", []):
                self.check_id(identifier)
                child = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                               (identifier,)).fetchone())
                if child.get("kind") != "feedback" or child.get("parent_id") != run_id:
                    raise KeyError("Feedback not found")
                states.append(child)
            expired = [state for state in states if self.expired(state)]
            if expired and parent["version"] != version:
                raise StudyConflict("计划已变化，请刷新后核对中断。")
            for state in expired:
                payload = self.payload(state)
                payload.update(error_code="worker_interrupted",
                               finished_at=datetime.now(timezone.utc).isoformat())
                for event in payload.get("trace", []):
                    if event["status"] == "running":
                        event["status"] = "interrupted"
                db.execute("UPDATE study_runs SET status='interrupted', payload=?, "
                           "version=version+1 WHERE id=? AND status='running'",
                           (json.dumps(payload), state["id"]))
            if expired and all(state["id"] != run_id for state in expired):
                db.execute("UPDATE study_runs SET version=version+1 WHERE id=?", (run_id,))
        return self.get(run_id)

    def delete(self, run_id, version):
        """Erase plan/answer payloads, retaining only dedupe/cost/quota tombstones."""
        self.check_id(run_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                            (run_id,)).fetchone())
            if parent["status"] == "deleted":
                return {"deleted": True}
            if parent.get("kind") == "feedback":
                raise KeyError("Plan not found")
            if parent["version"] != version:
                raise StudyConflict("计划已变化，请刷新后重新确认删除。")
            states = [parent]
            for identifier in parent.get("feedback_ids", []):
                self.check_id(identifier)
                child = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                               (identifier,)).fetchone())
                if child.get("parent_id") != run_id or child.get("kind") != "feedback":
                    raise KeyError("Feedback not found")
                states.append(child)
            if any(state["status"] == "running" and not self.expired(state) for state in states):
                raise StudyConflict("运行尚未结束；请等待完成或在检查点过期后删除。")
            tombstone = json.dumps({"kind": "deleted", "deleted_at":
                                   datetime.now(timezone.utc).isoformat()})
            for state in states:
                db.execute("UPDATE study_runs SET status='deleted', course='_deleted', "
                           "fingerprint='deleted', payload=?, version=version+1 WHERE id=?",
                           (tombstone, state["id"]))
        return {"deleted": True}

    def confirm(self, run_id, version):
        self.check_id(run_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM study_runs WHERE id=?", (run_id,)).fetchone()
            state = self.decode(row)
            if state.get("kind") == "feedback":
                raise StudyConflict("反馈草案必须通过所属计划单独确认。")
            if state["status"] == "active":
                return self.get(run_id)  # Confirmation is idempotent; never recreates tasks.
            if state["status"] != "draft" or state["version"] != version:
                raise StudyConflict("计划不可确认或版本已变化，请刷新。")
            db.execute("UPDATE study_runs SET status='active', version=version+1 WHERE id=?",
                       (run_id,))
        return self.get(run_id)

    @staticmethod
    def payload(state):
        return {key: value for key, value in state.items() if key not in {
            "id", "course", "status", "version", "created_at", "feedback",
            "accounted_cost_usd", "uncertain_calls", "api_calls", "recovery_available"}}

    def begin_feedback(self, plan_id, task_id, request):
        self.check_id(plan_id)
        body = request.model_dump(mode="json")
        identity = {"plan_id": plan_id, "task_id": task_id, **body}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        request_id = uuid.uuid5(uuid.NAMESPACE_URL,
                               "study-feedback:" + plan_id + ":" + str(request.request_id))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                            (plan_id,)).fetchone())
            if parent.get("kind") == "feedback" or parent["status"] != "active":
                raise StudyConflict("请先确认学习计划。")
            previous = db.execute("SELECT * FROM study_runs WHERE request_id=?",
                                  (self.request_key(request_id),)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint or previous["status"] == "running":
                    raise StudyConflict("请求参数变化或反馈仍在生成，请刷新。")
                return self.decode(previous), False
            if parent["version"] != request.version:
                raise StudyConflict("计划已变化，请刷新后再提交答案。")
            task = next((t for t in parent["tasks"] if t["id"] == task_id), None)
            if task is None:
                raise KeyError("Task not found")
            ids = parent.get("feedback_ids", [])
            if len(ids) >= 10:
                raise StudyConflict("每份计划最多保存 10 次反馈。")
            self.allow_start(db)  # Shares the existing durable request quota, never expands it.
            feedback_id = self.new_id()
            payload = {"kind": "feedback", "parent_id": plan_id, "task_id": task_id,
                       "base_version": parent["version"] + 1, "request": body,
                       "source_task": task, "remaining_tasks": [
                           t for t in parent["tasks"] if not t["done"] and t["day"] > task["day"]],
                       "trace": [], "tasks": [], "adjustments": [], **self.lease()}
            now = datetime.now(timezone.utc).isoformat()
            # Separate metered operation in the same schema, excluded from course plan lists.
            db.execute("INSERT INTO study_runs VALUES (?, ?, ?, ?, 'running', 0, ?, ?)",
                       (feedback_id, self.request_key(request_id), fingerprint,
                        "_feedback:" + parent["course"], now, json.dumps(payload)))
            parent_payload = self.payload(parent)
            parent_payload["feedback_ids"] = [*ids, feedback_id]
            db.execute("UPDATE study_runs SET payload=?, version=version+1 WHERE id=?",
                       (json.dumps(parent_payload), plan_id))
            state = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                           (feedback_id,)).fetchone())
        return state, True

    def confirm_feedback(self, plan_id, feedback_id, version):
        self.check_id(plan_id)
        self.check_id(feedback_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                            (plan_id,)).fetchone())
            feedback = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                              (feedback_id,)).fetchone())
            if feedback.get("kind") != "feedback" or feedback.get("parent_id") != plan_id:
                raise KeyError("Feedback not found")
            if feedback["status"] == "active":
                return self.get(plan_id)  # Idempotent replay cannot reapply stale edits.
            if (parent["status"] != "active" or parent["version"] != version
                    or feedback["status"] != "draft"
                    or feedback["base_version"] != version
                    or feedback.get("assessment", {}).get("verdict") != "needs_review"
                    or not feedback.get("adjustments")):
                raise StudyConflict("调整草案不可应用或计划已变化，请重新获取反馈。")
            source = next(t for t in parent["tasks"] if t["id"] == feedback["task_id"])
            eligible = {t["id"]: t for t in parent["tasks"]
                        if not t["done"] and t["day"] > source["day"]}
            seen = set()
            for adjustment in feedback["adjustments"]:
                task = eligible.get(adjustment["task_id"])
                if task is None or task["id"] in seen:
                    raise StudyConflict("只允许调整未完成的后续任务。")
                seen.add(task["id"])
                # Whitelist descriptive fields only; IDs/day/minutes/progress are immutable.
                for key in ("title", "study_goal", "quiz_question", "quiz_answer", "citations"):
                    task[key] = adjustment[key]
            db.execute("UPDATE study_runs SET payload=?, version=version+1 WHERE id=?",
                       (json.dumps(self.payload(parent)), plan_id))
            audit = self.payload(feedback)
            audit.update(confirmed_at=datetime.now(timezone.utc).isoformat(),
                         applied_plan_version=version + 1)
            db.execute("UPDATE study_runs SET status='active', payload=?, version=version+1 "
                       "WHERE id=?", (json.dumps(audit), feedback_id))
        return self.get(plan_id)

    def set_task(self, run_id, task_id, done, version):
        self.check_id(run_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self.decode(db.execute("SELECT * FROM study_runs WHERE id=?",
                                           (run_id,)).fetchone())
            if state["status"] != "active" or state["version"] != version:
                raise StudyConflict("先确认计划；若版本已变化，请刷新后再操作。")
            tasks = state["tasks"]
            task = next((task for task in tasks if task["id"] == task_id), None)
            if task is None:
                raise KeyError("Task not found")
            task["done"] = done
            payload = {key: value for key, value in state.items()
                       if key not in {"id", "course", "status", "version", "created_at"}}
            db.execute("UPDATE study_runs SET payload=?, version=version+1 WHERE id=?",
                       (json.dumps(payload), run_id))
        return self.get(run_id)

    def reserve(self, run_id, amount, run_cap, daily_cap):
        self.check_id(run_id)
        day = datetime.now(timezone.utc).date().isoformat()
        charge_id = self.new_id()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self.running(db, run_id)
            used = db.execute("SELECT COALESCE(SUM(amount),0) FROM study_charges WHERE run_id=?",
                              (run_id,)).fetchone()[0]
            daily = db.execute("SELECT COALESCE(SUM(amount),0) FROM study_charges WHERE day=?",
                               (day,)).fetchone()[0]
            if used + amount > run_cap or daily + amount > daily_cap:
                raise StudyBudgetExceeded("估算费用预留已达上限；请求未发送。")
            self.allow_charge(db, amount, day)
            db.execute("INSERT INTO study_charges VALUES (?, ?, ?, ?, 0)",
                       (charge_id, run_id, day, amount))
            db.execute("UPDATE study_runs SET payload=? WHERE id=? AND status='running'",
                       (json.dumps({**self.payload(state), **self.lease()}), run_id))
        return charge_id

    def settle(self, charge_id, actual):
        self.check_id(charge_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT amount, settled FROM study_charges WHERE id=?",
                             (charge_id,)).fetchone()
            if not row or row["settled"] or not 0 <= actual <= row["amount"]:
                raise StudyBudgetExceeded("费用结算异常，保留预留额度并停止。")
            db.execute("UPDATE study_charges SET amount=?, settled=1 WHERE id=?",
                       (actual, charge_id))
