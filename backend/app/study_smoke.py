"""Explicit paid acceptance against ONLY the five fictional DEMO101 sources."""

import argparse
import json
import uuid

from fastapi.testclient import TestClient

from app.benchmark import prepare_hybrid
from app.config import PROJECT_ROOT, get_settings
from app.main import app, get_study_agent, get_study_store
from app.services.state import save_state
from app.services.study_agent import PlanRequest, StudyAgent
from app.services.study_store import StudyStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"api_calls": 0, "requires_explicit_approval": True}))
        return
    root = PROJECT_ROOT / "evals/reports/study-agent-smoke"
    # Rehydrate committed public embeddings locally; this does not call an API.
    base = prepare_hybrid(get_settings(), None, root / "runtime")
    config = base.config.model_copy(update={
        "conversation_db_path": root / "conversations.sqlite3",
        "agent_run_budget_usd": 0.05, "agent_daily_budget_usd": 0.05})
    if config.openai_model != "gpt-6-luna":
        raise ValueError("Preserve the approved model")
    store = StudyStore(root / "plans.sqlite3")
    agent = StudyAgent(config, store)
    request = PlanRequest(
        request_id=uuid.uuid5(uuid.NAMESPACE_URL, "coursepilot-study-agent-smoke-v1"),
        course="DEMO101",
        goal="请根据虚构课程资料安排 RAG 基础、检索和评测的复习，每天一道自测题。",
        days=3, daily_minutes=45)
    app.dependency_overrides[get_settings] = lambda: config
    app.dependency_overrides[get_study_store] = lambda: store
    app.dependency_overrides[get_study_agent] = lambda: agent
    try:
        client = TestClient(app)
        response = client.post("/api/study-plans", json=request.model_dump(mode="json"))
        response.raise_for_status()
        plan = response.json()
        checks = {"draft_generated": plan["status"] in {"draft", "active"},
                  "citations_present": bool(plan["tasks"]) and all(
                      task["citations"] for task in plan["tasks"])}
        if plan["status"] == "draft":
            url = f"/api/study-plans/{plan['id']}"
            before = plan["api_calls"]
            confirmed = client.post(url + "/confirm", json={
                "confirmed": True, "version": plan["version"]})
            confirmed.raise_for_status()
            active = confirmed.json()
            updated = client.patch(url + f"/tasks/{active['tasks'][0]['id']}", json={
                "done": True, "version": active["version"]})
            updated.raise_for_status()
            plan = updated.json()
            checks["confirmed_and_completed"] = plan["tasks"][0]["done"]
            checks["no_paid_calls_for_writes"] = plan["api_calls"] == before
            checks["persisted"] = StudyStore(store.path).get(plan["id"])["tasks"][0]["done"]
        elif plan["status"] == "active":
            checks["confirmed_and_completed"] = plan["tasks"][0]["done"]
        result = {"checks": checks, "passed": all(checks.values()),
                  "api_calls": plan["api_calls"], "estimated_usd": plan["accounted_cost_usd"],
                  "uncertain_calls": plan["uncertain_calls"], "error_code": plan.get("error_code"),
                  "task_count": len(plan["tasks"]), "trace": plan["trace"],
                  "scope": "One fictional development smoke test, not Agent task accuracy"}
        save_state(root / "latest.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
