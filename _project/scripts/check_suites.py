from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

LATEST_QUERY = "filter=latest&per_page=100"
LATEST_JQ = (
    ".check_runs[] | {id, name, status, conclusion, started_at, completed_at, details_url, head_sha,"
    " check_suite: {id: .check_suite.id}}"
)
REQUIRED_CONTEXTS_JQ = '.[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context'
SUCCESS = frozenset({"success"})
NON_BLOCKING = frozenset({"success", "neutral", "skipped"})
RERUN_FAILED = frozenset({"failure", "timed_out"})
RERUN_ALL = frozenset({"cancelled"})
SUPERSEDED_OK = "cancelled"
ACTIONS_RUN_RE = re.compile(r"/actions/runs/([0-9]+)(?:/|$)")
_EARLIEST = ""


def latest_check_runs_endpoint(repo: str, sha: str) -> str:
    return f"repos/{repo}/commits/{sha}/check-runs?{LATEST_QUERY}"


def required_contexts_endpoint(repo: str, branch: str) -> str:
    return f"repos/{repo}/rules/branches/{branch}"


def parse_required_contexts(text: str) -> tuple[str, ...]:
    names = dict.fromkeys(line.strip() for line in text.splitlines() if line.strip())
    if not names:
        raise ValueError("the branch rules list no required status checks")
    return tuple(names)


def required_contexts_from_rules(rules: Any) -> tuple[str, ...]:
    if not isinstance(rules, list):
        raise ValueError("the branch rules are not a list")
    names = [
        check["context"]
        for rule in rules
        if isinstance(rule, Mapping) and rule.get("type") == "required_status_checks"
        for check in (rule.get("parameters") or {}).get("required_status_checks") or []
    ]
    return parse_required_contexts("\n".join(names))


def parse_check_run_lines(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError("check run record is not an object")
        records.append(record)
    return records


@dataclass(frozen=True)
class Suite:
    name: str
    suite_id: int | None
    check_run_id: int | None
    status: str
    conclusion: str | None
    started_at: str
    url: str
    head_sha: str

    @property
    def actions_run_id(self) -> int | None:
        match = ACTIONS_RUN_RE.search(self.url)
        return int(match.group(1)) if match else None

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    def passes(self, passing: frozenset[str] = SUCCESS) -> bool:
        return self.completed and self.conclusion in passing

    def failed(self, passing: frozenset[str] = SUCCESS) -> bool:
        return self.completed and self.conclusion not in passing

    def describe(self) -> str:
        outcome = self.conclusion if self.completed else self.status
        return f"{self.name!r} suite {self.suite_id} {outcome} ({self.url or 'no run URL'})"

    def verdict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion,
            "started_at": self.started_at,
            "head_sha": self.head_sha,
            "suite_id": self.suite_id,
            "url": self.url,
        }


def _suite_from(record: Mapping[str, Any]) -> Suite:
    raw_suite = record.get("check_suite")
    suite_id = raw_suite.get("id") if isinstance(raw_suite, Mapping) else record.get("suite_id")
    check_run_id = record.get("id")
    return Suite(
        name=str(record.get("name") or ""),
        suite_id=suite_id if isinstance(suite_id, int) else None,
        check_run_id=check_run_id if isinstance(check_run_id, int) else None,
        status=str(record.get("status") or ""),
        conclusion=record.get("conclusion") if isinstance(record.get("conclusion"), str) else None,
        started_at=str(record.get("started_at") or _EARLIEST),
        url=str(record.get("details_url") or record.get("url") or ""),
        head_sha=str(record.get("head_sha") or ""),
    )


def _recency(suite: Suite) -> tuple[str, int]:
    return (suite.started_at, suite.check_run_id or 0)


