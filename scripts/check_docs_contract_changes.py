#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]

CLI_DESCRIPTION = (
    "Warn about entries of the exported docs contract that the PR head adds, removes or changes against its base. "
    "Never fails the build."
)
EXPORTER = "scripts/doc_contracts.py"
RELEVANT_PREFIXES = (
    "benchbox/",
    "quality/",
    "_project/design/",
    "tests/unit/generators/sf1_size_baseline.json",
    "pyproject.toml",
    "scripts/",
)
PUBLISH_LIST_NAMES = frozenset({"publish-allowlist.txt", "publish-exclusions.txt"})
EXPORT_TIMEOUT_SECONDS = 600
GIT_TIMEOUT_SECONDS = 120
IDENTITY_KEYS = ("platform", "key", "path", "symbol", "names", "name")
ROOT_ENTRY = "contract"
DETAIL_LIMIT = 400
IGNORED_FIELDS = frozenset({"contract.package.version"})
ADVICE = (
    "The site refuses a core bundle whose exported docs contract disagrees with its documentation or has entries the "
    "documentation does not cover, and keeps serving the previous bundle until a site change lands. Open the "
    "matching benchbox-site documentation change before this pull request merges."
)

Entries = dict[str, dict[str, Any]]


class Change(NamedTuple):
    action: str
    identity: str
    detail: str


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _identity(record: Mapping[str, Any], position: int) -> str:
    parts = []
    for key in IDENTITY_KEYS:
        value = record.get(key)
        if isinstance(value, list):
            value = "/".join(str(item) for item in value)
        if isinstance(value, str | int) and not isinstance(value, bool):
            parts.append(str(value))
    return " ".join(parts) if parts else f"#{position}"


def _children(value: Any) -> dict[str, Any]:
    if not _is_record_list(value):
        return dict(value)
    children: dict[str, Any] = {}
    for position, record in enumerate(value):
        name = _identity(record, position)
        while name in children:
            name += "'"
        children[name] = record
    return children


def _is_branch(value: Any) -> bool:
    return isinstance(value, dict) or _is_record_list(value)


def _collect(node: Any, path: str, entries: Entries) -> None:
    children = _children(node)
    own = {key: value for key, value in children.items() if not _is_branch(value)}
    own = {key: value for key, value in own.items() if f"{path}.{key}" not in IGNORED_FIELDS}
    if own or not children:
        entries[path] = own
    for key, value in children.items():
        if _is_branch(value):
            _collect(value, f"{path}.{key}", entries)


def contract_entries(contracts: Mapping[str, Any]) -> Entries:
    entries: Entries = {}
    _collect(contracts, ROOT_ENTRY, entries)
    return entries


def _show(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _clip(text: str) -> str:
    return text if len(text) <= DETAIL_LIMIT else text[: DETAIL_LIMIT - 3] + "..."


def _field_change(before: Any, after: Any) -> str:
    if isinstance(before, list) and isinstance(after, list):
        gone = [item for item in before if item not in after]
        new = [item for item in after if item not in before]
        if gone or new:
            return "".join([f"+{_show(item)} " for item in new] + [f"-{_show(item)} " for item in gone]).strip()
    return f"{_show(before)} -> {_show(after)}"


def diff_entries(base: Entries, head: Entries) -> list[Change]:
    changes: list[Change] = []
    for identity in sorted(head.keys() - base.keys()):
        changes.append(Change("added", identity, "new in this pull request"))
    for identity in sorted(base.keys() - head.keys()):
        changes.append(Change("removed", identity, "no longer exported"))
    for identity in sorted(base.keys() & head.keys()):
        before, after = base[identity], head[identity]
        moved = [
            f"{field}: {_field_change(before.get(field), after.get(field))}"
            for field in sorted(before.keys() | after.keys())
            if before.get(field) != after.get(field)
        ]
        if moved:
            changes.append(Change("changed", identity, _clip("; ".join(moved))))
    return changes


def _escape(text: str, *, prop: bool = False) -> str:
    escaped = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def render_annotations(changes: Sequence[Change]) -> list[str]:
    return [
        f"::warning title={_escape(f'Docs contract {change.action}', prop=True)}::"
        f"{_escape(f'{change.identity} ({change.detail}). The site documentation must cover it.')}"
        for change in changes
    ]


def render_summary(changes: Sequence[Change]) -> str:
    lines = [f"## Docs contract changes ({len(changes)})", "", ADVICE, ""]
    lines.extend(f"- **{change.action}** `{change.identity}`: {change.detail}" for change in changes)
    return "\n".join(lines) + "\n"


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=GIT_TIMEOUT_SECONDS,
    )


def changed_paths(base_sha: str) -> list[str] | None:
    result = _git(["diff", "--name-only", "--no-renames", f"{base_sha}...HEAD"])
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def is_relevant(paths: Sequence[str]) -> bool:
    return any(path.startswith(RELEVANT_PREFIXES) or path.rsplit("/", 1)[-1] in PUBLISH_LIST_NAMES for path in paths)


def export_contracts(tree: Path, output: Path) -> dict[str, Any]:
    exporter = tree / EXPORTER
    if not exporter.is_file():
        raise RuntimeError(f"{EXPORTER} does not exist in {tree}")
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.doc_contracts", "--output", str(output)],
        cwd=tree,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=EXPORT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise RuntimeError(f"export failed in {tree}: {' | '.join(tail)}")
    return json.loads(output.read_text(encoding="utf-8"))


def export_base_contracts(base_sha: str, scratch: Path) -> dict[str, Any]:
    if _git(["cat-file", "-e", f"{base_sha}^{{commit}}"]).returncode != 0:
        raise RuntimeError(f"base commit {base_sha} is not present in the checkout")
    tree = scratch / "base-tree"
    added = _git(["worktree", "add", "--detach", str(tree), base_sha])
    if added.returncode != 0:
        raise RuntimeError(f"cannot check out {base_sha}: {added.stderr.strip()}")
    try:
        return export_contracts(tree, scratch / "base-doc-contracts.json")
    finally:
        _git(["worktree", "remove", "--force", str(tree)])


def compare(base_sha: str) -> list[Change]:
    with tempfile.TemporaryDirectory(prefix="docs-contract-diff-") as name:
        scratch = Path(name)
        base = export_base_contracts(base_sha, scratch)
        head = export_contracts(REPO_ROOT, scratch / "head-doc-contracts.json")
    return diff_entries(contract_entries(base), contract_entries(head))


def publish(changes: Sequence[Change], summary_path: str | None) -> None:
    for line in render_annotations(changes):
        print(line)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(render_summary(changes))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--base-sha", default=os.environ.get("EVENT_BASE_SHA", ""))
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    parser.add_argument("--always", action="store_true", help="Skip the changed-path relevance filter.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.base_sha:
        print("notice: docs contract comparison skipped: no base commit given")
        return 0
    try:
        if not args.always:
            paths = changed_paths(args.base_sha)
            if paths is not None and not is_relevant(paths):
                print("docs contract comparison skipped: no changed path feeds the exported contract")
                return 0
        changes = compare(args.base_sha)
        if not changes:
            print("docs contract unchanged")
            return 0
        publish(changes, args.summary)
    except Exception as error:
        print(f"::notice title=Docs contract comparison skipped::{_escape(f'{type(error).__name__}: {error}')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
