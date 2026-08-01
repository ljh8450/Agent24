from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import mimetypes
import os
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .avatars import notionists_svg
from .errors import DomainError
from .openapi import build_openapi_document
from .service import ResearchAgent

APP_ROOT = Path(__file__).resolve().parents[1]


def _load_local_env(path: Path) -> None:
    """Load project-local secrets without overriding an explicitly exported environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


_load_local_env(APP_ROOT / ".env")
ROOT = Path(os.getenv("PERSONA_RESTORER_ROOT", APP_ROOT)).resolve()
STATIC = APP_ROOT / "static"
agent = ResearchAgent(ROOT)
RUN_LOCKS: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

STREAM_TOOL_TEXT = {
    "policy.plan_request": "정책 대상, 핵심 가정, 권리 검토 기준을 설계했습니다.",
    "web.parallel_korean_policy_research": "한국 공공기관과 연구기관의 신뢰 출처를 병렬로 탐색하고 있습니다.",
    "source.fetch_snapshot": "찾은 원문을 내려받고 해시가 있는 증거 스냅샷으로 고정하고 있습니다.",
    "llm.extract_constraint_candidates": "원문의 모집단과 수치를 PGM 제약 후보로 대조하고 있습니다.",
    "review.auto_approve_exact_constraints": "모집단이 정확히 일치하는 제약만 코드 규칙으로 자동 승인하고 있습니다. 사람 검토가 아닙니다.",
    "statistics.identification_bounds": "승인된 근거의 식별구간과 결합분포를 계산하고 있습니다.",
    "personas.sample_joint_distribution": "결합분포에서 완전 합성 페르소나 패널을 구성하고 있습니다.",
    "llm.narrate_personas": "식별된 속성 범위 안에서 페르소나 설명을 생성하고 있습니다.",
    "policy.weighted_panel_interviews": "권리·법률 쟁점과 정책 대안을 같은 패널에서 검토하고 있습니다.",
    "report.write_provenance": "근거, 계산, 패널, 한계를 재현 가능한 보고서로 묶고 있습니다.",
}


def _json(status: int, value: Any) -> tuple[int, bytes, str]:
    return status, json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8"


def _sse(event: str, value: Any) -> bytes:
    data = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode()


async def _send_sse(send: Any, event: str, value: Any) -> None:
    await send({"type": "http.response.body", "body": _sse(event, value), "more_body": True})


async def _send_text_delta(send: Any, text: str) -> None:
    """Send small UTF-8-safe text chunks so the answer visibly grows in real time."""
    chunk_size = 12
    for start in range(0, len(text), chunk_size):
        await _send_sse(send, "message.delta", {"delta": text[start : start + chunk_size]})
        await asyncio.sleep(0.01)


async def _read_body(receive: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            return b"".join(chunks)


async def _payload(receive: Any) -> dict[str, Any]:
    raw = await _read_body(receive)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DomainError("INVALID_JSON", "요청 본문은 JSON이어야 합니다.") from error
    if not isinstance(value, dict):
        raise DomainError("INVALID_JSON", "요청 본문은 JSON 객체여야 합니다.")
    return value


async def _emit_tool_events(send: Any, events: list[dict[str, Any]]) -> None:
    for item in events:
        if not item.get("type", "").startswith("tool."):
            continue
        tool = str(item.get("payload", {}).get("tool", ""))
        await _send_sse(
            send,
            "tool.update",
            {
                "tool": tool,
                "status": item["type"].removeprefix("tool."),
                "text": STREAM_TOOL_TEXT.get(tool, "실행 메타데이터를 처리하고 있습니다."),
                "code": item.get("payload", {}).get("code"),
            },
        )
        if item["type"] == "tool.completed":
            text = STREAM_TOOL_TEXT.get(tool)
            if text:
                await _send_sse(send, "message.stream.start", {"phase": "tool"})
                await _send_text_delta(send, text)


async def _stream_agent_review(receive: Any, send: Any) -> None:
    """Run the autonomous workflow while emitting answer and tool-state SSE frames."""
    body = await _payload(receive)
    created = await asyncio.to_thread(
        agent.chat,
        str(body.get("text", "")),
        body.get("event_id"),
    )
    run_id = created["run"]["id"]
    seen_events = len(created["run"].get("events", []))
    headers = [
        (b"content-type", b"text/event-stream; charset=utf-8"),
        (b"cache-control", b"no-cache, no-transform"),
        (b"x-accel-buffering", b"no"),
        (b"access-control-allow-origin", b"*"),
    ]
    await send({"type": "http.response.start", "status": 201, "headers": headers})
    await _send_sse(send, "review.accepted", {"run": created["run"]})
    await _send_sse(send, "message.accepted", {"text": created["message"]})

    if created["run"].get("status") == "safety_blocked":
        completed = {
            **created,
            "research": {"results": [], "failed_queries": []},
            "artifacts": {},
        }
        await _send_sse(send, "review.completed", completed)
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        return

    task = asyncio.create_task(asyncio.to_thread(agent.continue_autonomous_review, run_id, created["message"]))
    try:
        while not task.done():
            current = await asyncio.to_thread(agent.store.get_run, run_id)
            events = current.get("events", [])
            new_events = events[seen_events:]
            await _emit_tool_events(send, new_events)
            if new_events:
                seen_events = len(events)
                await _send_sse(send, "run.updated", {"run": current})
            await asyncio.sleep(0.12)

        completed = await task
        final_run = completed["run"]
        await _emit_tool_events(send, final_run.get("events", [])[seen_events:])
        await _send_sse(send, "run.updated", {"run": final_run})
        await _send_sse(send, "research.completed", completed.get("research", {}))
        await _send_sse(send, "message.stream.start", {"phase": "final"})
        await _send_text_delta(send, completed["message"])
        await _send_sse(send, "review.completed", completed)
    except Exception as error:  # The response already started; report a structured stream error.
        if isinstance(error, DomainError):
            payload = {"code": error.code, "message": error.message, "details": error.details}
        else:
            payload = {
                "code": "INTERNAL_ERROR",
                "message": "정책 검토 스트림 처리 중 오류가 발생했습니다.",
                "details": {"type": type(error).__name__},
            }
        await _send_sse(send, "error", payload)
    finally:
        await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _run_action(run_id: str, tool_name: str, function: Any, *args: Any) -> Any:
    """One session lane per run: state mutations and artifacts cannot race."""
    async with RUN_LOCKS[run_id]:
        await asyncio.to_thread(agent.store.append_event, run_id, "tool.started", {"tool": tool_name})
        try:
            result = await asyncio.to_thread(function, run_id, *args)
        except DomainError as error:
            await asyncio.to_thread(
                agent.store.append_event, run_id, "tool.failed", {"tool": tool_name, "code": error.code}
            )
            raise
        await asyncio.to_thread(agent.store.append_event, run_id, "tool.completed", {"tool": tool_name})
        return result


def _api_authorized(scope: dict[str, Any]) -> bool:
    headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
    required_token = os.getenv("GATEWAY_TOKEN")
    if required_token:
        return hmac.compare_digest(headers.get("authorization", ""), f"Bearer {required_token}")
    client = scope.get("client")
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


async def _dispatch(method: str, path: str, receive: Any) -> tuple[int, bytes, str]:
    if method == "GET" and path == "/api/health":
        return _json(200, {"status": "ok", "root": str(ROOT)})
    if method == "POST" and path == "/api/chat":
        body = await _payload(receive)
        return _json(201, await asyncio.to_thread(agent.chat, str(body.get("text", "")), body.get("event_id")))
    if method == "POST" and path == "/api/agent/review":
        body = await _payload(receive)
        return _json(
            201,
            await asyncio.to_thread(
                agent.autonomous_review,
                str(body.get("text", "")),
                body.get("event_id"),
            ),
        )
    if method == "GET" and path.startswith("/api/avatars/notionists/") and path.endswith(".svg"):
        seed = path.removeprefix("/api/avatars/notionists/").removesuffix(".svg")
        return 200, await asyncio.to_thread(notionists_svg, ROOT, seed), "image/svg+xml; charset=utf-8"
    if not path.startswith("/api/runs/"):
        raise DomainError("NOT_FOUND", "API 경로를 찾을 수 없습니다.", status=404)
    tail = path.removeprefix("/api/runs/").split("/")
    run_id = tail[0]
    action = "/".join(tail[1:])
    if method == "GET" and not action:
        return _json(200, await asyncio.to_thread(agent.store.get_run, run_id))
    if method == "GET" and action.startswith("artifacts/"):
        filename = action.removeprefix("artifacts/")
        content = await asyncio.to_thread(agent.store.read_artifact, run_id, filename)
        media_type = (
            "text/html; charset=utf-8"
            if filename.endswith(".html")
            else "application/x-ndjson; charset=utf-8"
            if filename.endswith(".jsonl")
            else "application/json; charset=utf-8"
            if filename.endswith(".json")
            else "text/markdown; charset=utf-8"
        )
        return 200, content, media_type
    body = await _payload(receive) if method == "POST" else {}
    if method == "POST" and action == "sources/kosis":
        return _json(201, await _run_action(run_id, "kosis.statistics_openapi", agent.fetch_kosis, body))
    if method == "POST" and action == "sources/data-go":
        return _json(201, await _run_action(run_id, "data_go_kr.openapi", agent.fetch_data_go, body))
    raise DomainError("NOT_FOUND", "API 경로를 찾을 수 없습니다.", status=404)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] != "http":
        return
    method = scope["method"].upper()
    path = unquote(scope["path"])
    if method == "OPTIONS":
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-methods", b"GET,POST,OPTIONS"),
                    (b"access-control-allow-headers", b"content-type"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})
        return
    try:
        if method == "GET" and path == "/openapi.json":
            status, content, media_type = _json(200, build_openapi_document())
        elif path.startswith("/api/"):
            if not _api_authorized(scope):
                status, content, media_type = _json(
                    401,
                    {
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "Gateway API는 loopback 또는 GATEWAY_TOKEN 인증이 필요합니다.",
                        }
                    },
                )
            elif method == "POST" and path == "/api/agent/review/stream":
                await _stream_agent_review(receive, send)
                return
            else:
                status, content, media_type = await _dispatch(method, path, receive)
        elif method == "GET":
            relative = "index.html" if path == "/" else path.removeprefix("/")
            candidate = (STATIC / relative).resolve()
            if STATIC not in candidate.parents or not candidate.is_file():
                raise DomainError("NOT_FOUND", "페이지를 찾을 수 없습니다.", status=404)
            status, content, media_type = (
                200,
                candidate.read_bytes(),
                mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
            )
        else:
            raise DomainError("METHOD_NOT_ALLOWED", "지원하지 않는 HTTP method입니다.", status=405)
    except DomainError as error:
        status, content, media_type = _json(
            error.status, {"error": {"code": error.code, "message": error.message, "details": error.details}}
        )
    except Exception as error:  # Do not expose internals; operator can inspect the server log.
        status, content, media_type = _json(
            500,
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "내부 처리 오류가 발생했습니다.",
                    "details": {"type": type(error).__name__},
                }
            },
        )
    headers = [
        (b"content-type", media_type.encode()),
        (b"cache-control", b"no-store"),
        (b"access-control-allow-origin", b"*"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": content})
