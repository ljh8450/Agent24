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


if __name__ == "__main__":
    unittest.main()
