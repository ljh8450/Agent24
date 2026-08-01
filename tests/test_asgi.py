import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import asgi
from app.service import ResearchAgent


async def request(method, path, payload=None, client=("127.0.0.1", 41234)):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    received = False
    messages = []

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await asgi.app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json")],
            "client": client,
        },
        receive,
        send,
    )
    start, response = messages
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], headers, response["body"]


async def stream_request(method, path, payload=None, client=("127.0.0.1", 41234)):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    received = False
    messages = []

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await asgi.app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json"), (b"accept", b"text/event-stream")],
            "client": client,
        },
        receive,
        send,
    )
    start = messages[0]
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    content = b"".join(message.get("body", b"") for message in messages[1:])
    return start["status"], headers, content, messages


class GatewayEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_agent = asgi.agent
        asgi.agent = ResearchAgent(Path(self.temp.name))
        asgi.RUN_LOCKS.clear()

    def tearDown(self):
        asgi.agent = self.original_agent
        asgi.RUN_LOCKS.clear()
        self.temp.cleanup()

    def call(self, method, path, payload=None, client=("127.0.0.1", 41234)):
        status, headers, body = asyncio.run(request(method, path, payload, client))
        return status, headers, json.loads(body.decode("utf-8"))

    def test_gateway_auth_and_core_endpoints(self):
        status, _, health = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        status, _, blocked = self.call("GET", "/api/health", client=("203.0.113.10", 41234))
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["code"], "UNAUTHORIZED")
        status, cors_headers, _ = asyncio.run(request("OPTIONS", "/api/health"))
        self.assertEqual(status, 204)
        self.assertEqual(cors_headers["access-control-allow-methods"], "GET,POST,OPTIONS")

        status, _, started = self.call("POST", "/api/chat", {"text": "대전 성인 대학생의 정책 관심을 조사해줘"})
        self.assertEqual(status, 201)
        run_id = started["run"]["id"]
        self.assertIn("agent.intake_question", [event["payload"].get("tool") for event in started["run"]["events"]])
        status, _, retrieved = self.call("GET", f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(retrieved["id"], run_id)
        # 수동 연구 엔드포인트는 제거됨: 남은 API는 kosis/data-go 커넥터뿐이다.
        for removed in ("compute", "personas", "survey", "holdout/seal", "policy/plan", "report"):
            status, _, response = self.call("POST", f"/api/runs/{run_id}/{removed}", {})
            self.assertEqual(status, 404, removed)
            self.assertEqual(response["error"]["code"], "NOT_FOUND")

    def test_kosis_route_fails_closed_without_configured_key(self):
        status, _, started = self.call("POST", "/api/chat", {"text": "대전 인구 통계를 KOSIS로 조사해줘"})
        with patch.dict("os.environ", {}, clear=True):
            status, _, response = self.call(
                "POST", f"/api/runs/{started['run']['id']}/sources/kosis", {"user_stats_id": "sample"}
            )
            self.assertEqual(status, 400)
            self.assertEqual(response["error"]["code"], "KOSIS_KEY_REQUIRED")
            status, _, response = self.call(
                "POST", f"/api/runs/{started['run']['id']}/sources/data-go", {"service": "welfare_list"}
            )
            self.assertEqual(status, 400)
            self.assertEqual(response["error"]["code"], "DATA_GO_KEY_REQUIRED")

    def test_single_agent_review_completes_without_manual_inputs_or_quantitative_sources(self):
        with (
            patch("app.service.search_public_web", return_value=[]),
            patch.dict(
                "os.environ",
                {
                    "LLM_API_URL": "",
                    "LLM_API_KEY": "",
                    "LLM_MODEL": "",
                    "PERSONA_RESTORER_DEMO_MODEL": "0",
                },
            ),
        ):
            status, _, completed = self.call(
                "POST",
                "/api/agent/review",
                {"text": "수원에 사는 대학생들에게 투표권 소유 자격을 10살 올리고 싶어"},
            )

        self.assertEqual(status, 201)
        run = completed["run"]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["target_population"], "대한민국 수원, 대학 재학생")
        self.assertEqual(run["result"]["status"], "scenario_only")
        self.assertEqual(run["result"]["identification"]["lower"], 0.0)
        self.assertEqual(run["result"]["identification"]["upper"], 1.0)
        self.assertEqual(run["result"]["policy_review"]["status"], "COMPLETED_WITHOUT_LLM_INTERVIEWS")
        self.assertEqual(run["result"]["policy_review"]["panel"][0]["avatar"]["style"], "notionists")
        plan = next(event["payload"]["plan"] for event in run["events"] if event["type"] == "policy.plan_created")
        self.assertEqual(plan["rights_review"]["severity"], "high")
        self.assertNotIn("review.required", [event["type"] for event in run["events"]])
        tools = [event["payload"].get("tool") for event in run["events"] if event["type"] == "tool.completed"]
        self.assertIn("report.write_provenance", tools)
        self.assertEqual(
            set(completed["artifacts"]),
            {"html_report", "panel", "interviews", "evidence"},
        )
        for url in completed["artifacts"].values():
            artifact_status, _, artifact = asyncio.run(request("GET", url))
            self.assertEqual(artifact_status, 200)
            self.assertTrue(artifact)

    def test_notionists_avatar_is_served_from_same_origin(self):
        seed = "f3640270d85579d395da5591"
        fixture = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
        with patch("app.asgi.notionists_svg", return_value=fixture) as loader:
            status, headers, body = asyncio.run(request("GET", f"/api/avatars/notionists/{seed}.svg"))
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(body, fixture)
        loader.assert_called_once_with(asgi.ROOT, seed)

    def test_openapi_spec_and_swagger_ui_are_served(self):
        status, headers, spec = self.call("GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertIn("/api/health", spec["paths"])
        self.assertIn("ConstraintRequest", spec["components"]["schemas"])
        self.assertIn("/api/runs/{run_id}/sources/{source_id}/extract", spec["paths"])

        status, headers, body = asyncio.run(request("GET", "/swagger-ui/index.html"))
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/html")
        self.assertIn(b"SwaggerUIBundle", body)

    def test_agent_review_stream_emits_live_text_tool_updates_and_completion(self):
        with (
            patch("app.service.search_public_web", return_value=[]),
            patch.dict(
                "os.environ",
                {
                    "LLM_API_URL": "",
                    "LLM_API_KEY": "",
                    "LLM_MODEL": "",
                    "PERSONA_RESTORER_DEMO_MODEL": "0",
                },
            ),
        ):
            status, headers, body, messages = asyncio.run(
                stream_request(
                    "POST",
                    "/api/agent/review/stream",
                    {"text": "수원 대학생의 정책 참여 제도를 검토해줘"},
                )
            )

        stream = body.decode("utf-8")
        self.assertEqual(status, 201)
        self.assertEqual(headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertEqual(headers["x-accel-buffering"], "no")
        self.assertIn("event: review.accepted", stream)
        self.assertIn("event: message.accepted", stream)
        self.assertIn("event: message.stream.start", stream)
        self.assertGreater(stream.count("event: message.delta"), 5)
        self.assertIn("event: tool.update", stream)
        self.assertIn("event: run.updated", stream)
        self.assertIn("event: research.completed", stream)
        self.assertIn("event: review.completed", stream)
        self.assertGreater(len(messages), 8)
        self.assertFalse(messages[-1].get("more_body", False))


if __name__ == "__main__":
    unittest.main()
