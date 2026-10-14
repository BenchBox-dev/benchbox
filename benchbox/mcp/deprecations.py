from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from benchbox.core.deprecation_notices import DeprecationNotice, run_notices, run_scope
from benchbox.core.run_selection import resolve_run_selection

_PARAMETER_FORM = re.compile(r"\(([a-z_]+='[^']*')\)")
_PLATFORM_FLAG = re.compile(r"'--platform ([^']+)'")
_VALUE_FLAG = re.compile(r"'--([a-z][a-z-]*) ([^']+)'")


def mcp_replacement(replacement: str) -> str:
    parameter = _PARAMETER_FORM.search(replacement)
    if parameter is not None:
        return parameter.group(1)
    platform = _PLATFORM_FLAG.search(replacement)
    if platform is not None:
        return platform.group(1)
    flag = _VALUE_FLAG.search(replacement)
    if flag is not None:
        return f"{flag.group(1).replace('-', '_')}='{flag.group(2)}'"
    return replacement


def serialize_notice(notice: DeprecationNotice) -> dict[str, str]:
    replacement = mcp_replacement(notice.replacement)
    return {
        "option": notice.option,
        "replacement": replacement,
        "removed_in": notice.removed_in,
        "message": (
            f"Platform option '{notice.option}' for '{notice.platform}' is deprecated and will be removed in "
            f"BenchBox {notice.removed_in}; use {replacement} instead."
        ),
    }


def serialize_notices(notices: Iterable[DeprecationNotice]) -> list[dict[str, str]]:
    return [serialize_notice(notice) for notice in notices]


def current_deprecations() -> list[dict[str, str]]:
    return serialize_notices(run_notices())


def attach_deprecations(response: dict[str, Any]) -> dict[str, Any]:
    response["deprecations"] = current_deprecations()
    return response


def request_deprecations(request: Mapping[str, Any]) -> list[dict[str, str]]:
    with run_scope(log=False):
        try:
            resolve_run_selection(
                str(request["platform"]),
                str(request.get("execution_engine") or "default"),
                request.get("platform_options") or {},
            )
        except ValueError:
            pass
        return current_deprecations()


__all__ = [
    "attach_deprecations",
    "current_deprecations",
    "mcp_replacement",
    "request_deprecations",
    "serialize_notice",
    "serialize_notices",
]
