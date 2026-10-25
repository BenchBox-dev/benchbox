from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import click

DEFAULT_SERVICE_URL = "https://api.benchbox.dev/v1"
KEYRING_SERVICE_NAME = "benchbox.submit"
TOKEN_ENV_VARS = ("BENCHBOX_SUBMIT_TOKEN", "BENCHBOX_SERVICE_TOKEN")


class SubmissionAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubmissionToken:
    token: str
    source: str


@dataclass(frozen=True)
class SubmissionAuthStatus:
    authenticated: bool
    source: str | None = None
    service_url: str | None = None


def normalize_service_url(service_url: str) -> str:

    value = service_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _load_keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise SubmissionAuthError(
            "The Python keyring package is required for stored hosted-submit credentials."
        ) from exc
    return keyring


def _keyring_error_types() -> tuple[type[BaseException], ...]:
    try:
        from keyring.errors import KeyringError
    except ImportError:
        return (Exception,)
    return (KeyringError,)


def _password_delete_not_found_types() -> tuple[type[BaseException], ...]:
    try:
        from keyring.errors import PasswordDeleteError
    except ImportError:
        return ()
    return (PasswordDeleteError,)


class SubmissionAuthStore:
    def __init__(self, *, service_name: str = KEYRING_SERVICE_NAME, keyring_backend: Any | None = None) -> None:
        self.service_name = service_name
        self._keyring_backend = keyring_backend

    @property
    def keyring_backend(self) -> Any:
        return self._keyring_backend if self._keyring_backend is not None else _load_keyring()

    def get_token(self, service_url: str) -> str | None:
        username = normalize_service_url(service_url)
        try:
            token = self.keyring_backend.get_password(self.service_name, username)
        except _keyring_error_types() as exc:
            raise SubmissionAuthError(f"Could not read hosted-submit credentials from the OS keyring: {exc}") from exc
        return token.strip() if isinstance(token, str) and token.strip() else None

    def save_token(self, service_url: str, token: str) -> None:
        clean_token = token.strip()
        if not clean_token:
            raise SubmissionAuthError("Hosted-submit token cannot be empty.")
        username = normalize_service_url(service_url)
        try:
            self.keyring_backend.set_password(self.service_name, username, clean_token)
        except _keyring_error_types() as exc:
            raise SubmissionAuthError(f"Could not save hosted-submit credentials to the OS keyring: {exc}") from exc

    def delete_token(self, service_url: str) -> bool:
        if self.get_token(service_url) is None:
            return False
        username = normalize_service_url(service_url)
        try:
            self.keyring_backend.delete_password(self.service_name, username)
        except _password_delete_not_found_types():
            return False
        except _keyring_error_types() as exc:
            raise SubmissionAuthError(f"Could not delete hosted-submit credentials from the OS keyring: {exc}") from exc
        return True


def _env_token() -> SubmissionToken | None:
    for name in TOKEN_ENV_VARS:
        value = os.environ.get(name)
        if value and value.strip():
            return SubmissionToken(token=value.strip(), source=f"environment:{name}")
    return None


def get_submission_auth_status(
    service_url: str = DEFAULT_SERVICE_URL,
    *,
    store: SubmissionAuthStore | None = None,
) -> SubmissionAuthStatus:

    env_token = _env_token()
    normalized_url = normalize_service_url(service_url)
    if env_token:
        return SubmissionAuthStatus(authenticated=True, source=env_token.source, service_url=normalized_url)

    auth_store = store or SubmissionAuthStore()
    if auth_store.get_token(service_url):
        return SubmissionAuthStatus(authenticated=True, source="keyring", service_url=normalized_url)
    return SubmissionAuthStatus(authenticated=False, service_url=normalized_url)


def resolve_submission_token(
    service_url: str = DEFAULT_SERVICE_URL,
    *,
    prompt: bool = True,
    force_refresh: bool = False,
    store: SubmissionAuthStore | None = None,
) -> SubmissionToken:

    if not force_refresh:
        env_token = _env_token()
        if env_token:
            return env_token

    auth_store = store or SubmissionAuthStore()
    if not force_refresh:
        stored_token = auth_store.get_token(service_url)
        if stored_token:
            return SubmissionToken(token=stored_token, source="keyring")

    if not prompt:
        raise SubmissionAuthError(
            "No hosted-submit token found. Run `benchbox auth login`, set BENCHBOX_SUBMIT_TOKEN, "
            "or set BENCHBOX_SERVICE_TOKEN as a fallback."
        )

    try:
        token = click.prompt("BenchBox service token", hide_input=True, confirmation_prompt=False).strip()
    except (click.Abort, EOFError) as exc:
        raise SubmissionAuthError(
            "No hosted-submit token found. Run `benchbox auth login`, set BENCHBOX_SUBMIT_TOKEN, "
            "or set BENCHBOX_SERVICE_TOKEN as a fallback."
        ) from exc

    auth_store.save_token(service_url, token)
    return SubmissionToken(token=token, source="keyring")


def refresh_submission_token(
    service_url: str = DEFAULT_SERVICE_URL,
    *,
    store: SubmissionAuthStore | None = None,
) -> SubmissionToken:

    env_token = _env_token()
    if env_token:
        raise SubmissionAuthError(
            f"{env_token.source} is active. Update or unset that environment variable before refreshing stored credentials."
        )
    return resolve_submission_token(service_url, prompt=True, force_refresh=True, store=store)


__all__ = [
    "DEFAULT_SERVICE_URL",
    "KEYRING_SERVICE_NAME",
    "TOKEN_ENV_VARS",
    "SubmissionAuthError",
    "SubmissionAuthStatus",
    "SubmissionAuthStore",
    "SubmissionToken",
    "get_submission_auth_status",
    "normalize_service_url",
    "refresh_submission_token",
    "resolve_submission_token",
]
