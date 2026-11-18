from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED_VALUE = "<redacted>"

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")

_AUTHORITY_USERINFO_SCHEMES = frozenset({"abfs", "abfss", "wasb", "wasbs"})

_URI_USERINFO_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*)(?P<sep>://)(?P<userinfo>[^/?#\s]+)@",
    flags=re.IGNORECASE,
)

_URI_QUERY_OR_FRAGMENT_PARAM_RE = re.compile(
    r"(?P<sep>[?&#])(?P<name>[^=?#&]+)=(?P<value>[^?#&]*)",
)


def _normalize_secret_key(key: str) -> str:
    return _NON_ALNUM_RE.sub("", key.lower())


_SECRET_KEY_PARTS = tuple(
    _normalize_secret_key(part)
    for part in (
        "password",
        "token",
        "secret",
        "access_key",
        "key_id",
        "private_key",
        "session_token",
        "connection_string",
        "credential",
        "dsn",
        "sas",
        "api_key",
        "account_key",
    )
)
_EXACT_SECRET_KEYS = frozenset({"passwd", "pwd", "pat"})
_USERNAME_KEYS = frozenset({"user", "username", "userid", "pguser", "dbuser", "serviceaccount"})


def _is_username_key(key: str) -> bool:
    return _normalize_secret_key(key) in _USERNAME_KEYS


def _redact_uri_userinfo_match(match: re.Match[str]) -> str:
    if match.group("scheme").lower() in _AUTHORITY_USERINFO_SCHEMES:
        return match.group(0)
    userinfo = match.group("userinfo")
    if ":" not in userinfo:
        return match.group(0)
    return f"{match.group('scheme')}{match.group('sep')}****@"


def _redact_uri_query_or_fragment_param(match: re.Match[str]) -> str:
    if is_secret_option_key(match.group("name")):
        return f"{match.group('sep')}{match.group('name')}=****"
    return match.group(0)


def _scrub_uri_credentials(value: str) -> str:
    scrubbed = _URI_USERINFO_RE.sub(_redact_uri_userinfo_match, value)
    return _URI_QUERY_OR_FRAGMENT_PARAM_RE.sub(_redact_uri_query_or_fragment_param, scrubbed)


_INTERNAL_OPTION_KEYS = {
    "_explicit_platform_options",
    "verbosity_settings",
    "verbose",
    "verbose_level",
    "verbose_enabled",
    "very_verbose",
    "quiet",
    "tuning_enabled",
    "unified_tuning_configuration",
    "df_tuning_config",
    "capture_plans",
    "analyze_plans",
    "strict_plan_capture",
    "plan_queries",
    "execution_mode",
    "execution_engine",
    "benchmark",
    "scale_factor",
    "tuning_config",
    "driver_package",
    "driver_version_resolved",
    "driver_version_actual",
    "driver_runtime_strategy",
    "driver_runtime_path",
    "driver_runtime_python_executable",
    "driver_auto_install",
    "driver_auto_install_used",
}


def is_secret_option_key(key: str) -> bool:
    normalized = _normalize_secret_key(key)
    return normalized in _EXACT_SECRET_KEYS or any(part in normalized for part in _SECRET_KEY_PARTS)


def sanitize_platform_options(
    options: Mapping[str, Any] | None,
    *,
    exclude_internal: bool = False,
    redact_usernames: bool = True,
) -> dict[str, Any]:
    if not options:
        return {}

    sanitized: dict[str, Any] = {}
    for key, value in options.items():
        key_str = str(key)
        if exclude_internal and (key_str.startswith("_") or key_str in _INTERNAL_OPTION_KEYS):
            continue
        if is_secret_option_key(key_str) or (redact_usernames and _is_username_key(key_str)):
            sanitized[key_str] = REDACTED_VALUE
        else:
            sanitized[key_str] = _sanitize_option_value(
                value,
                exclude_internal=exclude_internal,
                redact_usernames=redact_usernames,
            )
    return sanitized


def build_platform_options_capture(
    *,
    requested_options: Mapping[str, Any] | None = None,
    requested_sources: Mapping[str, str] | None = None,
    database_options: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    source_map = {str(key): str(value) for key, value in (requested_sources or {}).items()}

    for key, value in _iter_public_options(database_options):
        values[key] = value
        sources[key] = "applied_defaults"

    for key, value in _iter_public_options(requested_options):
        requested_source = source_map.get(key, "requested")
        if requested_source == "registered_default" and key in values:
            continue
        values[key] = value
        sources[key] = requested_source

    sanitized_values = sanitize_platform_options(values, exclude_internal=True, redact_usernames=False)
    return sanitized_values, {key: sources[key] for key in sanitized_values if key in sources}


def _iter_public_options(options: Mapping[str, Any] | None):
    if not options:
        return
    for key, value in options.items():
        key_str = str(key)
        if key_str.startswith("_") or key_str in _INTERNAL_OPTION_KEYS or value is None:
            continue
        yield key_str, value


def _sanitize_option_value(value: Any, *, exclude_internal: bool = False, redact_usernames: bool = True) -> Any:
    if isinstance(value, str):
        return _scrub_uri_credentials(value)
    if isinstance(value, Mapping):
        return sanitize_platform_options(
            value,
            exclude_internal=exclude_internal,
            redact_usernames=redact_usernames,
        )
    if isinstance(value, list):
        return [
            _sanitize_option_value(
                item,
                exclude_internal=exclude_internal,
                redact_usernames=redact_usernames,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            _sanitize_option_value(
                item,
                exclude_internal=exclude_internal,
                redact_usernames=redact_usernames,
            )
            for item in value
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        to_dict = getattr(value, "to_dict", None)
    except Exception:
        return f"<unserializable:{type(value).__name__}>"
    if callable(to_dict):
        try:
            as_dict = to_dict()
        except Exception:
            return f"<unserializable:{type(value).__name__}>"
        if isinstance(as_dict, Mapping):
            return sanitize_platform_options(
                as_dict,
                exclude_internal=exclude_internal,
                redact_usernames=redact_usernames,
            )
        return _sanitize_option_value(
            as_dict,
            exclude_internal=exclude_internal,
            redact_usernames=redact_usernames,
        )
    return f"<unserializable:{type(value).__name__}>"


__all__ = [
    "REDACTED_VALUE",
    "build_platform_options_capture",
    "is_secret_option_key",
    "sanitize_platform_options",
]
