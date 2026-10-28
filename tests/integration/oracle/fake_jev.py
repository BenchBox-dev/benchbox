from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeJev:
    def __init__(self) -> None:
        self.answers: list[dict[str, Any] | None] = []
        self.requests: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    def start(self) -> FakeJev:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with fake._lock:
                    fake.requests.append(body)
                    answer = fake.answers.pop(0) if fake.answers else None
                if answer is None:
                    self._send(503, {"error": {"message": "the judgment service is down"}})
                else:
                    self._send(200, {"answers": {"recovery": answer}})

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def script(self, answers: list[dict[str, Any] | None]) -> None:
        with self._lock:
            self.answers = list(answers)
            self.requests = []

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
