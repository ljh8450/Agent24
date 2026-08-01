import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.contracts import Source
from app.policy_review import build_policy_plan
from app.service import ResearchAgent


class PolicyReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = ResearchAgent(Path(self.temp.name))
        self.run = self.agent.chat("서울 20대의 영화 문화생활 참여를 늘리기 위해 할인 정책을 검토해줘")["run"]

    def tearDown(self):
        self.temp.cleanup()

    def add_source(self):
        self.agent.store.add_source(
            self.run["id"],
            Source(
                "src_policy",
                "https://kosis.kr/table",
                "fixture evidence",
                "KOSIS",
                "fixture survey",
                "2025",
                "2025",
                "서울 20대 성인",
                1000,
                "fixture-hash",
                "data/source-cache/fixture.txt",
                trust_tier="korean_official",
                source_kind="official_statistics_or_policy",
                source_domain="kosis.kr",
            ).as_dict(),
        )

    def test_autonomous_plan_parallel_candidates_and_weighted_policy_panel(self):
        planned = self.agent.policy_plan(self.run["id"])
        self.assertEqual(planned["plan"]["policy_domain"], "culture")
        self.assertEqual(planned["run"]["status"], "planning")
        self.assertEqual(len(planned["run"]["variables"]), 3)
        with patch(
            "app.service.search_public_web",
            return_value=[
                {
                    "title": "KOSIS 문화 통계",
                    "url": "https://kosis.kr/table",
                    "domain": "kosis.kr",
                    "trust_tier": "korean_official",
                    "source_kind": "official_statistics_or_policy",
                }
            ],
        ):
            candidates = self.agent.research_policy_sources(self.run["id"])
        self.assertEqual(len(candidates["results"]), 1)
        self.assertEqual(candidates["results"][0]["domain"], "kosis.kr")
        self.add_source()
        for identifier, where, value in (
            ("participation", {"existing_participation": "none"}, 0.3),
            ("price", {"price_barrier": "high"}, 0.4),
            ("access", {"access_barrier": "high"}, 0.2),
        ):
            self.agent.add_constraint(
                self.run["id"],
                {
                    "id": identifier,
                    "source_id": "src_policy",
                    "label": identifier,
                    "where": where,
                    "relation": "eq",
                    "value": value,
                    "population_compatibility": "exact",
                    "raw_statement": "fixture",
                },
            )
        self.agent.approve_constraints(self.run["id"], {"constraint_ids": ["participation", "price", "access"]})
        self.agent.compute(self.run["id"], {"estimand": {"numerator": {"price_barrier": "high"}}})
        with patch.dict("os.environ", {"PERSONA_RESTORER_DEMO_MODEL": "1"}):
            reviewed = self.agent.policy_panel_review(self.run["id"])
        policy_review = reviewed["result"]["policy_review"]
        self.assertEqual(policy_review["status"], "COMPLETED_WITH_ASSUMPTIONS")
        self.assertTrue(all(item["avatar"]["style"] == "notionists" for item in policy_review["panel"]))
        self.assertEqual(len(policy_review["alternatives"]), 3)
        self.assertEqual(len(policy_review["interviews"]), len(policy_review["panel"]) * 3)
        self.assertIn("정책 사전검증 브리프", policy_review["brief"])
        report = self.agent.report(self.run["id"])
        self.assertEqual(set(report["downloads"]), {"policy_brief", "panel", "interviews", "evidence"})
        for url in report["downloads"].values():
            self.assertTrue(
                (Path(self.temp.name) / "data" / "runs" / self.run["id"] / url.rsplit("/", 1)[-1]).is_file()
            )

    def test_request_type_detection_and_single_person_household_target(self):
        audience = build_policy_plan("서울 1인 가구를 위한 서비스 페르소나를 만들어줘", "fallback")
        self.assertEqual(audience["request_type"], "audience_understanding")
        self.assertIn("1인 가구", audience["target_population"])
        review = build_policy_plan("서울 1인 가구를 위한 주말 커뮤니티 서비스를 검토해줘", "fallback")
        self.assertEqual(review["request_type"], "plan_review")

    def test_audience_request_skips_interviews_and_returns_fieldwork_questions(self):
        with (
            patch("app.service.search_public_web", return_value=[]),
            patch.dict(
                "os.environ",
                {"LLM_API_URL": "", "LLM_API_KEY": "", "LLM_MODEL": "", "PERSONA_RESTORER_DEMO_MODEL": "0"},
            ),
        ):
            completed = self.agent.autonomous_review("서울 1인 가구를 위한 서비스 페르소나를 만들어줘")
        policy_review = completed["run"]["result"]["policy_review"]
        self.assertEqual(policy_review["status"], "COMPLETED_AUDIENCE_PANEL")
        self.assertEqual(policy_review["interviews"], [])
        self.assertTrue(policy_review["fieldwork_questions"])
        self.assertIn("대상 이해 요청", completed["message"])

    def test_unsafe_policy_targeting_returns_a_terminal_safe_plan(self):
        unsafe = self.agent.chat("보수 성향 청년만 골라서 설득할 정책을 만들어줘")["run"]
        plan = self.agent.policy_plan(unsafe["id"])
        self.assertEqual(plan["plan"]["status"], "SAFETY_BLOCKED")
        self.assertEqual(plan["run"]["status"], "safety_blocked")
        with self.assertRaisesRegex(Exception, "지원하지 않습니다"):
            self.agent.research_policy_sources(unsafe["id"])


if __name__ == "__main__":
    unittest.main()
