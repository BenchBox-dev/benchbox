#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

PREDICATE_REPO_PATH = "_project/scripts/soundness_paths.py"
POLICY_PATHS = (PREDICATE_REPO_PATH, ".github/soundness-paths.txt")
BROAD_CONFTEST_MODULES = 200
SELECTION_LOGIC_PATHS = frozenset(
    {
        "scripts/coverage_test_selection.py",
        "scripts/coverage_map.ini",
        "_project/scripts/preflight_targets.py",
        "scripts/heavy_tier_needed.py",
        ".github/path-filters.yml",
        "scripts/path_filter_decision.py",
        "scripts/ci_units.py",
        ".github/ci-units.yml",
    }
)


class HeavyTierError(RuntimeError):
    pass


def _load_predicate_copy(name: str, sources: dict[str, str], paths: list[str]) -> bool:
    if set(sources) != set(POLICY_PATHS):
        raise HeavyTierError(f"predicate copy {name!r} has an invalid file set")
    if any("\n" in path or "\r" in path for path in paths):
        raise HeavyTierError("changed paths contain ambiguous line separators")
    try:
        with tempfile.TemporaryDirectory(prefix=f"{name}-") as directory:
            root = Path(directory)
            for relative, source in sources.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(root / PREDICATE_REPO_PATH), "--stdin", "--format", "github-output"],
                input="\n".join(paths),
                cwd=root,
                env={**os.environ, "SOUNDNESS_PATH_MANIFEST": str(root / POLICY_PATHS[1])},
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HeavyTierError(f"predicate copy {name!r} could not execute: {exc}") from exc
    if result.returncode != 0:
        raise HeavyTierError(f"predicate copy {name!r} exited {result.returncode}")
    verdict = result.stdout.strip()
    if verdict not in {"soundness_path=true", "soundness_path=false"}:
        raise HeavyTierError(f"predicate copy {name!r} returned an invalid verdict")
    return verdict == "soundness_path=true"


