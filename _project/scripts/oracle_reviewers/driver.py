from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from . import cli, github, isolation, omp_pin

POLICY_DEFAULT = ".github/oracle-reviewers.yml"
RESULT_FILE = "result.json"
STATE_FILE = Path("state") / github.STATE_FILE
FINAL_FILES = (
    "status.json",
    "comment.json",
    "review.json",
    str(STATE_FILE),
    str(Path("attempt") / github.STATE_FILE),
)
SCAN_SECRETS = ("TYPESAFE_API_KEY", "TS_OAUTH_CI_SECRET", "JOB_TOKEN")
REDACTED_SECRETS = ("JOB_TOKEN",)
SILENCED = ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")
PLAN_VOLATILE = ("created_at",)
DEVELOP = "develop"
VERSION_TIMEOUT_SECONDS = 60
POSTED = "success"
POSTING = (cli.REVIEW, cli.SUCCESS, cli.FORK, cli.CARRY, cli.REFUSED, cli.HOLD)


@dataclass(frozen=True)
class Layout:
    plan: Path
    attempts: Path
    final: Path
    classification: Path
    workspace: Path
    home_root: Path
    shadow: Path

    @classmethod
    def ci(cls, temp: Path, workspace: Path) -> Layout:
        return cls(
            plan=temp / "oracle-plan",
            attempts=temp / "oracle-attempts",
            final=temp / "oracle-final",
            classification=temp / "oracle-classification" / cli.CLASSIFICATION_FILE,
            workspace=workspace,
            home_root=temp,
            shadow=temp / "oracle-shadow",
        )

    def shadowed(self) -> Layout:
        return replace(self, plan=self.shadow / "plan", final=self.shadow / "final")

    @property
    def plan_file(self) -> Path:
        return self.plan / cli.PLAN_FILE


class Sink(Protocol):
    def status(self, repo: str, head: str, payload: Path) -> None: ...

    def comment(self, repo: str, pr: int, payload: Path) -> None: ...

    def review(self, repo: str, pr: int, payload: Path) -> None: ...

    def dispatch_gate(self, repo: str, pr: int, head: str) -> None: ...


@dataclass(frozen=True)
class GitHubSink:
    app_token: str
    job_token: str

    def status(self, repo: str, head: str, payload: Path) -> None:
        github.post_file(f"repos/{repo}/statuses/{head}", payload, self.app_token)

    def comment(self, repo: str, pr: int, payload: Path) -> None:
        github.post_file(f"repos/{repo}/issues/{pr}/comments", payload, self.app_token)

    def review(self, repo: str, pr: int, payload: Path) -> None:
        github.post_file(f"repos/{repo}/pulls/{pr}/reviews", payload, self.app_token)

    def dispatch_gate(self, repo: str, pr: int, head: str) -> None:
        if github.pull_base_ref(repo, pr, self.app_token) == DEVELOP:
            github.dispatch_gate(repo, pr, head, self.job_token)


@dataclass
class RecordingSink:
    intents: list[dict[str, Any]] = field(default_factory=list)

    def status(self, repo: str, head: str, payload: Path) -> None:
        self.intents.append({"kind": "status", "target": f"{repo}/statuses/{head}", "payload": _read(payload)})

    def comment(self, repo: str, pr: int, payload: Path) -> None:
        self.intents.append({"kind": "comment", "target": f"{repo}/issues/{pr}", "payload": _read(payload)})

    def review(self, repo: str, pr: int, payload: Path) -> None:
        self.intents.append({"kind": "review", "target": f"{repo}/pulls/{pr}", "payload": _read(payload)})

    def dispatch_gate(self, repo: str, pr: int, head: str) -> None:
        self.intents.append({"kind": "gate", "target": f"{repo}/pulls/{pr}", "payload": head})


class Environment(Protocol):
    layout: Layout
    policy: Path
    pr: str
    repo: str
    has_app: bool
    scan_roots: tuple[Path, ...]
    scan_secrets: tuple[str, ...]
    redacted_secrets: tuple[str, ...]

    def sink(self) -> Sink: ...

    def isolation(self) -> int: ...


