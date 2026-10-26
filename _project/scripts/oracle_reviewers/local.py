from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import attempts as attempt_files, cli, credentials, protocol, redaction_check, selection
from .classifier import ChangedFile, matching, soundness_scope
from .classify import Classification
from .jev import JevError
from .policy import Policy, load_policy, parse_policy
from .verdict import DO_NOT_SHIP

BLOCKING_SEVERITIES = ("Critical", "High")
OVERRIDE_PATTERN = re.compile(r"^[ \t]*Local oracle override:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
KEYCHAIN_SERVICE = "TYPESAFE_API_KEY"
LOCAL_CLIENT = "oracle-local"
CLOUD_CLIENT = "oracle-cloud"
LOCAL_GATEWAY_URL = "http://127.0.0.1:4000"
LOCAL_TOKEN_PATH = Path(".omp") / "oracle-gateway" / "local-client.token"
CLOUD_TOKEN_ENV = "ORACLE_CLOUD_GATEWAY_TOKEN"
CLOUD_SESSION_ENV = "BENCHBOX_EPHEMERAL_CLONE"
STATE_DIR_ENV = "BENCHBOX_LOCAL_ORACLE_DIR"
WORKFLOW_FILE = ".github/workflows/oracle-verdict.yml"
SOUNDNESS_MANIFEST = ".github/soundness-paths.txt"
HIDDEN_TEXT = re.compile(r"<!--.*?-->|^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.DOTALL | re.MULTILINE)
PLACEHOLDER = re.compile(r"<.*>")
OMP_VERSION_PIN = re.compile(r"^[ \t]*OMP_VERSION:[ \t]*\"?([0-9][^\s\"]*)", re.MULTILINE)
OMP_VERSION_OUTPUT = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+[^\s]*")
REMOTE_SLUG = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
DEFAULT_REPOSITORY = "BenchBox-dev/BenchBox"
CACHE_SCHEMA = 1
LOCK_WAIT_SECONDS = 3600.0
LOCK_POLL_SECONDS = 1.0
REACHABILITY_SECONDS = 10
SUMMARY_LIMIT = 1200
DETAIL_LIMIT = 600
FINDING_LIMIT = 20
CLEAR = "clear"
BLOCKED = "blocked"
EXIT_PROCEED = 0
EXIT_REFUSED = 1
EXIT_UNAVAILABLE = 2
ADVISORY = "Local oracle review (advisory; it may differ from the CI oracle and posts nothing to GitHub)"


class Unavailable(Exception):
    pass


@dataclass(frozen=True)
class Credentials:
    client: str
    url: str
    token: str


class Reporter:
    def __init__(self) -> None:
        self.secrets: tuple[str, ...] = ()

    def hide(self, *values: str) -> None:
        self.secrets = tuple(dict.fromkeys((*self.secrets, *(value for value in values if value))))

    def say(self, text: str, *, error: bool = False) -> None:
        print(credentials.redact(text, self.secrets), file=sys.stderr if error else sys.stdout)


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
        ["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True, check=False
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


STATUS_NAMES = {"A": "added", "C": "added", "D": "removed", "R": "renamed"}


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


def build_plan(
    policy: Policy, root: Path, base_ref: str, head: str, out_dir: Path, manifest: str | None = None
) -> dict[str, Any]:
    base = _git(root, "merge-base", base_ref, head).strip()
    plan = cli._base_plan(policy, _repository(root), None, uuid.uuid4().hex)
    plan.update({"base_sha": base, "base_ref": base_ref.removeprefix("origin/"), "head_sha": head})
    plan["head_tree"] = _git(root, "rev-parse", f"{head}^{{tree}}").strip()
    files = changed_files(root, base, head)
    predicate = trusted_predicate(root, base_ref) if manifest is None else soundness_predicate(manifest)
    if not files:
        plan["decision_reason"] = f"no committed changes against {base_ref}"
        return plan
    scope = soundness_scope(files, policy.classifier, predicate)
    plan["scope_paths"] = list(scope.paths)
    plan["gate_paths"] = list(scope.gate_paths)
    plan["scope_reasons"] = list(scope.reasons)
    if not scope.soundness:
        plan["decision"] = cli.SUCCESS
        plan["decision_reason"] = "only exempt paths changed"
        return plan
    raw_diff = _git_bytes(root, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", base, head)
    full_diff = raw_diff.decode("utf-8", errors="replace")
    in_scope = set(scope.paths)
    scoped = cli._scoped_files(files, in_scope, policy, predicate)
    reason = "local review of the committed change"
    plan["manual"] = False
    plan["previous_state"] = None
    plan["open_findings"] = {}
    plan["disputes"] = []
    plan["protocol"] = {
        "kind": protocol.FIRST,
        "reason": reason,
        "cycle": 1,
        "round": 1,
        "base_ref": plan["base_ref"],
        "patch_map": {},
        "patch_digest": hashlib.sha256(raw_diff).hexdigest()[:32],
        "strikes": 0,
        "struck_digests": [],
        "max_do_not_ship": policy.protocol.max_do_not_ship,
        "prior": [],
        "next_id": 1,
        "changed": None,
        "previous": None,
        "forced": False,
        "seen": [],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    cli._plan_review(
        plan,
        out_dir,
        policy,
        protocol.Round(protocol.FIRST, reason),
        files,
        scoped,
        in_scope,
        full_diff,
        None,
        cli._now(),
    )
    plan["decision_reason"] = reason
    return plan


def soundness_predicate(manifest: str) -> Any:
    from _project.scripts import soundness_paths

    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "soundness-paths.txt"
        path.write_text(manifest, encoding="utf-8")
        try:
            rules = soundness_paths._load_rules(path)
        except ValueError as exc:
            raise Unavailable(f"the soundness manifest is invalid: {exc}") from exc
    regexes = tuple(re.compile(rule.pattern) for rule in rules if rule.kind == "regex")
    globs = tuple(rule.pattern for rule in rules if rule.kind == "glob")
    files = frozenset(rule.pattern for rule in rules if rule.kind in {"file", "glob"})
    prefixes = tuple(rule.pattern for rule in rules if rule.kind == "prefix")

    def is_soundness(path: str) -> bool:
        normalized = soundness_paths.normalize_path(path)
        return bool(normalized) and (
            any(regex.match(normalized) is not None for regex in regexes)
            or any(soundness_paths._glob_matches(normalized, pattern) for pattern in globs)
            or normalized in files
            or normalized.startswith(prefixes)
        )

    return is_soundness


def trusted_predicate(root: Path, base_ref: str) -> Any:
    return soundness_predicate(trusted_text(root, base_ref, SOUNDNESS_MANIFEST))


def command_plan(args: argparse.Namespace) -> int:
    policy = load_policy(Path(args.policy))
    out_dir = Path(args.out)
    try:
        head = _git(Path.cwd(), "rev-parse", "HEAD").strip()
        plan = build_plan(policy, Path.cwd(), args.base_ref, head, out_dir)
    except Unavailable as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    return cli._finish_plan(out_dir, plan)


def state_dir() -> Path:
    configured = os.environ.get(STATE_DIR_ENV)
    return Path(configured) if configured else Path.home() / ".cache" / "benchbox" / "local-oracle"


@contextlib.contextmanager
def exclusive(directory: Path, reporter: Reporter) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    announced = False
    with open(directory / "review.lock", "a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Unavailable("another local oracle review kept running on this machine") from None
                if not announced:
                    reporter.say("Waiting for another local oracle review on this machine to finish.")
                    announced = True
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def trusted_text(root: Path, base_ref: str, path: str) -> str:
    return _git(root, "show", f"{base_ref}:{path}")


def pinned_omp_version(workflow_text: str) -> str:
    match = OMP_VERSION_PIN.search(workflow_text)
    if match is None:
        raise Unavailable("the CI omp pin (OMP_VERSION in oracle-verdict.yml) is missing")
    return match.group(1)


def installed_omp_version() -> str:
    binary = shutil.which("omp")
    if binary is None:
        raise Unavailable("omp is not installed")
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unavailable(f"omp --version failed: {type(exc).__name__}") from exc
    match = OMP_VERSION_OUTPUT.search(result.stdout + result.stderr)
    if result.returncode != 0 or match is None:
        raise Unavailable("omp --version gave no version")
    return match.group(0)


def check_harness(workflow_text: str) -> str:
    installed = installed_omp_version()
    pinned = pinned_omp_version(workflow_text)
    if installed != pinned:
        raise Unavailable(f"omp version mismatch: installed {installed}, CI pins {pinned}; install the pinned build")
    return installed


def resolve_credentials(environ: Mapping[str, str] | None = None) -> Credentials:
    env = os.environ if environ is None else environ
    if env.get(CLOUD_TOKEN_ENV) or env.get(CLOUD_SESSION_ENV) == "1":
        url = env.get("ORACLE_GATEWAY_URL", "").strip()
        token = env.get(CLOUD_TOKEN_ENV, "").strip()
        if not url or not token:
            raise Unavailable(f"the {CLOUD_CLIENT} client needs ORACLE_GATEWAY_URL and {CLOUD_TOKEN_ENV}")
        return Credentials(CLOUD_CLIENT, url, token)
    path = Path.home() / LOCAL_TOKEN_PATH
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        raise Unavailable(f"the {LOCAL_CLIENT} client token is missing or empty at ~/{LOCAL_TOKEN_PATH}")
    return Credentials(LOCAL_CLIENT, LOCAL_GATEWAY_URL, token)


def reachable(url: str) -> bool:
    request = urllib.request.Request(f"{url.rstrip('/')}/healthz", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=REACHABILITY_SECONDS):
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


@contextlib.contextmanager
def _environment(updates: Mapping[str, str]) -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in (*updates, "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")}
    os.environ.update(updates)
    os.environ.pop("GITHUB_OUTPUT", None)
    os.environ.pop("GITHUB_STEP_SUMMARY", None)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def classify(plan: dict[str, Any], reporter: Reporter, keyed: bool) -> Classification:
    if keyed:
        try:
            return cli._classify_plan(plan)
        except (JevError, KeyError, TypeError, ValueError) as exc:
            raise Unavailable(f"classification unavailable: {exc}") from exc
    paths = [item["path"] for item in plan.get("reviewed_files", [])]
    flags = matching(paths, plan.get("gate_path_list", []))
    template = "gate" if flags else "standard"
    reporter.say(
        f"No classifier key ({KEYCHAIN_SERVICE} in the keychain or the environment): used the fixed default, "
        f"the {plan['buckets']['medium']} reviewer with the {template} template."
    )
    return Classification("medium", "routine", template, 0.0, tuple(flags), ())


def machinery_digest() -> str:
    package = Path(__file__).resolve().parent
    files = sorted(item for item in (*package.glob("*.py"), *package.glob("templates/*")) if item.is_file())
    digest = hashlib.sha256()
    for item in (*files, cli.OMP_CONFIG_SOURCE):
        digest.update(item.name.encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _cache_path(directory: Path, plan: Mapping[str, Any], policy_text: str, version: str, keyed: bool) -> Path:
    policy_hash = hashlib.sha256(policy_text.encode("utf-8")).hexdigest()
    parts = (
        plan["protocol"]["patch_digest"],
        plan["base_sha"],
        plan["head_tree"],
        policy_hash,
        version,
        f"keyed={keyed}",
        machinery_digest(),
        CACHE_SCHEMA,
    )
    key = hashlib.sha256("\0".join(map(str, parts)).encode("utf-8")).hexdigest()
    return directory / "cache" / f"{key}.json"


def _load_cached(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(data, dict) and data.get("decision") in (CLEAR, BLOCKED) and isinstance(data.get("findings"), list)
    return data if ok else None


def _store(path: Path, result: dict[str, Any], secrets: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    scratch.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        redaction_check.sanitize(scratch, secrets)
    except redaction_check.Unredactable:
        scratch.unlink(missing_ok=True)
        return
    os.replace(scratch, path)


def _stage_workspace(root: Path, head: str, workspace: Path) -> None:
    _git(root, "clone", "--quiet", "--no-checkout", "--shared", str(root), str(workspace))
    _git(workspace, "checkout", "--quiet", "--detach", head)


def _run_attempts(plan_path: Path, work: Path, classification_path: Path, reporter: Reporter) -> None:
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            code = cli.command_attempts(
                argparse.Namespace(
                    plan=str(plan_path),
                    attempts_dir=str(work / "attempts"),
                    classification=str(classification_path),
                    workspace=str(work / "workspace"),
                    home_root=str(work / "homes"),
                    allow_loopback_http=True,
                )
            )
    finally:
        for line in captured.getvalue().splitlines():
            if line.startswith("slot "):
                reporter.say(line)
    if code != 0:
        raise Unavailable("the reviewer's exit could not be confirmed clean")


def _result(plan: dict[str, Any], classification: Classification, work: Path) -> dict[str, Any]:
    run_id = plan["run_id"]
    loaded = attempt_files.load(work / "attempts", plan, run_id)
    step, errors = attempt_files.decide(cli._selection(plan, classification), loaded)
    if errors:
        raise Unavailable("; ".join(errors))
    decided = [item for item in loaded.attempts if item.outcome in (selection.PASS, selection.FAIL)]
    if step.kind not in (selection.PASS, selection.FAIL) or not decided:
        reasons = [
            f"{item.reviewer}: {item.absence}{' (' + item.detail + ')' if item.detail else ''}"
            for item in loaded.attempts
        ] or list(step.reasons)
        raise Unavailable("no reviewer produced a verdict: " + "; ".join(reasons))
    verdict = loaded.verdicts[decided[0].slot]
    blocking = [item for item in verdict.defects if item.severity in BLOCKING_SEVERITIES]
    blocked = bool(blocking) or verdict.decision == DO_NOT_SHIP
    return {
        "decision": BLOCKED if blocked else CLEAR,
        "reviewer": decided[0].reviewer,
        "summary": verdict.summary,
        "review_decision": verdict.decision,
        "findings": [item.to_json() for item in verdict.defects],
    }


def _severity_rank(finding: Mapping[str, Any]) -> int:
    order = ("Critical", "High", "Medium", "Low")
    return order.index(finding["severity"]) if finding["severity"] in order else len(order)


def _show(result: Mapping[str, Any], reporter: Reporter) -> None:
    reporter.say(f"{ADVISORY}: reviewer {result['reviewer']}, decision {result['review_decision']}.")
    if result["summary"]:
        reporter.say(str(result["summary"])[:SUMMARY_LIMIT])
    ordered = sorted(result["findings"], key=_severity_rank)
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


def _decide(result: Mapping[str, Any], body: str, reporter: Reporter) -> int:
    if result["decision"] == CLEAR:
        reporter.say("Local oracle review: no Critical or High findings; pr-open may proceed.")
        return EXIT_PROCEED
    reason = override_reason(body)
    if reason:
        reporter.say(f"Local oracle override accepted; pr-open proceeds. Reason: {reason}")
        return EXIT_PROCEED
    if reason is None:
        reporter.say(
            "Refusing to open the PR: the local oracle review found Critical or High findings. Fix them, or add a "
            "'Local oracle override: <reason>' line to the PR body file (PR_BODY_FILE).",
            error=True,
        )
    else:
        reporter.say(
            "Refusing to open the PR: the 'Local oracle override:' line in the PR body file has an empty reason.",
            error=True,
        )
    return EXIT_REFUSED


def _reviewed(
    root: Path,
    head: str,
    args: argparse.Namespace,
    policy: Policy,
    texts: tuple[str, str],
    work: Path,
    reporter: Reporter,
) -> dict[str, Any] | None:
    policy_text, workflow_text = texts
    plan_dir = work / "plan"
    plan = build_plan(policy, root, args.base_ref, head, plan_dir)
    if plan["decision"] != cli.REVIEW:
        reporter.say(f"{ADVISORY}: no review needed ({plan['decision_reason']}).")
        return None
    version = check_harness(workflow_text)
    key = os.environ.get("TYPESAFE_API_KEY", "") or keychain_key()
    reporter.hide(key)
    cache = _cache_path(state_dir(), plan, policy_text, version, bool(key))
    cached = _load_cached(cache)
    if cached is not None:
        reporter.say("Reusing the cached local review of this unchanged patch.")
        return cached
    access = resolve_credentials()
    reporter.hide(access.token, access.url)
    if not reachable(access.url):
        raise Unavailable(f"the {access.client} gateway is unreachable")
    updates = {
        "OMP_GATEWAY_TOKEN": access.token,
        "ORACLE_GATEWAY_URL": access.url,
        "GITHUB_RUN_ID": plan["run_id"],
        **({"TYPESAFE_API_KEY": key} if key else {}),
    }
    with _environment(updates):
        classification = classify(plan, reporter, bool(key))
        classification_path = plan_dir / cli.CLASSIFICATION_FILE
        cli._write_json(classification_path, classification.to_json())
        plan_path = plan_dir / cli.PLAN_FILE
        cli._write_json(plan_path, plan)
        (work / "homes").mkdir()
        _stage_workspace(root, plan["head_sha"], work / "workspace")
        _run_attempts(plan_path, work, classification_path, reporter)
        for path in redaction_check.json_files([work / "attempts"]):
            try:
                redaction_check.sanitize(path, ())
            except redaction_check.Unredactable as exc:
                raise Unavailable(str(exc)) from exc
        result = _result(plan, classification, work)
    _store(cache, result, reporter.secrets)
    return result


def _review(args: argparse.Namespace, reporter: Reporter) -> int:
    root = Path.cwd()
    head = _git(root, "rev-parse", args.head).strip()
    body = override_body(args.body_file)
    policy_text = trusted_text(root, args.base_ref, args.policy)
    workflow_text = trusted_text(root, args.base_ref, WORKFLOW_FILE)
    policy = parse_policy(yaml.safe_load(policy_text))
    with exclusive(state_dir(), reporter):
        work = Path(tempfile.mkdtemp(prefix="oracle-local-"))
        try:
            result = _reviewed(root, head, args, policy, (policy_text, workflow_text), work, reporter)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if result is None:
        return EXIT_PROCEED
    _show(result, reporter)
    return _decide(result, body, reporter)


def command_review(args: argparse.Namespace) -> int:
    reporter = Reporter()
    try:
        return _review(args, reporter)
    except Unavailable as exc:
        reporter.say(f"Refusing to open the PR: the local oracle review is unavailable: {exc}", error=True)
        reporter.say("A 'Local oracle override:' line does not cover an unavailable review.", error=True)
        return EXIT_UNAVAILABLE
