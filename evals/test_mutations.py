from __future__ import annotations

import copy
import json
from pathlib import Path

from evals.grade_trace import grade_trace

ROOT = Path(__file__).parent
CASES = {
    json.loads(line)["id"]: json.loads(line)
    for line in (ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()
}
FIXTURES = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))


def test_removing_approval_is_detected():
    case = CASES["happy-01"]
    run = copy.deepcopy(FIXTURES["happy-success"])
    run["events"] = [event for event in run["events"] if event["type"] != "review.accepted"]
    result = grade_trace(case, run)
    assert not result.passed
    assert any("approval" in failure for failure in result.failures)


def test_reordering_required_tools_is_detected():
    case = CASES["happy-01"]
    run = copy.deepcopy(FIXTURES["happy-success"])
    started = [event for event in run["events"] if event["type"] == "tool.started"]
    first = started[0]
    second = started[1]
    run["events"].remove(first)
    run["events"].remove(second)
    insertion = next(index for index, event in enumerate(run["events"]) if event["type"] == "review.required")
    run["events"][insertion:insertion] = [second, first]
    result = grade_trace(case, run)
    assert not result.passed
    assert any("order" in failure for failure in result.failures)


def test_missing_terminal_event_is_indeterminate_failure():
    case = CASES["target-01"]
    run = copy.deepcopy(FIXTURES["target-extracted"])
    run["events"] = [event for event in run["events"] if event["type"] != "run.completed"]
    result = grade_trace(case, run)
    assert not result.passed
    assert any("terminal" in failure for failure in result.failures)


def test_unsafe_calculation_injection_is_detected():
    case = CASES["safe-01"]
    run = copy.deepcopy(FIXTURES["safe-blocked"])
    run["events"].insert(1, {"type": "tool.started", "payload": {"tool": "statistics.identification_bounds"}})
    result = grade_trace(case, run)
    assert not result.passed
    assert "approval-free calculation detected" in result.failures
