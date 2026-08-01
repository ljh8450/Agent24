from __future__ import annotations

import json
from pathlib import Path

from app.store import ProjectStore
from evals.grade_trace import grade_file


def test_sqlite_events_round_trip_into_gradeable_run_json(tmp_path: Path):
    store = ProjectStore(tmp_path)
    run_id = "sqlite-trace-fixture"
    run = {
        "id": run_id,
        "session_key": "eval-session",
        "question": "서울 청년 주거지원 안내를 검토한다.",
        "target_population": "청년",
        "status": "running",
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
    }
    store.create_run(run)
    store.append_event(run_id, "tool.started", {"tool": "policy.plan_request"})
    store.append_event(run_id, "review.required", {"reason": "constraint approval"})
    store.append_event(run_id, "review.accepted", {})
    store.append_event(run_id, "tool.started", {"tool": "statistics.identification_bounds"})
    store.append_event(run_id, "tool.completed", {"tool": "statistics.identification_bounds"})
    store.append_event(run_id, "run.completed", {})
    store.update_run(run_id, status="completed", result={"outcome": "SUCCESS", "intent": "policy_review"})
    stored = store.get_run(run_id)
    artifact = tmp_path / store.write_artifact(run_id, "run.json", json.dumps(stored, ensure_ascii=False))

    case = {
        "id": "sqlite-round-trip",
        "expected": {
            "outcome": "SUCCESS",
            "intent": "policy_review",
            "required_tools": ["policy.plan_request", "statistics.identification_bounds"],
            "approval_required": True,
        },
    }
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    result = grade_file(case, artifact)

    assert result.passed
    assert "terminal_status_consistency" in result.checks
    assert any(event["type"] == "review.accepted" for event in stored["events"])
