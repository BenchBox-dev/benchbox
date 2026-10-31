# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_SECRET_KEY_PATTERN = (
    r"(?:(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|account[_-]?key|"
    r"key[_-]?id|credential|dsn|connection[_-]?string|private[_-]?key|sas)[a-z0-9_-]*|"
    r"pat)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(({_SECRET_KEY_PATTERN})\s*=\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^&\s,;'\")]+)",
    flags=re.IGNORECASE,
)
_SECRET_SAS_KEY_PATTERN = r"(?:storage[_-]?sas(?:[_-]?token)?|sas)[a-z0-9_-]*"
_SECRET_SAS_ASSIGNMENT_RE = re.compile(
    rf"(({_SECRET_SAS_KEY_PATTERN})\s*=\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;'\")]+)",
    flags=re.IGNORECASE,
)
_SECRET_PRIVATE_KEY_KEY_PATTERN = r"private[_-]?key[a-z0-9_-]*"
_SECRET_PRIVATE_KEY_BODY_LINE = r"[A-Za-z0-9+/=_]+"
_SECRET_PRIVATE_KEY_ASSIGNMENT_RE = re.compile(
    rf"(({_SECRET_PRIVATE_KEY_KEY_PATTERN})\s*=\s*)"
    r"(?:"
    r"'[^']*'"
    r"|\"[^\"]*\""
    r"|-----BEGIN[^\n]*-----[\s\S]*?-----END[^\n]*-----"
    rf"|-----BEGIN[^\n]*-----(?:\r?\n{_SECRET_PRIVATE_KEY_BODY_LINE})*"
    rf"|[^\s,;'\")]+(?:\r?\n{_SECRET_PRIVATE_KEY_BODY_LINE})*"
    r")",
    flags=re.IGNORECASE,
)
_SECRET_QUOTED_ASSIGNMENT_RE = re.compile(
    rf"""(?P<prefix>["']{_SECRET_KEY_PATTERN}["']\s*:\s*)(?P<quote>["'])(?P<value>(?:\\.|(?!(?P=quote)).)*(?P=quote))""",
    flags=re.IGNORECASE,
)
_SECRET_COLON_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>{_SECRET_KEY_PATTERN}\s*:\s*)(?P<value>'[^']*'|\"[^\"]*\"|[^&\s,;]+)",
    flags=re.IGNORECASE,
)
_SECRET_PROSE_CONNECTOR_RE = re.compile(
    rf"(?P<prefix>\b{_SECRET_KEY_PATTERN}\s+(?:is|was|equals?|set\s+to|configured\s+as)\s+)"
    r"(?P<value>[^\s,;:()]+)",
    flags=re.IGNORECASE,
)
_SECRET_PROSE_RE = re.compile(
    rf"(?P<prefix>\b{_SECRET_KEY_PATTERN}\s+)(?P<value>[^\s,;:()]+)",
    flags=re.IGNORECASE,
)
_NON_SECRET_SECRET_WORDS = frozenset(
    {
        "blank",
        "configured",
        "empty",
        "expired",
        "failed",
        "field",
        "found",
        "invalid",
        "is",
        "lookup",
        "missing",
        "must",
        "not",
        "null",
        "present",
        "provided",
        "required",
        "set",
        "setting",
        "should",
        "store",
        "undefined",
        "value",
        "was",
    }
)
_BARE_PROSE_OPAQUE_ALPHA_MIN = 20
_BARE_PROSE_ALLCAPS_MIN = 6
_URL_USERINFO_RE = re.compile(r"(://)[^/@\s]+@")


def _is_non_secret_word(value: str) -> bool:
    return value.lower() in _NON_SECRET_SECRET_WORDS


def _is_credential_like_bare_prose_value(value: str) -> bool:
    if _is_non_secret_word(value):
        return False
    if not value.isalpha():
        return True
    if value.isupper() and len(value) >= _BARE_PROSE_ALLCAPS_MIN:
        return True
    if len(value) >= _BARE_PROSE_OPAQUE_ALPHA_MIN:
        return True
    return False