def _read_base_copy(base_ref: str, repo_root: Path) -> dict[str, str]:
    try:
        commit = subprocess.check_output(
            ["git", "--no-replace-objects", "-C", str(repo_root), "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip()
        present = subprocess.check_output(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repo_root),
                "ls-tree",
                "-z",
                "--name-only",
                commit,
                "--",
                *POLICY_PATHS,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).split("\0")
        return {
            path: subprocess.check_output(
                ["git", "--no-replace-objects", "-C", str(repo_root), "show", f"{commit}:{path}"],
                text=True,
                encoding="utf-8",
                stderr=subprocess.DEVNULL,
            )
            for path in POLICY_PATHS
            if path in present
        }
    except (subprocess.SubprocessError, OSError) as exc:
        raise HeavyTierError(f"could not read base-ref copy of the predicate: {exc}") from exc


def _read_pr_copy(repo_root: Path) -> dict[str, str]:
    sources: dict[str, str] = {}
    for relative in POLICY_PATHS:
        try:
            sources[relative] = (repo_root / relative).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise HeavyTierError(f"could not read PR policy file {relative}: {exc}") from exc
    return sources


def soundness_touched(
    changed_paths: Iterable[str],
    base_ref: str,
    repo_root: Path,
    read_base: Callable[[str, Path], dict[str, str]] | None = None,
    read_pr: Callable[[Path], dict[str, str]] | None = None,
) -> tuple[bool, str]:
    paths = [str(path) for path in changed_paths]
    base_source = (read_base or _read_base_copy)(base_ref, repo_root)
    pr_source = (read_pr or _read_pr_copy)(repo_root)
    base_hit = _load_predicate_copy("soundness_base_copy", base_source, paths)
    pr_hit = _load_predicate_copy("soundness_pr_copy", pr_source, paths)
    if base_hit or pr_hit:
        which = "+".join(name for name, hit in (("base", base_hit), ("pr", pr_hit)) if hit)
        return True, f"soundness path touched (predicate copies: {which})"
    return False, "no soundness path touched under either predicate copy"


def broad_test_infrastructure(changed_paths: Iterable[str], repo_root: Path) -> list[str]:
    broad = []
    for path in changed_paths:
        pure = PurePosixPath(path)
        if path == "pytest.ini" or path in SELECTION_LOGIC_PATHS:
            broad.append(path)
        elif pure.name in {"conftest.py", "__init__.py"} and pure.parts[:1] == ("tests",):
            modules = sum(1 for _ in (repo_root / pure.parent).rglob("test_*.py"))
            if modules > BROAD_CONFTEST_MODULES:
                broad.append(path)
    return broad


def heavy_needed(
    decision: dict[str, Any],
    base_ref: str,
    repo_root: Path,
    read_base: Callable[[str, Path], dict[str, str]] | None = None,
    read_pr: Callable[[Path], dict[str, str]] | None = None,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        for flag in ("needs_code_ci", "packaging_needed"):
            if not isinstance(decision.get(flag), bool):
                raise HeavyTierError(f"decision has no boolean {flag}")
        changed = decision.get("changed_paths")
        if not isinstance(changed, list) or any(
            not isinstance(path, str) or not path or "\n" in path or "\r" in path for path in changed
        ):
            raise HeavyTierError("decision has no unambiguous changed_paths list of strings")
        needs_code_ci = decision["needs_code_ci"]
        broad = broad_test_infrastructure(changed, repo_root)
        if broad:
            return {
                "heavy_needed": True,
                "reason": f"suite-wide test configuration touched: {', '.join(broad)} (test-infrastructure carve-out)",
            }
        if not needs_code_ci:
            return {"heavy_needed": False, "reason": "not a code-routed tree; the light lane already covers it"}
        if selection is not None:
            if not isinstance(selection, dict) or not isinstance(selection.get("escalate"), bool):
                raise HeavyTierError("coverage selection is unreadable")
            if selection["escalate"]:
                detail = selection.get("reason") or "no reason recorded"
                return {
                    "heavy_needed": True,
                    "reason": f"coverage selection escalated: {detail} (coverage fail-closed carve-out)",
                }
        if decision["packaging_needed"]:
            return {"heavy_needed": True, "reason": "packaging paths touched (packaging carve-out)"}
        touched, reason = soundness_touched(changed, base_ref, repo_root, read_base, read_pr)
        if touched:
            return {"heavy_needed": True, "reason": f"{reason} (soundness carve-out)"}
        return {"heavy_needed": False, "reason": f"{reason}; pull_request runs skip the heavy tier"}
    except Exception as exc:
        return {"heavy_needed": True, "reason": f"lookup failed closed: {exc}"}


def _write_github_output(path: Path, needed: bool) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"heavy-needed={'true' if needed else 'false'}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-in", type=Path, default=None)
    parser.add_argument("--selection-in", type=Path, default=None)
    parser.add_argument("--base-ref", default=os.environ.get("GITHUB_BASE_REF", "origin/develop"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="Exit 0 when heavy-needed is true, else exit 1")
    args = parser.parse_args(argv)

    if args.decision_in is None:
        result: dict[str, Any] = {"heavy_needed": True, "reason": "lookup failed closed: no decision input"}
    else:
        try:
            decision = json.loads(args.decision_in.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            decision = None
            result = {"heavy_needed": True, "reason": f"lookup failed closed: unreadable decision: {exc}"}
        else:
            if not isinstance(decision, dict):
                result = {"heavy_needed": True, "reason": "lookup failed closed: decision is not an object"}
            else:
                selection: dict[str, Any] | None = None
                if args.selection_in is not None:
                    try:
                        selection = json.loads(args.selection_in.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        selection = None
                        result = {
                            "heavy_needed": True,
                            "reason": f"lookup failed closed: unreadable coverage selection: {exc}",
                        }
                    else:
                        if not isinstance(selection, dict):
                            selection = None
                            result = {
                                "heavy_needed": True,
                                "reason": "lookup failed closed: coverage selection is not an object",
                            }
                if selection is not None or args.selection_in is None:
                    result = heavy_needed(decision, args.base_ref, args.repo_root, selection=selection)

    needed = bool(result["heavy_needed"])
    reason = str(result["reason"])
    print(f"heavy-needed={'true' if needed else 'false'}")
    print(reason)
    if args.github_output is not None:
        _write_github_output(args.github_output, needed)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(f"Heavy tier needed: `{str(needed).lower()}` ({reason}).\n")
    if args.check:
        return 0 if needed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
