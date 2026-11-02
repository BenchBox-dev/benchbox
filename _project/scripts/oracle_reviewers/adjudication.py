from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .reset import ATTESTERS

PREFIX = "Oracle adjudication: "
TRIGGER = PREFIX.rstrip()
WITHDRAW = "WITHDRAW"
MAINTAIN = "MAINTAIN"
ACTIONS = (WITHDRAW, MAINTAIN)
GRAMMAR = f"{PREFIX}WITHDRAW|MAINTAIN D<n> - <reason>"
REASON_LIMIT = 200
_COMMENT = re.compile(
    rf"{re.escape(PREFIX)}(?P<action>{'|'.join(ACTIONS)}) (?P<id>D[1-9][0-9]{{0,3}}) - "
    rf"(?P<reason>\S[^\n]{{0,{REASON_LIMIT - 1}}})"
)


@dataclass(frozen=True)
class Adjudication:
    action: str
    defect_id: str
    reason: str


def parse(body: str | None) -> Adjudication | None:
    match = _COMMENT.fullmatch((body or "").strip())
    return Adjudication(match["action"], match["id"], match["reason"].rstrip()) if match else None


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def valid(comment: Mapping[str, Any], after: datetime | None) -> Adjudication | None:
    created = _moment(comment.get("created_at"))
    if (
        comment.get("login") not in ATTESTERS
        or comment.get("user_type") != "User"
        or comment.get("updated_at") not in (None, comment.get("created_at"))
        or created is None
        or after is None
        or created <= after
    ):
        return None
    return parse(comment.get("body"))
