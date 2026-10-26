from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_PATH = "quality/docs-ownership.json"
GATE_PATH = "scripts/check_docs_freeze.py"
REGISTRATIONS = (".github/workflows/ci.yml", "make/documentation.mk")
GATE_NAME = "check_docs_freeze"


def load_split_ownership() -> Any:
    spec = importlib.util.spec_from_file_location(
        "docs_history_filter", Path(__file__).with_name("docs_history_filter.py")
    )
    if spec is None or spec.loader is None:
        raise ValueError("cannot load docs_history_filter next to the freeze gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.split_ownership


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def blob(root: Path, rev: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"], capture_output=True, check=False)
    return result.stdout if result.returncode == 0 else None


def changed_paths(root: Path, base: str, head: str) -> list[tuple[str, str]]:
    raw = git(root, "diff", "--name-status", "--no-renames", "-z", f"{base}...{head}")
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    pairs = []
    for index in range(0, len(fields) - 1, 2):
        pairs.append((fields[index][:1], fields[index + 1]))
    return pairs


def load_manifest(root: Path, rev: str) -> dict[str, Any] | None:
    data = blob(root, rev, OWNERSHIP_PATH)
    return None if data is None else json.loads(data)


def frozen_paths(manifest: dict[str, Any], split_ownership: Any) -> set[str]:
    owned, excluded = split_ownership(manifest)
    directories = tuple(path for path in excluded if path.endswith("/"))
    return {path for path in owned - excluded if not path.startswith(directories)}


def generated_paths(manifest: dict[str, Any]) -> set[str]:
    return {entry["path"] for entry in manifest.get("generated", [])}


def is_removal(root: Path, head: str) -> bool:
    if blob(root, head, GATE_PATH) is not None:
        return False
    for path in REGISTRATIONS:
        content = blob(root, head, path)
        if content is not None and GATE_NAME.encode() in content:
            return False
    return True


def violations(root: Path, base: str, head: str) -> list[str]:
    split_ownership = load_split_ownership()
    base_manifest = load_manifest(root, base)
    if base_manifest is None:
        raise ValueError(f"{OWNERSHIP_PATH} is missing at the base revision")
    head_manifest = load_manifest(root, head)
    removal = is_removal(root, head)
    frozen_base = frozen_paths(base_manifest, split_ownership)
    frozen_head = frozen_paths(head_manifest, split_ownership) if head_manifest is not None else set()
    found: list[str] = []
    for status, path in changed_paths(root, base, head):
        if path == OWNERSHIP_PATH:
            if removal:
                continue
            if head_manifest is None:
                found.append(f"{path}: deleted while site-owned sources are frozen")
            elif frozen_base != frozen_head or generated_paths(base_manifest) != generated_paths(head_manifest):
                changed = sorted(frozen_base ^ frozen_head)[:5]
                found.append(f"{path}: changes the frozen site-owned set (for example {changed})")
        elif path in frozen_base:
            if status == "D" and removal:
                continue
            found.append(f"{path}: {'deleted' if status == 'D' else 'changed'} while frozen as site-owned")
        elif path in frozen_head:
            found.append(f"{path}: site-owned source added while frozen")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when site-owned documentation sources change during the freeze.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", required=True, help="trusted base revision")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        found = violations(root, args.base, args.head)
    except (OSError, ValueError) as error:
        print(f"docs freeze gate error: {error}", file=sys.stderr)
        return 2
    if found:
        print("Site-owned documentation sources are frozen for the site migration:", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        print(
            "Make the change in the site repository after the import, or land the reviewed migration removal "
            "that deletes this gate, the frozen sources and the ownership entries together.",
            file=sys.stderr,
        )
        return 1
    print("docs freeze gate: no frozen site-owned source changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
