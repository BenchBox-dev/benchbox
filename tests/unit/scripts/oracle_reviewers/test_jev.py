from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from _project.scripts.oracle_reviewers.jev import JevError, JevSession

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Server:
    def __init__(self) -> None:
        self.responses: list[tuple[int, bytes]] = []
        self.requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []


@pytest.fixture
def server() -> Iterator[tuple[_Server, str]]:
    state = _Server()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            state.requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(length))))
            status, body = state.responses.pop(0)
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return None

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _ok(question: str, answer: dict[str, Any]) -> tuple[int, bytes]:
    return 200, json.dumps({"model": "jev-1.14.0", "answers": {question: answer}, "usage": {}}).encode()


def test_ask_posts_the_judgment_and_returns_answers(server: tuple[_Server, str]) -> None:
    state, url = server
    state.responses.append(_ok("size", {"choice": "large"}))
    with JevSession(base_url=url, api_key="k-test") as session:
        assert session.ask({"text": "x"}, {"size": {"type": "choice"}}) == {"size": {"choice": "large"}}
    path, headers, body = state.requests[0]
    assert path == "/v1/systemone"
    assert headers["Authorization"] == "Bearer k-test"
    assert body == {"model": "jev-1.14.0", "state": {"text": "x"}, "questions": {"size": {"type": "choice"}}}


def test_non_200_raises_with_status_and_message(server: tuple[_Server, str]) -> None:
    state, url = server
    state.responses.append((429, json.dumps({"error": {"message": "slow down"}}).encode()))
    with JevSession(base_url=url, api_key="k") as session, pytest.raises(JevError) as excinfo:
        session.ask({}, {})
    assert excinfo.value.status == 429
    assert excinfo.value.message == "slow down"


@pytest.mark.parametrize("body", [b"not json", b'{"model": "jev"}'])
def test_unusable_response_raises(server: tuple[_Server, str], body: bytes) -> None:
    state, url = server
    state.responses.append((200, body))
    with JevSession(base_url=url, api_key="k") as session, pytest.raises(JevError):
        session.ask({}, {})


def test_missing_key_raises_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(JevError, match="TYPESAFE_API_KEY"), JevSession():
        pass


def test_unreachable_service_raises() -> None:
    session = JevSession(base_url="http://127.0.0.1:9", api_key="k", timeout_seconds=5)
    with session, pytest.raises(JevError, match="unreachable"):
        session.ask({}, {})


def test_oversize_request_raises_before_sending() -> None:
    session = JevSession(base_url="http://127.0.0.1:9", api_key="k")
    with pytest.raises(JevError, match="32 MiB"):
        session.ask("x" * (33 * 1024 * 1024), {})