@dataclass(frozen=True)
class CiEnvironment:
    layout: Layout
    policy: Path
    pr: str
    repo: str
    has_app: bool
    scan_roots: tuple[Path, ...]
    app_token: str
    job_token: str
    scan_secrets: tuple[str, ...] = SCAN_SECRETS
    redacted_secrets: tuple[str, ...] = REDACTED_SECRETS

    @classmethod
    def from_environ(cls, environ: Mapping[str, str], policy: Path) -> CiEnvironment:
        temp = Path(environ["RUNNER_TEMP"])
        workspace = Path(environ.get("GITHUB_WORKSPACE") or temp)
        scratch = environ.get("TMPDIR") or "/tmp"
        roots = (
            Path(environ.get("HOME", "/")),
            temp,
            workspace / "trusted",
            workspace / "workspace",
            Path("/tmp"),
            Path("/var/tmp"),
            Path("/dev/shm"),
            Path(scratch),
        )
        return cls(
            layout=Layout.ci(temp, workspace / "workspace"),
            policy=policy,
            pr=environ.get("PR_NUMBER", ""),
            repo=environ.get("GITHUB_REPOSITORY", ""),
            has_app=environ.get("HAS_APP") == "true",
            scan_roots=roots,
            app_token=environ.get("GH_TOKEN", ""),
            job_token=environ.get("JOB_TOKEN", ""),
        )

    def sink(self) -> Sink:
        return GitHubSink(self.app_token, self.job_token)

    def isolation(self) -> int:
        return isolation.main()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_if(path: Path) -> Any:
    return _read(path) if path.is_file() else None


@contextlib.contextmanager
def _silenced(enabled: bool) -> Iterator[None]:
    saved = {name: os.environ.pop(name) for name in SILENCED if enabled and name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _scan(env: Environment) -> int:
    return cli.command_scan_disk(
        argparse.Namespace(root=[str(root) for root in env.scan_roots], env=list(env.scan_secrets))
    )


def prepare(env: Environment, args: argparse.Namespace) -> int:
    shadow = bool(getattr(args, "shadow", False))
    layout = env.layout.shadowed() if shadow else env.layout
    with _silenced(shadow):
        code = cli.command_plan(argparse.Namespace(policy=str(env.policy), pr=env.pr, out=str(layout.plan)))
    if shadow or code != 0 or _read(layout.plan_file)["decision"] != cli.REVIEW:
        return code
    try:
        cli.command_select(
            argparse.Namespace(
                plan=str(layout.plan_file),
                attempts_dir=str(layout.attempts),
                slot=1,
                classification=str(layout.classification),
                workspace=None,
            )
        )
    except (Exception, SystemExit) as exc:
        cli._summary(f"oracle-verdict: classification failed: {type(exc).__name__}: {exc}")
    return 0


def _reported_version(binary: Path) -> str | None:
    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=VERSION_TIMEOUT_SECONDS,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def harness(env: Environment, args: argparse.Namespace) -> int:
    try:
        binary = omp_pin.install(omp_pin.pin_from_text(env.policy.read_text(encoding="utf-8")))
        version = _reported_version(binary)
    except (OSError, omp_pin.PinError, subprocess.SubprocessError) as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        version = None
    if version is None:
        print("::error::the pinned omp build could not be installed; no reviewer will run")
        cli._output({"state": cli.OMP_INSTALL_FAILED})
        return 0
    cli._summary(version)
    target = os.environ.get("GITHUB_ENV")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"ORACLE_OMP_BINARY={binary}\n")
    cli._output({"state": cli.OMP_INSTALLED})
    return 0


def scan(env: Environment, args: argparse.Namespace) -> int:
    return _scan(env)


def isolate(env: Environment, args: argparse.Namespace) -> int:
    return env.isolation()


def _attempt_args(layout: Layout) -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(layout.plan_file),
        attempts_dir=str(layout.attempts),
        classification=str(layout.classification),
        workspace=str(layout.workspace),
        home_root=str(layout.home_root),
    )


def attempts(env: Environment, args: argparse.Namespace) -> int:
    return cli.command_attempts(_attempt_args(env.layout))