def suites_by_name(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Suite]]:
    newest: dict[tuple[str, int | None], Suite] = {}
    for record in records:
        suite = _suite_from(record)
        key = (suite.name, suite.suite_id)
        if key not in newest or _recency(suite) > _recency(newest[key]):
            newest[key] = suite
    grouped: dict[str, list[Suite]] = {}
    for suite in newest.values():
        grouped.setdefault(suite.name, []).append(suite)
    for suites in grouped.values():
        suites.sort(key=_recency, reverse=True)
    return grouped


@dataclass(frozen=True)
class NameEvaluation:
    name: str
    suites: tuple[Suite, ...]
    passing: frozenset[str] = SUCCESS

    @property
    def missing(self) -> bool:
        return not self.suites

    @property
    def superseded(self) -> tuple[Suite, ...]:
        if not self.suites or not self.suites[0].passes(self.passing):
            return ()
        return tuple(suite for suite in self.suites[1:] if suite.completed and suite.conclusion == SUPERSEDED_OK)

    @property
    def failed(self) -> tuple[Suite, ...]:
        return tuple(suite for suite in self.suites if suite.failed(self.passing) and suite not in self.superseded)

    @property
    def pending(self) -> tuple[Suite, ...]:
        return tuple(suite for suite in self.suites if not suite.completed)

    @property
    def hidden_failures(self) -> tuple[Suite, ...]:
        if not self.suites or not self.suites[0].passes(self.passing):
            return ()
        return self.failed

    @property
    def green(self) -> bool:
        return bool(self.suites) and not self.failed and not self.pending

    def worst(self) -> Suite | None:
        if self.failed:
            return self.failed[0]
        if self.pending:
            return self.pending[0]
        return self.suites[0] if self.suites else None


def evaluate(
    records: Iterable[Mapping[str, Any]], names: Sequence[str], passing: frozenset[str] = SUCCESS
) -> dict[str, NameEvaluation]:
    grouped = suites_by_name(records)
    return {name: NameEvaluation(name, tuple(grouped.get(name, ())), passing) for name in names}


def all_green(records: Iterable[Mapping[str, Any]], names: Sequence[str], passing: frozenset[str] = SUCCESS) -> bool:
    if not names:
        return False
    return all(item.green for item in evaluate(records, names, passing).values())


def hidden_failure_reasons(evaluations: Mapping[str, NameEvaluation]) -> list[str]:
    return [
        f"required check {suite.describe()} is hidden behind a newer success; "
        "the merge rollup still counts it, so run make pr-rerun"
        for item in evaluations.values()
        for suite in item.hidden_failures
    ]


@dataclass(frozen=True)
class SuiteRerun:
    run_id: int
    scope: str
    suites: tuple[Suite, ...]

    def arguments(self, repo: str) -> list[str]:
        base = ["run", "rerun", str(self.run_id), "--repo", repo]
        return base if self.scope == "all" else [*base, "--failed"]


class Unrerunnable(ValueError):
    pass


def plan_reruns(evaluations: Mapping[str, NameEvaluation]) -> list[SuiteRerun]:
    by_run: dict[int, list[Suite]] = {}
    for item in evaluations.values():
        for suite in item.failed:
            if suite.conclusion not in RERUN_FAILED | RERUN_ALL:
                raise Unrerunnable(f"{suite.describe()} ended {suite.conclusion!r}; re-running it is not supported")
            run_id = suite.actions_run_id
            if run_id is None:
                raise Unrerunnable(f"{suite.describe()} has no Actions run to re-run")
            by_run.setdefault(run_id, []).append(suite)
    return [
        SuiteRerun(
            run_id,
            "all" if any(suite.conclusion in RERUN_ALL for suite in suites) else "failed",
            tuple(suites),
        )
        for run_id, suites in sorted(by_run.items())
    ]


def verdicts(records: Iterable[Mapping[str, Any]], passing: frozenset[str] = NON_BLOCKING) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, suites in suites_by_name(records).items():
        item = NameEvaluation(name, tuple(suites), passing)
        worst = item.worst()
        if worst is not None:
            result[name] = worst.verdict() | {"hidden_failure": worst in item.hidden_failures}
    return result
