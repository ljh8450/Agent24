import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from app import personas
from app.errors import DomainError

LLM_ENV = {"LLM_API_URL": "https://example.com/v1/chat/completions", "LLM_API_KEY": "k", "LLM_MODEL": "m"}

PANEL = [
    {
        "id": "P01",
        "weight_display": "50.0%",
        "attributes": [{"variable": "need", "value": "high"}],
    },
    {
        "id": "P02",
        "weight_display": "50.0%",
        "attributes": [{"variable": "need", "value": "low"}],
    },
]
PLAN = {
    "policy_focus": "테스트",
    "interview_questions": ["q1"],
    "alternatives": [
        {"id": "original", "label": "원안", "hypothesis": "h"},
        {"id": "alternative_1", "label": "대안", "hypothesis": "h"},
    ],
}


def _completion(content: dict) -> io.BytesIO:
    body = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()
    return io.BytesIO(body)


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.com", status, "err", None, io.BytesIO(b""))


class FakeResponse:
    def __init__(self, payload: dict):
        self.raw = _completion(payload)

    def read(self):
        return self.raw.read()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CallJsonModelTests(unittest.TestCase):
    def test_transient_429_is_retried_once_then_succeeds(self):
        responses = [_http_error(429), FakeResponse({"ok": True})]
        with (
            patch.dict("os.environ", LLM_ENV),
            patch("app.personas.time.sleep") as slept,
            patch("app.personas.urllib.request.urlopen") as urlopen,
        ):
            urlopen.side_effect = [responses[0], responses[1]]
            result = personas._call_json_model("prompt")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        slept.assert_called_once()

    def test_permanent_401_fails_without_retry(self):
        with (
            patch.dict("os.environ", LLM_ENV),
            patch("app.personas.time.sleep") as slept,
            patch("app.personas.urllib.request.urlopen", side_effect=_http_error(401)) as urlopen,
        ):
            with self.assertRaises(DomainError) as caught:
                personas._call_json_model("prompt")
        self.assertEqual(caught.exception.code, "LLM_JSON_FAILED")
        self.assertEqual(caught.exception.details.get("status"), 401)
        self.assertEqual(urlopen.call_count, 1)
        slept.assert_not_called()


def _valid_interviews():
    return {
        "interviews": [
            {
                "segment_id": segment["id"],
                "response": "support",
                "reason": "r",
                "barrier": "b",
                "suggested_change": "s",
            }
            for segment in PANEL
        ]
    }


class SimulatePolicyInterviewTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict("os.environ", {**LLM_ENV, "PERSONA_RESTORER_DEMO_MODEL": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_failing_alternative_does_not_drop_the_rest(self):
        def fake_call(prompt: str):
            if '"label": "대안"' in prompt:
                raise DomainError("LLM_JSON_FAILED", "boom")
            return _valid_interviews()

        with patch("app.personas._call_json_model", side_effect=fake_call):
            interviews = personas.simulate_policy_interviews(PANEL, PLAN, seed=1)
        self.assertEqual({item["policy_id"] for item in interviews}, {"original"})
        self.assertEqual(len(interviews), len(PANEL))

    def test_contract_violation_is_repaired_with_one_feedback_retry(self):
        calls: list[str] = []

        def fake_call(prompt: str):
            calls.append(prompt)
            if len(calls) == 1:
                return {"interviews": [{"segment_id": "P01", "response": "support"}]}
            return _valid_interviews()

        plan = {**PLAN, "alternatives": [PLAN["alternatives"][0]]}
        with patch("app.personas._call_json_model", side_effect=fake_call):
            interviews = personas.simulate_policy_interviews(PANEL, plan, seed=1)
        self.assertEqual(len(calls), 2)
        self.assertIn("broke the JSON contract", calls[1])
        self.assertEqual(len(interviews), len(PANEL))

    def test_all_alternatives_failing_raises(self):
        with patch("app.personas._call_json_model", side_effect=DomainError("LLM_JSON_FAILED", "boom")):
            with self.assertRaises(DomainError):
                personas.simulate_policy_interviews(PANEL, PLAN, seed=1)


class PublishedTablePreviewTests(unittest.TestCase):
    def test_search_tokens_come_from_the_attached_document_not_the_request_wording(self):
        calls: list[str] = []

        def fake_search(token, limit):
            calls.append(token)
            return [{"survey_name": "사회조사", "table_name": f"{token} 표"}]

        with (
            patch.dict(os.environ, {"KOSIS_API_KEY": "test-key"}),
            patch("app.sources.search_kosis_tables", side_effect=fake_search),
        ):
            titles = personas._published_table_preview(
                "해당 정책 가능성 조사해줘", "# 서울 청년 문화패스 기획서\n서울 거주 청년에게 문화 이용권을 지급한다."
            )

        self.assertTrue(titles)
        # 요청문의 일반어가 아니라 첨부 문서의 주제어로 검색해야 한다.
        self.assertIn("문화패스", calls)
        self.assertNotIn("가능성", calls)
        self.assertNotIn("정책", calls)

    def test_real_table_titles_are_injected_into_the_plan_prompt(self):
        tables = [
            {"survey_name": "주거실태조사", "table_name": "지역별 소득계층별 점유형태", "org_id": "116", "table_id": "T1", "path": ""}
        ]
        with (
            patch.dict("os.environ", {**LLM_ENV, "KOSIS_API_KEY": "k"}),
            patch("app.sources.search_kosis_tables", return_value=tables),
            patch("app.personas._call_json_model", return_value={}) as call,
        ):
            personas.llm_policy_plan("서울 청년 월세 부담 정책을 검토해줘")
        prompt = call.call_args.args[0]
        self.assertIn("주거실태조사 — 지역별 소득계층별 점유형태", prompt)
        self.assertIn("Published tables", prompt)

    def test_without_kosis_key_plan_prompt_is_unchanged(self):
        with (
            patch.dict("os.environ", {**LLM_ENV, "KOSIS_API_KEY": ""}),
            patch("app.personas._call_json_model", return_value={}) as call,
        ):
            personas.llm_policy_plan("서울 청년 월세 부담 정책을 검토해줘")
        self.assertNotIn("Published tables", call.call_args.args[0])


class PartialCoverageFlagTests(unittest.TestCase):
    def make(self, category, value, note=""):
        return {
            "label": category,
            "where": {"housing_type": category},
            "relation": "eq",
            "value": value,
            "mapping_note": note,
        }

    def test_partial_sum_gets_warning_note(self):
        candidates = [self.make("multi_family", 0.021), self.make("studio_or_other", 0.014)]
        personas._flag_partial_variable_coverage(candidates)
        for candidate in candidates:
            self.assertIn("부분 매핑 경고", candidate["mapping_note"])
            self.assertIn("0.04", candidate["mapping_note"])

    def test_full_coverage_stays_clean_and_duplicates_count_once(self):
        candidates = [
            self.make("multi_family", 0.40),
            self.make("multi_family", 0.38),  # 기간 중복 — 첫 값만 집계
            self.make("officetel", 0.35),
            self.make("studio_or_other", 0.25),
        ]
        personas._flag_partial_variable_coverage(candidates)
        for candidate in candidates:
            self.assertNotIn("부분 매핑 경고", candidate.get("mapping_note", ""))


class DecideNextEvidenceActionTests(unittest.TestCase):
    BASE = {
        "round": 1,
        "rounds_left": 2,
        "approved_count": 0,
        "candidate_count": 0,
        "broader_candidates": 0,
        "tried_kosis_queries": [],
        "tried_web_queries": ["청년 주거 통계"],
        "kosis_available": False,
        "policy_focus": "주거 안정",
        "target_population": "서울 청년",
    }

    def test_fallback_prefers_broader_then_kosis_then_search_then_stop(self):
        with patch("app.personas._call_json_model", side_effect=DomainError("LLM_NOT_CONFIGURED", "no llm")):
            broader = personas.decide_next_evidence_action({**self.BASE, "broader_candidates": 2})
            kosis = personas.decide_next_evidence_action({**self.BASE, "kosis_available": True})
            search = personas.decide_next_evidence_action(self.BASE)
            stop = personas.decide_next_evidence_action({**self.BASE, "round": 2})
        self.assertEqual(broader["action"], "approve_broader")
        self.assertEqual(kosis["action"], "kosis")
        self.assertEqual(search["action"], "search")
        self.assertEqual(stop["action"], "stop")

    def test_llm_decision_filters_tried_queries_and_caps_three(self):
        raw = {
            "action": "search",
            "queries": ["청년 주거 통계", "국토부 주거실태조사", "q2", "q3", "q4"],
            "reason": "새 출처가 필요합니다.",
        }
        with patch("app.personas._call_json_model", return_value=raw):
            decision = personas.decide_next_evidence_action(self.BASE)
        self.assertEqual(decision["action"], "search")
        self.assertNotIn("청년 주거 통계", decision["queries"])
        self.assertEqual(len(decision["queries"]), 3)

    def test_contract_violation_gets_one_repair_then_fallback(self):
        with patch(
            "app.personas._call_json_model",
            side_effect=[{"action": "nonsense"}, {"action": "still_bad"}],
        ) as calls:
            decision = personas.decide_next_evidence_action(self.BASE)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(decision["action"], "search")  # 결정론 폴백


if __name__ == "__main__":
    unittest.main()
