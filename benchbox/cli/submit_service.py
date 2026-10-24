from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import error, request

import benchbox
from benchbox.cli.submit_auth import normalize_service_url
from benchbox.core.results.canonical_json import canonical_json_text
from benchbox.utils import tls

PUBLIC_RESULTS_BASE_URL = "https://benchbox.dev/results/r"
_TRANSIENT_STATUS_CODES = {500, 502, 503, 504}
_TERMINAL_STATUSES = {"published", "rejected"}


class HostedSubmitError(RuntimeError):
    pass


class HostedSubmitUnauthorized(HostedSubmitError):
    pass


class _HostedSubmitTransientError(HostedSubmitError):
    pass


@dataclass(frozen=True)
class HostedSubmitResult:
    status: str
    idempotency_key: str
    submission_id: str | None = None
    public_result_id: str | None = None
    public_url: str | None = None
    status_history: tuple[str, ...] = ()


@dataclass(frozen=True)
class _HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


UrlOpen = Callable[..., Any]
Sleep = Callable[[float], None]


def make_idempotency_key(service_url: str, bundle_hash: str) -> str:

    normalized = normalize_service_url(service_url)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{normalized}:{bundle_hash}"))


def submit_hosted_bundle(
    *,
    service_url: str,
    token: str,
    source_path: Path,
    companions: list[Path],
    manifest: Mapping[str, Any],
    bundle_hash: str,
    visibility: str,
    idempotency_key: str | None,
    wait: bool,
    opener: UrlOpen = tls.urlopen,
    sleep: Sleep = time.sleep,
    upload_attempts: int = 3,
    poll_attempts: int = 10,
    request_timeout: float = 30.0,
    initial_backoff_seconds: float = 5.0,
    max_backoff_seconds: float = 60.0,
) -> HostedSubmitResult:

    resolved_key = idempotency_key or make_idempotency_key(service_url, bundle_hash)
    response = _upload_with_retries(
        service_url=service_url,
        token=token,
        source_path=source_path,
        companions=companions,
        manifest=manifest,
        visibility=visibility,
        idempotency_key=resolved_key,
        opener=opener,
        sleep=sleep,
        max_attempts=upload_attempts,
        timeout=request_timeout,
        initial_backoff_seconds=initial_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
    )

    if response.status == 401:
        raise HostedSubmitUnauthorized("Hosted submission token was rejected by the service.")
    if response.status in _TRANSIENT_STATUS_CODES:
        _raise_for_response(response, _json_payload_or_empty(response))

    if response.status == 200:
        payload = _json_payload(response)
        return _result_from_success_payload(payload, idempotency_key=resolved_key)

    if response.status in {202, 409}:
        payload = _json_payload(response)
        submission_id = _require_submission_id(payload)
        status = str(payload.get("status") or "pending")
        history = (status,)
        if not wait:
            return HostedSubmitResult(
                status=status,
                idempotency_key=resolved_key,
                submission_id=submission_id,
                public_result_id=_optional_str(payload.get("public_result_id")),
                public_url=_public_url(payload),
                status_history=history,
            )
        return poll_submission_status(
            service_url=service_url,
            token=token,
            submission_id=submission_id,
            idempotency_key=resolved_key,
            opener=opener,
            sleep=sleep,
            max_attempts=poll_attempts,
            timeout=request_timeout,
            initial_backoff_seconds=initial_backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
            status_history=history,
        )

    _raise_for_response(response, _json_payload_or_empty(response))
    raise HostedSubmitError(f"Unexpected hosted submission response: HTTP {response.status}")