def scrub_secret_material(text: str) -> str:

    def replace_quoted(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{match.group('quote')}****{match.group('quote')}"

    def replace_colon(match: re.Match[str]) -> str:
        value = match.group("value")
        if _is_non_secret_word(value):
            return match.group(0)
        return f"{match.group('prefix')}****"

    def replace_connector_prose(match: re.Match[str]) -> str:
        value = match.group("value")
        if _is_non_secret_word(value):
            return match.group(0)
        return f"{match.group('prefix')}****"

    def replace_bare_prose(match: re.Match[str]) -> str:
        value = match.group("value")
        if not _is_credential_like_bare_prose_value(value):
            return match.group(0)
        return f"{match.group('prefix')}****"

    scrubbed = _SECRET_QUOTED_ASSIGNMENT_RE.sub(replace_quoted, text)
    scrubbed = _SECRET_PRIVATE_KEY_ASSIGNMENT_RE.sub(r"\1****", scrubbed)
    scrubbed = _SECRET_SAS_ASSIGNMENT_RE.sub(r"\1****", scrubbed)
    scrubbed = _SECRET_ASSIGNMENT_RE.sub(r"\1****", scrubbed)
    scrubbed = _SECRET_COLON_ASSIGNMENT_RE.sub(replace_colon, scrubbed)
    scrubbed = _SECRET_PROSE_CONNECTOR_RE.sub(replace_connector_prose, scrubbed)
    scrubbed = _SECRET_PROSE_RE.sub(replace_bare_prose, scrubbed)
    return _URL_USERINFO_RE.sub(r"\1****@", scrubbed)


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VALIDATION_UNKNOWN_PLATFORM = "VALIDATION_UNKNOWN_PLATFORM"
    VALIDATION_UNKNOWN_BENCHMARK = "VALIDATION_UNKNOWN_BENCHMARK"
    VALIDATION_INVALID_SCALE_FACTOR = "VALIDATION_INVALID_SCALE_FACTOR"
    VALIDATION_INVALID_QUERY_ID = "VALIDATION_INVALID_QUERY_ID"
    VALIDATION_INVALID_PHASE = "VALIDATION_INVALID_PHASE"
    VALIDATION_INVALID_FORMAT = "VALIDATION_INVALID_FORMAT"
    VALIDATION_UNSUPPORTED_PLATFORM = "VALIDATION_UNSUPPORTED_PLATFORM"
    VALIDATION_UNSUPPORTED_MODE = "VALIDATION_UNSUPPORTED_MODE"

    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    PLATFORM_DEPENDENCIES_MISSING = "PLATFORM_DEPENDENCIES_MISSING"
    PLATFORM_CREDENTIALS_MISSING = "PLATFORM_CREDENTIALS_MISSING"
    PLATFORM_CONNECTION_FAILED = "PLATFORM_CONNECTION_FAILED"

    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"

    BENCHMARK_EXECUTION_FAILED = "BENCHMARK_EXECUTION_FAILED"
    BENCHMARK_DATA_GENERATION_FAILED = "BENCHMARK_DATA_GENERATION_FAILED"
    BENCHMARK_QUERY_FAILED = "BENCHMARK_QUERY_FAILED"
    BENCHMARK_VALIDATION_FAILED = "BENCHMARK_VALIDATION_FAILED"

    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_INVALID_FORMAT = "RESOURCE_INVALID_FORMAT"
    RESOURCE_ACCESS_DENIED = "RESOURCE_ACCESS_DENIED"

    INTERNAL_ERROR = "INTERNAL_ERROR"
    INTERNAL_TIMEOUT = "INTERNAL_TIMEOUT"
    INTERNAL_OUT_OF_MEMORY = "INTERNAL_OUT_OF_MEMORY"


class ErrorCategory(str, Enum):
    CLIENT = "client"
    PLATFORM = "platform"
    EXECUTION = "execution"
    SERVER = "server"


ERROR_CATEGORIES: dict[ErrorCode, ErrorCategory] = {
    ErrorCode.VALIDATION_ERROR: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_UNKNOWN_PLATFORM: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_UNKNOWN_BENCHMARK: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_INVALID_SCALE_FACTOR: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_INVALID_QUERY_ID: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_INVALID_PHASE: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_INVALID_FORMAT: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_UNSUPPORTED_PLATFORM: ErrorCategory.CLIENT,
    ErrorCode.VALIDATION_UNSUPPORTED_MODE: ErrorCategory.CLIENT,
    ErrorCode.PLATFORM_UNAVAILABLE: ErrorCategory.PLATFORM,
    ErrorCode.PLATFORM_DEPENDENCIES_MISSING: ErrorCategory.PLATFORM,
    ErrorCode.PLATFORM_CREDENTIALS_MISSING: ErrorCategory.PLATFORM,
    ErrorCode.PLATFORM_CONNECTION_FAILED: ErrorCategory.PLATFORM,
    ErrorCode.DEPENDENCY_MISSING: ErrorCategory.PLATFORM,
    ErrorCode.BENCHMARK_EXECUTION_FAILED: ErrorCategory.EXECUTION,
    ErrorCode.BENCHMARK_DATA_GENERATION_FAILED: ErrorCategory.EXECUTION,
    ErrorCode.BENCHMARK_QUERY_FAILED: ErrorCategory.EXECUTION,
    ErrorCode.BENCHMARK_VALIDATION_FAILED: ErrorCategory.EXECUTION,
    ErrorCode.RESOURCE_NOT_FOUND: ErrorCategory.CLIENT,
    ErrorCode.RESOURCE_INVALID_FORMAT: ErrorCategory.CLIENT,
    ErrorCode.RESOURCE_ACCESS_DENIED: ErrorCategory.PLATFORM,
    ErrorCode.INTERNAL_ERROR: ErrorCategory.SERVER,
    ErrorCode.INTERNAL_TIMEOUT: ErrorCategory.SERVER,
    ErrorCode.INTERNAL_OUT_OF_MEMORY: ErrorCategory.SERVER,
}


@dataclass
class MCPError:
    code: ErrorCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    suggestion: str | None = None
    retry_hint: bool = False

    @property
    def category(self) -> ErrorCategory:
        return ERROR_CATEGORIES.get(self.code, ErrorCategory.SERVER)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error": True,
            "error_code": self.code.value,
            "error_category": self.category.value,
            "message": self.message,
        }

        if self.details:
            result["details"] = self.details

        if self.suggestion:
            result["suggestion"] = self.suggestion

        if self.retry_hint:
            result["retry_hint"] = True

        return result


