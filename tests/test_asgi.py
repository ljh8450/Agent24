import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import asgi
from app.contracts import Source
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

    def add_fixture_source(self, run_id):
        snapshot = Path(self.temp.name) / "data/source-cache/fixture.txt"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text("대전 성인 대학생 정책 관심도 조사 원문", encoding="utf-8")
        asgi.agent.store.add_source(
            run_id,
            Source(
                "src_api_fixture",
                "https://kosis.kr/table",
                "fixture table",
                "KOSIS",
                "fixture survey",
                "2025",
                "2025",
                "adult students",
                1000,
                "fixture-hash",
                "data/source-cache/fixture.txt",
                trust_tier="korean_official",
                source_kind="official_statistics_or_policy",
                source_domain="kosis.kr",
            ).as_dict(),
        )

    def test_gateway_auth_catalog_and_full_endpoint_flow(self):
        status, _, health = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        status, _, blocked = self.call("GET", "/api/health", client=("203.0.113.10", 41234))
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["code"], "UNAUTHORIZED")
        status, cors_headers, _ = asyncio.run(request("OPTIONS", "/api/health"))
        self.assertEqual(status, 204)
        self.assertEqual(cors_headers["access-control-allow-methods"], "GET,POST,OPTIONS")
        status, _, catalog = self.call("GET", "/api/source-catalog")
        self.assertEqual(status, 200)
        self.assertIn("KOSIS 국가통계포털", [item["label"] for item in catalog["sources"]])

        status, _, started = self.call("POST", "/api/chat", {"text": "대전 성인 대학생의 정책 관심을 조사해줘"})
        self.assertEqual(status, 201)
        run_id = started["run"]["id"]
        self.assertIn("agent.intake_question", [event["payload"].get("tool") for event in started["run"]["events"]])
        status, _, retrieved = self.call("GET", f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(retrieved["id"], run_id)
        status, _, planned = self.call("POST", f"/api/runs/{run_id}/policy/plan", {})
        self.assertEqual(status, 200)
        self.assertIn("alternatives", planned["plan"])
        with patch(
            "app.service.search_public_web",
            return_value=[
                {
                    "title": "KOSIS 정책 후보",
                    "url": "https://kosis.kr/policy",
                    "domain": "kosis.kr",
                    "trust_tier": "korean_official",
                    "source_kind": "official_statistics_or_policy",
                }
            ],
        ):
            status, _, autonomous_search = self.call("POST", f"/api/runs/{run_id}/policy/research", {})
        self.assertEqual(status, 200)
        self.assertEqual(autonomous_search["results"][0]["domain"], "kosis.kr")

        schema = {
            "variables": [
                {"id": "region", "categories": ["daejeon", "other"]},
                {"id": "interest", "categories": ["high", "low"]},
            ]
        }
        status, _, schema_result = self.call("POST", f"/api/runs/{run_id}/schema", schema)
        self.assertEqual(status, 200)
        self.assertEqual(len(schema_result["variables"]), 2)
        with patch(
            "app.service.search_public_web",
            return_value=[
                {
                    "title": "KOSIS 후보",
                    "url": "https://kosis.kr/index/index.do",
                    "domain": "kosis.kr",
                    "trust_tier": "korean_official",
                    "source_kind": "official_statistics_or_policy",
                }
            ],
        ):
            status, _, source_search = self.call(
                "POST",
                f"/api/runs/{run_id}/sources/search",
                {"query": "대전 학생 통계", "trusted_korean_only": True},
            )
        self.assertEqual(status, 200)
        self.assertEqual(source_search["results"][0]["domain"], "kosis.kr")
        self.add_fixture_source(run_id)
        with patch(
            "app.service.fetch_source",
            return_value=Source(
                "src_fetch_fixture",
                "https://kosis.kr/fetch",
                "fetched fixture",
                "KOSIS",
                "fetched survey",
                "2025",
                "2025",
                "adult students",
                10,
                "fetched-hash",
                "data/source-cache/fetched.txt",
            ),
        ):
            status, _, fetched = self.call(
                "POST",
                f"/api/runs/{run_id}/sources/fetch",
                {"url": "https://kosis.kr/fetch", "metadata": {}},
            )
        self.assertEqual(status, 201)
        self.assertEqual(fetched["id"], "src_fetch_fixture")
        with patch("app.personas._configured_llm", return_value=None):
            status, _, extraction_error = self.call("POST", f"/api/runs/{run_id}/sources/src_api_fixture/extract", {})
        self.assertEqual(status, 400)
        self.assertEqual(extraction_error["error"]["code"], "LLM_NOT_CONFIGURED")

        for identifier, where, value in (
            ("region", {"region": "daejeon"}, 0.2),
            ("interest", {"interest": "high"}, 0.5),
        ):
            status, _, _ = self.call(
                "POST",
                f"/api/runs/{run_id}/constraints",
                {
                    "id": identifier,
                    "source_id": "src_api_fixture",
                    "label": identifier,
                    "where": where,
                    "relation": "eq",
                    "value": value,
                    "population_compatibility": "exact",
                    "raw_statement": "fixture",
                },
            )
            self.assertEqual(status, 201)
        status, _, _ = self.call(
            "POST",
            f"/api/runs/{run_id}/constraints/approve",
            {"constraint_ids": ["region", "interest"], "override_notes": {}},
        )
        self.assertEqual(status, 200)
        status, _, computed = self.call(
            "POST",
            f"/api/runs/{run_id}/compute",
            {"estimand": {"numerator": {"interest": "high"}, "denominator": {"region": "daejeon"}}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(computed["result"]["status"], "feasible")
        self.assertIn(
            "statistics.identification_bounds",
            [event["payload"].get("tool") for event in computed["events"]],
        )
        status, _, sampled = self.call(
            "POST",
            f"/api/runs/{run_id}/personas",
            {"adult_population_confirmed": True, "count": 3, "seed": 7},
        )
        self.assertEqual(status, 200)
        personas = sampled["result"]["personas"]["items"]
        self.assertEqual(len(personas), 3)
        status, _, persona_answer = self.call(
            "POST",
            f"/api/runs/{run_id}/persona-chat",
            {
                "persona_id": personas[0]["id"],
                "question": "어느 지역에 속하나요?",
                "allowed_variable": "region",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(persona_answer["status"], "answered_sampled_attribute")
        with patch.dict("os.environ", {"PERSONA_RESTORER_DEMO_MODEL": "1"}):
            status, _, narrated = self.call("POST", f"/api/runs/{run_id}/narratives", {})
        self.assertEqual(status, 200)
        self.assertEqual(len(narrated["result"]["personas"]["narratives"]), 3)
        with patch.dict("os.environ", {"PERSONA_RESTORER_DEMO_MODEL": "1"}):
            status, _, panel_review = self.call("POST", f"/api/runs/{run_id}/policy/panel-review", {})
        self.assertEqual(status, 200)
        self.assertEqual(len(panel_review["result"]["policy_review"]["alternatives"]), 3)
        with patch.dict("os.environ", {"PERSONA_RESTORER_DEMO_MODEL": "1"}):
            status, _, surveyed = self.call(
                "POST", f"/api/runs/{run_id}/survey", {"policy_question": "청년 주거 지원을 확대해야 하나요?"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(surveyed["result"]["survey"]["mode"], "deterministic_demo")
        status, _, sealed = self.call("POST", f"/api/runs/{run_id}/holdout/seal", {})
        self.assertEqual(status, 200)
        status, _, evaluated = self.call(
            "POST",
            f"/api/runs/{run_id}/holdout/evaluate",
            {"actual_distribution": sealed["result"]["distribution"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(evaluated["result"]["holdout"]["evaluation"]["tv_distance"], 0.0)

        status, _, report = self.call("POST", f"/api/runs/{run_id}/report", {})
        self.assertEqual(status, 200)
        self.assertEqual(report["run"]["status"], "completed")
        status, headers, markdown_report = asyncio.run(request("GET", report["report_url"]))
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/markdown; charset=utf-8")
        self.assertIn(b"#", markdown_report)
        status, markdown_headers, markdown = asyncio.run(request("GET", f"/api/runs/{run_id}/artifacts/report.md"))
        self.assertEqual(status, 200)
        self.assertEqual(markdown_headers["content-type"], "text/markdown; charset=utf-8")
        self.assertIn(b"#", markdown)
        status, json_headers, manifest = asyncio.run(request("GET", f"/api/runs/{run_id}/artifacts/run.json"))
        self.assertEqual(status, 200)
        self.assertEqual(json_headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(manifest)["id"], run_id)
        self.assertEqual(set(report["downloads"]), {"panel", "interviews"})
        for url in report["downloads"].values():
            status, _, artifact = asyncio.run(request("GET", url))
            self.assertEqual(status, 200)
            self.assertTrue(artifact)

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
            {"report", "panel", "interviews"},
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
        self.assertEqual(spec["components"]["schemas"]["PersonasRequest"]["properties"]["count"]["maximum"], 3)
        self.assertIn("ConstraintRequest", spec["paths"]["/api/runs/{run_id}/constraints"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"])
        self.assertIn("/api/runs/{run_id}/sources/{source_id}/extract", spec["paths"])
        self.assertIn("bearerAuth", spec["components"]["securitySchemes"])

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
