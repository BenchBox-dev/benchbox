from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import cli, commands, credentials, driver, edits, github, isolation, protocol, redaction_check, report, selection
from .classifier import ChangedFile, soundness_scope
from .commands import LOOPBACK_PROXY
from .policy import Policy, load_policy
from .verdict import DO_NOT_SHIP

BLOCKING_SEVERITIES = ("Critical", "High")
OVERRIDE_PATTERN = re.compile(r"^Local oracle override:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
KEYCHAIN_SERVICE = "TYPESAFE_API_KEY"
KEYCHAIN_STEP = f'security add-generic-password -s {KEYCHAIN_SERVICE} -a "$USER" -w'
LOCAL_CLIENT = "oracle-local"
CLOUD_CLIENT = "oracle-cloud"
LOCAL_GATEWAY_URL = "http://127.0.0.1:4000"
GATEWAY_DIRECTORY = Path(".omp") / "oracle-gateway"
LOCAL_TOKEN_PATH = GATEWAY_DIRECTORY / "local-client.token"
HOST_TOKEN_FILES = {
    "ORACLE_HOST_TOKEN_LOCAL": GATEWAY_DIRECTORY / "local-client.token",
    "ORACLE_HOST_TOKEN_CLOUD": GATEWAY_DIRECTORY / "cloud-client.token",
    "ORACLE_HOST_TOKEN_CLIENT": GATEWAY_DIRECTORY / "client.token",
}
CLOUD_TOKEN_ENV = "ORACLE_CLOUD_GATEWAY_TOKEN"
CLOUD_SESSION_ENV = "BENCHBOX_EPHEMERAL_CLONE"
CLOUD_STATUS_FILE = "benchbox-cloud-gateway.status"
SOUNDNESS_MANIFEST = ".github/soundness-paths.txt"
INERT_MANIFEST = ".github/inert-paths.txt"
HIDDEN_TEXT = re.compile(
    r"<!--.*?(?:-->|\Z)"
    r"|^[ \t]*(`{3,}+)[^\n]*\n.*?(?:^ {0,3}\1`*[ \t]*$|\Z)"
    r"|^[ \t]*(~{3,}+)[^\n]*\n.*?(?:^ {0,3}\2~*[ \t]*$|\Z)"
    r"|<details\b.*?(?:</details>|\Z)"
    r"|^(?: {4}|\t)[^\n]*$",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
PLACEHOLDER = re.compile(r"<.*>")
REMOTE_SLUG = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
DEFAULT_REPOSITORY = "BenchBox-dev/BenchBox"
CACHE_SCHEMA = 2
CAPTURE_DIR = "captures"
POOL_BLOCKS_FILE = "pool-blocks.json"
LOCK_WAIT_SECONDS = 3600.0
LOCK_POLL_SECONDS = 1.0
REACHABILITY_SECONDS = 10
SUMMARY_LIMIT = 1200
DETAIL_LIMIT = 600
FINDING_LIMIT = 20
ERROR_LIMIT = 600
EXIT_PROCEED = 0
EXIT_REFUSED = 1
EXIT_UNAVAILABLE = 2
ADVISORY = "Local oracle review (advisory; it may differ from the CI oracle and posts nothing to GitHub)"
WITHHELD_FROM_PHASES = frozenset(
    {*credentials.NAMES, "TS_AUTHKEY", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", *HOST_TOKEN_FILES}
)
HANDOVER_ENV = "ORACLE_LOCAL_HANDOVER_FD"
ROOT_ENV = "ORACLE_LOCAL_ROOT"
WORK_ENV = "ORACLE_LOCAL_WORK"
BASE_ENV = "ORACLE_LOCAL_BASE"
HEAD_ENV = "ORACLE_LOCAL_HEAD"
PR_ENV = "ORACLE_LOCAL_PR"
REPOSITORY_ENV = "ORACLE_LOCAL_REPOSITORY"
STATE_ENV = "ORACLE_LOCAL_STATE"
PROXY_ENV = "ORACLE_LOCAL_PROXY"
SCAN_ENV = "ORACLE_LOCAL_SCAN"
EXCLUDE_ENV = "ORACLE_LOCAL_EXCLUDE"
PACKAGE_ROOT = Path(__file__).resolve().parents[3]
STATUS_NAMES = {"A": "added", "C": "added", "D": "removed", "R": "renamed"}
GITHUB_ACCESS_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")
SCRUBBED_AT_START = frozenset({*credentials.NAMES, "TS_AUTHKEY", *GITHUB_ACCESS_ENV})
GATEWAY_CANONICAL = (
    "clients.json",
    "client.token",
    "local-client.token",
    "cloud-client.token",
    "cloud-gateway-url.txt",
    "run.sh",
    "home",
)
PRIOR_FILE = "prior.json"


class Unavailable(Exception):
    pass


@dataclass(frozen=True)
class Credentials:
    client: str
    url: str
    token: str
    proxy: str = ""


class Reporter:
    def __init__(self) -> None:
        self.secrets: tuple[str, ...] = ()

    def hide(self, *values: str) -> None:
        self.secrets = tuple(dict.fromkeys((*self.secrets, *(value for value in values if value))))

    def say(self, text: str, *, error: bool = False) -> None:
        print(credentials.redact(text, self.secrets), file=sys.stderr if error else sys.stdout)


def state_dir() -> Path:
    return Path.home() / ".cache" / "benchbox" / "local-oracle"


def _git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise Unavailable(f"git {args[0]} failed: {result.stderr.decode('utf-8', errors='replace').strip()[:300]}")
    return result.stdout


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode != 0:
        raise Unavailable(f"git {args[0]} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def _repository(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    match = REMOTE_SLUG.search(result.stdout.strip()) if result.returncode == 0 else None
    return match.group(1) if match else DEFAULT_REPOSITORY


def _name_status(root: Path, base: str, head: str) -> list[tuple[str, str, str | None]]:
    tokens = _git(root, "diff", "--name-status", "-z", "-M", base, head).split("\0")
    entries: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        code = tokens[index][0]
        if code in "RC":
            entries.append((code, tokens[index + 2], tokens[index + 1]))
            index += 3
        else:
            entries.append((code, tokens[index + 1], None))
            index += 2
    return entries


def _numstat(root: Path, base: str, head: str) -> dict[str, tuple[int, int]]:
    tokens = _git(root, "diff", "--numstat", "-z", "-M", base, head).split("\0")
    counts: dict[str, tuple[int, int]] = {}
    index = 0
    while index < len(tokens) and tokens[index]:
        added, deleted, path = tokens[index].split("\t", 2)
        step = 1
        if not path:
            path = tokens[index + 2]
            step = 3
        counts[path] = (int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0)
        index += step
    return counts


def file_states(root: Path, base: str, head: str) -> list[protocol.FileState]:
    tokens = _git(
        root, "diff", "--raw", "-z", "--full-index", "--no-abbrev", "--no-ext-diff", "--no-textconv", "-M", base, head
    ).split("\0")
    states: list[protocol.FileState] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        old_mode, new_mode, old_id, new_id, code = tokens[index].removeprefix(":").split(" ")
        renamed = code[0] in "RC"
        previous, path = (tokens[index + 1], tokens[index + 2]) if renamed else (None, tokens[index + 1])
        old = None if code[0] == "A" else (old_mode, old_id)
        new = None if code[0] == "D" else (new_mode, new_id)
        states.append(protocol.file_state(path, previous, old, new))
        index += 3 if renamed else 2
    return states


def changed_files(root: Path, base: str, head: str) -> list[ChangedFile]:
    counts = _numstat(root, base, head)
    return [
        ChangedFile(
            path=path,
            previous_path=previous,
            additions=counts.get(path, (0, 0))[0],
            deletions=counts.get(path, (0, 0))[1],
            status=STATUS_NAMES.get(code, "modified"),
        )
        for code, path, previous in _name_status(root, base, head)
    ]


def _load_pool_blocks(path: Path) -> dict[str, selection.PoolBlock]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {pool: selection.PoolBlock.from_json(block) for pool, block in data.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _store_pool_blocks(path: Path, blocks: Mapping[str, Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    scratch.write_text(json.dumps(dict(blocks), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(scratch, path)


@dataclass(frozen=True)
class LocalEnvironment:
    layout: driver.Layout
    policy: Path
    pr: str
    repo: str
    root: Path
    base_ref: str
    head: str
    state: Path
    scan_roots: tuple[Path, ...]
    scan_excludes: tuple[Path, ...]
    proxy: str
    loopback_http: bool = True
    has_app: bool = False
    scan_secrets: tuple[str, ...] = ("TYPESAFE_API_KEY", "TS_AUTHKEY", "OMP_GATEWAY_TOKEN", *HOST_TOKEN_FILES)
    redacted_secrets: tuple[str, ...] = ("TYPESAFE_API_KEY", "TS_AUTHKEY", "OMP_GATEWAY_TOKEN", *HOST_TOKEN_FILES)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str], policy: Path) -> LocalEnvironment:
        work = Path(environ[WORK_ENV])
        return cls(
            layout=driver.Layout.local(work),
            policy=policy,
            pr=environ.get(PR_ENV, ""),
            repo=environ[REPOSITORY_ENV],
            root=Path(environ[ROOT_ENV]),
            base_ref=environ[BASE_ENV],
            head=environ[HEAD_ENV],
            state=Path(environ[STATE_ENV]),
            scan_roots=tuple(Path(item) for item in environ.get(SCAN_ENV, "").split(os.pathsep) if item),
            scan_excludes=tuple(Path(item) for item in environ.get(EXCLUDE_ENV, "").split(os.pathsep) if item),
            proxy=environ.get(PROXY_ENV, ""),
        )

    def sink(self) -> driver.Sink:
        raise driver.CredentialError("the local review posts nothing")

    def session(self) -> contextlib.AbstractContextManager[None]:
        return commands.gateway_proxy(self.proxy)

    def isolation(self) -> int:
        if not sys.platform.startswith("linux"):
            print("oracle-local: process isolation checks need Linux and were skipped on this machine")
            return 0
        try:
            isolation.require_isolation()
        except (OSError, ValueError, KeyError) as exc:
            print(f"oracle-local: isolation prerequisite not met, the review runs without it: {exc}")
            return 0
        print("oracle-local: Linux non-root UID, ptrace capabilities and Yama prerequisite passed")
        return 0

    def plan(self) -> int:
        policy = load_policy(self.policy)
        out_dir = self.layout.plan
        out_dir.mkdir(parents=True, exist_ok=True)
        pr = int(self.pr) if self.pr else None
        try:
            return self._plan(policy, out_dir, pr)
        except Unavailable as exc:
            print(f"oracle-verdict: error: {exc}", file=sys.stderr)
            return 2

    def _plan(self, policy: Policy, out_dir: Path, pr: int | None) -> int:
        base = _git(self.root, "merge-base", self.base_ref, self.head).strip()
        plan = cli._base_plan(policy, self.repo, pr, os.environ["GITHUB_RUN_ID"])
        plan.update({"base_sha": base, "base_ref": self.base_ref.removeprefix("origin/"), "head_sha": self.head})
        files = changed_files(self.root, base, self.head)
        if not files:
            plan["decision_reason"] = f"no committed changes against {self.base_ref}"
            return cli._finish_plan(out_dir, plan)
        scope = soundness_scope(files, policy.classifier, cli._soundness_predicate())
        plan["scope_paths"] = list(scope.paths)
        plan["gate_paths"] = list(scope.gate_paths)
        plan["scope_reasons"] = list(scope.reasons)
        if not scope.soundness:
            plan["decision"] = cli.SUCCESS
            plan["decision_reason"] = "only exempt paths changed"
            return cli._finish_plan(out_dir, plan)
        plan["manual"] = False
        plan["previous_state"] = None
        found = protocol.History()
        base_changed: tuple[datetime | None, bool] = (None, False)
        if pr is not None:
            try:
                found = cli._plan_history(self.repo, pr, policy.bot_login)
            except edits.EditStateError as exc:
                print(f"oracle-verdict: error: {exc}", file=sys.stderr)
                return 2
            if found.error is not None:
                plan.update({"decision": cli.HOLD, "decision_reason": found.error})
                return cli._finish_plan(out_dir, plan)
            base_changed = cli._base_changed_at(self.repo, pr)
        cli._write_json(out_dir / PRIOR_FILE, found.latest)
        full_diff = _git_bytes(
            self.root, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", base, self.head
        ).decode("utf-8", errors="replace")
        blocks = selection.active_blocks(
            _load_pool_blocks(self.state / POOL_BLOCKS_FILE), datetime.now(UTC), same_head=False
        )
        return cli.plan_decision(
            plan,
            policy,
            out_dir,
            cli.RoundInputs(
                files=files,
                in_scope=set(scope.paths),
                full_diff=full_diff,
                states=file_states(self.root, base, self.head),
                found=found,
                previous_strikes=0,
                pool_blocks=blocks,
                manual=False,
                event_name="pull_request_target",
                base_changed_at=base_changed[0],
                base_change_unreadable=base_changed[1],
            ),
        )


def cloud_gateway_proxy() -> str:
    try:
        git_dir = _git(Path.cwd(), "rev-parse", "--absolute-git-dir").strip()
        lines = (Path(git_dir) / CLOUD_STATUS_FILE).read_text(encoding="utf-8").splitlines()
    except (Unavailable, OSError, ValueError):
        return ""
    for line in lines:
        name, _, value = line.partition("=")
        if name == "gateway_proxy" and LOOPBACK_PROXY.fullmatch(value.strip()):
            return value.strip()
    return ""


def resolve_credentials(environ: Mapping[str, str] | None = None) -> Credentials:
    env = {**os.environ, **_handed_over} if environ is None else environ
    if env.get(CLOUD_TOKEN_ENV) or env.get(CLOUD_SESSION_ENV) == "1":
        url = env.get("ORACLE_GATEWAY_URL", "").strip()
        token = env.get(CLOUD_TOKEN_ENV, "").strip()
        if not url or not token:
            raise Unavailable(f"the {CLOUD_CLIENT} client needs ORACLE_GATEWAY_URL and {CLOUD_TOKEN_ENV}")
        return Credentials(CLOUD_CLIENT, url, token, cloud_gateway_proxy())
    path = Path.home() / LOCAL_TOKEN_PATH
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        raise Unavailable(f"the {LOCAL_CLIENT} client token is missing or empty at ~/{LOCAL_TOKEN_PATH}")
    return Credentials(LOCAL_CLIENT, LOCAL_GATEWAY_URL, token)


class _ForcedProxy(urllib.request.BaseHandler):
    handler_order = 50

    def __init__(self, proxy: str) -> None:
        self.proxy = proxy.removeprefix("http://")

    def _route(self, request: urllib.request.Request) -> urllib.request.Request:
        request.set_proxy(self.proxy, request.type)
        return request

    http_request = https_request = _route


def reachable(url: str, proxy: str = "") -> bool:
    request = urllib.request.Request(f"{url.rstrip('/')}/healthz", method="GET")
    handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({})]
    if proxy:
        handlers.append(_ForcedProxy(proxy))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=REACHABILITY_SECONDS):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def keychain_key() -> str:
    if sys.platform != "darwin" or shutil.which("security") is None:
        return ""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


_handed_over: dict[str, str] = {}


def withhold_secrets() -> None:
    global _handed_over
    descriptor = os.environ.pop(HANDOVER_ENV, None)
    received: dict[str, str] = {}
    if descriptor is not None:
        with os.fdopen(int(descriptor), "r", encoding="utf-8") as handle:
            received = json.load(handle)
    held = {name: os.environ[name] for name in sorted(SCRUBBED_AT_START) if os.environ.get(name)}
    if not held:
        _handed_over = received
        return
    read, write = os.pipe()
    os.write(write, json.dumps({**received, **held}).encode("utf-8"))
    os.close(write)
    os.set_inheritable(read, True)
    scrubbed = {name: value for name, value in os.environ.items() if name not in held}
    scrubbed[HANDOVER_ENV] = str(read)
    os.execve(sys.executable, sys.orig_argv, scrubbed)


def _held(name: str) -> str:
    return _handed_over.get(name) or os.environ.get(name, "")


@contextlib.contextmanager
def _github_access() -> Iterator[None]:
    granted = {name: _held(name) for name in GITHUB_ACCESS_ENV if _held(name)}
    saved = {name: os.environ.get(name) for name in granted}
    os.environ.update(granted)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def classifier_key() -> str:
    return _held("TYPESAFE_API_KEY") or keychain_key()


def host_token_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for name, relative in HOST_TOKEN_FILES.items():
        try:
            value = (Path.home() / relative).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            values[name] = value
    return values


@dataclass(frozen=True)
class Trusted:
    policy: str
    soundness: str
    inert: str

    @classmethod
    def read(cls, root: Path, base_ref: str, policy_path: str) -> Trusted:
        return cls(
            _git(root, "show", f"{base_ref}:{policy_path}"),
            _git(root, "show", f"{base_ref}:{SOUNDNESS_MANIFEST}"),
            _git(root, "show", f"{base_ref}:{INERT_MANIFEST}"),
        )

    def write(self, directory: Path) -> tuple[Path, Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = (directory / "oracle-reviewers.yml", directory / "soundness-paths.txt", directory / "inert-paths.txt")
        for path, text in zip(paths, (self.policy, self.soundness, self.inert), strict=True):
            path.write_text(text, encoding="utf-8")
        return paths

    def digest(self) -> str:
        package = Path(__file__).resolve().parent
        files = sorted(item for item in (*package.glob("*.py"), *package.glob("templates/*")) if item.is_file())
        digest = hashlib.sha256()
        for text in (self.policy, self.soundness, self.inert):
            digest.update(hashlib.sha256(text.encode("utf-8")).digest())
        for item in (*files, cli.OMP_CONFIG_SOURCE):
            digest.update(item.name.encode("utf-8"))
            digest.update(item.read_bytes())
        return digest.hexdigest()


VOLATILE_PLAN_KEYS = frozenset({"run_id", "run_uuid", "created_at", "head_sha", "pool_blocked_until"})


def cache_key(plan: Mapping[str, Any], head_tree: str, trusted: Trusted) -> str:
    fingerprint = {key: value for key, value in plan.items() if key not in VOLATILE_PLAN_KEYS}
    parts = (CACHE_SCHEMA, json.dumps(fingerprint, sort_keys=True), head_tree, trusted.digest())
    return hashlib.sha256("\0".join(map(str, parts)).encode("utf-8")).hexdigest()


def cache_path(directory: Path, key: str) -> Path:
    return directory / "cache" / f"{key}.json"


@dataclass(frozen=True)
class Outcome:
    blocked: bool
    label: str
    reviewer: str
    summary: str
    findings: tuple[dict[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "label": self.label,
            "reviewer": self.reviewer,
            "summary": self.summary,
            "findings": list(self.findings),
        }

    @classmethod
    def from_json(cls, data: Any) -> Outcome | None:
        if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
            return None
        if not isinstance(data.get("blocked"), bool) or not all(
            isinstance(data.get(key), str) for key in ("label", "reviewer", "summary")
        ):
            return None
        return cls(data["blocked"], data["label"], data["reviewer"], data["summary"], tuple(data["findings"]))


def _entry_finding(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "severity": entry.get("severity", ""),
        "file": entry.get("file", ""),
        "line": entry.get("line", 0),
        "end_line": entry.get("end_line"),
        "title": entry.get("title", ""),
        "detail": entry.get("detail", ""),
        "id": entry.get("id"),
    }


def _blocking(findings: tuple[dict[str, Any], ...]) -> bool:
    return any(item["severity"] in BLOCKING_SEVERITIES for item in findings)


def judged_outcome(judgement: protocol.Judgement, reviewer: str) -> Outcome:
    findings = (
        *(_entry_finding(item.to_json()) for item in judgement.defects),
        *(_entry_finding(entry) for entry in judgement.carried),
    )
    blocked = judgement.decision == DO_NOT_SHIP or _blocking(findings)
    return Outcome(blocked, judgement.label, reviewer, judgement.summary, findings)


def recorded_outcome(record: Mapping[str, Any]) -> Outcome:
    findings = tuple(_entry_finding(entry) for entry in record.get("open_defects", ()))
    blocked = record["decision"] in (DO_NOT_SHIP, protocol.REFUSED) or _blocking(findings)
    return Outcome(
        blocked, protocol.LABELS[record["decision"]], record.get("reviewer", ""), record.get("summary", ""), findings
    )


def _load_cached(path: Path) -> Outcome | None:
    try:
        return Outcome.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _store(path: Path, outcome: Outcome, secrets: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    scratch.write_text(json.dumps(outcome.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        redaction_check.sanitize(scratch, secrets)
    except redaction_check.Unredactable:
        scratch.unlink(missing_ok=True)
        return
    os.replace(scratch, path)


@contextlib.contextmanager
def exclusive(lock_path: Path, reporter: Reporter, holder: str = "") -> Iterator[None]:
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    announced = False
    with open(lock_path, "a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Unavailable("another local oracle review of this patch kept running") from None
                if not announced:
                    reporter.say(
                        f"Waiting for another local oracle review of this patch to finish{_held_by(lock_path)}."
                    )
                    announced = True
                time.sleep(LOCK_POLL_SECONDS)
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(holder)
            handle.flush()
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _held_by(lock_path: Path) -> str:
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f" ({text})" if text else ""


def _current_branch(root: Path) -> str:
    return _git(root, "branch", "--show-current").strip()


def _holder(root: Path) -> str:
    started = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    return f"branch {_current_branch(root) or 'detached HEAD'}, started {started}"


def open_pull(repo: str, branch: str) -> int | None:
    if not branch:
        return None
    owner = repo.split("/", 1)[0]
    try:
        pulls = github.get_paginated(f"repos/{repo}/pulls?state=open&head={owner}:{branch}&per_page=100")
    except github.GitHubError as exc:
        raise Unavailable(f"the open pull request for this branch could not be read: {exc}") from exc
    numbers = [int(item["number"]) for item in pulls if isinstance(item, dict) and "number" in item]
    return numbers[0] if len(numbers) == 1 else None


def _stage_workspace(root: Path, head: str, workspace: Path) -> None:
    _git(root, "clone", "--quiet", "--no-checkout", "--shared", str(root), str(workspace))
    _git(workspace, "checkout", "--quiet", "--detach", head)


def _keep_captures(source: Path, head: str, secrets: tuple[str, ...]) -> None:
    files = sorted(source.glob("capture-*.json")) if source.is_dir() else []
    if not files:
        return
    target = state_dir() / CAPTURE_DIR / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{head[:12]}"
    target.mkdir(parents=True, exist_ok=True)
    for path in files:
        try:
            redaction_check.sanitize(path, secrets)
        except redaction_check.Unredactable:
            continue
        shutil.copyfile(path, target / path.name)


@dataclass
class Run:
    root: Path
    work: Path
    policy: Path
    soundness: Path
    inert: Path
    repository: str
    head: str
    base_ref: str
    pr: int | None
    run_id: str
    reporter: Reporter
    key: str
    omp_binary: str = ""
    access: Credentials | None = None
    relayed: set[str] = field(default_factory=set)

    @property
    def layout(self) -> driver.Layout:
        return driver.Layout.local(self.work)

    def _environ(self, phase: str, grants: Mapping[str, str]) -> dict[str, str]:
        kept = {name: value for name, value in os.environ.items() if name not in WITHHELD_FROM_PHASES}
        if phase == "prepare":
            kept.update({name: _held(name) for name in GITHUB_ACCESS_ENV if _held(name)})
        scan_roots = [self.work, state_dir()]
        excludes: list[Path] = []
        gateway = Path.home() / GATEWAY_DIRECTORY
        if gateway.is_dir():
            scan_roots.append(gateway)
            excludes.extend(gateway / name for name in GATEWAY_CANONICAL)
        if os.environ.get(CLOUD_SESSION_ENV) == "1":
            scan_roots.append(Path.home())
        kept.update(
            {
                "PYTHONPATH": str(PACKAGE_ROOT),
                "SOUNDNESS_PATH_MANIFEST": str(self.soundness),
                "INERT_PATH_MANIFEST": str(self.inert),
                "GITHUB_RUN_ID": self.run_id,
                "GITHUB_OUTPUT": str(self.work / "outputs" / f"{phase}.txt"),
                "GITHUB_ENV": str(self.work / "harness-env.txt"),
                WORK_ENV: str(self.work),
                ROOT_ENV: str(self.root),
                BASE_ENV: self.base_ref,
                HEAD_ENV: self.head,
                PR_ENV: "" if self.pr is None else str(self.pr),
                REPOSITORY_ENV: self.repository,
                STATE_ENV: str(state_dir()),
                PROXY_ENV: "" if self.access is None else self.access.proxy,
                SCAN_ENV: os.pathsep.join(str(path) for path in scan_roots),
                EXCLUDE_ENV: os.pathsep.join(str(path) for path in excludes),
                **grants,
            }
        )
        return kept

    def grants(self, phase: str) -> dict[str, str]:
        gateway = (
            {}
            if self.access is None
            else {"OMP_GATEWAY_TOKEN": self.access.token, "ORACLE_GATEWAY_URL": self.access.url}
        )
        key = {"TYPESAFE_API_KEY": self.key} if self.key else {}
        hosts = host_token_values()
        held = {name: _held(name) for name in ("TS_AUTHKEY",) if _held(name)}
        if phase == "prepare" or phase == "recover":
            return key
        if phase == "scan":
            return {**key, **held, **hosts, **{k: v for k, v in gateway.items() if k == "OMP_GATEWAY_TOKEN"}}
        if phase == "attempts":
            return gateway
        if phase == "finalize":
            return {**key, **held, **hosts, **gateway}
        return {}

    def phase(self, phase: str, extra: Mapping[str, str] | None = None) -> tuple[int, dict[str, str]]:
        outputs = self.work / "outputs"
        outputs.mkdir(exist_ok=True)
        output = outputs / f"{phase}.txt"
        output.write_text("", encoding="utf-8")
        argv = [
            sys.executable,
            "-m",
            "_project.scripts.oracle_reviewers.cli",
            "drive",
            "--policy",
            str(self.policy),
            "--environment",
            driver.LOCAL,
            phase,
        ]
        result = subprocess.run(
            argv,
            cwd=PACKAGE_ROOT,
            env={**self._environ(phase, self.grants(phase)), **(extra or {})},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self._relay(phase, result)
        values: dict[str, str] = {}
        for line in output.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            values[name] = value
        return result.returncode, values

    def _relay(self, phase: str, result: subprocess.CompletedProcess[str]) -> None:
        for line in result.stdout.splitlines():
            if line.startswith(("slot ", "oracle-verdict:", "oracle-local:")) and line not in self.relayed:
                self.relayed.add(line)
                self.reporter.say(line)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [f"phase {phase} failed"]
            self.reporter.say(tail[0][:ERROR_LIMIT], error=True)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _decided(run: Run, plan: dict[str, Any]) -> Outcome:
    os.environ["GITHUB_RUN_ID"] = run.run_id
    layout = run.layout
    _, loaded, step, errors = cli.decided_attempts(plan, layout.attempts, layout.classification, layout.workspace)
    diff = layout.plan / cli.DIFF_FILE
    commentable = cli.commentable_lines(diff.read_bytes().decode("utf-8")) if diff.is_file() else {}
    final = report.finalize(
        plan, step, errors, loaded.attempts, loaded.verdicts, commentable, workspace=layout.workspace, secrets=()
    )
    if final.judgement is None:
        reasons = "; ".join(errors) if errors else final.description
        raise Unavailable(f"no reviewer produced a verdict: {reasons}")
    return judged_outcome(final.judgement, final.reviewer or "")


def _persist_pool_blocks(run: Run) -> None:
    state = run.layout.final / driver.STATE_FILE
    if state.is_file():
        _store_pool_blocks(state_dir() / POOL_BLOCKS_FILE, _read(state).get("pool_blocked_until", {}))


def _attempt_loop(run: Run, plan: Mapping[str, Any]) -> None:
    previous = -1
    for _ in range(int(plan["max_attempts"])):
        code, values = run.phase("attempts", {"ORACLE_OMP_BINARY": run.omp_binary})
        if code != 0:
            raise Unavailable("the reviewer's exit could not be confirmed clean")
        recovered, _ = run.phase("recover")
        if recovered != 0:
            raise Unavailable("the invalid attempt could not be classified")
        count = int(values.get("attempts") or 0)
        if count == previous:
            return
        previous = count


def _install_harness(run: Run) -> None:
    code, values = run.phase("harness")
    env_file = run.work / "harness-env.txt"
    installed = values.get("state") == cli.OMP_INSTALLED and env_file.is_file()
    if code != 0 or not installed:
        raise Unavailable("the pinned omp build could not be installed")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name == "ORACLE_OMP_BINARY":
            run.omp_binary = value


def _scan_clean(run: Run, when: str) -> None:
    code, _ = run.phase("scan")
    if code != 0:
        raise Unavailable(f"a secret value is on disk ({when}); remove it and run again")


def _fresh_review(run: Run, plan: dict[str, Any]) -> Outcome:
    layout = run.layout
    layout.home_root.mkdir(parents=True, exist_ok=True)
    _stage_workspace(run.root, run.head, layout.workspace)
    _scan_clean(run, "before the reviewer runs")
    _install_harness(run)
    run.phase("isolation")
    _attempt_loop(run, plan)
    _scan_clean(run, "after the reviewer exits")
    code, _ = run.phase("finalize", {"OMP_INSTALL_STATE": cli.OMP_INSTALLED})
    if code != 0:
        raise Unavailable("the verdict could not be finalised without leaving a secret value in the results")
    _persist_pool_blocks(run)
    _keep_captures(layout.attempts / cli.CAPTURE_DIR, run.head, run.reporter.secrets)
    return _decided(run, plan)


def _severity_rank(finding: Mapping[str, Any]) -> int:
    order = ("Critical", "High", "Medium", "Low")
    return order.index(finding["severity"]) if finding["severity"] in order else len(order)


def _show(outcome: Outcome, reporter: Reporter) -> None:
    reporter.say(f"{ADVISORY}: reviewer {outcome.reviewer or 'none'}, decision {outcome.label}.")
    if outcome.summary:
        reporter.say(str(outcome.summary)[:SUMMARY_LIMIT])
    ordered = sorted(outcome.findings, key=_severity_rank)
    for finding in ordered[:FINDING_LIMIT]:
        reporter.say(
            f"  [{finding['severity']}] {finding['file']}:{finding['line']} {finding['title']}\n"
            f"      {str(finding['detail'])[:DETAIL_LIMIT]}"
        )
    if len(ordered) > FINDING_LIMIT:
        reporter.say(f"  ... and {len(ordered) - FINDING_LIMIT} more finding(s)")


def override_body(body_file: str) -> str:
    if not body_file:
        return ""
    try:
        return Path(body_file).read_text(encoding="utf-8")
    except OSError:
        return ""


def override_reason(body: str) -> str | None:
    reasons = OVERRIDE_PATTERN.findall(HIDDEN_TEXT.sub("", body))
    if not reasons:
        return None
    real = (reason for reason in reasons if reason and not PLACEHOLDER.fullmatch(reason))
    return next(real, "")


def _decide(outcome: Outcome, body: str, reporter: Reporter) -> int:
    if not outcome.blocked:
        reporter.say("Local oracle review: no Critical or High findings; pr-open may proceed.")
        return EXIT_PROCEED
    reason = override_reason(body)
    if reason:
        reporter.say(f"Local oracle override accepted; pr-open proceeds. Reason: {reason}")
        return EXIT_PROCEED
    if reason is None:
        reporter.say(
            f"Refusing to open the PR: the local oracle review decided {outcome.label} with Critical or High findings "
            "or too many open defects. Fix them, or add a "
            "'Local oracle override: <reason>' line to the PR body file (PR_BODY_FILE).",
            error=True,
        )
    else:
        reporter.say(
            "Refusing to open the PR: the 'Local oracle override:' line in the PR body file has an empty reason.",
            error=True,
        )
    return EXIT_REFUSED


def _prior_outcome(run: Run, plan: Mapping[str, Any]) -> Outcome | None:
    prior = _read(run.layout.plan / PRIOR_FILE) if (run.layout.plan / PRIOR_FILE).is_file() else None
    if plan["decision"] == cli.CARRY:
        prior = (plan.get("protocol") or {}).get("previous") or prior
    return None if prior is None else recorded_outcome(prior)


def _planned(run: Run) -> tuple[dict[str, Any], Outcome | None]:
    code, _ = run.phase("prepare")
    plan_file = run.layout.plan_file
    if code != 0 or not plan_file.is_file():
        raise Unavailable("the review plan could not be built")
    plan = _read(plan_file)
    decision = plan["decision"]
    if decision == cli.REVIEW:
        return plan, None
    if decision in (cli.SUCCESS, cli.SKIP, cli.CARRY):
        known = _prior_outcome(run, plan) if decision != cli.SUCCESS else None
        if known is None:
            run.reporter.say(f"{ADVISORY}: no review needed ({plan['decision_reason']}).")
            return plan, Outcome(False, "SHIP", "", "", ())
        return plan, known
    raise Unavailable(f"the oracle holds this change ({decision}): {plan['decision_reason']}")


def _reviewed(run: Run, trusted: Trusted) -> Outcome:
    plan, settled = _planned(run)
    if settled is not None:
        return settled
    if not run.key:
        raise Unavailable(
            f"no classifier key ({KEYCHAIN_SERVICE}) was found in the environment or the keychain; "
            f"store it with: {KEYCHAIN_STEP}"
        )
    access = resolve_credentials()
    run.reporter.hide(access.token, access.url)
    if not reachable(access.url, access.proxy):
        hint = "; run make cloud-gateway" if access.client == CLOUD_CLIENT and not access.proxy else ""
        raise Unavailable(f"the {access.client} gateway is unreachable{hint}")
    run.access = access
    head_tree = _git(run.root, "rev-parse", f"{run.head}^{{tree}}").strip()
    cache = cache_path(state_dir(), cache_key(plan, head_tree, trusted))
    cached = _load_cached(cache)
    if cached is not None:
        run.reporter.say("Reusing the cached local review of this unchanged patch.")
        return cached
    with exclusive(cache.with_suffix(".lock"), run.reporter, _holder(run.root)):
        cached = _load_cached(cache)
        if cached is not None:
            run.reporter.say("Reusing the cached local review of this unchanged patch.")
            return cached
        outcome = _fresh_review(run, plan)
        _store(cache, outcome, run.reporter.secrets)
    return outcome


def _review(args: argparse.Namespace, reporter: Reporter) -> int:
    root = Path.cwd()
    head = _git(root, "rev-parse", args.head).strip()
    body = override_body(args.body_file)
    trusted = Trusted.read(root, args.base_ref, args.policy)
    repository = _repository(root)
    key = classifier_key()
    reporter.hide(key, *(_held(name) for name in ("TS_AUTHKEY", *GITHUB_ACCESS_ENV)), *host_token_values().values())
    with _github_access():
        pr = open_pull(repository, _current_branch(root))
    work = Path(tempfile.mkdtemp(prefix="oracle-local-"))
    try:
        policy, soundness, inert = trusted.write(work / "trusted")
        run = Run(
            root,
            work,
            policy,
            soundness,
            inert,
            repository,
            head,
            args.base_ref,
            pr,
            uuid.uuid4().hex,
            reporter,
            key,
        )
        outcome = _reviewed(run, trusted)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _show(outcome, reporter)
    return _decide(outcome, body, reporter)


def command_review(args: argparse.Namespace) -> int:
    reporter = Reporter()
    try:
        return _review(args, reporter)
    except Unavailable as exc:
        reporter.say(f"Refusing to open the PR: the local oracle review is unavailable: {exc}", error=True)
        reporter.say("A 'Local oracle override:' line does not cover an unavailable review.", error=True)
        return EXIT_UNAVAILABLE
