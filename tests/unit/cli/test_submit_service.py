from __future__ import annotations

import json
from pathlib import Path
from urllib import error

import pytest

from benchbox.cli.submit_service import (
    HostedSubmitError,
    HostedSubmitUnauthorized,
    make_idempotency_key,
    submit_hosted_bundle,
)
from benchbox.core.results.canonical_json import canonical_json_text

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class FakeResponse:
    def __init__(
        self,
        status: int,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> None:
        self._status = status
        self._body = raw_body if raw_body is not None else json.dumps(payload or {}).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class FakeOpener:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, *, timeout: float):
        self.requests.append(req)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _bundle(tmp_path: Path) -> tuple[Path, list[Path]]:
    source = tmp_path / "tpch_duckdb.json"
    source.write_text('{"schema_version": "2.0"}', encoding="utf-8")
    companion = tmp_path / "tpch_duckdb.plans.json"
    companion.write_text('{"plans": []}', encoding="utf-8")
    return source, [companion]


def _manifest() -> dict:
    return {
        "bundle_file": "tpch_duckdb.json",
        "bundle_hash": "a" * 64,
        "submission_path": "hosted-service",
    }


def test_make_idempotency_key_is_stable_per_service_and_bundle() -> None:
    key_a = make_idempotency_key("HTTPS://API.BENCHBOX.DEV/v1/", "a" * 64)
    key_b = make_idempotency_key("https://api.benchbox.dev/v1", "a" * 64)
    key_c = make_idempotency_key("https://api.benchbox.dev/v1", "b" * 64)

    assert key_a == key_b
    assert key_a != key_c


