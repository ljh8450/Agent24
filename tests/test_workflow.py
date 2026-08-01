import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.contracts import Source
from app.errors import DomainError
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
        self.assertEqual(computed["result"]["cross_constraint_count"], 0)
        self.assertIn("독립으로 처리", computed["result"]["assumption"])
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
        report_path = Path(self.temp.name) / report["artifact"]
        self.assertTrue(report_path.is_file())
        html = report_path.read_text(encoding="utf-8")
        self.assertIn("가정 없는 식별구간", html)
        self.assertIn("정책 검토 보고서", html)

    def test_variable_categories_accept_code_label_objects(self):
        from app.contracts import Variable

        variable = Variable.parse(
            {
                "id": "housing_tenure_type",
                "label": "점유 형태",
                "categories": [{"code": "jeonse", "label": "전세"}, {"code": "monthly_rent", "label": "월세"}],
            }
        )
        self.assertEqual(variable.categories, ("jeonse", "monthly_rent"))

    def test_set_variables_rejects_nonhuman_persona_schema(self):
        with self.assertRaisesRegex(DomainError, "현실의 성인 인간"):
            self.agent.set_variables(
                self.run["id"],
                {"variables": [{"id": "identity", "categories": ["마법사", "티라노사우르스"]}]},
            )

    def test_set_variables_allows_vehicle_access_as_a_realistic_attribute(self):
        updated = self.agent.set_variables(
            self.run["id"],
            {"variables": [{"id": "vehicle_access", "categories": ["car", "no_car"]}]},
        )
        self.assertEqual(updated["variables"][0]["id"], "vehicle_access")

    def test_llm_labels_flow_to_a_unique_weighted_panel_and_report(self):
        llm_plan = {
            "policy_focus": "청년 주거 지원",
            "target_population": "서울 청년 1인 가구",
            "variables": [
                {
                    "id": "rent_burden",
                    "label": "월세 부담",
                    "categories": [
                        {"code": "under_20", "label": "월세 부담 20% 미만"},
                        {"code": "over_20", "label": "월세 부담 20% 이상"},
                    ],
                },
                {
                    "id": "housing_type",
                    "label": "주거 유형",
                    "categories": [
                        {"code": "officetel", "label": "오피스텔"},
                        {"code": "studio", "label": "원룸"},
                    ],
                },
                {
                    "id": "service_use",
                    "label": "주거 지원 이용",
                    "categories": [
                        {"code": "none", "label": "이용 경험 없음"},
                        {"code": "used", "label": "이용 경험 있음"},
                    ],
                },
            ],
            "alternatives": [{"label": "월세 지원"}],
            "evidence_queries": ["서울 청년 주거 통계"],
        }
        with patch("app.service.llm_policy_plan", return_value=llm_plan):
            run = self.agent.chat("서울 청년 1인 가구 주거 지원 정책을 검토해줘")["run"]

        self.agent._scenario_only_model(run["id"])
        with patch.dict("os.environ", {"PERSONA_RESTORER_DEMO_MODEL": "1"}):
            reviewed = self.agent.policy_panel_review(run["id"])

        panel = reviewed["result"]["policy_review"]["panel"]
        signatures = [
            tuple((attribute["variable_code"], attribute["code"]) for attribute in persona["attributes"])
            for persona in panel
        ]
        self.assertEqual(len(panel), 8)
        self.assertEqual(len(signatures), len(set(signatures)))
        self.assertAlmostEqual(sum(persona["weight"] for persona in panel), 1.0)
        self.assertIn("월세 부담 20% 미만", {item["value"] for item in panel[0]["attributes"]})

        report = self.agent.report(run["id"])
        html = (Path(self.temp.name) / report["artifact"]).read_text(encoding="utf-8")
        self.assertIn("월세 부담 20% 미만", html)
        self.assertIn("오피스텔", html)
        self.assertNotIn(">under_20<", html)

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
