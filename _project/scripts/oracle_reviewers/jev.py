from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

DEFAULT_BASE_URL = "https://api.typesafe.ai"
MAX_REQUEST_BYTES = 32 * 1024 * 1024


class JevError(Exception):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class JevSession:
    def __init__(
        self,
        model: str = "jev-1.14.0",
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.model = model
        self.base_url = (base_url or os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    def __enter__(self) -> JevSession:
        if not self.api_key:
            raise JevError(None, "TYPESAFE_API_KEY is not set")
        return self

    def __exit__(self, *ignored: object) -> None:
        return None

    def ask(self, state: object, questions: Mapping[str, object]) -> dict[str, Any]:
        try:
            payload = json.dumps({"model": self.model, "state": state, "questions": dict(questions)}).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise JevError(None, f"judgment request is not JSON: {exc}") from exc
        if len(payload) > MAX_REQUEST_BYTES:
            raise JevError(None, "judgment request exceeds 32 MiB")
        request = urllib.request.Request(
            f"{self.base_url}/v1/systemone",
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise JevError(exc.code, _error_message(exc.read())) from exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise JevError(None, f"the judgment service is unreachable: {exc}") from exc
        try:
            content = json.loads(raw)
        except ValueError as exc:
            raise JevError(None, "the judgment service answered malformed JSON") from exc
        if not isinstance(content, dict) or not isinstance(content.get("answers"), dict):
            raise JevError(None, "the judgment service answered without answers")
        return content["answers"]


def _error_message(raw: bytes) -> str:
    try:
        content = json.loads(raw)
    except ValueError:
        return ""
    error = content.get("error") if isinstance(content, dict) else None
    message = error.get("message", "") if isinstance(error, dict) else error
    return str(message or "")[:500]