def test_submit_hosted_bundle_posts_multipart_auth_and_idempotency(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    opener = FakeOpener(FakeResponse(200, {"status": "already_published", "public_result_id": "r1"}))

    result = submit_hosted_bundle(
        service_url="https://api.benchbox.dev/v1",
        token="secret-token",
        source_path=source,
        companions=companions,
        manifest=_manifest(),
        bundle_hash="a" * 64,
        visibility="public",
        idempotency_key="fixed-key",
        wait=True,
        opener=opener,
        sleep=lambda _seconds: None,
    )

    assert result.status == "already_published"
    assert result.public_url == "https://benchbox.dev/results/r/r1"
    req = opener.requests[0]
    headers = {key.lower(): value for key, value in req.header_items()}
    assert req.full_url == "https://api.benchbox.dev/v1/submissions"
    assert headers["authorization"] == "Bearer secret-token"
    assert headers["idempotency-key"] == "fixed-key"
    assert b'name="bundle"; filename="tpch_duckdb.json"' in req.data
    assert b'name="manifest"; filename="tpch_duckdb.manifest.json"' in req.data
    assert b'name="companions[]"; filename="tpch_duckdb.plans.json"' in req.data
    assert b"secret-token" not in req.data


def test_submit_hosted_bundle_writes_canonical_manifest_part(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    manifest = {
        "submitted_by": "José",
        "submission_path": "hosted-service",
        "bundle_hash": "a" * 64,
        "bundle_file": "tpch_duckdb.json",
    }
    opener = FakeOpener(FakeResponse(200, {"status": "already_published", "public_result_id": "r1"}))

    submit_hosted_bundle(
        service_url="https://api.benchbox.dev/v1",
        token="secret-token",
        source_path=source,
        companions=companions,
        manifest=manifest,
        bundle_hash="a" * 64,
        visibility="public",
        idempotency_key="fixed-key",
        wait=True,
        opener=opener,
        sleep=lambda _seconds: None,
    )

    manifest_part = canonical_json_text(manifest).encode("utf-8")
    req = opener.requests[0]
    assert b'name="manifest"; filename="tpch_duckdb.manifest.json"' in req.data
    assert manifest_part in req.data
    assert b"Jos\\u00e9" not in req.data
    assert req.data.index(b'"bundle_file"') < req.data.index(b'"submitted_by"')


def test_submit_hosted_bundle_retries_upload_and_polls_to_publication(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    sleeps: list[float] = []
    opener = FakeOpener(
        FakeResponse(503, {"message": "temporary"}),
        FakeResponse(202, {"submission_id": "sub1", "status": "pending"}),
        FakeResponse(200, {"submission_id": "sub1", "status": "validated"}),
        FakeResponse(200, {"submission_id": "sub1", "status": "published", "public_result_id": "r1"}),
    )

    result = submit_hosted_bundle(
        service_url="https://api.benchbox.dev/v1",
        token="secret-token",
        source_path=source,
        companions=companions,
        manifest=_manifest(),
        bundle_hash="a" * 64,
        visibility="public",
        idempotency_key=None,
        wait=True,
        opener=opener,
        sleep=sleeps.append,
    )

    assert result.status == "published"
    assert result.submission_id == "sub1"
    assert result.status_history == ("pending", "validated", "published")
    assert [req.get_method() for req in opener.requests] == ["POST", "POST", "GET", "GET"]
    assert sleeps[:2] == [5.0, 5.0]


def test_submit_hosted_bundle_retries_network_errors(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    opener = FakeOpener(
        error.URLError("temporary outage"),
        FakeResponse(200, {"status": "already_published", "public_result_id": "r1"}),
    )

    result = submit_hosted_bundle(
        service_url="https://api.benchbox.dev/v1",
        token="secret-token",
        source_path=source,
        companions=companions,
        manifest=_manifest(),
        bundle_hash="a" * 64,
        visibility="public",
        idempotency_key=None,
        wait=True,
        opener=opener,
        sleep=lambda _seconds: None,
    )

    assert result.public_result_id == "r1"
    assert len(opener.requests) == 2


def test_submit_hosted_bundle_no_wait_returns_pending_without_poll(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    opener = FakeOpener(FakeResponse(202, {"submission_id": "sub1", "status": "pending"}))

    result = submit_hosted_bundle(
        service_url="https://api.benchbox.dev/v1",
        token="secret-token",
        source_path=source,
        companions=companions,
        manifest=_manifest(),
        bundle_hash="a" * 64,
        visibility="unlisted",
        idempotency_key=None,
        wait=False,
        opener=opener,
        sleep=lambda _seconds: None,
    )

    assert result.status == "pending"
    assert result.submission_id == "sub1"
    assert len(opener.requests) == 1


def test_submit_hosted_bundle_unauthorized_raises(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    opener = FakeOpener(FakeResponse(401, raw_body=b"<html>bad token</html>"))

    with pytest.raises(HostedSubmitUnauthorized):
        submit_hosted_bundle(
            service_url="https://api.benchbox.dev/v1",
            token="secret-token",
            source_path=source,
            companions=companions,
            manifest=_manifest(),
            bundle_hash="a" * 64,
            visibility="private",
            idempotency_key=None,
            wait=True,
            opener=opener,
            sleep=lambda _seconds: None,
        )


def test_submit_hosted_bundle_last_5xx_does_not_require_json_body(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    opener = FakeOpener(FakeResponse(503, raw_body=b"<html>temporary</html>"))

    with pytest.raises(HostedSubmitError, match="HTTP 503"):
        submit_hosted_bundle(
            service_url="https://api.benchbox.dev/v1",
            token="secret-token",
            source_path=source,
            companions=companions,
            manifest=_manifest(),
            bundle_hash="a" * 64,
            visibility="private",
            idempotency_key=None,
            wait=True,
            opener=opener,
            sleep=lambda _seconds: None,
            upload_attempts=1,
        )


def test_submit_hosted_bundle_rate_limit_with_non_json_body_reports_retry_after(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    opener = FakeOpener(FakeResponse(429, headers={"Retry-After": "30"}, raw_body=b"<html>slow down</html>"))

    with pytest.raises(HostedSubmitError, match="rate limited.*30"):
        submit_hosted_bundle(
            service_url="https://api.benchbox.dev/v1",
            token="secret-token",
            source_path=source,
            companions=companions,
            manifest=_manifest(),
            bundle_hash="a" * 64,
            visibility="private",
            idempotency_key=None,
            wait=True,
            opener=opener,
            sleep=lambda _seconds: None,
            upload_attempts=1,
        )


def test_submit_hosted_bundle_validation_error_with_non_json_body_reports_422(tmp_path: Path) -> None:
    source, companions = _bundle(tmp_path)
    opener = FakeOpener(FakeResponse(422, raw_body=b"<html>bad bundle</html>"))

    with pytest.raises(HostedSubmitError, match="validation failed"):
        submit_hosted_bundle(
            service_url="https://api.benchbox.dev/v1",
            token="secret-token",
            source_path=source,
            companions=companions,
            manifest=_manifest(),
            bundle_hash="a" * 64,
            visibility="private",
            idempotency_key=None,
            wait=True,
            opener=opener,
            sleep=lambda _seconds: None,
            upload_attempts=1,
        )


def test_submit_hosted_bundle_5xx_retry_exhaustion_surfaces_server_message(tmp_path: Path) -> None:

    source, companions = _bundle(tmp_path)
    opener = FakeOpener(
        FakeResponse(503, payload={"message": "ingest-cluster restarting"}),
        FakeResponse(503, payload={"message": "ingest-cluster restarting"}),
    )

    with pytest.raises(HostedSubmitError, match="ingest-cluster restarting"):
        submit_hosted_bundle(
            service_url="https://api.benchbox.dev/v1",
            token="secret-token",
            source_path=source,
            companions=companions,
            manifest=_manifest(),
            bundle_hash="a" * 64,
            visibility="private",
            idempotency_key=None,
            wait=True,
            opener=opener,
            sleep=lambda _seconds: None,
            upload_attempts=2,
        )


