from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from packaging.version import Version

CLI_DESCRIPTION = (
    "Release gate: the bundled SQLGlot runtime must be built on the newest upstream release.\n"
    "\n"
    "Reads the upstream commit recorded in benchbox/_vendor/sqlglot.json, lists the final\n"
    "release tags of the upstream repository, and fails when a newer tag exists. A newer\n"
    "tag is accepted only when scripts/sqlglot_refresh_waiver.json carries an unexpired,\n"
    "reasoned waiver for exactly that tag. An unreachable upstream also fails the gate.\n"
)

UPSTREAM_REPOSITORY = "https://github.com/tobymao/sqlglot"
PIN_PATH = Path("benchbox/_vendor/sqlglot.json")
WAIVER_PATH = Path("scripts/sqlglot_refresh_waiver.json")
MAX_WAIVER_DAYS = 31
REPO_ROOT = Path(__file__).resolve().parents[1]
_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_SHA = re.compile(r"[0-9a-f]{40}")

TagLister = Callable[[], Mapping[str, str]]


def list_upstream_tags() -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-remote", "--tags", UPSTREAM_REPOSITORY, "refs/tags/v*"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot list upstream tags: {result.stderr.strip() or result.returncode}")
    tags: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        name = ref.removeprefix("refs/tags/")
        peeled = name.endswith("^{}")
        name = name.removesuffix("^{}")
        if _TAG.fullmatch(name) and _SHA.fullmatch(sha) and (peeled or name not in tags):
            tags[name] = sha
    return tags


def _version(tag: str) -> Version:
    return Version(tag.removeprefix("v"))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def validate_waivers(waivers: Any, today: date) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not isinstance(waivers, list):
        return {}, ["waiver file: 'waivers' must be a list"]
    accepted: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for entry in waivers:
        try:
            tag, reason = entry["tag"], entry["reason"]
            approved, expires = date.fromisoformat(entry["approved"]), date.fromisoformat(entry["expires"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"waiver file: malformed entry {entry!r}")
            continue
        if not (isinstance(tag, str) and _TAG.fullmatch(tag) and isinstance(reason, str) and reason.strip()):
            problems.append(f"waiver file: entry for {tag!r} needs a final release tag and a reason")
        elif approved > today:
            problems.append(f"waiver for {tag}: approval date {approved} is in the future")
        elif expires - approved > timedelta(days=MAX_WAIVER_DAYS) or expires < approved:
            problems.append(f"waiver for {tag}: must expire within {MAX_WAIVER_DAYS} days of approval")
        elif expires >= today:
            accepted[tag] = entry
    return accepted, problems


def evaluate(pin: Mapping[str, Any], tags: Mapping[str, str], waivers: Any, today: date) -> list[str]:
    upstream_sha = pin.get("upstream_sha")
    if not isinstance(upstream_sha, str) or not _SHA.fullmatch(upstream_sha):
        return ["bundled SQLGlot pin has no valid upstream_sha"]
    pinned = [tag for tag, sha in tags.items() if sha == upstream_sha]
    if not pinned:
        return [f"bundled SQLGlot upstream commit {upstream_sha} is not a final upstream release tag"]
    base = max(pinned, key=_version)
    newer = sorted((tag for tag in tags if _version(tag) > _version(base)), key=_version)
    if not newer:
        return []
    latest = newer[-1]
    accepted, problems = validate_waivers(waivers, today)
    if latest in accepted:
        return problems
    message = (
        f"bundled SQLGlot is based on upstream {base} but {latest} is released; refresh the fork "
        f"(see downstream/MAINTAINING.md in {pin.get('repository')}) and adopt the new wheel, "
        f"or record a reviewed waiver for {latest} in {WAIVER_PATH}"
    )
    return [*problems, message]


def check(root: Path, lister: TagLister | None = None, today: date | None = None) -> list[str]:
    pin = load_json(root / PIN_PATH)
    waivers = load_json(root / WAIVER_PATH).get("waivers")
    try:
        tags = (lister or list_upstream_tags)()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return [str(error)]
    return evaluate(pin, tags, waivers, today or datetime.now(UTC).date())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    arguments = parser.parse_args(argv)
    problems = check(arguments.root)
    if problems:
        print("sqlglot upstream freshness FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("sqlglot upstream freshness OK: bundled runtime is on the newest upstream release or an approved waiver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
