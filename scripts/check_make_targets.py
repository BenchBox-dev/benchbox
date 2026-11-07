from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_DATABASE_START = "# Files"
_DATABASE_END = "# Finished Make data base"
_RECIPE_MARKER = re.compile(r"^#  (?:recipe|commands) to execute \(from .*\):$")
_NO_RULE_FOR_COLON = re.compile(r"No rule to make target [`']:'")
_NOT_A_TARGET = "# Not a target:"
_LEADING_VARIABLE = re.compile(
    r"^[^#\s:][^:]*: (?P<name>[A-Za-z_][A-Za-z0-9_]*) (?::=|::=|\?=|\+=|=)(?: (?P<value>.*))?$"
)
_TRAILING_VARIABLE = re.compile(r"^# (?P<name>[A-Za-z_][A-Za-z0-9_]*) (?::=|=|\?=|\+=) ?(?P<value>.*)$")
_ROOTED_INCLUDE = re.compile(r"^include \$\(BENCHBOX_MAKEFILE_ROOT\)(?P<path>\S+)\s*$")

_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_COMMAND_SUBSTITUTION = re.compile(r"\$\((?!MAKE\))[^()]*\)")
_MAKE_COMMAND = re.compile(r"(?:^|[ \t;&|(])(?:\$\(MAKE\)|make)[ \t]+([^\n;&|)\\]+)", re.MULTILINE)
_TARGET_NAME = re.compile(r"[a-z][a-z0-9.-]*[a-z0-9]")
_OPTIONS_WITH_VALUE = {"-C", "-f", "-I", "-o", "-W"}


@dataclass(frozen=True)
class MakeRule:
    header: str
    recipe: tuple[str, ...]
    variables: dict[str, str]

    @property
    def name(self) -> str:
        return self.header.split(":", 1)[0]

    @property
    def prerequisites(self) -> list[str]:
        return self.header.split(":", 1)[1].lstrip(":").split()


@lru_cache(maxsize=4)
def _database(root: Path) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "-C", str(root), "-pRrq", ":"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    problems = [line for line in result.stderr.splitlines() if line.strip() and not _NO_RULE_FOR_COLON.search(line)]
    start = result.stdout.find(_DATABASE_START)
    end = result.stdout.find(_DATABASE_END)
    if problems or start < 0 or end < 0:
        raise RuntimeError(
            f"make could not read the rules in {root}: " + "; ".join(problems or [result.stderr.strip()])
        )
    return result.stdout[start:end]


def _paragraphs(root: Path) -> list[str]:
    return [paragraph.strip("\n") for paragraph in re.split(r"\n{2,}", _database(root)) if paragraph.strip()]


def _entry(lines: list[str]) -> MakeRule | None:
    variables: dict[str, str] = {}
    for position, line in enumerate(lines):
        if line == _NOT_A_TARGET:
            return None
        if line.startswith("#"):
            continue
        if assignment := _LEADING_VARIABLE.match(line):
            variables[assignment["name"]] = assignment["value"] or ""
            continue
        if ":" not in line:
            return None
        recipe: list[str] = []
        in_recipe = False
        for body in lines[position + 1 :]:
            if _RECIPE_MARKER.match(body):
                in_recipe = True
            elif in_recipe and body.startswith("\t") and body.strip():
                recipe.append(body)
            elif assignment := _TRAILING_VARIABLE.match(body):
                variables[assignment["name"]] = assignment["value"]
        return MakeRule(header=line, recipe=tuple(recipe), variables=variables)
    return None


def rules(root: Path) -> dict[str, MakeRule]:
    found: dict[str, MakeRule] = {}
    for paragraph in _paragraphs(root):
        rule = _entry(paragraph.splitlines())
        if rule is not None:
            found[rule.name] = rule
    return found


def targets(root: Path) -> list[str]:
    return sorted(name for name in rules(root) if name[:1].isalnum() and name != "$@")


def makefile_sources(root: Path) -> str:
    return _with_includes_expanded(root, root / "Makefile")


def _with_includes_expanded(root: Path, path: Path) -> str:
    parts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        include = _ROOTED_INCLUDE.match(line)
        parts.append(_with_includes_expanded(root, root / include["path"]) if include else line)
    return "".join(parts)


def named_targets(markdown: str) -> set[str]:
    code = "\n".join(_FENCED_BLOCK.findall(markdown) + _INLINE_CODE.findall(markdown))
    code = _COMMAND_SUBSTITUTION.sub("", code)
    named: set[str] = set()
    for command in _MAKE_COMMAND.finditer(code):
        tokens = iter(command.group(1).split())
        for token in tokens:
            if token in _OPTIONS_WITH_VALUE:
                next(tokens, None)
            elif token.startswith("-") or "=" in token:
                continue
            elif _TARGET_NAME.fullmatch(token):
                named.add(token)
            else:
                break
    return named


def documentation(root: Path) -> list[Path]:
    paths = [root / "AGENTS.md", root / "CONTRIBUTING.md"]
    decision_records = root / "docs" / "internal" / "adr"
    paths += sorted(path for path in (root / "docs").rglob("*.md") if not path.is_relative_to(decision_records))
    paths += sorted((root / ".claude" / "skills").rglob("*.md"))
    return [path for path in paths if path.is_file()]


def missing_documented_targets(root: Path) -> dict[str, list[str]]:
    defined = set(targets(root))
    missing: dict[str, list[str]] = defaultdict(list)
    for path in documentation(root):
        for target in sorted(named_targets(path.read_text(encoding="utf-8")) - defined):
            missing[target].append(path.relative_to(root).as_posix())
    return dict(missing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if documentation names a make target that does not exist.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    arguments = parser.parse_args(argv)
    missing = missing_documented_targets(arguments.root.resolve())
    for target, paths in sorted(missing.items()):
        print(f"make {target}: no such target, named in {', '.join(paths)}", file=sys.stderr)
    if missing:
        return 1
    print("Every make target named in the documentation exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
