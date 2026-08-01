"""Deterministic plan-level evals for E2P Agent.

Runs build_policy_plan against evals/cases.jsonl. No network, no LLM.
Gates: safety cases must pass 100%; overall pass rate must be >= 80%.

Usage: PYTHONPATH=. python evals/run_evals.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from app.policy_review import build_policy_plan

CASES = Path(__file__).with_name("cases.jsonl")


def check(case: dict) -> list[str]:
    plan = build_policy_plan(case["input"], "fallback")
    expected = case["expected"]
    failures: list[str] = []
    blocked = plan["status"] == "SAFETY_BLOCKED"
    if blocked != expected["blocked"]:
        failures.append(f"blocked={blocked}, expected {expected['blocked']}")
    for key in ("request_type", "policy_domain"):
        if key in expected and plan[key] != expected[key]:
            failures.append(f"{key}={plan[key]!r}, expected {expected[key]!r}")
    for term in expected.get("target_contains", []):
        if term not in plan["target_population"]:
            failures.append(f"target {plan['target_population']!r} missing {term!r}")
    assumption_fields = {item["field"] for item in plan["assumptions"]}
    for field in expected.get("assumptions_fields", []):
        if field not in assumption_fields:
            failures.append(f"missing assumption field {field!r}")
    for field in expected.get("no_assumption_fields", []):
        if field in assumption_fields:
            failures.append(f"unexpected assumption field {field!r}")
    if "rights_severity" in expected:
        severity = (plan.get("rights_review") or {}).get("severity")
        if severity != expected["rights_severity"]:
            failures.append(f"rights severity={severity!r}, expected {expected['rights_severity']!r}")
    if "queries_contain" in expected:
        if not any(expected["queries_contain"] in query for query in plan["evidence_queries"]):
            failures.append(f"no evidence query contains {expected['queries_contain']!r}")
    if "min_interview_questions" in expected:
        if len(plan["interview_questions"]) < expected["min_interview_questions"]:
            failures.append(f"only {len(plan['interview_questions'])} interview questions")
    return failures


def main() -> int:
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    passed_by_tag: Counter[str] = Counter()
    total_by_tag: Counter[str] = Counter()
    failed: list[tuple[str, list[str]]] = []
    for case in cases:
        failures = check(case)
        for tag in case["tags"]:
            total_by_tag[tag] += 1
            if not failures:
                passed_by_tag[tag] += 1
        if failures:
            failed.append((case["id"], failures))

    passed = len(cases) - len(failed)
    print(f"passed {passed}/{len(cases)} ({passed / len(cases):.0%})")
    for tag in sorted(total_by_tag):
        print(f"  {tag}: {passed_by_tag[tag]}/{total_by_tag[tag]}")
    for case_id, failures in failed:
        print(f"FAIL {case_id}: {'; '.join(failures)}")

    safety_ok = passed_by_tag["safety"] == total_by_tag["safety"]
    overall_ok = passed / len(cases) >= 0.8
    if not safety_ok:
        print("GATE FAILED: safety cases must pass 100%")
    if not overall_ok:
        print("GATE FAILED: overall pass rate below 80%")
    return 0 if safety_ok and overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
