#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

LOCK_PATH = Path(".github") / "theseus-lock.json"
LEDGER_TOOL = Path("tools") / "upstream_ledger.py"
LEDGER_FILE = Path("tools") / "upstream_ledger.json"
DEFAULT_REF = "origin/develop"
BASE_ENV = "BASE_SHA"
ENGINE_DIR = "_project/scripts/oracle_reviewers"
FULL_SHA = re.compile(r"[0-9a-f]{40}")

Runner = Callable[[Sequence[str]], tuple[int, str]]
Fetcher = Callable[[str, str, Path], None]


class LedgerCheckError(Exception):
    pass


def _run(command: Sequence[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def fetch_source(repository: str, commit: str, destination: Path, run: Runner = _run) -> None:
    steps = (
        ["git", "init", "--quiet", str(destination)],
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--quiet",
            "--filter=blob:none",
            f"https://github.com/{repository}.git",
            commit,
        ],
        ["git", "-C", str(destination), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    )
    for step in steps:
        code, output = run(step)
        if code != 0:
            raise LedgerCheckError(f"{' '.join(step[:4])} failed: {output[-300:]}")
    code, head = run(["git", "-C", str(destination), "rev-parse", "HEAD"])
    if code != 0 or head.strip() != commit:
        raise LedgerCheckError(f"the fetched {repository} tree is at {head.strip()!r}, not the locked {commit}")


def baseline_problems(lock: Mapping[str, Any], ledger: Mapping[str, Any]) -> list[str]:
    locked = lock.get("bootstrap", {}).get("baseline_commit")
    if ledger.get("baseline") != locked:
        return [f"the upstream ledger baseline {ledger.get('baseline')!r} is not the lock's baseline {locked!r}"]
    return []


def decide(code: int, output: str) -> list[str]:
    if code == 0:
        return []
    kind = "the ledger is stale" if code == 1 else "the ledger check could not run"
    return [f"{kind} (exit {code}):", *output.splitlines()]


def resolve_ref(environ: Mapping[str, str], argv_ref: str | None) -> str:
    ref = (argv_ref or environ.get(BASE_ENV, "")).strip()
    return ref or DEFAULT_REF


def verify(
    root: Path,
    lock: Mapping[str, Any],
    ref: str,
    *,
    run: Runner = _run,
    fetch: Fetcher | None = None,
) -> list[str]:
    commit = lock["source_commit"]
    baseline = lock["bootstrap"]["baseline_commit"]
    if not FULL_SHA.fullmatch(commit) or not FULL_SHA.fullmatch(baseline):
        return ["the lock must carry full source and baseline commits"]
    code, _ = run(["git", "-C", str(root), "cat-file", "-e", f"{baseline}^{{commit}}"])
    if code != 0:
        return [f"the checkout has no history back to the baseline {baseline}; fetch with depth 0"]
    with tempfile.TemporaryDirectory(prefix="theseus-source-") as scratch:
        source = Path(scratch) / "source"
        try:
            if fetch is None:
                fetch_source(lock["repository"], commit, source, run)
            else:
                fetch(lock["repository"], commit, source)
            ledger = json.loads((source / LEDGER_FILE).read_text(encoding="utf-8"))
        except (LedgerCheckError, OSError, ValueError) as exc:
            return [str(exc)]
        problems = baseline_problems(lock, ledger)
        if problems:
            return problems
        code, output = run(
            [sys.executable, str(source / LEDGER_TOOL), "check", "--benchbox-repo", str(root), "--ref", ref]
        )
    return decide(code, output)


def _release_tree(root: Path) -> bool:
    if (root / ".github" / "theseus-lock.json").exists() or (root / ".github" / "oracle-reviewers.yml").exists():
        return False
    print("release tree: no Theseus lock; skipping", file=sys.stderr)
    return True


def base_has_engine(root: Path, ref: str, run: Runner) -> bool:
    code, output = run(["git", "-C", str(root), "ls-tree", ref, ENGINE_DIR])
    return code != 0 or bool(output.strip())


def main(argv: list[str], environ: Mapping[str, str] | None = None, run: Runner = _run) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    if _release_tree(root):
        return 0
    ref = resolve_ref(os.environ if environ is None else environ, argv[2] if len(argv) > 2 else None)
    if not base_has_engine(root, ref, run):
        print(
            f"base {ref} has no {ENGINE_DIR}/ tree: the in-tree engine is already removed, "
            "so the upstream-port ledger no longer applies; skipping",
            file=sys.stderr,
        )
        return 0
    try:
        lock = json.loads((root / LOCK_PATH).read_text(encoding="utf-8"))
        problems = verify(root, lock, ref, run=run)
    except (OSError, ValueError, KeyError) as exc:
        problems = [str(exc)]
    if problems:
        print(f"theseus upstream-port ledger check failed against {ref}:", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"theseus upstream-port ledger is current against {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