def recover(env: Environment, args: argparse.Namespace) -> int:
    layout = env.layout
    return cli.command_recover(
        argparse.Namespace(
            plan=str(layout.plan_file), attempts_dir=str(layout.attempts), workspace=str(layout.workspace)
        )
    )


def _recorded_now(path: Path) -> datetime | None:
    state = _read_if(path)
    return datetime.fromisoformat(state["updated_at"]) if state else None


def finalize(env: Environment, args: argparse.Namespace) -> int:
    shadow = bool(getattr(args, "shadow", False))
    layout = env.layout.shadowed() if shadow else env.layout
    omp_install = os.environ.get("OMP_INSTALL_STATE") or cli.OMP_INSTALLED
    with _silenced(shadow):
        if not shadow:
            cli.command_count_attempts(argparse.Namespace(attempts_dir=str(layout.attempts)))
        code = cli.command_finalize(
            argparse.Namespace(
                plan=str(layout.plan_file),
                attempts_dir=str(layout.attempts),
                out_dir=str(layout.final),
                classification=str(layout.classification),
                workspace=str(layout.workspace),
                omp_install=omp_install,
                env=list(env.redacted_secrets),
                now=_recorded_now(env.layout.final / STATE_FILE) if shadow else None,
            )
        )
        if code != 0:
            return code
        owned = [layout.final] if shadow else [layout.attempts, layout.final]
        return cli.command_redact_check(
            argparse.Namespace(
                path=[str(path) for path in owned],
                env=list(env.redacted_secrets),
                summary_file=[str(path / cli.SUMMARY_FILE) for path in owned],
            )
        )


def _post(env: Environment, layout: Layout, sink: Sink, plan: Mapping[str, Any]) -> None:
    final = layout.final
    pr, head = int(plan["pr"]), plan["head_sha"]
    sink.status(env.repo, head, final / "status.json")
    if (final / "comment.json").is_file():
        sink.comment(env.repo, pr, final / "comment.json")
    if (final / "review.json").is_file():
        sink.review(env.repo, pr, final / "review.json")


def publish_with(env: Environment, layout: Layout, sink: Sink, *, guarded: bool, skip: bool = False) -> int:
    plan = _read(layout.plan_file)
    final = layout.final
    has_state = (final / STATE_FILE).is_file()
    if not env.has_app:
        state = (_read_if(final / "status.json") or {}).get("state", "")
        print(
            "::notice::oracle-verdict is not posted because the App secrets are not set; "
            f"result {state} for {plan['head_sha']}"
        )
        if has_state:
            cli._output({"attempt_state": "true"})
        return 0
    code = 0
    if guarded:
        code, skip = cli.guard_decision(plan)
        if code == 0:
            cli._output({"skip": "true" if skip else "false"})
    if has_state and not skip:
        cli._output({"attempt_state": "true"})
    if code != 0 or skip:
        return code
    try:
        _post(env, layout, sink, plan)
        if has_state:
            cli._output({"state_recorded": "true"})
        sink.dispatch_gate(env.repo, int(plan["pr"]), plan["head_sha"])
    except github.GitHubError as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        return 1
    return 0


def publish(env: Environment, args: argparse.Namespace) -> int:
    return publish_with(env, env.layout, env.sink(), guarded=True)


def _outcomes() -> dict[str, str]:
    return {
        "status": os.environ.get("POSTED_STATUS", ""),
        "comment": os.environ.get("POSTED_COMMENT", ""),
        "review": os.environ.get("POSTED_REVIEW", ""),
        "gate": os.environ.get("POSTED_GATE", ""),
    }


def _posted_intents(env: Environment, plan: Mapping[str, Any], outcomes: Mapping[str, str]) -> list[dict[str, Any]]:
    final = env.layout.final
    pr, head = int(plan["pr"]), plan["head_sha"]
    candidates = (
        ("status", f"{env.repo}/statuses/{head}", _read_if(final / "status.json")),
        ("comment", f"{env.repo}/issues/{pr}", _read_if(final / "comment.json")),
        ("review", f"{env.repo}/pulls/{pr}", _read_if(final / "review.json")),
        ("gate", f"{env.repo}/pulls/{pr}", head),
    )
    return [
        {"kind": kind, "target": target, "payload": payload}
        for kind, target, payload in candidates
        if outcomes.get(kind) == POSTED
    ]


