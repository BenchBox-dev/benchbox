from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATABASE_START = "# Files"
_DATABASE_END = "# Finished Make data base"
_RECIPE_MARKER = re.compile(r"^#  (?:recipe|commands) to execute \(from .*\):$")
_NO_RULE_FOR_COLON = re.compile(r"No rule to make target [`']:'")
_TARGET_VARIABLE = re.compile(r"^# (?P<name>[A-Za-z_][A-Za-z0-9_]*) (?::=|=|\?=|\+=) ?(?P<value>.*)$")


@dataclass(frozen=True)
class MakeRule:
    header: str
    recipe: tuple[str, ...]
    variables: dict[str, str]

    @property
    def prerequisites(self) -> list[str]:
        return self.header.split(":", 1)[1].split()


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


def targets(root: Path) -> list[str]:
    names = set()
    for paragraph in _paragraphs(root):
        name = paragraph.split(":", 1)[0]
        if name.startswith(("#", ".")):
            continue
        if not name[:1].isalnum() or name == "$@":
            continue
        names.add(name)
    return sorted(names)


def rules(root: Path) -> dict[str, MakeRule]:
    found: dict[str, MakeRule] = {}
    for paragraph in _paragraphs(root):
        lines = paragraph.splitlines()
        header = lines[0]
        if header.startswith("#") or ":" not in header:
            continue
        recipe: list[str] = []
        variables: dict[str, str] = {}
        in_recipe = False
        for line in lines[1:]:
            if _RECIPE_MARKER.match(line):
                in_recipe = True
            elif in_recipe and line.startswith("\t") and line.strip():
                recipe.append(line)
            elif assignment := _TARGET_VARIABLE.match(line):
                variables[assignment["name"]] = assignment["value"]
        found[header.split(":", 1)[0]] = MakeRule(header=header, recipe=tuple(recipe), variables=variables)
    return found
