#!/usr/bin/env python3
"""Deterministic Anthropic-compatible gateway for the M6 trace E2E probe.

This is intentionally a test double for the provider, not for Claude Code.
Claude Code 2.1.278 still runs as the real CLI and creates its real native
session and subagent files. The gateway only makes the model response
deterministic so the E2E can assert parent/child recovery without an external
model bill or network dependency.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode()


def _message_start(message_id: str, model: str) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 17, "output_tokens": 1},
        },
    }


def _text_response(message_id: str, model: str, text: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("message_start", _message_start(message_id, model)),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "M6 deterministic internal reasoning."},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "m6-e2e-signature"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"input_tokens": 17, "output_tokens": 9},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


def _tool_response(
    message_id: str,
    model: str,
    calls: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", _message_start(message_id, model)),
    ]
    for index, call in enumerate(calls):
        events.extend(
            [
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": {},
                        },
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(call["input"], ensure_ascii=False),
                        },
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": index}),
            ]
        )
    events.extend(
        [
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"input_tokens": 17, "output_tokens": 31},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
    )
    return events


def _has_tool_result(body: dict[str, Any]) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            if any(isinstance(item, dict) and item.get("type") == "tool_result" for item in content):
                return True
    return False


def _tool_names(body: dict[str, Any]) -> set[str]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return set()
    return {
        str(tool.get("name"))
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }


def _contains_prompt(body: dict[str, Any], marker: str) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and marker in content:
            return True
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and marker in str(item.get("text") or ""):
                    return True
    return False


class GatewayState:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.request_count = 0
        self.agent_tool_name: str | None = None

    def response(self, body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        with self.lock:
            self.request_count += 1
            count = self.request_count
            tool_names = _tool_names(body)
            if "Agent" in tool_names:
                self.agent_tool_name = "Agent"
            elif "Task" in tool_names:
                self.agent_tool_name = "Task"
            log = {
                "request": count,
                "path": "/v1/messages",
                "model": body.get("model"),
                "stream": body.get("stream"),
                "tool_names": sorted(tool_names),
                "has_tool_result": _has_tool_result(body),
                "message_count": len(body.get("messages") or []),
            }
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(log, ensure_ascii=False) + "\n")

        model = str(body.get("model") or "claude-sonnet-4-20250514")
        tool_name = self.agent_tool_name
        has_tool_result = _has_tool_result(body)
        if not has_tool_result and tool_name and _contains_prompt(
            body, "Launch exactly two Agent subagents"
        ):
            calls = []
            for suffix in ("alpha", "beta"):
                calls.append(
                    {
                        "id": f"toolu_m6_e2e_{suffix}",
                        "name": tool_name,
                        "input": {
                            "description": f"Inspect {suffix}",
                            "prompt": (
                                f"Return exactly M6-{suffix.upper()}-OK and do not use tools."
                            ),
                            "subagent_type": "general-purpose",
                        },
                    }
                )
            return _tool_response(f"msg_m6_root_{count}", model, calls)
        if has_tool_result and tool_name:
            return _text_response(
                f"msg_m6_root_{count}",
                model,
                "M6 root complete. M6-ALPHA-OK M6-BETA-OK "
                "<script>window.__m6_xss=1</script>",
            )
        return _text_response(
            f"msg_m6_child_{count}",
            model,
            "M6-ALPHA-OK M6-BETA-OK",
        )


class Handler(BaseHTTPRequestHandler):
    state: GatewayState

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return
        events = self.state.response(body)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        for event, payload in events:
            self.wfile.write(_sse(event, payload))
            self.wfile.flush()
            time.sleep(0.005)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path.rstrip("/") in ("", "/healthz"):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self.send_error(404)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()

    state = GatewayState(args.log)
    handler = type("M6GatewayHandler", (Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
