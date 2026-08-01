import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.contracts import Source
from app.service import ResearchAgent


class EvidenceRecoveryLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = ResearchAgent(Path(self.temp.name))
        self.run = self.agent.chat("서울 청년 월세 지원 정책을 검토해줘")["run"]
        self.plan = self.agent._latest_policy_plan(self.run)

    def add_broader_candidate(self):
        self.agent.store.add_source(
            self.run["id"],
            Source(
                "src_loop",
                "https://kosis.kr/table",
                "국가 통계",
                "KOSIS",
                "전국 조사",
                "2025",
                "2025",
                "전국 청년",
                1000,
                "hash",
                "data/source-cache/loop.txt",
                trust_tier="korean_official",
            ).as_dict(),
        )
        variable = self.run["variables"][0]
        self.agent.add_constraint(
            self.run["id"],
            {
                "id": "broader_1",
                "source_id": "src_loop",
                "label": "national proxy",
                "where": {variable["id"]: variable["categories"][0]},
                "relation": "eq",
                "value": 0.4,
                "population_compatibility": "broader",
                "raw_statement": "전국 비율 40% (PRD_DE 2025)",
            },
        )

    def decisions(self):
        run = self.agent.store.get_run(self.run["id"])
        return [event["payload"] for event in run["events"] if event["type"] == "agent.decision"]

    def test_approve_broader_decision_approves_with_assumption_note(self):
        self.add_broader_candidate()
        with patch(
            "app.service.decide_next_evidence_action",
            return_value={"action": "approve_broader", "queries": [], "reason": "t"},
        ):
            approved = self.agent._evidence_recovery_loop(self.run["id"], self.plan)
        self.assertEqual(approved, ["broader_1"])
        constraint = self.agent.store.list_constraints(self.run["id"])[0]
        self.assertEqual(constraint["review_status"], "approved")
        self.assertIn("가정", constraint["override_note"])

    def test_stop_decision_ends_after_one_round(self):
        with patch(
            "app.service.decide_next_evidence_action",
            return_value={"action": "stop", "queries": [], "reason": "t"},
        ):
            approved = self.agent._evidence_recovery_loop(self.run["id"], self.plan)
        self.assertEqual(approved, [])
        self.assertEqual(len(self.decisions()), 1)

    def test_search_rounds_respect_two_round_budget(self):
        with (
            patch(
                "app.service.decide_next_evidence_action",
                side_effect=[
                    {"action": "search", "queries": ["새 검색어 1"], "reason": "t"},
                    {"action": "search", "queries": ["새 검색어 2"], "reason": "t"},
                    {"action": "search", "queries": ["새 검색어 3"], "reason": "t"},
                ],
            ) as decide,
            patch("app.service.search_public_web", return_value=[]),
        ):
            approved = self.agent._evidence_recovery_loop(self.run["id"], self.plan)
        self.assertEqual(approved, [])
        self.assertEqual(decide.call_count, 2)
        self.assertEqual(len(self.decisions()), 2)


class PartialCoverageLoopTests(EvidenceRecoveryLoopTests):
    def approve_first_variable(self):
        self.add_broader_candidate()
        self.agent.approve_constraints(self.run["id"], {"constraint_ids": ["broader_1"]})

    def test_partial_coverage_enters_loop_and_reports_uncovered_variables(self):
        self.approve_first_variable()
        captured: dict = {}

        def fake_decide(observation):
            captured.update(observation)
            return {"action": "stop", "queries": [], "reason": "t"}

        with patch("app.service.decide_next_evidence_action", side_effect=fake_decide):
            self.agent._evidence_recovery_loop(self.run["id"], self.plan)
        variable_ids = [variable["id"] for variable in self.run["variables"]]
        self.assertEqual(captured["covered_variables"], [variable_ids[0]])
        self.assertEqual(set(captured["uncovered_variables"]), set(variable_ids[1:]))
        self.assertEqual(captured["approved_count"], 1)

    def test_loop_noops_when_every_variable_is_covered(self):
        self.add_broader_candidate()
        for index, variable in enumerate(self.run["variables"][1:], start=2):
            self.agent.add_constraint(
                self.run["id"],
                {
                    "id": f"cover_{index}",
                    "source_id": "src_loop",
                    "label": f"cover {variable['id']}",
                    "where": {variable["id"]: variable["categories"][0]},
                    "relation": "eq",
                    "value": 0.5,
                    "population_compatibility": "exact",
                    "raw_statement": "fixture (PRD_DE 2025)",
                },
            )
        self.agent.approve_constraints(
            self.run["id"], {"constraint_ids": ["broader_1", "cover_2", "cover_3"]}
        )
        self.assertEqual(self.agent._uncovered_variables(self.run["id"]), [])
        with patch("app.service.decide_next_evidence_action") as decide:
            approved = self.agent._evidence_recovery_loop(self.run["id"], self.plan)
        decide.assert_not_called()
        self.assertEqual(approved, [])


