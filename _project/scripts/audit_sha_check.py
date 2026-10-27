#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
NUMERIC_EVIDENCE_RE = re.compile(
    r"(?:\b[0-9][0-9,]*(?:\.[0-9]+)?\s+"
    r"(?:tests?|checks?|queries|bundles|items|comments|findings|platforms|benchmarks|rows|files?|prs?)\b)"
    r"|(?:\b[0-9][0-9,]*(?:\.[0-9]+)?\s+(?:passed|failed|skipped|timed\s+out)\b)",
    re.IGNORECASE,
)


CLI_DESCRIPTION = "Validate tree and measurement SHA provenance on audit Markdown files."


class AuditShaError(Exception):
    pass


@dataclass(frozen=True)
class Frontmatter:
    start_line: int
    end_line: int
    fields: dict[str, str]


@dataclass(frozen=True)
class AuditValidation:
    develop_sha: str
    distance: int | None
    measured_at_sha: str | None
    replay_sha: str | None


def run_git(args: list[str]) -> str:
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
    return result.stdout.strip()


def git_ok(args: list[str]) -> bool:
    return (
        subprocess.run(
            ["git", *args],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        ).returncode
        == 0
    )


def _strip_scalar(value: str) -> str:
    value = value.split("#", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def parse_frontmatter(path: Path) -> Frontmatter | None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None

    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise AuditShaError(f"{path}: frontmatter starts with --- but has no closing ---")

    fields: dict[str, str] = {}
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        fields[key.strip()] = _strip_scalar(value)

    return Frontmatter(start_line=0, end_line=end_index, fields=fields)


def numeric_evidence_lines(path: Path, frontmatter: Frontmatter) -> list[int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        line_number
        for line_number, line in enumerate(lines, start=1)
        if line_number > frontmatter.end_line + 1 and NUMERIC_EVIDENCE_RE.search(line)
    ]


def _require_sha(path: Path, frontmatter: Frontmatter, field: str) -> str:
    value = frontmatter.fields.get(field, "").strip()
    if not value:
        raise AuditShaError(f"{path}: missing required {field} frontmatter field")
    if not SHA_RE.fullmatch(value):
        raise AuditShaError(f"{path}: {field} must be a full 40-character commit SHA, got {value!r}")
    if not git_ok(["cat-file", "-e", f"{value}^{{commit}}"]):
        raise AuditShaError(f"{path}: {field} {value} is not a local commit object")
    return value.lower()


def validate_audit(
    path: Path,
    target_ref: str,
    require_current: int | None = None,
    ancestry_ref: str = "HEAD",
) -> AuditValidation:
    if not path.exists():
        raise AuditShaError(f"{path}: file does not exist")
    if not path.is_file():
        raise AuditShaError(f"{path}: not a regular file")

    frontmatter = parse_frontmatter(path)
    if frontmatter is None:
        raise AuditShaError(f"{path}: missing YAML frontmatter with required develop_sha")

    develop_sha = _require_sha(path, frontmatter, "develop_sha")

    try:
        target_sha = run_git(["rev-parse", "--verify", f"{target_ref}^{{commit}}"])
    except AuditShaError as exc:
        raise AuditShaError(f"{path}: target ref {target_ref!r} is not available ({exc})") from exc

    if not git_ok(["merge-base", "--is-ancestor", develop_sha, target_sha]):
        raise AuditShaError(f"{path}: develop_sha {develop_sha} is not reachable from {target_ref} ({target_sha})")

    distance: int | None = None
    if require_current is not None:
        if require_current < 0:
            raise AuditShaError("--require-current must be non-negative")
        distance_text = run_git(["rev-list", "--count", f"{develop_sha}..{target_sha}"])
        distance = int(distance_text)
        if distance > require_current:
            raise AuditShaError(
                f"{path}: develop_sha {develop_sha} is {distance} commits behind {target_ref}; "
                f"allowed distance is {require_current}"
            )

    evidence_lines = numeric_evidence_lines(path, frontmatter)
    measured_at_sha: str | None = None
    if evidence_lines or frontmatter.fields.get("measured_at_sha"):
        measured_at_sha = _require_sha(path, frontmatter, "measured_at_sha")
        expected_measurement = frontmatter.fields.get("checked_sha", develop_sha).lower()
        if not SHA_RE.fullmatch(expected_measurement):
            raise AuditShaError(f"{path}: checked_sha must be a full 40-character commit SHA")
        if measured_at_sha != expected_measurement:
            raise AuditShaError(
                f"{path}: measured_at_sha {measured_at_sha} does not match the declared exact tree "
                f"{expected_measurement}"
            )
        if not git_ok(["merge-base", "--is-ancestor", measured_at_sha, ancestry_ref]):
            raise AuditShaError(f"{path}: measured_at_sha {measured_at_sha} is not reachable from {ancestry_ref}")

    replay_sha: str | None = None
    if frontmatter.fields.get("replay_sha") or frontmatter.fields.get("replay_scope"):
        if measured_at_sha is None:
            raise AuditShaError(f"{path}: replay_sha requires measured_at_sha")
        replay_sha = _require_sha(path, frontmatter, "replay_sha")
        replay_scope = frontmatter.fields.get("replay_scope", "").strip()
        if not replay_scope:
            raise AuditShaError(f"{path}: replay_sha requires a non-empty replay_scope")
        if not git_ok(["merge-base", "--is-ancestor", measured_at_sha, replay_sha]):
            raise AuditShaError(f"{path}: replay_sha {replay_sha} must descend from measured_at_sha {measured_at_sha}")
        if not git_ok(["merge-base", "--is-ancestor", replay_sha, ancestry_ref]):
            raise AuditShaError(f"{path}: replay_sha {replay_sha} is not reachable from {ancestry_ref}")

    return AuditValidation(develop_sha, distance, measured_at_sha, replay_sha)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("files", nargs="+", type=Path, help="Audit Markdown file(s) to validate")
    parser.add_argument(
        "--target-ref",
        default="origin/develop",
        help="Git ref the stamped develop_sha must be reachable from (default: origin/develop)",
    )
    parser.add_argument(
        "--require-current",
        type=int,
        metavar="N",
        help="Require develop_sha to be within N commits of --target-ref",
    )
    parser.add_argument(
        "--ancestry-ref",
        default="HEAD",
        help=(
            "Ref the stamped measured_at_sha/replay_sha must descend from "
            "(default: HEAD). Merge-queue runs check out a squashed preview "
            "commit that never contains the PR branch history, so those runs "
            "pass the PR head ref instead."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        for file_path in args.files:
            result = validate_audit(file_path, args.target_ref, args.require_current, args.ancestry_ref)
            details = [f"develop_sha={result.develop_sha}", f"target_ref={args.target_ref}"]
            if args.ancestry_ref != "HEAD":
                details.append(f"ancestry_ref={args.ancestry_ref}")
            if result.distance is not None:
                details.append(f"distance={result.distance}")
            if result.measured_at_sha is not None:
                details.append(f"measured_at_sha={result.measured_at_sha}")
            if result.replay_sha is not None:
                details.append(f"replay_sha={result.replay_sha}")
            print(f"OK {file_path}: {' '.join(details)}")
    except AuditShaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
