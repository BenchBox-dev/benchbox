from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import absence, protocol, selection
from .selection import Attempt, SelectionInput, Step
from .verdict import COMPLETE, SHIP, Verdict, VerdictError, validate

SCHEMA_VERSION = 2
ARTIFACT_KEYS = frozenset(
    {
        "schema",
        "run_id",
        "pr",
        "head_sha",
        "slot",
        "reviewer",
        "outcome",
        "absence",
        "verdict",
        "diagnostic",
    }
)
VERDICT_OUTCOME = "verdict"
ABSENT_OUTCOME = "absent"


def artifact_name(slot: int) -> str:
    return f"attempt-{slot}.json"


def build_artifact(
    *,
    run_id: str,
    pr: int,
    head_sha: str,
    slot: int,
    reviewer: str,
    verdict: Mapping[str, Any] | None,
    missing: absence.Absence | None,
    diagnostic: str = "",
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "pr": pr,
        "head_sha": head_sha,
        "slot": slot,
        "reviewer": reviewer,
        "outcome": VERDICT_OUTCOME if missing is None else ABSENT_OUTCOME,
        "absence": None
        if missing is None
        else {
            "kind": missing.kind,
            "detail": missing.detail,
            "reset_at": missing.reset_at.isoformat() if missing.reset_at else None,
            "provider": missing.provider,
            "unread": list(missing.unread),
        },
        "verdict": dict(verdict) if missing is None and verdict is not None else None,
        "diagnostic": diagnostic,
    }


@dataclass
class LoadedAttempts:
    attempts: list[Attempt] = field(default_factory=list)
    verdicts: dict[int, Verdict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _check(condition: bool, errors: list[str], message: str) -> bool:
    if not condition:
        errors.append(message)
    return condition


def _attempt(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    loaded: LoadedAttempts,
    workspace: Path | str | None = None,
) -> Attempt:
    slot = int(data["slot"])
    reviewer = str(data["reviewer"])
    if data["outcome"] == VERDICT_OUTCOME:
        try:
            verdict = validate(data["verdict"], trusted=True)
        except VerdictError as exc:
            return Attempt(slot, reviewer, selection.ABSENT, absence.INVALID, str(exc))
        if verdict.status != COMPLETE:
            return Attempt(slot, reviewer, selection.ABSENT, absence.INCOMPLETE, verdict.incomplete_reason)
        problem = protocol.contradiction(verdict, plan, workspace=workspace)
        if problem is not None:
            return Attempt(slot, reviewer, selection.ABSENT, absence.INVALID, problem)
        loaded.verdicts[slot] = verdict
        shipped = protocol.judge_planned(verdict, plan, workspace=workspace).decision == SHIP
        return Attempt(slot, reviewer, selection.PASS if shipped else selection.FAIL)
    missing = data["absence"] or {}
    kind = missing.get("kind")
    if kind not in absence.KINDS or kind == absence.OK:
        return Attempt(slot, reviewer, selection.ABSENT, absence.INVALID, "unknown absence record")
    reset_at = missing.get("reset_at")
    unread = missing.get("unread") or []
    if not isinstance(unread, list) or not all(isinstance(item, str) for item in unread):
        return Attempt(slot, reviewer, selection.ABSENT, absence.INVALID, "unknown absence record")
    return Attempt(
        slot,
        reviewer,
        selection.ABSENT,
        kind,
        str(missing.get("detail") or ""),
        datetime.fromisoformat(reset_at) if reset_at else None,
        str(missing.get("provider") or ""),
        tuple(unread),
    )


def load(
    directory: Path,
    plan: Mapping[str, Any],
    run_id: str,
    workspace: Path | str | None = None,
) -> LoadedAttempts:
    ws = workspace if workspace is not None else plan.get("workspace")
    loaded = LoadedAttempts()
    chain = {item["name"] for item in plan["chain"]}
    seen: set[int] = set()
    paths = sorted(directory.glob("attempt-*.json")) if directory.is_dir() else []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            loaded.errors.append(f"{path.name}: unreadable ({exc})")
            continue
        errors = loaded.errors
        if not _check(isinstance(data, dict) and set(data) == ARTIFACT_KEYS, errors, f"{path.name}: wrong keys"):
            continue
        valid = all(
            (
                _check(data["schema"] == SCHEMA_VERSION, errors, f"{path.name}: unknown schema"),
                _check(str(data["run_id"]) == run_id, errors, f"{path.name}: belongs to another run"),
                _check(data["pr"] == plan["pr"], errors, f"{path.name}: names another pull request"),
                _check(data["head_sha"] == plan["head_sha"], errors, f"{path.name}: names another head"),
                _check(
                    isinstance(data["slot"], int) and 1 <= data["slot"] <= int(plan["max_attempts"]),
                    errors,
                    f"{path.name}: invalid slot",
                ),
                _check(data["reviewer"] in chain, errors, f"{path.name}: reviewer outside the bucket chain"),
                _check(data["outcome"] in (VERDICT_OUTCOME, ABSENT_OUTCOME), errors, f"{path.name}: bad outcome"),
                _check(path.name == artifact_name(data["slot"]), errors, f"{path.name}: file name and slot differ"),
            )
        )
        if not valid:
            continue
        if not _check(data["slot"] not in seen, errors, f"{path.name}: duplicate slot"):
            continue
        seen.add(data["slot"])
        loaded.attempts.append(_attempt(data, plan, loaded, workspace=ws))
    loaded.attempts.sort(key=lambda attempt: attempt.slot)
    expected = list(range(1, len(loaded.attempts) + 1))
    _check([attempt.slot for attempt in loaded.attempts] == expected, loaded.errors, "attempt slots are not contiguous")
    return loaded


def replay(selection_input: SelectionInput, attempts: list[Attempt]) -> list[str]:
    errors: list[str] = []
    for index, attempt in enumerate(attempts):
        step = selection.next_step(selection_input, attempts[:index])
        expected = step.reviewer.name if step.kind == selection.REVIEW and step.reviewer else None
        if expected != attempt.reviewer:
            errors.append(f"slot {attempt.slot} ran {attempt.reviewer}, but the policy selects {expected or step.kind}")
    return errors


def decide(selection_input: SelectionInput, loaded: LoadedAttempts) -> tuple[Step, list[str]]:
    errors = list(loaded.errors) + replay(selection_input, loaded.attempts)
    step = selection.next_step(selection_input, loaded.attempts)
    return step, errors