class ConflictFallbackTests(unittest.TestCase):
    def test_conflicting_approvals_demote_part_of_core_and_recompute(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = ResearchAgent(Path(temp.name))
        run = agent.chat("대전 청년 버스 요금 지원을 검토해줘")["run"]
        agent.store.add_source(
            run["id"],
            Source(
                "src_conflict", "https://kosis.kr/t", "t", "KOSIS", "s", "2025", "2025", "p", 10, "h",
                "data/source-cache/x.txt", trust_tier="korean_official",
            ).as_dict(),
        )
        variable = run["variables"][0]
        # conflict_0은 id 사전순으로 앞서지만 broader·구연도 — 새 규칙에선 exact·최신인 conflict_1이 살아남아야 한다.
        specs = [("broader", "fixture (PRD_DE 2023)"), ("exact", "fixture (PRD_DE 2025)")]
        for index, (compat, statement) in enumerate(specs):
            agent.add_constraint(
                run["id"],
                {
                    "id": f"conflict_{index}",
                    "source_id": "src_conflict",
                    "label": variable["categories"][index],
                    "where": {variable["id"]: variable["categories"][index]},
                    "relation": "eq",
                    "value": 0.9,  # 둘이면 합 1.8 — 모순
                    "population_compatibility": compat,
                    "raw_statement": statement,
                },
            )
        agent.approve_constraints(run["id"], {"constraint_ids": ["conflict_0", "conflict_1"]})
        computed, mode = agent._compute_with_conflict_fallback(run["id"], ["conflict_0", "conflict_1"])
        self.assertEqual(mode, "approved_public_constraints_after_conflict")
        self.assertEqual(computed["result"]["status"], "feasible")
        statuses = {item["id"]: item["review_status"] for item in agent.store.list_constraints(run["id"])}
        self.assertEqual(statuses["conflict_0"], "conflicted")
        self.assertEqual(statuses["conflict_1"], "approved")


COLLECTION_TOOLS = {
    "web.parallel_korean_policy_research",
    "source.fetch_snapshot",
    "kosis.statistics_openapi",
    "llm.extract_constraint_candidates",
    "review.auto_approve_exact_constraints",
}


class StopContractTests(unittest.TestCase):
    """계약(#32): stop = 수집 중단. 파이프라인은 scenario_only로 정직하게 완주한다."""

    def test_stop_halts_collection_but_completes_the_run_honestly(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = ResearchAgent(Path(temp.name))
        env = {
            "LLM_API_URL": "https://example.com/v1/chat/completions",
            "LLM_API_KEY": "k",
            "LLM_MODEL": "m",
            "PERSONA_RESTORER_DEMO_MODEL": "1",
            "KOSIS_API_KEY": "",
        }
        with (
            patch.dict("os.environ", env),
            patch("app.service.search_public_web", return_value=[]),
            patch(
                "app.service.decide_next_evidence_action",
                return_value={"action": "stop", "queries": [], "reason": "검증용 안전 중단"},
            ),
            patch("app.service.synthesize_policy_insights", return_value="시뮬레이션 기반 인사이트"),
        ):
            completed = agent.autonomous_review("서울 청년 주거지원 안내 개선 정책을 검토해줘")

        run = completed["run"]
        events = run["events"]
        stop_index = next(
            index
            for index, event in enumerate(events)
            if event["type"] == "agent.decision" and event["payload"].get("action") == "stop"
        )
        started_after_stop = [
            event["payload"].get("tool")
            for event in events[stop_index + 1 :]
            if event["type"] == "tool.started" and event["payload"].get("tool") in COLLECTION_TOOLS
        ]
        self.assertEqual(started_after_stop, [])
        result = run["result"]
        self.assertEqual(result["status"], "scenario_only")
        self.assertIn("수집 중단 사유", result["evidence_gap"])
        self.assertIn("검증용 안전 중단", result["evidence_gap"])
        self.assertTrue(result.get("policy_review"))
        self.assertIn("html_report", completed["artifacts"])


class LowInformationGateTests(unittest.TestCase):
    def test_zero_value_eq_constraint_is_not_auto_approved(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = ResearchAgent(Path(temp.name))
        run = agent.chat("대전 청년 버스 요금 지원을 검토해줘")["run"]
        agent.store.add_source(
            run["id"],
            Source(
                "src_lowinfo", "https://kosis.kr/t", "t", "KOSIS", "s", "2025", "2025", "p", 10, "h",
                "data/source-cache/x.txt", trust_tier="korean_official",
            ).as_dict(),
        )
        variable = run["variables"][0]
        for index, value in enumerate([0.0, 0.3]):
            agent.add_constraint(
                run["id"],
                {
                    "id": f"lowinfo_{index}",
                    "source_id": "src_lowinfo",
                    "label": variable["categories"][index],
                    "where": {variable["id"]: variable["categories"][index]},
                    "relation": "eq",
                    "value": value,
                    "population_compatibility": "exact",
                    "raw_statement": "fixture (PRD_DE 2025)",
                },
            )
        approved = agent._autonomous_evidence_gate(run["id"])
        self.assertEqual(approved, ["lowinfo_1"])


if __name__ == "__main__":
    unittest.main()
