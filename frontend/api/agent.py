"""Signed public Agent gateway. All durable state and limits are PostgreSQL-backed."""

import hashlib
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_rag_backend"))
sys.path.insert(0, str(ROOT / "api"))

from app.services.public_study_store import PublicStudyStore  # noqa: E402
from app.services.study_agent import (  # noqa: E402
    Confirmation,
    PlanRequest,
    StudyAgent,
    TaskUpdate,
)
from app.services.study_feedback import FeedbackAgent, FeedbackRequest  # noqa: E402
from app.services.study_store import StudyBudgetExceeded, StudyConflict  # noqa: E402
from rag import demo_base  # noqa: E402


def valid_signature(body, timestamp, signature):
    secret = os.environ.get("DEMO_RATE_LIMIT_SALT", "")
    if len(secret) < 32:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 60:
            return False
    except (ValueError, TypeError):
        return False
    expected = hmac.new(secret.encode(), b"agent-v1." + timestamp.encode() + b"." + body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def configuration():
    if os.environ.get("DEMO_AGENT_ENABLED") != "true":
        raise RuntimeError("Agent disabled")
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("Persistent database missing")
    return dsn


def handle(body, store_factory=PublicStudyStore, base_factory=demo_base):
    dsn = configuration()  # Fail closed; no SQLite or in-memory persistence fallback.
    if not isinstance(body, dict) or set(body) != {"tenant", "quota_key", "action", "data"}:
        raise ValueError("Invalid envelope")
    if not all(isinstance(body[key], str) and re.fullmatch(r"[a-f0-9]{64}", body[key])
               for key in ("tenant", "quota_key")):
        raise ValueError("Invalid scope")
    data, action = body["data"], body["action"]
    if not isinstance(data, dict):
        raise ValueError("Invalid action data")
    if action == "draft":
        request = PlanRequest.model_validate_json(json.dumps(data))
        if request.course != "DEMO101" or request.days > 3 or request.daily_minutes > 90:
            raise ValueError("Public course/day/time limit")
        if len(request.goal) > 400:
            raise ValueError("Public goal size limit")
    elif action == "list":
        if data != {"course": "DEMO101"}:
            raise ValueError("Public course only")
    elif action not in {"get", "confirm", "task", "feedback", "confirm_feedback"}:
        raise ValueError("Action not allowed")
    store = store_factory(dsn, body["tenant"], body["quota_key"])
    if action == "list":
        return store.list("DEMO101")
    if action == "get":
        if set(data) != {"plan_id"}:
            raise ValueError("Invalid get")
        return store.get(data["plan_id"])
    if action == "confirm":
        if set(data) != {"plan_id", "confirmed", "version"}:
            raise ValueError("Invalid confirmation")
        confirmation = Confirmation.model_validate({k: v for k, v in data.items()
                                                   if k != "plan_id"})
        return store.confirm(data["plan_id"], confirmation.version)
    if action == "task":
        if set(data) != {"plan_id", "task_id", "done", "version"}:
            raise ValueError("Invalid update")
        update = TaskUpdate.model_validate({k: v for k, v in data.items()
                                           if k not in {"plan_id", "task_id"}})
        return store.set_task(data["plan_id"], data["task_id"], update.done, update.version)
    if action == "confirm_feedback":
        if set(data) != {"plan_id", "feedback_id", "confirmed", "version"}:
            raise ValueError("Invalid feedback confirmation")
        confirmation = Confirmation.model_validate({k: v for k, v in data.items()
                                                   if k not in {"plan_id", "feedback_id"}})
        return store.confirm_feedback(data["plan_id"], data["feedback_id"], confirmation.version)
    if action == "feedback":
        if set(data) != {"plan_id", "task_id", "request_id", "answer", "version"}:
            raise ValueError("Invalid feedback request")
        request = FeedbackRequest.model_validate_json(json.dumps({
            k: v for k, v in data.items() if k not in {"plan_id", "task_id"}}))
        if len(request.answer) > 400:
            raise ValueError("Public answer size limit")
        parent = store.get(data["plan_id"])
        if parent["course"] != "DEMO101" or parent.get("kind") == "feedback":
            raise KeyError("Plan not found")
    base = base_factory()
    config = base.config.model_copy(update={
        "agent_run_budget_usd": 0.05, "agent_daily_budget_usd": 0.10,
        "agent_max_turns": 4, "agent_deadline_seconds": 38,
        "agent_request_timeout_seconds": 8,
    })
    # New per-run RAG instance; never share mutable usage counters across visitors.
    if action == "feedback":
        return FeedbackAgent(config, store).run(data["plan_id"], data["task_id"], request)
    return StudyAgent(config, store).run(request)


class handler(BaseHTTPRequestHandler):
    def reply(self, status, data):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8000:
                return self.reply(400, {"detail": "请求大小无效。"})
            raw = self.rfile.read(length)
            if not valid_signature(raw, self.headers.get("X-Agent-Timestamp", ""),
                                   self.headers.get("X-Agent-Signature", "")):
                return self.reply(403, {"detail": "Forbidden"})
            return self.reply(200, handle(json.loads(raw)))
        except KeyError:
            return self.reply(404, {"detail": "计划或任务不存在。"})
        except StudyConflict:
            return self.reply(409, {"detail": "计划状态已变化，请刷新后重试。"})
        except StudyBudgetExceeded:
            return self.reply(429, {"detail": "Agent 次数或费用额度已用完。"})
        except (ValueError, TypeError):
            return self.reply(400, {"detail": "公开 Agent 请求无效。"})
        except Exception:
            # No provider/DB exception bodies: they may contain credentials or visitor content.
            return self.reply(503, {"detail": "Agent 暂不可用；不会自动重复生成，请稍后刷新。"})

    def log_message(self, format, *args):
        pass
