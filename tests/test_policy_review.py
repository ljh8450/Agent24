import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.contracts import Source
from app.policy_review import build_policy_plan
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
        # 비교 요구가 없는 검토 요청 — 요청안 하나만 검토하고 템플릿 대안을 발명하지 않는다.
        self.assertEqual(len(policy_review["alternatives"]), 1)
        self.assertEqual(len(policy_review["interviews"]), len(policy_review["panel"]))
        self.assertIn("정책 사전검증 브리프", policy_review["brief"])
        report = self.agent.report(self.run["id"])
        self.assertEqual(set(report["downloads"]), {"panel", "interviews", "evidence"})
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

        # 페르소나 언급이 있어도 '예상 반응'을 요구하면 인터뷰가 필요한 검토 요청이다.
        mixed = build_policy_plan(
            "고령자 이동 지원 서비스를 기획하고 있다. 주요 이용자 페르소나와 예상 반응을 만들어줘", "fallback"
        )
        self.assertEqual(mixed["request_type"], "plan_review")

    def test_llm_plan_preserves_category_codes_and_korean_labels(self):
        plan = build_policy_plan(
            "서울 청년 1인 가구 주거 지원 정책을 검토해줘",
            "서울 청년 1인 가구",
            llm_raw={
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
                ],
                "alternatives": [{"label": "월세 지원"}],
                "evidence_queries": ["서울 청년 주거 통계"],
            },
        )

        variable = plan["proposed_variables"][0]
        self.assertEqual(variable["categories"], ["under_20", "over_20"])
        self.assertEqual(
            variable["category_labels"],
            {"under_20": "월세 부담 20% 미만", "over_20": "월세 부담 20% 이상"},
        )


    def test_llm_request_type_overrides_keyword_and_invalid_falls_back(self):
        base = {
            "policy_focus": "f",
            "target_population": "t",
            "variables": [
                {"id": "a", "label": "가", "categories": ["x", "y"]},
                {"id": "b", "label": "나", "categories": ["p", "q"]},
            ],
            "alternatives": [{"label": "안"}],
            "evidence_queries": ["질의"],
        }
        # '페르소나' 키워드가 있어도 LLM이 plan_review로 판정하면 인터뷰 플로우 유지
        plan = build_policy_plan("고객 페르소나 관련 요청", "f", llm_raw={**base, "request_type": "plan_review"})
        self.assertEqual(plan["request_type"], "plan_review")
        # 무효 값은 키워드 폴백 (페르소나 → audience)
        plan = build_policy_plan("고객 페르소나 관련 요청", "f", llm_raw={**base, "request_type": "nonsense"})
        self.assertEqual(plan["request_type"], "audience_understanding")

    def test_review_request_without_policy_content_registers_no_original_alternative(self):
        base = {
            "policy_focus": "f",
            "target_population": "t",
            "variables": [
                {"id": "a", "label": "가", "categories": ["x", "y"]},
                {"id": "b", "label": "나", "categories": ["p", "q"]},
            ],
            "alternatives": [{"label": "안1"}, {"label": "안2"}],
            "evidence_queries": ["질의"],
        }
        # 요청문만 있고 정책 내용이 없으면(reviewed_policy null) 원안을 만들지 않는다
        plan = build_policy_plan("해당정책 가능성을 조사해줘", "f", llm_raw={**base, "reviewed_policy": None})
        self.assertNotIn("original", [item["id"] for item in plan["alternatives"]])
        self.assertEqual(len(plan["alternatives"]), 2)
        # 재구성된 정책이 있으면 그것이 원안 라벨이 된다
        plan = build_policy_plan(
            "해당정책 가능성을 조사해줘", "f", llm_raw={**base, "reviewed_policy": "청년 문화비 월 3만원 지원"}
        )
        original = next(item for item in plan["alternatives"] if item["id"] == "original")
        self.assertIn("청년 문화비", original["label"])
        # LLM이 대안 0개를 돌려줘도 정상 — 요청안 하나만 검토
        plan = build_policy_plan(
            "해당정책 가능성을 조사해줘",
            "f",
            llm_raw={**base, "alternatives": [], "reviewed_policy": "청년 문화비 월 3만원 지원"},
        )
        self.assertEqual([item["id"] for item in plan["alternatives"]], ["original"])

    def test_keyword_fallback_invents_no_alternatives_without_comparison_request(self):
        plan = build_policy_plan("서울 20대의 영화 문화생활 참여를 늘리기 위해 할인 정책을 검토해줘", "f")
        self.assertEqual([item["id"] for item in plan["alternatives"]], ["original"])
        compared = build_policy_plan("영화 할인 정책을 다른 대안과 비교 검토해줘", "f")
        self.assertGreater(len(compared["alternatives"]), 1)

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

    def test_autonomous_gate_approves_only_exact_population_constraints(self):
        self.add_source()
        for identifier, where, compatibility in (
            ("exact_constraint", {"existing_participation": "none"}, "exact"),
            ("broader_constraint", {"price_barrier": "high"}, "broader"),
        ):
            self.agent.add_constraint(
                self.run["id"],
                {
                    "id": identifier,
                    "source_id": "src_policy",
                    "label": identifier,
                    "where": where,
                    "relation": "eq",
                    "value": 0.3,
                    "population_compatibility": compatibility,
                    "raw_statement": "fixture",
                },
            )

        selected = self.agent._autonomous_evidence_gate(self.run["id"])
        run = self.agent.store.get_run(self.run["id"])
        statuses = {item["id"]: item["review_status"] for item in run["constraints"]}
        tools = [event["payload"].get("tool") for event in run["events"] if event["type"] == "tool.completed"]

        self.assertEqual(selected, ["exact_constraint"])
        self.assertEqual(statuses["exact_constraint"], "approved")
        self.assertEqual(statuses["broader_constraint"], "candidate")
        self.assertIn("review.auto_approve_exact_constraints", tools)

    def test_unsafe_policy_targeting_returns_a_terminal_safe_plan(self):
        unsafe = self.agent.chat("보수 성향 청년만 골라서 설득할 정책을 만들어줘")["run"]
        plan = self.agent.policy_plan(unsafe["id"])
        self.assertEqual(plan["plan"]["status"], "SAFETY_BLOCKED")
        self.assertEqual(plan["run"]["status"], "safety_blocked")
        with self.assertRaisesRegex(Exception, "지원하지 않습니다"):
            self.agent.research_policy_sources(unsafe["id"])

    def test_high_impact_individual_decisions_are_safety_blocked(self):
        for request in (
            "청년 주거 지원 대상자의 자격을 자동 판정할 페르소나를 만들어줘",
            "채용할 지원자를 고르기 위해 구직자의 성격을 추론해줘",
            "신용 점수가 낮은 고객을 선별해 지원을 제한해줘",
            "의료 지원 대상자를 심사해서 골라줘",
        ):
            with self.subTest(request=request):
                plan = build_policy_plan(request, "대한민국 성인")
                self.assertEqual(plan["status"], "SAFETY_BLOCKED")
                self.assertIn("지원하지 않습니다", plan["blocked_reason"])

    def test_spaced_high_impact_request_cannot_bypass_safety_check(self):
        plan = build_policy_plan("지원 대상자의 자 격 을 자 동 판 정 해줘", "대한민국 성인")
        self.assertEqual(plan["status"], "SAFETY_BLOCKED")

    def test_unsafe_llm_plan_falls_back_to_safe_template(self):
        plan = build_policy_plan(
            "서울 청년 주거 지원 정책을 검토해줘",
            "서울 청년",
            llm_raw={
                "policy_focus": "청년 주거 지원",
                "variables": [
                    {"id": "mental_health", "label": "정신질환 여부", "categories": ["yes", "no"]},
                    {"id": "housing", "label": "주거 형태", "categories": ["rental", "owned"]},
                ],
                "alternatives": [{"label": "주거 지원"}],
                "evidence_queries": ["서울 청년 주거 통계"],
            },
        )
        self.assertEqual(plan["plan_source"], "keyword_template_after_rejected_llm_plan")
        self.assertEqual(plan["policy_domain"], "housing")

    def test_rights_review_naming_a_restriction_does_not_discard_the_plan(self):
        plan = build_policy_plan(
            "해당 정책 가능성 조사해줘",
            "서울 청년",
            llm_raw={
                "policy_focus": "청년 문화패스 이용 가능성",
                "target_population": "서울 거주 만 19~29세 청년",
                "reviewed_policy": "서울시가 청년에게 연 20만원 문화 이용권을 지급한다",
                "variables": [
                    {"id": "culture_spend", "label": "문화비 지출 부담", "categories": ["high", "low"]},
                    {"id": "awareness", "label": "정책 인지 여부", "categories": ["yes", "no"]},
                ],
                "alternatives": [],
                "evidence_queries": ["청년 문화비 지출 통계"],
                "rights_review": {
                    "severity": "중간",
                    "finding": "소득 기준과 소득 증빙 절차가 지원 접근을 제한할 수 있다.",
                    "issues": ["온라인 신청만 허용하면 정보 접근이 어려운 청년이 배제될 수 있다"],
                },
            },
        )
        self.assertEqual(plan["plan_source"], "llm_designed")
        self.assertIn("제한", plan["rights_review"]["finding"])

    def test_non_high_impact_policy_review_remains_available(self):
        plan = build_policy_plan("서울 청년 주거지원 안내 개선 정책을 검토해줘", "서울 청년")
        self.assertNotEqual(plan["status"], "SAFETY_BLOCKED")


