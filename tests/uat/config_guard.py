from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

GUARDED_TREES = (("tests/uat/configs", "*.y*ml"), ("docker", "*.y*ml"))
GUARDED_KEY_RE = re.compile(
    r"timeout|memory|mem_limit|memswap|shm_size|floor|min_pairs|validator|reserve|min_gib|retries|start_period"
)
_ABSENT = "<absent>"


@dataclass(frozen=True)
class GuardedChange:
    path: str
    key: str
    base: object
    head: object

    def render(self) -> str:
        return f"{self.path}: {self.key}: {self.base!r} -> {self.head!r}"


def guarded_values(text: str) -> dict[str, object]:
    document = yaml.safe_load(text)
    found: dict[str, object] = {}
    _collect(document, "", found)
    return found


def _collect(node: object, path: str, found: dict[str, object]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, (Mapping, list)):
                _collect(value, child, found)
            elif GUARDED_KEY_RE.search(str(key)):
                found[child] = value
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _collect(value, f"{path}[{index}]", found)


def diff_guarded_values(path: str, base_text: str | None, head_text: str | None) -> list[GuardedChange]:
    base = guarded_values(base_text) if base_text is not None else {}
    head = guarded_values(head_text) if head_text is not None else {}
    return [
        GuardedChange(path, key, base.get(key, _ABSENT), head.get(key, _ABSENT))
        for key in sorted(base.keys() | head.keys())
        if base.get(key, _ABSENT) != head.get(key, _ABSENT)
    ]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def _guarded_paths(root: Path, base_ref: str) -> list[str]:
    tracked_in_base = _git(root, "ls-tree", "-r", "--name-only", base_ref)
    if tracked_in_base.returncode != 0:
        raise RuntimeError(f"cannot read {base_ref}: {tracked_in_base.stderr.strip()}")
    paths = {line for line in tracked_in_base.stdout.splitlines() if _is_guarded_path(line)}
    for tree, pattern in GUARDED_TREES:
        paths.update(str(path.relative_to(root)) for path in (root / tree).rglob(pattern))
    return sorted(paths)


def _is_guarded_path(path: str) -> bool:
    return any(path.startswith(f"{tree}/") and fnmatch.fnmatch(path, f"*/{pattern}") for tree, pattern in GUARDED_TREES)


def changed_guarded_values(root: Path, base_ref: str) -> list[GuardedChange]:
    changes: list[GuardedChange] = []
    for relative in _guarded_paths(root, base_ref):
        shown = _git(root, "show", f"{base_ref}:{relative}")
        base_text = shown.stdout if shown.returncode == 0 else None
        file_path = root / relative
        head_text = file_path.read_text(encoding="utf-8") if file_path.is_file() else None
        changes.extend(diff_guarded_values(relative, base_text, head_text))
    return changes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a timeout, memory-limit, validator or floor value differs from a base ref"
    )
    parser.add_argument("--base", required=True, help="Git ref the working tree is compared against")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    changes = changed_guarded_values(args.root.resolve(), args.base)
    for change in changes:
        print(change.render(), file=sys.stderr)
    if changes:
        return 1
    print(f"no timeout, memory-limit, validator or floor value differs from {args.base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