def poll_submission_status(
    *,
    service_url: str,
    token: str,
    submission_id: str,
    idempotency_key: str,
    opener: UrlOpen = tls.urlopen,
    sleep: Sleep = time.sleep,
    max_attempts: int = 10,
    timeout: float = 30.0,
    initial_backoff_seconds: float = 5.0,
    max_backoff_seconds: float = 60.0,
    status_history: tuple[str, ...] = (),
) -> HostedSubmitResult:

    history = list(status_history)
    backoff = initial_backoff_seconds
    url = _status_url(service_url, submission_id)

    for attempt in range(1, max_attempts + 1):
        try:
            response = _send_request(
                request.Request(url, headers=_auth_headers(token), method="GET"),
                opener=opener,
                timeout=timeout,
            )
        except _HostedSubmitTransientError as exc:
            if attempt >= max_attempts:
                raise HostedSubmitError(f"Hosted submission status polling failed after retries: {exc}") from exc
            sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
            continue

        if response.status == 401:
            raise HostedSubmitUnauthorized("Hosted submission token was rejected while polling status.")

        if response.status in _TRANSIENT_STATUS_CODES and attempt < max_attempts:
            sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
            continue
        if response.status in _TRANSIENT_STATUS_CODES:
            _raise_for_response(response, _json_payload_or_empty(response))

        if response.status != 200:
            _raise_for_response(response, _json_payload_or_empty(response))

        payload = _json_payload(response)

        status = str(payload.get("status") or "pending")
        if not history or history[-1] != status:
            history.append(status)

        if status == "rejected":
            reason = _optional_str(payload.get("rejection_reason")) or "no rejection reason provided"
            raise HostedSubmitError(f"Hosted submission was rejected: {reason}")

        if status in _TERMINAL_STATUSES:
            return HostedSubmitResult(
                status=status,
                idempotency_key=idempotency_key,
                submission_id=submission_id,
                public_result_id=_optional_str(payload.get("public_result_id")),
                public_url=_public_url(payload),
                status_history=tuple(history),
            )

        if attempt < max_attempts:
            sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)

    raise HostedSubmitError(
        f"Timed out waiting for hosted submission {submission_id} after {max_attempts} status checks."
    )


