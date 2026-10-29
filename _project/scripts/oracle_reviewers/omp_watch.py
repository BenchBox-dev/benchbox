from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import absence, omp_bump, omp_pin
from .github import RUN_TITLE_PULL, TRUSTED_EVENTS, WORKFLOW_PATH, GitHubError, get_json, parse_time
from .policy import load_policy

POLICY_PATH = omp_bump.POLICY_PATH
POLICY_GIT_PATH = ".github/oracle-reviewers.yml"
WINDOW_REVIEWS = 10
WINDOW = timedelta(hours=48)
BASELINE = timedelta(days=7)
RETENTION = timedelta(days=21)
RATE_FACTOR = 2
MIN_COUNTED = 2
COUNTED_KINDS = frozenset({absence.INVALID, absence.EMPTY, absence.ERROR, absence.TIMEOUT})
REPORTED_KINDS = (
    absence.QUOTA,
    absence.AUTH,
    absence.INCOMPLETE,
    absence.INVALID,
    absence.EMPTY,
    absence.ERROR,
    absence.TIMEOUT,
)
CONTRACT_SIGNATURES = (
    (absence.EMPTY, "the reviewer ended without a final answer"),
    (absence.EMPTY, "exit code 0 with no output"),
    (absence.ERROR, "the reviewer could not be started"),
)
ATTEMPT_ARTIFACT_NAMES = tuple(f"oracle-verdict-attempt-{slot}" for slot in range(1, 6))
TRUSTED_BRANCH = "develop"
ORACLE_WORKFLOW = WORKFLOW_PATH
ORACLE_EVENTS = TRUSTED_EVENTS | {"push"}
WATCH_WORKFLOW = ".github/workflows/omp-pin-watch.yml"
WATCH_EVENTS = frozenset({"schedule", "workflow_dispatch"})
LEDGER_ARTIFACT = "omp-watch-ledger"
LEDGER_FILE = "ledger.json"
LEDGER_SCHEMA = 1
DETAIL_LIMIT = 160
HISTORY_LIMIT = 40
ARTIFACT_PAGE = 100
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_WORKERS = 8
PENDING, CLEAR, REVERT, IDLE = "pending", "clear", "revert", "idle"
BOT_AUTHOR_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
REVERT_SUBJECT_PREFIX = "revert(oracle): restore omp "
LEDGER_PAGES = 10

JsonGetter = Callable[[str], Any]
BytesGetter = Callable[[str], bytes]


class WatchError(Exception):
    pass


