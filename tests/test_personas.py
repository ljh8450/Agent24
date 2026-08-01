import io
import json
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