CLIP_CHARACTERS = 2000


def _clip(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= CLIP_CHARACTERS else text[:CLIP_CHARACTERS] + "..."


def _without(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key not in PLAN_VOLATILE}


def _plan_difference(real: Layout, shadow: Layout) -> tuple[dict[str, Any], Any]:
    real_plan, shadow_plan = _read_if(real.plan_file), _read_if(shadow.plan_file)
    if shadow_plan is None:
        return {"plan": {"shadow": None, "posted": "unavailable" if real_plan is None else "present"}}, None
    if real_plan is None:
        return {"plan": {"posted": None, "shadow": "present"}}, None
    ours, theirs = _without(shadow_plan), _without(real_plan)
    if ours != theirs:
        keys = sorted(key for key in {*ours, *theirs} if ours.get(key) != theirs.get(key))
        differing = {key: {"shadow": _clip(ours.get(key)), "posted": _clip(theirs.get(key))} for key in keys}
        return {"plan": differing}, real_plan
    return {}, real_plan


def _final_differences(real: Layout, shadow: Layout) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name in FINAL_FILES:
        theirs, ours = _read_if(real.final / name), _read_if(shadow.final / name)
        if theirs != ours:
            found[f"final/{name}"] = {"shadow": _clip(ours), "posted": _clip(theirs)}
    return found


def _differences(env: Environment, outcomes: Mapping[str, str], skip: bool) -> dict[str, Any]:
    real, shadow = env.layout, env.layout.shadowed()
    found, real_plan = _plan_difference(real, shadow)
    if not real_plan or real_plan.get("pr") is None or real_plan["decision"] not in POSTING:
        return found
    found.update(_final_differences(real, shadow))
    sink = RecordingSink()
    with _silenced(True):
        publish_with(env, shadow, sink, guarded=False, skip=skip)
    expected = [] if skip or not env.has_app else _posted_intents(env, real_plan, outcomes)
    if sink.intents != expected:
        found["publish"] = {"shadow": _clip(sink.intents), "posted": _clip(expected)}
    return found


def compare(env: Environment, args: argparse.Namespace) -> int:
    skip = os.environ.get("GUARD_SKIP") == "true" or os.environ.get("GUARD_OUTCOME") == "failure"
    differences = _differences(env, _outcomes(), skip)
    equal = not differences
    shadow = env.layout.shadowed()
    result = {"equal": equal, "differences": differences}
    shadow.shadow.mkdir(parents=True, exist_ok=True)
    (shadow.shadow / RESULT_FILE).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cli._output({"equal": "true" if equal else "false"})
    names = ", ".join(sorted(differences))
    cli._summary(
        "oracle-verdict shadow: the driver's outputs equal the posted outputs"
        if equal
        else f"oracle-verdict shadow: the driver's outputs differ from the posted outputs in {names}"
    )
    return 0


def sweep(env: Environment, args: argparse.Namespace) -> int:
    return cli.command_sweep(argparse.Namespace(policy=str(env.policy)))


Phase = Callable[[Environment, argparse.Namespace], int]
SHADOWED = frozenset({"prepare", "finalize"})
PHASES: dict[str, Phase] = {
    "prepare": prepare,
    "harness": harness,
    "scan": scan,
    "isolation": isolate,
    "attempts": attempts,
    "recover": recover,
    "finalize": finalize,
    "publish": publish,
    "compare": compare,
    "sweep": sweep,
}


def command_drive(args: argparse.Namespace) -> int:
    env = CiEnvironment.from_environ(os.environ, Path(args.policy))
    return int(args.phase_handler(env, args))


def register(commands: Any) -> None:
    drive = commands.add_parser("drive")
    drive.add_argument("--policy", default=POLICY_DEFAULT)
    drive.set_defaults(handler=command_drive)
    phases = drive.add_subparsers(dest="phase", required=True)
    for name, handler in PHASES.items():
        phase = phases.add_parser(name)
        if name in SHADOWED:
            phase.add_argument("--shadow", action="store_true")
        phase.set_defaults(phase_handler=handler)