def make_error(
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
    suggestion: str | None = None,
    retry_hint: bool = False,
) -> dict[str, Any]:
    error = MCPError(
        code=code,
        message=message,
        details=details or {},
        suggestion=suggestion,
        retry_hint=retry_hint,
    )
    return error.to_dict()


def make_validation_error(
    message: str,
    details: dict[str, Any] | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    return make_error(
        ErrorCode.VALIDATION_ERROR,
        message,
        details=details,
        suggestion=suggestion,
    )


def make_not_found_error(
    resource_type: str,
    resource_id: str,
    available: list[str] | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "resource_type": resource_type,
        "requested": resource_id,
    }
    if available:
        details["available"] = available

    if suggestion is None and resource_type:
        suggestion = f"Use list_{resource_type}s() to see available options"

    return make_error(
        ErrorCode.RESOURCE_NOT_FOUND,
        f"{resource_type.capitalize()} '{resource_id}' not found",
        details=details,
        suggestion=suggestion,
    )


def make_benchmark_load_error(benchmark: str, error: Exception) -> dict[str, Any]:
    from benchbox.utils.dependencies import BenchmarkDependencyError

    if isinstance(error, BenchmarkDependencyError):
        return make_error(
            ErrorCode.DEPENDENCY_MISSING,
            error.summary,
            details={"benchmark": benchmark, "module": error.module, "extras": list(error.extras)},
            suggestion=error.remedy,
        )
    return make_error(ErrorCode.INTERNAL_ERROR, str(error), details={"benchmark": benchmark})


def make_platform_error(
    code: ErrorCode,
    platform: str,
    message: str,
    installation_command: str | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {"platform": platform}
    suggestion = installation_command if installation_command else None

    return make_error(
        code,
        message,
        details=details,
        suggestion=suggestion,
    )


def make_unsupported_mode_error(
    platform: str,
    requested_mode: str,
    supported_modes: list[str],
) -> dict[str, Any]:
    return make_error(
        ErrorCode.VALIDATION_UNSUPPORTED_MODE,
        f"Platform '{platform}' does not support {requested_mode} mode",
        details={
            "platform": platform,
            "requested_mode": requested_mode,
            "supported_modes": supported_modes,
        },
        suggestion=f"Use one of: {', '.join(supported_modes)}" if supported_modes else None,
    )


def make_execution_error(
    message: str,
    execution_id: str | None = None,
    exception: Exception | None = None,
    retry_hint: bool = False,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if execution_id:
        details["execution_id"] = execution_id
    if exception:
        details["exception_type"] = type(exception).__name__
        details["exception_message"] = scrub_secret_material(str(exception))

    return make_error(
        ErrorCode.BENCHMARK_EXECUTION_FAILED,
        scrub_secret_material(message),
        details=details,
        retry_hint=retry_hint,
    )
