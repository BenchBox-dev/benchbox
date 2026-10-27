#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from audit_sha_check import AuditShaError, parse_frontmatter, run_git

CLI_DESCRIPTION = "Backfill `develop_sha` frontmatter into historical audit Markdown files."


@dataclass(frozen=True)
class Derivation:
    develop_sha: str
    source: str
    introducing_commit: str


def git_lines(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        command = " ".join(["git", *args])
        raise AuditShaError(f"{command} failed: {detail}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def repo_root() -> Path:
    return Path(run_git(["rev-parse", "--show-toplevel"]))


def relative_to_root(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_ancestor(commit: str, target_ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, target_ref],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        ).returncode
        == 0
    )


def first_parent(commit: str) -> str:
    return run_git(["rev-parse", f"{commit}^1"])


def introducing_commit(path: Path, root: Path, target_ref: str) -> tuple[str, str]:
    rel_path = relative_to_root(path, root)

    head_candidates = git_lines(["log", "--diff-filter=A", "--reverse", "--format=%H", "--", rel_path])
    if head_candidates and is_ancestor(head_candidates[0], target_ref):
        return head_candidates[0], "introducing commit"

    target_candidates = git_lines(["log", "--diff-filter=A", "--reverse", "--format=%H", target_ref, "--", rel_path])
    if target_candidates and is_ancestor(target_candidates[0], target_ref):
        return target_candidates[0], "merge commit"

    if head_candidates:
        return head_candidates[0], "parent fallback"

    raise AuditShaError(f"{rel_path}: could not find an introducing commit")


def derive_develop_sha(path: Path, root: Path, target_ref: str) -> Derivation:
    commit, source = introducing_commit(path, root, target_ref)
    return Derivation(develop_sha=first_parent(commit), source=source, introducing_commit=commit)


def has_develop_sha(path: Path) -> str | None:
    frontmatter = parse_frontmatter(path)
    if frontmatter is None:
        return None
    value = frontmatter.fields.get("develop_sha")
    return value or None


def stamp_text(original: str, develop_sha: str, comment: str | None = None) -> str:
    stamp_lines = []
    if comment:
        stamp_lines.append(f"# {comment}\n")
    stamp_lines.append(f"develop_sha: {develop_sha}\n")

    lines = original.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                lines[index:index] = stamp_lines
                return "".join(lines)
        raise AuditShaError("frontmatter starts with --- but has no closing ---")

    return f"---\n{''.join(stamp_lines)}---\n{original}"


def audit_paths(root: Path, paths: list[Path]) -> list[Path]:
    if paths:
        return sorted(path if path.is_absolute() else root / path for path in paths)
    return sorted((root / "_project" / "audits").glob("*.md"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("paths", nargs="*", type=Path, help="Specific audit Markdown files to backfill")
    parser.add_argument(
        "--target-ref",
        default="origin/develop",
        help="Git ref used to derive historical develop parents (default: origin/develop)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print derivations without modifying files")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repo_root()

    try:
        for path in audit_paths(root, args.paths):
            if not path.exists():
                raise AuditShaError(f"{path}: file does not exist")
            existing = has_develop_sha(path)
            rel_path = relative_to_root(path, root)
            if existing:
                print(f"{rel_path}: already-stamped develop_sha={existing}")
                continue

            derivation = derive_develop_sha(path, root, args.target_ref)
            print(
                f"{rel_path}: backfilled develop_sha={derivation.develop_sha} "
                f"source={derivation.source} introducing_commit={derivation.introducing_commit}"
            )
            if not args.dry_run:
                original = path.read_text(encoding="utf-8")
                comment = None
                if derivation.source == "parent fallback":
                    comment = f"audit_sha_backfill parent fallback from {derivation.introducing_commit}"
                path.write_text(stamp_text(original, derivation.develop_sha, comment), encoding="utf-8")
    except AuditShaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
