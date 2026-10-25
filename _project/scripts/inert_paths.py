#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

try:
    from _project.scripts.soundness_paths import _glob_matches, _load_rules, normalize_path
except ImportError:
    from soundness_paths import _glob_matches, _load_rules, normalize_path

DATA_PATH = Path(
    os.environ.get(
        "INERT_PATH_MANIFEST",
        str(Path(__file__).resolve().parents[2] / ".github" / "inert-paths.txt"),
    )
)
READERS_PATH = Path(__file__).resolve().parents[1] / "inert-doc-readers.json"
_VALID_KINDS = frozenset({"file", "glob", "prefix", "regex", "except"})
_RULES = _load_rules(DATA_PATH, _VALID_KINDS)
EXEMPT_PATTERNS = tuple(rule.pattern for rule in _RULES if rule.kind != "except")
EXCEPT_PATTERNS = tuple(rule.pattern for rule in _RULES if rule.kind == "except")
_EXEMPT_PREFIXES = tuple(rule.pattern for rule in _RULES if rule.kind == "prefix")
_EXEMPT_FILES = tuple(rule.pattern for rule in _RULES if rule.kind in {"file", "glob"})
_EXEMPT_GLOBS = tuple(rule.pattern for rule in _RULES if rule.kind == "glob")
_EXEMPT_REGEXES = tuple(re.compile(rule.pattern) for rule in _RULES if rule.kind == "regex")
SCAN_DIRS = ("benchbox", "scripts", "_project/scripts", "tests")
WORKFLOW_GLOB = ".github/workflows/*.yml"
MAKE_GLOB = "make/*.mk"
MAKEFILE = "Makefile"
_PATH_CTOR_NAMES = frozenset({"Path", "PurePath", "PurePosixPath", "PureWindowsPath", "PosixPath", "WindowsPath"})
_SINK_CALL_NAMES = frozenset({"open"})
_SINK_CALL_ATTRS = frozenset(
    {
        "join",
        "exists",
        "isfile",
        "isdir",
        "lexists",
        "getsize",
        "getmtime",
        "listdir",
        "scandir",
        "makedirs",
        "mkdir",
        "remove",
        "unlink",
        "rmdir",
        "rename",
        "replace",
        "stat",
        "readlink",
        "symlink",
        "copy",
        "copy2",
        "copyfile",
        "move",
        "glob",
        "rglob",
        "open",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "touch",
        "iterdir",
        "joinpath",
    }
)
_YAML_MAKE_CANDIDATE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*(?:\.(?:md|mdx|rst|txt|json|jsonl|yaml|yml))\b")
MAX_CANDIDATE_SEGMENTS = 8


def matches_exemption(path: str, rules: tuple = _RULES) -> bool:
    normalized = normalize_path(path)
    if not normalized:
        return False
    prefixes = tuple(rule.pattern for rule in rules if rule.kind == "prefix")
    files = tuple(rule.pattern for rule in rules if rule.kind in {"file", "glob"})
    globs = tuple(rule.pattern for rule in rules if rule.kind == "glob")
    regexes = [re.compile(rule.pattern) for rule in rules if rule.kind == "regex"]
    return (
        any(regex.match(normalized) is not None for regex in regexes)
        or any(_glob_matches(normalized, pattern) for pattern in globs)
        or normalized in files
        or normalized.startswith(prefixes)
    )


def matches_exception(path: str, patterns: tuple[str, ...] = EXCEPT_PATTERNS) -> bool:
    return any(_glob_matches(normalize_path(path), pattern) for pattern in patterns)


def is_machine_read_doc(path: str) -> bool:
    return matches_exception(path)


