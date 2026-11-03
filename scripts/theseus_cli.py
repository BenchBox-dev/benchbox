#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

BOOTSTRAP_PATH = "_project/scripts/theseus_bootstrap.py"
POLICY_PATH = ".github/oracle-reviewers.yml"
TRUSTED_TREE = ".github"
DEVELOP_REF = "origin/develop"
FETCH_BRANCH = "develop"
LOCAL_STATE = Path(".cache") / "benchbox" / "local-oracle"
OMP_CACHE = Path(".cache") / "benchbox" / "omp"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
GATE_WAITING_EXIT = 3
EXIT_UNAVAILABLE = 2

Runner = Callable[[list[str]], tuple[int, str]]


class TheseusError(Exception):
    pass


def _release_tree(root: Path) -> bool:
    if (root / ".github" / "theseus-lock.json").exists() or (root / ".github" / "oracle-reviewers.yml").exists():
        return False
    print("release tree: no Theseus lock; skipping", file=sys.stderr)
    return True


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise TheseusError(f"git {args[0]} failed: {detail}")
    return result.stdout


def resolve_commit(root: Path, ref: str = DEVELOP_REF) -> str:
    if FULL_SHA.fullmatch(ref):
        return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode("utf-8").strip()
    if ref == DEVELOP_REF:
        _git(root, "fetch", "origin", FETCH_BRANCH, "--quiet")
    return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode("utf-8").strip()


def environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    home = Path.home()
    env.setdefault("THESEUS_LOCAL_STATE", str(home / LOCAL_STATE))
    env.setdefault("THESEUS_OMP_CACHE", str(home / OMP_CACHE))
    if env.get("BENCHBOX_EPHEMERAL_CLONE") == "1":
        env.setdefault("THESEUS_EPHEMERAL_CLONE", "1")
    return env


def install(root: Path, commit: str) -> Path:
    if not FULL_SHA.fullmatch(commit):
        raise TheseusError(f"the trusted commit must be a full SHA, not {commit!r}")
    bootstrap = _git(root, "show", f"{commit}:{BOOTSTRAP_PATH}")
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-", "install", "--repo", str(root), "--commit", commit],
        input=bootstrap,
        capture_output=True,
        env=environment(),
        check=False,
    )
    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")
    code = result.returncode
    lines = out.strip().splitlines()
    if code != 0 or not lines:
        detail = (err.strip() or out.strip() or "no output")[-400:]
        raise TheseusError(f"the verified Theseus install at {commit[:12]} was refused: {detail}")
    program = Path(lines[-1])
    if not program.is_file():
        raise TheseusError(f"the verified Theseus install printed a missing executable {program}")
    return program


def executable(root: Path, ref: str = DEVELOP_REF) -> Path:
    return install(root, resolve_commit(root, ref))


def site_directory(root: Path, ref: str = DEVELOP_REF) -> Path:
    return executable(root, ref).parent.parent / "site"


def member_binding(root: Path, base: str, head: str, ref: str = DEVELOP_REF) -> dict[str, str]:
    site = site_directory(root, ref).resolve()
    if str(site) not in sys.path:
        sys.path.insert(0, str(site))
    sys.dont_write_bytecode = True
    try:
        local = importlib.import_module("theseus_reviewer.local")
    except ImportError as exc:
        raise TheseusError(f"the verified Theseus install at {site} cannot be imported: {exc}") from exc
    if not Path(local.__file__ or "").resolve().is_relative_to(site):
        raise TheseusError(f"theseus_reviewer was imported from outside the verified install at {site}")
    try:
        return dict(local.member_binding(root, base, head))
    except (local.Unavailable, OSError) as exc:
        raise TheseusError(f"the member binding of {base[:12]}..{head[:12]} cannot be computed: {exc}") from exc


def launcher_environment(program: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = environment(base)
    env["PATH"] = os.pathsep.join([str(program.parent), env.get("PATH", "")])
    return env


def run(
    root: Path,
    ref: str,
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    program = executable(root, ref)
    return subprocess.run(
        ["theseus", *args],
        cwd=cwd or root,
        env=launcher_environment(program, env),
        capture_output=capture,
        text=True,
        check=False,
    )


def gate_refusals(run_command: Runner, repo: str, number: int, head: str, label: str = "pr-arm") -> list[str]:
    code, out = run_command(
        [
            "sh",
            "-c",
            'set -eu; export GITHUB_TOKEN="$(gh auth token)"; exec "$@"',
            "sh",
            sys.executable,
            str(Path(__file__).resolve()),
            "gate",
            "--repo",
            repo,
            "--pr",
            str(number),
            "--head-sha",
            head,
        ]
    )
    if code == 0:
        return []
    detail = out.strip().splitlines()[-1][:300] if out.strip() else "no output"
    if code == GATE_WAITING_EXIT:
        return [f"a fresh oracle-gate evaluation of {head[:9]} does not pass: {detail}"]
    return [f"a fresh oracle-gate evaluation of {head[:9]} failed (exit {code}): {detail}"]


def gate(args: argparse.Namespace) -> int:
    root = Path.cwd()
    commit = resolve_commit(root, args.trusted_ref)
    with tempfile.TemporaryDirectory(prefix="theseus-gate-root-") as scratch:
        archive = subprocess.run(
            ["git", "-C", str(root), "archive", "--format=tar", commit, TRUSTED_TREE], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", scratch], input=archive, check=True)
        command = [
            "gate",
            "--repo",
            args.repo,
            "--pr",
            str(args.pr),
            "--head-sha",
            args.head_sha,
            "--policy",
            POLICY_PATH,
            "--root",
            scratch,
        ]
        if not args.post:
            command.append("--no-post")
        return run(root, commit, command).returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the verified Theseus CLI from a trusted BenchBox commit.")
    parser.add_argument("--trusted-ref", default=DEVELOP_REF)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("path", help="Print the verified theseus executable.")
    run_parser = commands.add_parser("run", help="Run theseus with the remaining arguments.")
    run_parser.add_argument("rest", nargs=argparse.REMAINDER)
    gate_parser = commands.add_parser("gate", help="Evaluate the oracle gate from the trusted policy.")
    gate_parser.add_argument("--repo", required=True)
    gate_parser.add_argument("--pr", type=int, required=True)
    gate_parser.add_argument("--head-sha", required=True)
    gate_parser.add_argument("--post", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "path":
            if _release_tree(root):
                return 0
            print(executable(root, args.trusted_ref))
            return 0
        if args.command == "gate":
            return gate(args)
        rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
        return run(root, args.trusted_ref, rest).returncode
    except TheseusError as exc:
        print(f"theseus: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())
