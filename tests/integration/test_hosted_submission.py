from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from urllib.parse import urlparse

import pytest
from click.testing import CliRunner

import benchbox.cli.submit_auth as submit_auth
import benchbox.cli.submit_service as submit_service

submit_module = import_module("benchbox.cli.commands.submit")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture(autouse=True)
def _community_publish_salt(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "integration-test-community-publish-salt")


class FakeKeyring:
    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.passwords.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.passwords[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.passwords.pop((service_name, username), None)


class MockHostedState:
    def __init__(self) -> None:
        self.fail_first_post = False
        self.reject_stale_token = False
        self.post_count = 0
        self.get_count = 0
        self.auth_headers: list[str | None] = []
        self.idempotency_keys: list[str | None] = []
        self.request_bodies: list[bytes] = []
        self.published_by_key: dict[str, str] = {}


def _make_handler(state: MockHostedState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            state.post_count += 1
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            auth_header = self.headers.get("Authorization")
            idempotency_key = self.headers.get("Idempotency-Key")
            state.auth_headers.append(auth_header)
            state.idempotency_keys.append(idempotency_key)
            state.request_bodies.append(body)

            if state.fail_first_post and state.post_count == 1:
                self._send_json(503, {"message": "temporary"})
                return

            if state.reject_stale_token and auth_header == "Bearer stale-token":
                self._send_json(401, {"message": "bad token"})
                return

            if idempotency_key in state.published_by_key:
                self._send_json(
                    200,
                    {
                        "status": "already_published",
                        "submission_id": "sub1",
                        "public_result_id": state.published_by_key[idempotency_key],
                    },
                )
                return

            state.published_by_key[str(idempotency_key)] = "r1"
            self._send_json(202, {"submission_id": "sub1", "status": "pending"})

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.endswith("/submissions/sub1/status"):
                state.get_count += 1
                self._send_json(
                    200,
                    {
                        "submission_id": "sub1",
                        "status": "published",
                        "public_result_id": "r1",
                    },
                )
                return
            self._send_json(404, {"message": "not found"})

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@pytest.fixture
def hosted_service():
    state = MockHostedState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def result_file(tmp_path: Path) -> Path:

    path = tmp_path / "tpch_duckdb.json"
    path.write_text(
        json.dumps(
            {
                "version": "2.1",
                "benchmark": {"id": "tpch", "name": "tpch", "scale_factor": 0.01},
                "platform": {"name": "duckdb"},
                "run": {
                    "id": "run1",
                    "timestamp": "2026-05-01T12:00:00+00:00",
                    "total_duration_ms": 12340,
                },
                "summary": {
                    "queries": {"total": 22, "passed": 22, "failed": 0},
                    "validation": "passed",
                },
                "queries": [{"id": f"Q{i}", "ms": 100.0, "status": "SUCCESS"} for i in range(1, 23)],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fast_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    real_submit = submit_service.submit_hosted_bundle

    def submit_without_sleep(**kwargs):
        return real_submit(**kwargs, sleep=lambda _seconds: None, initial_backoff_seconds=0.0)

    monkeypatch.setattr(submit_module, "submit_hosted_bundle", submit_without_sleep)


def test_hosted_submission_success_polls_and_records_history(
    hosted_service, result_file: Path, monkeypatch: pytest.MonkeyPatch, fast_transport
) -> None:
    state, service_url = hosted_service
    monkeypatch.setenv("BENCHBOX_SUBMIT_TOKEN", "test-token")

    result = CliRunner().invoke(submit_module.submit, [str(result_file), "--service", service_url])

    assert result.exit_code == 0, result.output
    assert state.post_count == 1
    assert state.get_count == 1
    assert state.auth_headers == ["Bearer test-token"]
    assert b'name="bundle"; filename="tpch_duckdb.json"' in state.request_bodies[0]
    assert b'name="manifest"; filename="tpch_duckdb.manifest.json"' in state.request_bodies[0]
    assert "https://benchbox.dev/results/r/r1" in result.output

    history_files = list(result_file.parent.glob("*.submission.json"))
    assert len(history_files) == 1
    history = json.loads(history_files[0].read_text(encoding="utf-8"))
    assert history["public_url"] == "https://benchbox.dev/results/r/r1"
    assert "test-token" not in json.dumps(history)


def test_hosted_submission_retries_first_5xx_with_same_idempotency_key(
    hosted_service, result_file: Path, monkeypatch: pytest.MonkeyPatch, fast_transport
) -> None:
    state, service_url = hosted_service
    state.fail_first_post = True
    monkeypatch.setenv("BENCHBOX_SUBMIT_TOKEN", "test-token")

    result = CliRunner().invoke(submit_module.submit, [str(result_file), "--service", service_url])

    assert result.exit_code == 0, result.output
    assert state.post_count == 2
    assert state.idempotency_keys[0] == state.idempotency_keys[1]


def test_hosted_submission_401_prompts_refresh_and_retries(
    hosted_service, result_file: Path, monkeypatch: pytest.MonkeyPatch, fast_transport
) -> None:
    state, service_url = hosted_service
    state.reject_stale_token = True
    fake_keyring = FakeKeyring()
    username = submit_auth.normalize_service_url(service_url)
    fake_keyring.set_password(submit_auth.KEYRING_SERVICE_NAME, username, "stale-token")
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.delenv("BENCHBOX_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(submit_auth, "_load_keyring", lambda: fake_keyring)

    result = CliRunner().invoke(
        submit_module.submit,
        [str(result_file), "--service", service_url],
        input="fresh-token\n",
    )

    assert result.exit_code == 0, result.output
    assert state.auth_headers == ["Bearer stale-token", "Bearer fresh-token"]
    assert fake_keyring.get_password(submit_auth.KEYRING_SERVICE_NAME, username) == "fresh-token"
    assert "fresh-token" not in result.output


def test_hosted_submission_idempotent_resubmit_returns_existing_url(
    hosted_service, result_file: Path, monkeypatch: pytest.MonkeyPatch, fast_transport
) -> None:
    state, service_url = hosted_service
    monkeypatch.setenv("BENCHBOX_SUBMIT_TOKEN", "test-token")
    runner = CliRunner()

    first = runner.invoke(submit_module.submit, [str(result_file), "--service", service_url])
    second = runner.invoke(submit_module.submit, [str(result_file), "--service", service_url])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert state.post_count == 2
    assert state.idempotency_keys[0] == state.idempotency_keys[1]
    assert "already_published" in second.output
