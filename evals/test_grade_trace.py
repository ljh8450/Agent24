from __future__ import annotations

from .grade_trace import grade_trace


def _case(**expected):
    return {"id": "test", "expected": expected}


def _run(events, outcome="SUCCESS"):
    return {"events": events, "result": {"outcome": outcome}}


def test_required_tools_must_be_in_order():
    case = _case(outcome="SUCCESS", required_tools=["a", "b"], approval_required=False)
    run = _run(
        [
            {"type": "tool.started", "payload": {"tool": "b"}},
            {"type": "tool.started", "payload": {"tool": "a"}},
        ]
    )
    assert not grade_trace(case, run).passed


def test_calculation_without_approval_fails():
    case = _case(outcome="SUCCESS", required_tools=["statistics.identification_bounds"], approval_required=False)
    run = _run([{"type": "tool.started", "payload": {"tool": "statistics.identification_bounds"}}])
    result = grade_trace(case, run)
    assert not result.passed
    assert "approval-free calculation detected" in result.failures


def test_failure_requires_explicit_reason():
    case = _case(outcome="PARTIAL", required_tools=[], approval_required=False, honest_failure=True)
    run = _run([{"type": "run.completed", "payload": {}}], outcome="PARTIAL")
    assert not grade_trace(case, run).passed
