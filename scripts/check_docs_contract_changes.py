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
RELEVANT_PREFIXES = ("benchbox/", EXPORTER)
EXPORT_TIMEOUT_SECONDS = 600
GIT_TIMEOUT_SECONDS = 120
CLI_OPTION_FIELDS = ("is_flag", "choices")
PLATFORM_OPTION_FIELDS = ("value_kind", "choices", "default", "aliases")
MCP_PARAM_FIELDS = ("type", "required", "default")
ADVICE = (
    "The site refuses a core bundle whose exported docs contract has entries its documentation does not cover, and "
    "keeps serving the previous bundle until a site change lands. Open the matching benchbox-site documentation "
    "change before this pull request merges."
)

Entries = dict[str, dict[str, Any]]


class Change(NamedTuple):
    action: str
    identity: str
    detail: str


def _fields(source: Mapping[str, Any], names: Sequence[str]) -> dict[str, Any]:
    return {name: source.get(name) for name in names}


def contract_entries(contracts: Mapping[str, Any]) -> Entries:
    entries: Entries = {}
    mcp = contracts.get("mcp") or {}
    for tool, definition in (mcp.get("tools") or {}).items():
        entries[f"mcp tool {tool}"] = {}
        for name, param in (definition.get("params") or {}).items():
            entries[f"mcp tool {tool} parameter {name}"] = _fields(param, MCP_PARAM_FIELDS)
    for tool in mcp.get("remote_only_tools") or []:
        entries[f"mcp remote-only tool {tool}"] = {}
    for command in (contracts.get("cli") or {}).get("commands") or []:
        entries[f"cli command {command['path']}"] = {"is_group": command.get("is_group")}
        for option in command.get("options") or []:
            names = "/".join(option["names"])
            entries[f"cli option {command['path']} {names}"] = _fields(option, CLI_OPTION_FIELDS)
    for platform, options in ((contracts.get("platform_options") or {}).get("platforms") or {}).items():
        for name, spec in options.items():
            entries[f"platform option {platform} {name}"] = _fields(spec, PLATFORM_OPTION_FIELDS)
    return entries


def _show(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def diff_entries(base: Entries, head: Entries) -> list[Change]:
    changes: list[Change] = []
    for identity in sorted(head.keys() - base.keys()):
        changes.append(Change("added", identity, "new in this pull request"))
    for identity in sorted(base.keys() - head.keys()):
        changes.append(Change("removed", identity, "no longer exported"))
    for identity in sorted(base.keys() & head.keys()):
        before, after = base[identity], head[identity]
        moved = [
            f"{field}: {_show(before.get(field))} -> {_show(after.get(field))}"
            for field in sorted(before.keys() | after.keys())
            if before.get(field) != after.get(field)
        ]
        if moved:
            changes.append(Change("changed", identity, "; ".join(moved)))
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


def _git(args: Sequence[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
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
    return any(path.startswith(RELEVANT_PREFIXES) for path in paths)


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
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"::notice title=Docs contract comparison skipped::{_escape(str(error))}")
        return 0
    if not changes:
        print("docs contract unchanged")
        return 0
    publish(changes, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
