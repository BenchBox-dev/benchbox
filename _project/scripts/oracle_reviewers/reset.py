from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

ATTESTERS = frozenset({"joeharris76"})
OWNER_INSTRUCTION = "Owner instruction"
REASON_LIMIT = 200
_RESET = re.compile(r"Oracle backoff reset: (?P<sha>[0-9a-f]{40})\nReason: (?P<reason>[^\n]+)")


def reset_body(head_sha: str, reason: str) -> str:
    return f"Oracle backoff reset: {head_sha}\nReason: {reason}"


def valid_reason(reason: str) -> bool:
    return bool(reason.strip()) and reason == reason.strip() and "\n" not in reason and len(reason) <= REASON_LIMIT


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _owner_comment(comment: Mapping[str, Any]) -> bool:
    user = comment.get("user") or {}
    return (
        user.get("login") in ATTESTERS
        and user.get("type") == "User"
        and comment.get("updated_at") in (None, comment.get("created_at"))
    )


def has_owner_instruction(comments: Iterable[Mapping[str, Any]], head_sha: str, before: datetime | None = None) -> bool:
    for comment in comments:
        body = comment.get("body") or ""
        created = _moment(comment.get("created_at"))
        if (
            _owner_comment(comment)
            and OWNER_INSTRUCTION in body
            and head_sha in body
            and created is not None
            and (before is None or created < before)
        ):
            return True
    return False


def reset_moment(comments: Iterable[Mapping[str, Any]], head_sha: str) -> datetime | None:
    listed = list(comments)
    found: list[datetime] = []
    for comment in listed:
        match = _RESET.fullmatch((comment.get("body") or "").strip())
        created = _moment(comment.get("created_at"))
        if (
            match
            and match.group("sha") == head_sha
            and _owner_comment(comment)
            and created is not None
            and has_owner_instruction(listed, head_sha, created)
        ):
            found.append(created)
    return max(found, default=None)
