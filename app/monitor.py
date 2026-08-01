from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

from .store import ProjectStore

APP_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.getenv("PERSONA_RESTORER_ROOT", APP_ROOT)).resolve()
STATIC = APP_ROOT / "monitor"
store = ProjectStore(ROOT)


def _json(status: int, value: Any) -> tuple[int, bytes, str]:
    return status, json.dumps(value, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8"


def _query(scope: dict[str, Any]) -> dict[str, str]:
    raw = scope.get("query_string", b"").decode()
    return {key: values[-1] for key, values in parse_qs(raw).items() if values}


def _loopback(scope: dict[str, Any]) -> bool:
    client = scope.get("client")
    return bool(client and client[0] in {"127.0.0.1", "::1"})


def _event_query(scope: dict[str, Any]) -> tuple[int, str | None, int]:
    query = _query(scope)
    try:
        after = max(0, int(query.get("after", "0")))
        limit = min(max(1, int(query.get("limit", "500"))), 1000)
    except ValueError as error:
        raise ValueError("after와 limit은 정수여야 합니다.") from error
    return after, query.get("run_id") or None, limit


async def _event_stream(scope: dict[str, Any], receive: Any, send: Any) -> None:
    after, run_id, _ = _event_query(scope)
    headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
    try:
        after = max(after, int(headers.get("last-event-id", "0")))
    except ValueError:
        pass
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache, no-transform"),
                (b"x-accel-buffering", b"no"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b": raw event stream\n\n", "more_body": True})
    try:
        while True:
            records = await asyncio.to_thread(store.list_event_records, after, run_id, 500)
            for record in records:
                after = int(record["id"])
                data = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                frame = f"id: {after}\nevent: raw.event\ndata: {data}\n\n".encode()
                await send({"type": "http.response.body", "body": frame, "more_body": True})
            try:
                message = await asyncio.wait_for(receive(), timeout=0.2)
                if message["type"] == "http.disconnect":
                    break
            except TimeoutError:
                pass
    finally:
        await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _dispatch(scope: dict[str, Any], receive: Any, send: Any) -> None:
    method = scope["method"].upper()
    path = unquote(scope["path"])
    if path.startswith("/api/") and not _loopback(scope):
        status, content, media_type = _json(401, {"error": "이 모니터는 loopback에서만 접근할 수 있습니다."})
    elif method == "GET" and path == "/api/health":
        status, content, media_type = _json(200, {"status": "ok", "root": str(ROOT)})
    elif method == "GET" and path == "/api/runs":
        runs = await asyncio.to_thread(store.list_runs, 30)
        status, content, media_type = _json(
            200,
            {
                "runs": [
                    {
                        "id": run["id"],
                        "question": run["question"],
                        "status": run["status"],
                        "updated_at": run["updated_at"],
                    }
                    for run in runs
                ]
            },
        )
    elif method == "GET" and path == "/api/events":
        after, run_id, limit = _event_query(scope)
        records = await asyncio.to_thread(store.list_event_records, after, run_id, limit)
        status, content, media_type = _json(200, {"events": records})
    elif method == "GET" and path == "/api/events/stream":
        await _event_stream(scope, receive, send)
        return
    elif method == "GET" and not path.startswith("/api/"):
        relative = "index.html" if path == "/" else path.removeprefix("/")
        candidate = (STATIC / relative).resolve()
        if STATIC not in candidate.parents or not candidate.is_file():
            status, content, media_type = _json(404, {"error": "페이지를 찾을 수 없습니다."})
        else:
            status = 200
            content = candidate.read_bytes()
            media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    else:
        status, content, media_type = _json(404, {"error": "경로를 찾을 수 없습니다."})
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", media_type.encode()), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": content})


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] != "http":
        return
    try:
        await _dispatch(scope, receive, send)
    except ValueError as error:
        status, content, media_type = _json(400, {"error": str(error)})
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", media_type.encode()), (b"cache-control", b"no-store")],
            }
        )
        await send({"type": "http.response.body", "body": content})
