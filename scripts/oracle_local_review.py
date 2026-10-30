#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

DEVELOP_REF = "origin/develop"
FETCH_BRANCH = "develop"
REVIEWER_PATHS = (
    ".github/oracle-reviewers.yml",
    ".github/oracle-omp-config.yml",
    ".github/soundness-paths.txt",
    ".github/inert-paths.txt",
    "_project/scripts/oracle_reviewers",
    "_project/scripts/soundness_paths.py",
    "_project/scripts/inert_paths.py",
)
EXPORT_KEEP = 3
EXPORT_PARENT = Path(".cache") / "benchbox" / "local-oracle" / "export"
COMPLETE_MARKER = ".complete"
PYTHON_VERSION = "3.12"
YAML_REQUIREMENT = "pyyaml==6.0.3"
REVIEW_MODULE = "_project.scripts.oracle_reviewers.cli"
EXIT_UNAVAILABLE = 2

Launcher = Callable[[list[str], dict[str, str]], int]


class ExportError(Exception):
    pass


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise ExportError(f"git {args[0]} failed: {detail}")
    return result.stdout


def resolve_reviewer_commit(root: Path, ref: str) -> str:
    if ref == DEVELOP_REF:
        _git(root, "fetch", "origin", FETCH_BRANCH, "--quiet")
    return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode("utf-8").strip()


def export_reviewer(root: Path, commit: str, parent: Path) -> Path:
    target = parent / commit
    if (target / COMPLETE_MARKER).is_file():
        return target
    parent.mkdir(parents=True, exist_ok=True)
    scratch = parent / f".partial-{uuid.uuid4().hex}"
    try:
        archive = _git(root, "archive", "--format=tar", commit, "--", *REVIEWER_PATHS)
        scratch.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(scratch, filter="data")
        (scratch / COMPLETE_MARKER).write_text(commit + "\n", encoding="utf-8")
        try:
            scratch.rename(target)
        except OSError:
            if not (target / COMPLETE_MARKER).is_file():
                raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return target


def prune_exports(parent: Path, keep: Path) -> None:
    others = sorted(
        (item for item in parent.iterdir() if item.is_dir() and not item.name.startswith(".") and item != keep),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in others[EXPORT_KEEP - 1 :]:
        shutil.rmtree(stale, ignore_errors=True)


def review_command(args: argparse.Namespace) -> list[str]:
    return [
        "uv",
        "run",
        "--isolated",
        "--no-project",
        "--python",
        PYTHON_VERSION,
        "--with",
        YAML_REQUIREMENT,
        "--",
        "python",
        "-m",
        REVIEW_MODULE,
        "local-review",
        "--policy",
        args.policy,
        "--base-ref",
        args.base_ref,
        "--head",
        args.head,
        "--body-file",
        args.body_file,
    ]


def launch(argv: list[str], env: dict[str, str]) -> int:
    os.execvpe(argv[0], argv, env)
    return EXIT_UNAVAILABLE


def run(args: argparse.Namespace, launcher: Launcher = launch) -> int:
    root = Path.cwd()
    parent = Path.home() / EXPORT_PARENT
    try:
        commit = resolve_reviewer_commit(root, args.reviewer_ref)
        target = export_reviewer(root, commit, parent)
        prune_exports(parent, target)
    except (ExportError, OSError, tarfile.TarError) as exc:
        print(
            f"Refusing to open the PR: the trusted reviewer copy of {args.reviewer_ref} could not be prepared "
            f"({exc}); the local oracle review cannot run.",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE
    env = {**os.environ, "PYTHONPATH": str(target)}
    return launcher(review_command(args), env)


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description="Run the local oracle review from a trusted export of develop.")
    built.add_argument("--policy", default=".github/oracle-reviewers.yml")
    built.add_argument("--base-ref", default=DEVELOP_REF)
    built.add_argument("--head", default="HEAD")
    built.add_argument("--body-file", default="")
    built.add_argument("--reviewer-ref", default=DEVELOP_REF)
    return built


def main(argv: Sequence[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
