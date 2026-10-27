from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

BATCH_SIZE = 100
REVIEW_EDITS_QUERY = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on PullRequestReview {
      id
      lastEditedAt
      includesCreatedEdit
    }
  }
}
"""
SHIP = "SHIP"
SUCCESS = "success"
FAILURE = "failure"
DECISIVE = (SUCCESS, FAILURE)
EDITED_REASON = "an oracle review was edited"
_RUN_SUFFIX = re.compile(r" \[run (?P<run>[0-9a-z]+)\]$")


class EditStateError(RuntimeError):
    pass


def is_edited(last_edited_at: Any, includes_created_edit: Any) -> bool:
    return last_edited_at is not None or bool(includes_created_edit)


def node_edited(node: Any) -> bool:
    if not isinstance(node, Mapping) or "lastEditedAt" not in node or "includesCreatedEdit" not in node:
        raise EditStateError("GitHub returned no edit state for an oracle object")
    return is_edited(node["lastEditedAt"], node["includesCreatedEdit"])


def batches(node_ids: Sequence[str]) -> list[list[str]]:
    return [list(node_ids[start : start + BATCH_SIZE]) for start in range(0, len(node_ids), BATCH_SIZE)]


def parse_review_edits(payload: Any, node_ids: Iterable[str]) -> dict[str, tuple[Any, bool]]:
    if not isinstance(payload, Mapping):
        raise EditStateError("GitHub GraphQL returned a malformed edit-state response")
    if payload.get("errors"):
        raise EditStateError(f"GitHub GraphQL returned errors: {payload['errors']}")
    data = payload.get("data")
    nodes = data.get("nodes") if isinstance(data, Mapping) else None
    if not isinstance(nodes, list):
        raise EditStateError("GitHub GraphQL returned no edit state")
    found: dict[str, tuple[Any, bool]] = {}
    for node in nodes:
        if isinstance(node, Mapping) and isinstance(node.get("id"), str):
            node_edited(node)
            found[node["id"]] = (node["lastEditedAt"], bool(node["includesCreatedEdit"]))
    missing = [node_id for node_id in node_ids if node_id not in found]
    if missing:
        raise EditStateError(f"GitHub GraphQL returned no edit state for {len(missing)} review(s)")
    return found


def run_suffix(description: str | None) -> str | None:
    match = _RUN_SUFFIX.search(description or "")
    return match.group("run") if match else None


def _is_app(creator: Any, bot_login: str) -> bool:
    return (
        isinstance(creator, Mapping)
        and (creator.get("login") or "").removesuffix("[bot]") == bot_login
        and creator.get("type") == "Bot"
    )


def app_verdict_statuses(
    statuses: Iterable[Mapping[str, Any]], context: str, bot_login: str
) -> list[Mapping[str, Any]]:
    return sorted(
        (
            status
            for status in statuses
            if isinstance(status, Mapping)
            and status.get("context") == context
            and status.get("state") in DECISIVE
            and status.get("created_at")
            and _is_app(status.get("creator"), bot_login)
        ),
        key=lambda status: (str(status["created_at"]), status.get("id") or 0),
    )


def expected_state(decision: str) -> str:
    return SUCCESS if decision == SHIP else FAILURE


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _nearest(statuses: list[Mapping[str, Any]], near: datetime | None) -> Mapping[str, Any] | None:
    if not statuses:
        return None
    if near is None:
        return statuses[-1]
    stamped = [(status, _moment(status["created_at"])) for status in statuses]
    timed = [(status, moment) for status, moment in stamped if moment is not None]
    if not timed:
        return statuses[-1]
    return min(timed, key=lambda item: abs((item[1] - near).total_seconds()))[0]


def chosen_status(
    statuses: Iterable[Mapping[str, Any]],
    context: str,
    bot_login: str,
    run_uuid: str | None,
    decision: str,
    near: datetime | None = None,
) -> Mapping[str, Any] | None:
    verdicts = app_verdict_statuses(statuses, context, bot_login)
    if run_uuid is None:
        return _nearest(verdicts, near)
    matching = [status for status in verdicts if run_suffix(status.get("description")) == run_uuid]
    if matching:
        return _nearest(matching, near)
    if decision == SHIP:
        return None
    return _nearest([status for status in verdicts if run_suffix(status.get("description")) is None], near)


def status_agrees(
    statuses: Iterable[Mapping[str, Any]],
    context: str,
    bot_login: str,
    record: Mapping[str, Any],
    near: datetime | None = None,
) -> bool:
    status = chosen_status(statuses, context, bot_login, record.get("run_uuid"), record["decision"], near)
    return status is not None and status.get("state") == expected_state(record["decision"])
