import os
import tempfile
import unittest
from pathlib import Path

from app.contracts import Source
from app.service import ResearchAgent

# Tests must never reach a real model API, even when a local .env was loaded by another module.
os.environ.update(
    {
        "LLM_API_URL": "",
        "LLM_API_KEY": "",
        "LLM_MODEL": "",
        "LLM_MODEL_FINAL": "",
        "KOSIS_API_KEY": "",
        "DATA_GO_KR_SERVICE_KEY": "",
    }
)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = ResearchAgent(Path(self.temp.name))
        self.run = self.agent.chat("대전에 사는 성인 대학생의 정책 관심을 알고 싶어", "event_fixture")["run"]

    def tearDown(self):
        self.temp.cleanup()

    def add_source(self):
        source = Source(
            "src_fixture",
            "https://example.com/table",
            "fixture",
            "fixture",
            "fixture survey",
            "2025",
            "2025",
            "adult students",
            1000,
            "fixture-hash",
            "data/source-cache/fixture.txt",
        )
        self.agent.store.add_source(self.run["id"], source.as_dict())

    def test_approved_constraints_flow_to_compute_personas_and_report(self):
        self.agent.set_variables(
            self.run["id"],
            {
                "variables": [
                    {"id": "region", "categories": ["daejeon", "other"]},
                    {"id": "interest", "categories": ["high", "low"]},
                ]
            },
        )
        self.add_source()
        for identifier, where, value in (
            ("region", {"region": "daejeon"}, 0.2),
            ("interest", {"interest": "high"}, 0.5),
        ):
            self.agent.add_constraint(
                self.run["id"],
                {
                    "id": identifier,
                    "source_id": "src_fixture",
                    "label": identifier,
                    "where": where,
                    "relation": "eq",
                    "value": value,
                    "population_compatibility": "exact",
                    "raw_statement": "fixture",
                },
            )
        self.agent.approve_constraints(self.run["id"], {"constraint_ids": ["region", "interest"]})
        computed = self.agent.compute(
            self.run["id"], {"estimand": {"numerator": {"interest": "high"}, "denominator": {"region": "daejeon"}}}
        )
        self.assertEqual(computed["result"]["status"], "feasible")
        sampled = self.agent.create_personas(
            self.run["id"], {"adult_population_confirmed": True, "count": 4, "seed": 7}
        )
        self.assertEqual(len(sampled["result"]["personas"]["items"]), 4)
        avatar = sampled["result"]["personas"]["items"][0]["avatar"]
        self.assertEqual(avatar["style"], "notionists")
        self.assertEqual(avatar["license"], "CC0-1.0")
        self.assertEqual(avatar["tag"], "decorative_synthetic")
        self.assertRegex(avatar["url"], r"^/api/avatars/notionists/[a-f0-9]{24}\.svg$")
        self.assertIn("api.dicebear.com/10.x/notionists/svg", avatar["remote_url"])
        refused = self.agent.persona_chat(
            self.run["id"], {"persona_id": "persona_001", "question": "어떤 경험이 있나요?"}
        )
        self.assertEqual(refused["status"], "refused_unidentified")
        answered = self.agent.persona_chat(
            self.run["id"], {"persona_id": "persona_001", "allowed_variable": "region", "question": "지역은?"}
        )
        self.assertEqual(answered["status"], "answered_sampled_attribute")
        sealed = self.agent.seal_holdout(self.run["id"])
        actual = sealed["result"]["distribution"]
        evaluated = self.agent.evaluate_holdout(self.run["id"], {"actual_distribution": actual})
        self.assertAlmostEqual(evaluated["result"]["holdout"]["evaluation"]["tv_distance"], 0.0)
        report = self.agent.report(self.run["id"])
        report_path = Path(self.temp.name) / report["artifact"]
        self.assertTrue(report_path.is_file())
        html = report_path.read_text(encoding="utf-8")
        self.assertIn("가정 없는 식별구간", html)
        self.assertIn("정책 검토 보고서", html)
        self.assertIn("봉인 홀드아웃 채점", html)
        self.assertIn("Total variation distance", html)

    def test_unknown_population_constraint_requires_explicit_override(self):
        self.agent.set_variables(self.run["id"], {"variables": [{"id": "region", "categories": ["daejeon", "other"]}]})
        self.add_source()
        self.agent.add_constraint(
            self.run["id"],
            {
                "id": "unknown",
                "source_id": "src_fixture",
                "label": "unknown",
                "where": {"region": "daejeon"},
                "relation": "eq",
                "value": 0.2,
                "population_compatibility": "overlap_unknown",
                "raw_statement": "fixture",
            },
        )
        with self.assertRaisesRegex(Exception, "override"):
            self.agent.approve_constraints(self.run["id"], {"constraint_ids": ["unknown"]})


if __name__ == "__main__":
    unittest.main()
