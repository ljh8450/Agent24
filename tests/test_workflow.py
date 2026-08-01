import tempfile
import unittest
from pathlib import Path

from app.contracts import Source
from app.service import ResearchAgent


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
        report = self.agent.report(self.run["id"])
        self.assertIn("가정 없는 식별구간", report["html"])
        self.assertTrue((Path(self.temp.name) / report["artifact"]).is_file())

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