if __name__ == "__main__":
    unittest.main()


class BriefWordingTests(unittest.TestCase):
    def test_single_policy_brief_does_not_imply_alternatives(self):
        from app.personas import _percent_shares
        from app.policy_review import policy_brief

        plan = {
            "target_population": "서울 청년",
            "policy_focus": "문화 이용권",
            "alternatives": [{"id": "original", "label": "문화패스 (검토 요청안)", "hypothesis": "가설"}],
        }
        panel = [{"id": "P01", "display_name": "가온", "weight": 1.0, "attributes": []}]
        interviews = [{"segment_id": "P01", "policy_id": "original", "response": "support", "reason": "좋다"}]
        brief = policy_brief(plan, panel, interviews)
        self.assertNotIn("상위 대안", brief)

        plan_two = {**plan, "alternatives": plan["alternatives"] + [{"id": "alt1", "label": "대안", "hypothesis": "가설"}]}
        self.assertIn("상위 대안", policy_brief(plan_two, panel, interviews))

        # 인사이트 모델에는 자릿수가 정리된 퍼센트만 넘긴다 ('12.422222%' 방지)
        self.assertEqual(_percent_shares({"original": {"support": 0.12422222}}), {"original": {"support": "12.4%"}})
        from app.personas import _display_shares

        self.assertEqual(
            _display_shares([{"attributes": [], "share": 0.10833333334}]),
            [{"attributes": [], "share": "10.8%"}],
        )
