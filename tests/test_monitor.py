import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app import monitor
from app.store import ProjectStore


async def request(path, client=("127.0.0.1", 41234)):
    received = False
    messages = []

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await monitor.app(
        {
            "type": "http",
            "method": "GET",
            "path": path.split("?", 1)[0],
            "query_string": path.partition("?")[2].encode(),
            "headers": [],
            "client": client,
        },
        receive,
        send,
    )
    start = messages[0]
    body = b"".join(message.get("body", b"") for message in messages[1:])
    return start["status"], body


class RawEventMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_store = monitor.store
        monitor.store = ProjectStore(Path(self.temp.name))
        run = {
            "id": "run_monitor",
            "session_key": "monitor:test",
            "question": "실시간 이벤트 동기화 테스트",
            "target_population": "테스트 모집단",
            "status": "running",
            "created_at": "2026-08-02T00:00:00+00:00",
            "updated_at": "2026-08-02T00:00:00+00:00",
        }
        monitor.store.create_run(run)
        monitor.store.append_event("run_monitor", "tool.started", {"tool": "web.search"})
        monitor.store.append_event("run_monitor", "tool.completed", {"tool": "web.search", "stored": 2})

    def tearDown(self):
        monitor.store = self.original_store
        self.temp.cleanup()

    def test_event_api_returns_exact_rows_in_cursor_order(self):
        status, body = asyncio.run(request("/api/events?run_id=run_monitor&after=1"))
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual([event["id"] for event in payload["events"]], [2, 3])
        self.assertEqual(payload["events"][0]["type"], "tool.started")
        self.assertEqual(payload["events"][0]["payload"], {"tool": "web.search"})
        self.assertEqual(payload["events"][1]["type"], "tool.completed")

    def test_monitor_page_is_served_but_api_is_loopback_only(self):
        status, body = asyncio.run(request("/"))
        self.assertEqual(status, 200)
        self.assertIn(b"Raw event monitor", body)

        status, body = asyncio.run(request("/api/events", client=("203.0.113.5", 41234)))
        self.assertEqual(status, 401)
        self.assertIn("loopback", body.decode())


if __name__ == "__main__":
    unittest.main()