def is_inert_path(path: str) -> bool:
    return matches_exemption(path) and not matches_exception(path)


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _collect_bindings(tree: ast.AST) -> dict[str, list[ast.expr]]:
    bindings: dict[str, list[ast.expr]] = {}

    def add(name: str, expr: ast.expr) -> None:
        bindings.setdefault(name, []).append(expr)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _target_names(target):
                    add(name, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None or isinstance(node, ast.NamedExpr):
            for name in _target_names(node.target):
                add(name, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if isinstance(node.iter, (ast.Tuple, ast.List)):
                for name in _target_names(node.target):
                    for elt in node.iter.elts:
                        add(name, elt)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = node.args.posonlyargs + node.args.args
            offset = len(positional) - len(node.args.defaults)
            for arg, default in zip(positional[offset:], node.args.defaults):
                add(arg.arg, default)
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if default is not None:
                    add(arg.arg, default)
    return bindings


def _resolve_joined(
    parts: list[ast.expr], bindings: dict[str, list[ast.expr]], home: str, active: frozenset[str]
) -> list[str]:
    combos = [""]
    for part in parts:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            options = [part.value]
        elif isinstance(part, ast.FormattedValue):
            options = _resolve(part.value, bindings, home, active)
        else:
            return []
        combos = [prefix + option for prefix in combos for option in options]
        if len(combos) > 64:
            return []
    return combos


def _resolve_call(
    func: ast.expr, args: list[ast.expr], bindings: dict[str, list[ast.expr]], home: str, active: frozenset[str]
) -> list[str]:
    if isinstance(func, ast.Name) and func.id in _PATH_CTOR_NAMES:
        resolved: list[str] = []
        for arg in args:
            resolved.extend(_resolve(arg, bindings, home, active))
        if not args:
            return []
        return [posixpath.join(*resolved)] if len(resolved) > 1 else resolved
    if isinstance(func, ast.Attribute) and func.attr == "join":
        resolved = []
        for arg in args:
            resolved.extend(_resolve(arg, bindings, home, active))
        return [posixpath.join(*resolved)] if resolved else []
    if isinstance(func, ast.Attribute) and func.attr == "joinpath":
        resolved = _resolve(func.value, bindings, home, active)
        tails: list[str] = []
        for arg in args:
            tails.extend(_resolve(arg, bindings, home, active))
        if not resolved or not tails:
            return []
        return [posixpath.join(base, *tails) for base in resolved]
    if isinstance(func, ast.Attribute) and func.attr == "resolve":
        return [posixpath.normpath(candidate) for candidate in _resolve(func.value, bindings, home, active)]
    return []


def _resolve(
    expr: ast.expr, bindings: dict[str, list[ast.expr]], home: str, active: frozenset[str] = frozenset()
) -> list[str]:
    if isinstance(expr, ast.Constant):
        return [expr.value] if isinstance(expr.value, str) else []
    if isinstance(expr, ast.Name):
        if expr.id == "__file__":
            return [home]
        if expr.id in active:
            return []
        return [
            candidate
            for bound in bindings.get(expr.id, ())
            for candidate in _resolve(bound, bindings, home, active | {expr.id})
        ]
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return [
            left.rstrip("/") + "/" + right
            for left in _resolve(expr.left, bindings, home, active)
            for right in _resolve(expr.right, bindings, home, active)
        ]
    if isinstance(expr, ast.Attribute) and expr.attr == "parent":
        return [
            posixpath.dirname(candidate)
            for candidate in _resolve(expr.value, bindings, home, active)
            if candidate not in ("", ".")
        ]
    if isinstance(expr, ast.Attribute) and expr.attr == "parents":
        return _resolve(expr.value, bindings, home, active)
    if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant) and isinstance(expr.slice.value, int):
        base = expr.value
        if isinstance(base, ast.Attribute) and base.attr == "parents":
            depth = expr.slice.value + 1
            return [
                posixpath.dirname(candidate)
                if depth == 1
                else posixpath.normpath(posixpath.join(candidate, *([os.pardir] * depth)))
                for candidate in _resolve(base.value, bindings, home, active)
            ]
        return []
    if isinstance(expr, ast.Call):
        return _resolve_call(expr.func, list(expr.args), bindings, home, active)
    if isinstance(expr, ast.JoinedStr):
        return _resolve_joined(list(expr.values), bindings, home, active)
    return []


def _is_path_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id in _PATH_CTOR_NAMES:
        return True
    return isinstance(func, ast.Attribute) and func.attr in {"join", "joinpath"}


def _is_sink_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _SINK_CALL_NAMES
    return isinstance(func, ast.Attribute) and func.attr in _SINK_CALL_ATTRS


def _to_repo_relative(candidate: str, repo_root: str) -> str | None:
    normalized = posixpath.normpath(candidate.replace("\\", "/"))
    if not normalized or normalized == ".":
        return None
    if posixpath.isabs(normalized):
        relative = posixpath.relpath(normalized, repo_root.replace("\\", "/"))
        if relative == "." or relative.startswith(".."):
            return None
        return relative
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or None


def file_candidates(source: str, abs_path: str, repo_root: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    bindings = _collect_bindings(tree)
    home = os.path.abspath(abs_path)
    base = os.path.abspath(repo_root)
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            or isinstance(node, ast.Call)
            and _is_path_call(node)
        ):
            targets = [node]
        elif isinstance(node, ast.Call) and _is_sink_call(node):
            for arg in list(node.args) + [keyword.value for keyword in node.keywords]:
                for candidate in _resolve(arg, bindings, home):
                    relative = _to_repo_relative(candidate, base)
                    if relative:
                        found.add(relative)
            continue
        else:
            continue
        for target in targets:
            for candidate in _resolve(target, bindings, home):
                relative = _to_repo_relative(candidate, base)
                if relative:
                    found.add(relative)
    return found


def _strip_inline_comment(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return ""
    cut = line.find(" #")
    return line[:cut] if cut != -1 else line


def text_candidates(text: str, skip_echo_lines: bool = False) -> set[str]:
    lines = [_strip_inline_comment(line) for line in text.splitlines()]
    if skip_echo_lines:
        lines = [line for line in lines if not re.match(r"\s*@?(echo|printf)\b", line)]
    return set(_YAML_MAKE_CANDIDATE.findall("\n".join(lines)))


def tracked_files(repo_root: str | Path) -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        text=True,
        capture_output=True,
        timeout=120,
    )
    return {entry for entry in proc.stdout.split("\0") if entry}


def basename_index(tracked: set[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in tracked:
        index.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    return index


def to_tracked(candidates: set[str], tracked: set[str], basenames: dict[str, list[str]]) -> set[str]:
    hits: set[str] = set()
    for candidate in candidates:
        if len(candidate.split("/")) > MAX_CANDIDATE_SEGMENTS:
            continue
        if candidate in tracked:
            hits.add(candidate)
        elif "/" not in candidate:
            hits.update(basenames.get(candidate, ()))
    return hits


def _is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path == "tests" or path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def scan_tree(repo_root: str | Path) -> dict[str, list[str]]:
    root = Path(repo_root)
    tracked = tracked_files(root)
    basenames = basename_index(tracked)
    refs: dict[str, list[str]] = {}
    for scan in SCAN_DIRS:
        for path in sorted((root / scan).rglob("*.py")):
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = to_tracked(file_candidates(source, str(path), str(root)), tracked, basenames)
            own = path.relative_to(root).as_posix()
            hits.discard(own)
            if hits:
                refs[own] = sorted(hits)
    workflow_paths = sorted((root / ".github" / "workflows").glob("*.yml"))
    make_paths = [root / MAKEFILE] + sorted((root / "make").glob("*.mk"))
    for path in workflow_paths + make_paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        hits = to_tracked(text_candidates(text, skip_echo_lines=path in make_paths), tracked, basenames)
        if hits:
            refs[path.relative_to(root).as_posix()] = sorted(hits)
    return refs


def doc_reader_index(repo_root: str | Path, refs: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    index: dict[str, set[str]] = {}
    for source, hits in (scan_tree(repo_root) if refs is None else refs).items():
        if not _is_test_file(source):
            continue
        for hit in hits:
            if is_machine_read_doc(hit):
                index.setdefault(hit, set()).add(source)
    return {doc: sorted(tests) for doc, tests in sorted(index.items())}


def write_readers(repo_root: str | Path) -> dict[str, list[str]]:
    index = doc_reader_index(repo_root)
    path = Path(repo_root) / "_project" / "inert-doc-readers.json"
    path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def load_readers(repo_root: str | Path) -> dict[str, list[str]]:
    path = Path(repo_root) / "_project" / "inert-doc-readers.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {doc: sorted(tests) for doc, tests in data.items() if isinstance(tests, list)}


def tracked_symlink_entries(repo_root: str | Path) -> list[tuple[str, str]]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-s"],
        check=True,
        text=True,
        capture_output=True,
        timeout=120,
    )
    entries: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        meta, _, name = line.partition("\t")
        if meta.split(" ", 1)[0] != "120000" or not name:
            continue
        try:
            entries.append((name, os.readlink(Path(repo_root) / name)))
        except OSError:
            continue
    return entries


def symlink_violations(entries: list[tuple[str, str]]) -> list[str]:
    problems: list[str] = []
    for name, target in entries:
        if is_inert_path(name):
            problems.append(f"tracked symlink {name} points from an inert path")
        if posixpath.isabs(target):
            continue
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
        if resolved != "." and not resolved.startswith("..") and is_inert_path(resolved):
            problems.append(f"tracked symlink {name} points into inert path {resolved}")
    return problems


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    parser.add_argument("--regenerate-readers", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.regenerate_readers:
        index = write_readers(args.repo_root)
        print(f"wrote {len(index)} machine-read docs")
        return 0
    raise SystemExit("Provide --regenerate-readers.")


if __name__ == "__main__":
    raise SystemExit(main())