@dataclass(frozen=True)
class Record:
    artifact_id: int
    run_id: int
    created_at: datetime
    reviewer: str
    slot: int
    outcome: str
    kind: str
    detail: str

    @property
    def counted(self) -> bool:
        return self.kind in COUNTED_KINDS

    @property
    def contract_error(self) -> bool:
        return any(self.kind == kind and self.detail.startswith(text) for kind, text in CONTRACT_SIGNATURES)

    def to_json(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "created_at": self.created_at.isoformat(),
            "reviewer": self.reviewer,
            "slot": self.slot,
            "outcome": self.outcome,
            "kind": self.kind,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Record:
        try:
            return cls(
                int(data["artifact_id"]),
                int(data["run_id"]),
                _aware(datetime.fromisoformat(str(data["created_at"]))),
                str(data["reviewer"]),
                int(data["slot"]),
                str(data["outcome"]),
                str(data["kind"]),
                str(data["detail"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WatchError(f"the ledger holds a malformed record: {exc}") from exc


@dataclass(frozen=True)
class PolicyCommit:
    sha: str
    committed_at: datetime
    subject: str
    text: str
    author_email: str = ""


@dataclass(frozen=True)
class PinChange:
    sha: str
    merged_at: datetime
    subject: str
    author_email: str
    version: str
    previous_version: str
    previous_text: str

    @property
    def is_revert(self) -> bool:
        return self.author_email == BOT_AUTHOR_EMAIL and self.subject.startswith(REVERT_SUBJECT_PREFIX)


@dataclass(frozen=True)
class Mix:
    attempts: int
    counted: int
    kinds: Mapping[str, int]

    @property
    def rate(self) -> float:
        return self.counted / self.attempts if self.attempts else 0.0


@dataclass(frozen=True)
class Assessment:
    state: str
    reasons: tuple[str, ...]
    baseline: Mix
    window: Mix
    reviews: int
    closes_at: datetime
    contract_errors: tuple[Record, ...] = ()

    def render(self, change: PinChange) -> str:
        lines = [
            f"omp {change.previous_version} -> {change.version} merged {change.merged_at.isoformat()}: {self.state}",
            f"window: {self.reviews} of {WINDOW_REVIEWS} reviews, closes by {self.closes_at.isoformat()}",
            _mix_line("baseline (previous 7 days)", self.baseline),
            _mix_line("window", self.window),
        ]
        lines += [f"- {reason}" for reason in self.reasons]
        return "\n".join(lines) + "\n"


def _aware(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _mix_line(label: str, mix: Mix) -> str:
    kinds = ", ".join(f"{kind}={mix.kinds.get(kind, 0)}" for kind in REPORTED_KINDS)
    return f"{label}: {mix.attempts} attempts, {mix.counted} absent or invalid ({mix.rate:.1%}); {kinds}"


def parse_attempt(artifact: Mapping[str, Any], payload: Mapping[str, Any], run: Mapping[str, Any]) -> Record | None:
    started = parse_time(run.get("run_started_at") or run.get("created_at"))
    if started is None:
        return None
    try:
        missing = payload.get("absence")
        kind = absence.OK if missing is None else str(missing["kind"])
        detail = "" if missing is None else str(missing.get("detail", ""))
        return Record(
            int(artifact["id"]),
            int(run["id"]),
            _aware(started),
            str(payload["reviewer"]),
            int(payload["slot"]),
            str(payload["outcome"]),
            kind,
            detail[:DETAIL_LIMIT],
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_ledger(text: str) -> list[Record]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise WatchError("the ledger is not JSON") from exc
    if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA or not isinstance(data.get("records"), list):
        raise WatchError("the ledger has an unknown shape")
    return [Record.from_json(item) for item in data["records"]]


def dump_ledger(records: Iterable[Record]) -> str:
    ordered = sorted(records, key=lambda record: (record.created_at, record.run_id, record.slot))
    payload = {"schema": LEDGER_SCHEMA, "records": [record.to_json() for record in ordered]}
    return json.dumps(payload, indent=1, sort_keys=True) + "\n"


def merge(known: Iterable[Record], fresh: Iterable[Record], keep_since: datetime) -> list[Record]:
    merged = {record.artifact_id: record for record in known}
    merged.update({record.artifact_id: record for record in fresh})
    return [record for record in merged.values() if record.created_at >= keep_since]


def _version(commit: PolicyCommit) -> str | None:
    try:
        return omp_pin.pin_from_text(commit.text).version
    except omp_pin.PinError:
        return None


def latest_pin_change(history: Sequence[PolicyCommit]) -> PinChange | None:
    for newer, older in zip(history, history[1:]):
        current, previous = _version(newer), _version(older)
        if current is None or previous is None:
            return None
        if current != previous:
            return PinChange(
                newer.sha, _aware(newer.committed_at), newer.subject, newer.author_email, current, previous, older.text
            )
    return None


def _window(records: Sequence[Record], merged_at: datetime) -> tuple[list[Record], int]:
    chosen: list[Record] = []
    runs: list[int] = []
    for record in sorted(records, key=lambda item: (item.created_at, item.run_id, item.slot)):
        if record.created_at < merged_at or record.created_at >= merged_at + WINDOW:
            continue
        if record.run_id not in runs:
            if len(runs) == WINDOW_REVIEWS:
                break
            runs.append(record.run_id)
        chosen.append(record)
    return chosen, len(runs)


def _mix(records: Iterable[Record]) -> Mix:
    items = list(records)
    return Mix(len(items), sum(record.counted for record in items), Counter(record.kind for record in items))


def assess(records: Sequence[Record], change: PinChange, now: datetime) -> Assessment:
    baseline = _mix(record for record in records if change.merged_at - BASELINE <= record.created_at < change.merged_at)
    chosen, reviews = _window(records, change.merged_at)
    window = _mix(chosen)
    closes_at = change.merged_at + WINDOW
    contract = tuple(record for record in chosen if record.contract_error)
    reasons: list[str] = [
        f"harness-contract error in run {record.run_id}: {record.kind}: {record.detail}" for record in contract
    ]
    if window.counted >= MIN_COUNTED and window.rate > RATE_FACTOR * baseline.rate:
        reasons.append(
            f"absent or invalid attempts are {window.rate:.1%} against a 7-day baseline of {baseline.rate:.1%}, "
            f"more than {RATE_FACTOR} times the baseline"
        )
    if reasons:
        return Assessment(REVERT, tuple(reasons), baseline, window, reviews, closes_at, contract)
    settled = reviews >= WINDOW_REVIEWS or now >= closes_at
    return Assessment(CLEAR if settled else PENDING, (), baseline, window, reviews, closes_at, contract)


def render_revert_body(change: PinChange, assessment: Assessment) -> str:
    reasons = "\n".join(f"- {reason}" for reason in assessment.reasons)
    return (
        f"## Description\n\nRestores the pinned omp build {change.previous_version} after {change.version} merged on "
        f"{change.merged_at.isoformat()}. Only the `omp:` section of the reviewer policy changes.\n\n"
        f"The first reviews on {change.version} tripped the post-bump watch:\n\n{reasons}\n\n"
        f"```\n{assessment.render(change)}```\n\n"
        "## Before merge\n\nThe required `oracle-gate` holds any pull request that changes the pin until the owner "
        "posts the compatibility evidence for the pin that pull request carries, here "
        f"{change.previous_version}. From this branch on the owner's Mac (darwin-arm64) run `make "
        f"omp-candidate-check VERSION={change.previous_version}` and post the line it prints as a comment on this pull "
        "request; a later push invalidates it. The build host does not mirror builds, so the check fails at the "
        f"download if {change.previous_version} is no longer served.\n"
    )


def revert_policy(current_text: str, change: PinChange) -> str:
    return omp_bump.replace_pin(current_text, omp_pin.pin_from_text(change.previous_text))


def policy_history(path: str = POLICY_GIT_PATH, limit: int = HISTORY_LIMIT) -> list[PolicyCommit]:
    log = _git("log", f"-n{limit}", "--format=%H%x1f%cI%x1f%ae%x1f%s", "--", path)
    commits: list[PolicyCommit] = []
    for line in log.splitlines():
        sha, committed, author, subject = line.split("\x1f", 3)
        text = _git("show", f"{sha}:{path}")
        commits.append(PolicyCommit(sha, datetime.fromisoformat(committed), subject, text, author))
    return commits


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode != 0:
        raise WatchError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def _download(path: str) -> bytes:
    failure = ""
    for attempt in range(DOWNLOAD_ATTEMPTS):
        result = subprocess.run(["gh", "api", path], capture_output=True, check=False)
        if result.returncode == 0:
            return result.stdout
        failure = result.stderr.decode("utf-8", "replace").strip()[:300]
        time.sleep(attempt + 1)
    raise WatchError(f"gh api {path} failed: {failure}")


def _unzip(blob: bytes, suffix: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = [name for name in archive.namelist() if name.endswith(suffix)]
            if len(names) != 1:
                raise WatchError(f"the artifact holds {len(names)} files ending {suffix}")
            return archive.read(names[0]).decode("utf-8")
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise WatchError(f"the artifact is not a readable archive: {exc}") from exc


def _base_refs(repo: str, run: Mapping[str, Any], getter: JsonGetter) -> set[Any]:
    listed = {
        (pull.get("base") or {}).get("ref") for pull in run.get("pull_requests") or [] if isinstance(pull, Mapping)
    }
    if listed:
        return listed
    match = RUN_TITLE_PULL.search(str(run.get("display_title") or ""))
    if match is None:
        return set()
    try:
        return {(getter(f"repos/{repo}/pulls/{match.group(1)}").get("base") or {}).get("ref")}
    except GitHubError:
        return set()


def produced_by(
    run: Mapping[str, Any], repo: str, workflow: str, events: frozenset[str], getter: JsonGetter = get_json
) -> bool:
    event = run.get("event")
    if not (
        str(run.get("path", "")).split("@", 1)[0] == workflow
        and str((run.get("repository") or {}).get("full_name", "")).lower() == repo.lower()
        and event in events
    ):
        return False
    if event == "pull_request_target":
        return _base_refs(repo, run, getter) == {TRUSTED_BRANCH}
    return run.get("head_branch") == TRUSTED_BRANCH


def _trusted_runs(
    repo: str, run_ids: Iterable[int], workflow: str, events: frozenset[str], getter: JsonGetter
) -> dict[int, Mapping[str, Any]]:
    def lookup(run_id: int) -> tuple[int, Mapping[str, Any]]:
        try:
            run = getter(f"repos/{repo}/actions/runs/{run_id}")
        except GitHubError:
            return run_id, {}
        return run_id, run if produced_by(run, repo, workflow, events, getter) else {}

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        return {run_id: run for run_id, run in pool.map(lookup, sorted(set(run_ids))) if run}


def _run_id(artifact: Mapping[str, Any]) -> int:
    return int((artifact.get("workflow_run") or {}).get("id", 0))


def restore_ledger(repo: str, getter: JsonGetter = get_json, downloader: BytesGetter = _download) -> list[Record]:
    for page in range(1, LEDGER_PAGES + 1):
        listing = getter(
            f"repos/{repo}/actions/artifacts?name={LEDGER_ARTIFACT}&per_page={ARTIFACT_PAGE}&page={page}"
        ).get("artifacts", [])
        live = sorted(
            (item for item in listing if not item.get("expired")),
            key=lambda item: str(item.get("created_at", "")),
            reverse=True,
        )
        trusted = _trusted_runs(repo, (_run_id(item) for item in live), WATCH_WORKFLOW, WATCH_EVENTS, getter)
        for artifact in live:
            if _run_id(artifact) in trusted:
                blob = downloader(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip")
                return load_ledger(_unzip(blob, LEDGER_FILE))
        if len(listing) < ARTIFACT_PAGE:
            break
    return []


def collect(
    repo: str,
    horizon: datetime,
    known: Iterable[int],
    *,
    reviewers: frozenset[str],
    getter: JsonGetter = get_json,
    downloader: BytesGetter = _download,
) -> list[Record]:
    seen = set(known)
    wanted = [
        artifact
        for name in ATTEMPT_ARTIFACT_NAMES
        for artifact in _artifacts(repo, name, horizon, getter)
        if not artifact.get("expired") and artifact["id"] not in seen
    ]
    runs = _trusted_runs(repo, (_run_id(artifact) for artifact in wanted), ORACLE_WORKFLOW, ORACLE_EVENTS, getter)
    trusted = [artifact for artifact in wanted if _run_id(artifact) in runs]

    def fetch(artifact: Mapping[str, Any]) -> Record | None:
        text = _unzip(downloader(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"), ".json")
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise WatchError(f"artifact {artifact['id']} is not JSON") from exc
        return parse_attempt(artifact, payload, runs[_run_id(artifact)]) if isinstance(payload, dict) else None

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        records = list(pool.map(fetch, trusted))
    return [record for record in records if record is not None and record.reviewer in reviewers]


def _artifacts(repo: str, name: str, horizon: datetime, getter: JsonGetter) -> Iterable[Mapping[str, Any]]:
    page = 1
    while True:
        listing = getter(f"repos/{repo}/actions/artifacts?name={name}&per_page={ARTIFACT_PAGE}&page={page}").get(
            "artifacts", []
        )
        for artifact in listing:
            created = parse_time(artifact.get("created_at"))
            if created is not None and _aware(created) >= horizon:
                yield artifact
        oldest = parse_time(listing[-1].get("created_at")) if listing else None
        if len(listing) < ARTIFACT_PAGE or (oldest is not None and _aware(oldest) < horizon):
            return
        page += 1


def _publish(values: Mapping[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in values.items())


def command_evaluate(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    try:
        change = latest_pin_change(policy_history())
        if change is None or change.is_revert:
            _publish({"state": IDLE, "version": ""})
            print("no pin bump to watch")
            return 0
        policy_text = Path(args.policy).read_text(encoding="utf-8")
        reviewers = frozenset(
            name for name, reviewer in load_policy(Path(args.policy)).reviewers.items() if reviewer.harness == "omp"
        )
        known = restore_ledger(args.repo)
        horizon = change.merged_at - BASELINE
        fresh = collect(args.repo, horizon, (record.artifact_id for record in known), reviewers=reviewers)
        records = merge(known, fresh, change.merged_at - RETENTION)
    except (OSError, WatchError, GitHubError, omp_pin.PinError, omp_bump.BumpError) as exc:
        print(f"omp-watch: error: {exc}", file=sys.stderr)
        return 1
    assessment = assess(records, change, now)
    report = assessment.render(change)
    (out / LEDGER_FILE).write_text(dump_ledger(records), encoding="utf-8")
    (out / "assessment.txt").write_text(report, encoding="utf-8")
    if assessment.state == REVERT:
        try:
            (out / "policy.yml").write_text(revert_policy(policy_text, change), encoding="utf-8")
        except (omp_bump.BumpError, omp_pin.PinError) as exc:
            print(f"omp-watch: error: {exc}", file=sys.stderr)
            return 1
        (out / "pr-body.md").write_text(render_revert_body(change, assessment), encoding="utf-8")
    _publish({"state": assessment.state, "version": change.version, "previous": change.previous_version})
    print(report, end="")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omp-watch")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--repo", required=True)
    evaluate.add_argument("--policy", default=str(POLICY_PATH))
    evaluate.add_argument("--out-dir", required=True)
    evaluate.set_defaults(handler=command_evaluate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
