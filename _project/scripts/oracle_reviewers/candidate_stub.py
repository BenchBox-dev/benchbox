from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

LOOPBACK = "127.0.0.1"
EVENT_STREAM = "text/event-stream"
JSON_BODY = "application/json"
STUB_MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True)
class UpstreamTurn:
    status: int
    body: bytes
    content_type: str = EVENT_STREAM


def _events(events: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode("utf-8")


def _message_start() -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_stub",
            "type": "message",
            "role": "assistant",
            "model": STUB_MODEL,
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
    }


def _finish(reason: str) -> list[dict[str, Any]]:
    return [
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason, "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]


def text_turn(text: str) -> UpstreamTurn:
    events = [
        _message_start(),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        *_finish("end_turn"),
    ]
    return UpstreamTurn(200, _events(events))


def tool_turn(name: str, arguments: Mapping[str, Any], call_id: str) -> UpstreamTurn:
    events = [
        _message_start(),
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)},
        },
        *_finish("tool_use"),
    ]
    return UpstreamTurn(200, _events(events))


def error_turn(status: int, kind: str, message: str) -> UpstreamTurn:
    body = json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode("utf-8")
    return UpstreamTurn(status, body, JSON_BODY)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


class _Listener:
    def __init__(self) -> None:
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def _handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        raise NotImplementedError

    @property
    def origin(self) -> str:
        assert self._server is not None
        return f"http://{LOOPBACK}:{self._server.server_port}"

    def __enter__(self) -> Self:
        self._server = _Server((LOOPBACK, 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._server is not None and self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)


class UpstreamStub(_Listener):
    def __init__(self, script: Sequence[UpstreamTurn] = ()) -> None:
        super().__init__()
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return self.origin

    def load(self, script: Sequence[UpstreamTurn]) -> None:
        with self._lock:
            self.script = list(script)
            self.requests.clear()

    def _next(self, request: dict[str, Any]) -> UpstreamTurn:
        with self._lock:
            self.requests.append(request)
            return self.script[min(len(self.requests), len(self.script)) - 1]

    def _handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    body = {}
                turn = stub._next({"path": self.path, "body": body})
                self.send_response(turn.status)
                self.send_header("content-type", turn.content_type)
                self.end_headers()
                self.wfile.write(turn.body)

            def log_message(self, *args: Any) -> None:
                return

        return Handler


class CanaryServer(_Listener):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[str] = []

    @property
    def url(self) -> str:
        return f"{self.origin}/canary"

    def _handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        canary = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                canary.hits.append(self.path)
                self.send_response(200)
                self.send_header("content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"canary page body")

            def log_message(self, *args: Any) -> None:
                return

        return Handler
