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


def test_loop_budget_mutation_is_detected():
    case = CASES["loop-budget-01"]
    run = copy.deepcopy(FIXTURES["loop-two-rounds"])
    run["events"].insert(6, {"type": "agent.evidence_round", "payload": {"round": 3}})
    run["events"].insert(7, {"type": "agent.decision", "payload": {"round": 3, "action": "stop"}})
    result = grade_trace(case, run)
    assert not result.passed
    assert any("budget" in failure for failure in result.failures)


def test_collection_tool_after_stop_mutation_is_detected():
    case = CASES["loop-budget-02"]
    run = copy.deepcopy(FIXTURES["loop-stop"])
    # 수집 계열 도구가 stop 뒤에 다시 돌면 계약 위반이다.
    run["events"].insert(3, {"type": "tool.started", "payload": {"tool": "kosis.statistics_openapi"}})
    result = grade_trace(case, run)
    assert not result.passed
    assert any("after stop" in failure for failure in result.failures)


def test_pipeline_tool_after_stop_is_allowed():
    case = CASES["loop-budget-02"]
    run = copy.deepcopy(FIXTURES["loop-stop"])
    # 정직 완주 계약: stop 이후에도 보고서 등 파이프라인은 계속 실행된다.
    # (통계 도구는 별도의 '승인 없는 계산' 검사가 scenario 마커를 요구하므로 여기선 보고서 도구로 검증)
    run["events"].insert(3, {"type": "tool.started", "payload": {"tool": "report.write_provenance"}})
    result = grade_trace(case, run)
    assert result.passed, result.failures


def test_broader_note_mutation_is_detected():
    case = CASES["loop-broader-note-01"]
    run = copy.deepcopy(FIXTURES["broader-approved"])
    run["constraints"][0]["override_note"] = ""
    result = grade_trace(case, run)
    assert not result.passed
    assert any("assumption note" in failure for failure in result.failures)