def _upload_with_retries(
    *,
    service_url: str,
    token: str,
    source_path: Path,
    companions: list[Path],
    manifest: Mapping[str, Any],
    visibility: str,
    idempotency_key: str,
    opener: UrlOpen,
    sleep: Sleep,
    max_attempts: int,
    timeout: float,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
) -> _HTTPResponse:
    body, content_type = _multipart_body(
        source_path=source_path,
        companions=companions,
        manifest=manifest,
        visibility=visibility,
    )
    headers = {
        **_auth_headers(token),
        "Content-Type": content_type,
        "Idempotency-Key": idempotency_key,
        "User-Agent": f"benchbox/{benchbox.__version__}",
    }
    url = _submissions_url(service_url)
    backoff = initial_backoff_seconds

    for attempt in range(1, max_attempts + 1):
        try:
            response = _send_request(
                request.Request(url, data=body, headers=headers, method="POST"),
                opener=opener,
                timeout=timeout,
            )
        except _HostedSubmitTransientError as exc:
            if attempt >= max_attempts:
                raise HostedSubmitError(f"Hosted submission upload failed after retries: {exc}") from exc
            sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
            continue
        if response.status in _TRANSIENT_STATUS_CODES and attempt < max_attempts:
            sleep(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
            continue
        return response

    raise HostedSubmitError("Hosted submission upload failed before receiving an HTTP response.")


def _send_request(req: request.Request, *, opener: UrlOpen, timeout: float) -> _HTTPResponse:
    try:
        with opener(req, timeout=timeout) as response:
            return _HTTPResponse(
                status=response.getcode(),
                headers=dict(response.headers.items()),
                body=response.read(),
            )
    except error.HTTPError as exc:
        return _HTTPResponse(status=exc.code, headers=dict(exc.headers.items()), body=exc.read())
    except error.URLError as exc:
        raise _HostedSubmitTransientError(str(exc.reason)) from exc
    except OSError as exc:
        raise _HostedSubmitTransientError(str(exc)) from exc


def _multipart_body(
    *,
    source_path: Path,
    companions: list[Path],
    manifest: Mapping[str, Any],
    visibility: str,
) -> tuple[bytes, str]:
    boundary = f"benchbox-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    _add_form_field(parts, boundary, "visibility", visibility)
    _add_file_field(parts, boundary, "bundle", source_path.name, source_path.read_bytes(), "application/json")
    manifest_name = f"{source_path.stem}.manifest.json"
    manifest_bytes = canonical_json_text(manifest).encode("utf-8")
    _add_file_field(parts, boundary, "manifest", manifest_name, manifest_bytes, "application/json")
    for companion in companions:
        _add_file_field(
            parts,
            boundary,
            "companions[]",
            companion.name,
            companion.read_bytes(),
            "application/json",
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _add_form_field(parts: list[bytes], boundary: str, name: str, value: str) -> None:
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
    parts.append(value.encode("utf-8") + b"\r\n")


def _add_file_field(
    parts: list[bytes],
    boundary: str,
    name: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> None:
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        (
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    parts.append(data + b"\r\n")


def _json_payload(response: _HTTPResponse) -> dict[str, Any]:
    if not response.body:
        return {}
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HostedSubmitError(f"Hosted service returned invalid JSON for HTTP {response.status}.") from exc
    if not isinstance(payload, dict):
        raise HostedSubmitError(f"Hosted service returned non-object JSON for HTTP {response.status}.")
    return payload


def _json_payload_or_empty(response: _HTTPResponse) -> dict[str, Any]:
    try:
        return _json_payload(response)
    except HostedSubmitError:
        return {}


def _result_from_success_payload(payload: Mapping[str, Any], *, idempotency_key: str) -> HostedSubmitResult:
    status = str(payload.get("status") or "published")
    return HostedSubmitResult(
        status=status,
        idempotency_key=idempotency_key,
        submission_id=_optional_str(payload.get("submission_id")),
        public_result_id=_optional_str(payload.get("public_result_id")),
        public_url=_public_url(payload),
        status_history=(status,),
    )


def _require_submission_id(payload: Mapping[str, Any]) -> str:
    submission_id = _optional_str(payload.get("submission_id"))
    if not submission_id:
        raise HostedSubmitError("Hosted service response did not include submission_id.")
    return submission_id


def _raise_for_response(response: _HTTPResponse, payload: Mapping[str, Any]) -> None:
    if response.status == 401:
        raise HostedSubmitUnauthorized("Hosted submission token was rejected by the service.")
    if response.status == 422:
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            details = "; ".join(str(item) for item in errors)
        else:
            details = _optional_str(payload.get("message")) or "schema or hash validation failed"
        raise HostedSubmitError(f"Hosted submission validation failed: {details}")
    if response.status == 429:
        retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
        suffix = f" Retry after {retry_after} seconds." if retry_after else ""
        raise HostedSubmitError(f"Hosted submission was rate limited.{suffix}")
    if response.status in _TRANSIENT_STATUS_CODES:
        message = _optional_str(payload.get("message")) or _optional_str(payload.get("error"))
        suffix = f": {message}" if message else "."
        raise HostedSubmitError(f"Hosted submission failed after retries: HTTP {response.status}{suffix}")
    message = _optional_str(payload.get("message")) or _optional_str(payload.get("error"))
    if message:
        raise HostedSubmitError(f"Hosted submission failed: HTTP {response.status}: {message}")
    raise HostedSubmitError(f"Hosted submission failed: HTTP {response.status}.")


def _public_url(payload: Mapping[str, Any]) -> str | None:
    public_url = _optional_str(payload.get("public_url"))
    if public_url:
        return public_url
    public_result_id = _optional_str(payload.get("public_result_id"))
    if public_result_id:
        return f"{PUBLIC_RESULTS_BASE_URL}/{public_result_id}"
    return None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _submissions_url(service_url: str) -> str:
    return f"{normalize_service_url(service_url)}/submissions"


def _status_url(service_url: str, submission_id: str) -> str:
    return f"{_submissions_url(service_url)}/{submission_id}/status"


__all__ = [
    "HostedSubmitError",
    "HostedSubmitResult",
    "HostedSubmitUnauthorized",
    "make_idempotency_key",
    "poll_submission_status",
    "submit_hosted_bundle",
]
