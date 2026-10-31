from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import cli, github, isolation, omp_pin

POLICY_DEFAULT = ".github/oracle-reviewers.yml"
STATE_FILE = Path("state") / github.STATE_FILE
SCAN_SECRETS = ("TYPESAFE_API_KEY", "TS_OAUTH_CI_SECRET", "JOB_TOKEN")
REDACTED_SECRETS = ("JOB_TOKEN",)
DEVELOP = "develop"
VERSION_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class Layout:
    plan: Path
    attempts: Path
    final: Path
    classification: Path
    workspace: Path
    home_root: Path

    @classmethod
    def ci(cls, temp: Path, workspace: Path) -> Layout:
        return cls(
            plan=temp / "oracle-plan",
            attempts=temp / "oracle-attempts",
            final=temp / "oracle-final",
            classification=temp / "oracle-classification" / cli.CLASSIFICATION_FILE,
            workspace=workspace,
            home_root=temp,
        )

    @property
    def plan_file(self) -> Path:
        return self.plan / cli.PLAN_FILE


class Sink(Protocol):
    def status(self, repo: str, head: str, payload: Path) -> None: ...

    def comment(self, repo: str, pr: int, payload: Path) -> None: ...

    def review(self, repo: str, pr: int, payload: Path) -> None: ...

    def dispatch_gate(self, repo: str, pr: int, head: str) -> None: ...

    def gate(self, repo: str, pr: int, head: str) -> None: ...


class CredentialError(Exception):
    pass


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

    def gate(self, repo: str, pr: int, head: str) -> None:
        github.post_gate(repo, pr, head, self.app_token)


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
        missing = [name for name, value in (("GH_TOKEN", self.app_token), ("JOB_TOKEN", self.job_token)) if not value]
        if missing:
            raise CredentialError(f"the App is configured but {', '.join(missing)} is empty")
        return GitHubSink(self.app_token, self.job_token)

    def isolation(self) -> int:
        return isolation.main()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_if(path: Path) -> Any:
    return _read(path) if path.is_file() else None


def _scan(env: Environment) -> int:
    return cli.command_scan_disk(
        argparse.Namespace(root=[str(root) for root in env.scan_roots], env=list(env.scan_secrets))
    )


def prepare(env: Environment, args: argparse.Namespace) -> int:
    layout = env.layout
    code = cli.command_plan(argparse.Namespace(policy=str(env.policy), pr=env.pr, out=str(layout.plan)))
    if code != 0 or _read(layout.plan_file)["decision"] != cli.REVIEW:
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
            handle.write(f"{omp_pin.BINARY_ENV}={binary}\n")
    cli._output({"state": cli.OMP_INSTALLED})
    return 0


def scan(env: Environment, args: argparse.Namespace) -> int:
    return _scan(env)


def isolate(env: Environment, args: argparse.Namespace) -> int:
    return env.isolation()


def attempts(env: Environment, args: argparse.Namespace) -> int:
    layout = env.layout
    return cli.command_attempts(
        argparse.Namespace(
            plan=str(layout.plan_file),
            attempts_dir=str(layout.attempts),
            classification=str(layout.classification),
            workspace=str(layout.workspace),
            home_root=str(layout.home_root),
        )
    )


def recover(env: Environment, args: argparse.Namespace) -> int:
    layout = env.layout
    return cli.command_recover(
        argparse.Namespace(
            plan=str(layout.plan_file), attempts_dir=str(layout.attempts), workspace=str(layout.workspace)
        )
    )


def finalize(env: Environment, args: argparse.Namespace) -> int:
    layout = env.layout
    cli.command_count_attempts(argparse.Namespace(attempts_dir=str(layout.attempts)))
    code = cli.command_finalize(
        argparse.Namespace(
            plan=str(layout.plan_file),
            attempts_dir=str(layout.attempts),
            out_dir=str(layout.final),
            classification=str(layout.classification),
            workspace=str(layout.workspace),
            omp_install=os.environ.get("OMP_INSTALL_STATE") or cli.OMP_INSTALLED,
            env=list(env.redacted_secrets),
            now=None,
        )
    )
    if code != 0:
        return code
    owned = [layout.attempts, layout.final]
    return cli.command_redact_check(
        argparse.Namespace(
            path=[str(path) for path in owned],
            env=list(env.redacted_secrets),
            summary_file=[str(path / cli.SUMMARY_FILE) for path in owned],
        )
    )


def _post(env: Environment, sink: Sink, plan: Mapping[str, Any]) -> None:
    final = env.layout.final
    pr, head = int(plan["pr"]), plan["head_sha"]
    if (final / "comment.json").is_file():
        sink.comment(env.repo, pr, final / "comment.json")
    if (final / "review.json").is_file():
        sink.review(env.repo, pr, final / "review.json")
    sink.status(env.repo, head, final / "status.json")


def _post_gate(env: Environment, sink: Sink, plan: Mapping[str, Any]) -> None:
    try:
        sink.gate(env.repo, int(plan["pr"]), plan["head_sha"])
    except github.GitHubError as exc:
        print(f"::warning::oracle-gate was not posted from this run: {exc}", file=sys.stderr)


def _credentialed_sink(env: Environment) -> Sink | None:
    try:
        return env.sink()
    except CredentialError as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        return None


def publish(env: Environment, args: argparse.Namespace) -> int:
    plan = _read(env.layout.plan_file)
    final = env.layout.final
    has_state = (final / STATE_FILE).is_file()
    if not env.has_app:
        state = (_read_if(final / "status.json") or {}).get("state", "")
        print(
            "::notice::oracle-verdict is not posted because the App secrets are not set; "
            f"result {state} for {plan['head_sha']}"
        )
        return 0
    sink = _credentialed_sink(env)
    if sink is None:
        return 1
    code, skip = cli.guard_decision(plan)
    if code == 0:
        cli._output({"skip": "true" if skip else "false"})
    if code != 0 or skip:
        return code
    try:
        _post(env, sink, plan)
    except github.GitHubError as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        return 1
    _post_gate(env, sink, plan)
    if has_state:
        cli._output({"state_recorded": "true"})
    return 0


def dispatch(env: Environment, args: argparse.Namespace) -> int:
    plan = _read(env.layout.plan_file)
    sink = _credentialed_sink(env)
    if sink is None:
        return 1
    try:
        sink.dispatch_gate(env.repo, int(plan["pr"]), plan["head_sha"])
    except github.GitHubError as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        return 1
    return 0


def sweep(env: Environment, args: argparse.Namespace) -> int:
    return cli.command_sweep(argparse.Namespace(policy=str(env.policy)))


Phase = Callable[[Environment, argparse.Namespace], int]
PHASES: dict[str, Phase] = {
    "prepare": prepare,
    "harness": harness,
    "scan": scan,
    "isolation": isolate,
    "attempts": attempts,
    "recover": recover,
    "finalize": finalize,
    "publish": publish,
    "dispatch": dispatch,
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
        phase.set_defaults(phase_handler=handler)
