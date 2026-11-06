#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

VALID_KINDS = frozenset({"file", "glob", "prefix", "regex"})

CLI_DESCRIPTION = "Manifest-backed soundness-path predicate shared by CI and local tooling."


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: str


def parse_rules(
    text: str, source: str = "manifest", valid_kinds: frozenset[str] = VALID_KINDS, label: str = "soundness path"
) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            kind, pattern = line.split("\t", 1)
        except ValueError as exc:
            raise ValueError(f"{source}:{line_number}: expected kind<TAB>pattern") from exc
        if kind not in valid_kinds:
            raise ValueError(f"{source}:{line_number}: unknown rule kind {kind!r}")
        if not pattern:
            raise ValueError(f"{source}:{line_number}: empty rule pattern")
        if kind == "prefix" and not pattern.endswith("/"):
            raise ValueError(f"{source}:{line_number}: prefix must end with '/': {pattern!r}")
        if kind == "regex":
            re.compile(pattern)
        rules.append(Rule(kind, pattern))
    if not rules:
        raise ValueError(f"{source}: {label} manifest is empty")
    return tuple(rules)


def load_rules(
    path: Path, valid_kinds: frozenset[str] = VALID_KINDS, label: str = "soundness path"
) -> tuple[Rule, ...]:
    return parse_rules(path.read_text(encoding="utf-8"), str(path), valid_kinds, label)


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def glob_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or (
        "/**/" in pattern and fnmatch.fnmatchcase(path, pattern.replace("/**/", "/"))
    )


def rules_match(path: str, rules: Sequence[Rule]) -> bool:
    normalized = normalize_path(path)
    if not normalized:
        return False
    for rule in rules:
        if rule.kind == "regex":
            if re.match(rule.pattern, normalized) is not None:
                return True
        elif rule.kind == "glob":
            if glob_matches(normalized, rule.pattern):
                return True
        elif rule.kind == "file":
            if normalized == rule.pattern:
                return True
        elif normalized.startswith(rule.pattern):
            return True
    return False


@dataclass(frozen=True)
class Matcher:
    rules: tuple[Rule, ...]

    def is_soundness_path(self, path: str) -> bool:
        return rules_match(path, self.rules)

    def any_soundness_path(self, paths: Iterable[str]) -> bool:
        return any(self.is_soundness_path(path) for path in paths)


def union_matches(path: str, matchers: Iterable[Matcher]) -> bool:
    return any(matcher.is_soundness_path(path) for matcher in matchers)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="Soundness-path manifest. Repeat for base and head copies: a path counts when any manifest lists it.",
    )
    parser.add_argument("--stdin", action="store_true", help="Read newline-delimited paths from stdin.")
    parser.add_argument("--paths-file", help="Read newline-delimited paths from a file.")
    parser.add_argument(
        "--format",
        choices=("plain", "shell", "github-output"),
        default="plain",
        help="Output format. Default prints true/false.",
    )
    parser.add_argument("--print-source-sha256", action="store_true", help="Print the sha256 of this module's bytes.")
    return parser.parse_args(argv)


def _read_paths(args: argparse.Namespace) -> list[str]:
    if args.stdin:
        return list(sys.stdin)
    if args.paths_file:
        return Path(args.paths_file).read_text(encoding="utf-8").splitlines()
    raise SystemExit("Provide --stdin or --paths-file.")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_source_sha256:
        import hashlib

        print(hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        return 0
    if not args.manifest:
        raise SystemExit("soundness_paths: --manifest is required; there is no default manifest.")
    try:
        matchers = [Matcher(load_rules(Path(manifest))) for manifest in args.manifest]
    except (OSError, ValueError) as exc:
        raise SystemExit(f"soundness_paths: {exc}") from exc
    paths = _read_paths(args)
    value = "true" if any(union_matches(path, matchers) for path in paths) else "false"
    if args.format == "github-output":
        print(f"soundness_path={value}")
    elif args.format == "shell":
        print(f"SOUNDNESS_PATH={value}")
    else:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
