#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DATA_PATH = Path(
    os.environ.get(
        "SOUNDNESS_PATH_MANIFEST",
        str(Path(__file__).resolve().parents[2] / ".github" / "soundness-paths.txt"),
    )
)
_VALID_KINDS = {"file", "glob", "prefix", "regex"}


CLI_DESCRIPTION = "Data-backed soundness-path predicate shared by CI and local tooling."


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: str


def _load_rules(path: Path = DATA_PATH, valid_kinds: frozenset[str] = _VALID_KINDS) -> tuple[Rule, ...]:
    manifest = "soundness path" if valid_kinds == _VALID_KINDS else "path"
    rules: list[Rule] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            kind, pattern = line.split("\t", 1)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: expected kind<TAB>pattern") from exc
        if kind not in valid_kinds:
            raise ValueError(f"{path}:{line_number}: unknown rule kind {kind!r}")
        if not pattern:
            raise ValueError(f"{path}:{line_number}: empty rule pattern")
        if kind == "prefix" and not pattern.endswith("/"):
            raise ValueError(f"{path}:{line_number}: prefix must end with '/': {pattern!r}")
        if kind == "regex":
            re.compile(pattern)
        rules.append(Rule(kind, pattern))
    if not rules:
        raise ValueError(f"{path}: {manifest} manifest is empty")
    return tuple(rules)


_RULES = _load_rules()
SOUNDNESS_PREFIXES = tuple(rule.pattern for rule in _RULES if rule.kind == "prefix")
SOUNDNESS_FILES = tuple(rule.pattern for rule in _RULES if rule.kind in {"file", "glob"})
SOUNDNESS_GLOBS = tuple(rule.pattern for rule in _RULES if rule.kind == "glob")
SOUNDNESS_REGEXES = tuple(re.compile(rule.pattern) for rule in _RULES if rule.kind == "regex")
_REGEX_PATTERNS = tuple(rule.pattern for rule in _RULES if rule.kind == "regex")
_VALIDATION_RE = re.compile(next(pattern for pattern in _REGEX_PATTERNS if "validation" in pattern))
OVERRIDE_FILES_GLOB = next(
    pattern for pattern in SOUNDNESS_GLOBS if pattern == "results-data/bundles/**/*.override.json"
)


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def surface_invariant_violations() -> list[str]:
    return [
        f"SOUNDNESS_PREFIXES entry {prefix!r} must end with '/' (exact files belong in SOUNDNESS_FILES)"
        for prefix in SOUNDNESS_PREFIXES
        if not prefix.endswith("/")
    ]


def is_soundness_path(path: str) -> bool:
    normalized = normalize_path(path)
    if not normalized:
        return False
    return (
        any(regex.match(normalized) is not None for regex in SOUNDNESS_REGEXES)
        or any(_glob_matches(normalized, pattern) for pattern in SOUNDNESS_GLOBS)
        or normalized in SOUNDNESS_FILES
        or normalized.startswith(SOUNDNESS_PREFIXES)
    )


def _glob_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or (
        "/**/" in pattern and fnmatch.fnmatchcase(path, pattern.replace("/**/", "/"))
    )


def any_soundness_path(paths: Iterable[str]) -> bool:
    return any(is_soundness_path(path) for path in paths)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--stdin", action="store_true", help="Read newline-delimited paths from stdin.")
    parser.add_argument("--paths-file", help="Read newline-delimited paths from a file.")
    parser.add_argument(
        "--format",
        choices=("plain", "shell", "github-output"),
        default="plain",
        help="Output format. Default prints true/false.",
    )
    return parser.parse_args(argv)


def _read_paths(args: argparse.Namespace) -> list[str]:
    if args.stdin:
        return list(sys.stdin)
    if args.paths_file:
        return Path(args.paths_file).read_text(encoding="utf-8").splitlines()
    raise SystemExit("Provide --stdin or --paths-file.")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    result = any_soundness_path(_read_paths(args))
    value = "true" if result else "false"
    if args.format == "github-output":
        print(f"soundness_path={value}")
    elif args.format == "shell":
        print(f"SOUNDNESS_PATH={value}")
    else:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
