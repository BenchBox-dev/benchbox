from __future__ import annotations

import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pytest

from tests.utilities import make_database

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(shutil.which("make") is None or sys.platform == "win32", reason="POSIX make is required"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_CEILING = 265

_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_COMMAND_SUBSTITUTION = re.compile(r"\$\((?!MAKE\))[^()]*\)")
_MAKE_COMMAND = re.compile(r"(?:^|[ \t;&|(])(?:\$\(MAKE\)|make)[ \t]+([^\n;&|)\\]+)", re.MULTILINE)
_TARGET_NAME = re.compile(r"[a-z][a-z0-9.-]*[a-z0-9]")
_OPTIONS_WITH_VALUE = {"-C", "-f", "-I", "-o", "-W"}


def _named_targets(markdown: str) -> set[str]:
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


def _documentation() -> list[Path]:
    paths = [REPO_ROOT / "AGENTS.md", REPO_ROOT / "CONTRIBUTING.md"]
    paths += sorted(path for path in (REPO_ROOT / "docs").rglob("*.md") if "adr" not in path.parts)
    paths += sorted((REPO_ROOT / ".claude" / "skills").rglob("*.md"))
    return [path for path in paths if path.is_file()]


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("Run `make lint typecheck` first.", {"lint", "typecheck"}),
        ("```bash\nmake worktree-create BRANCH=fix/x WORKTREE_PATH=../y\ncd ../y\n```", {"worktree-create"}),
        ("`make pr-ready PR=1 HEAD=$(git rev-parse HEAD)`", {"pr-ready"}),
        ("```make\n\t$(MAKE) skill-sync-check; \\\n```", {"skill-sync-check"}),
        ("`make -C ../other test-fast`", {"test-fast"}),
        ("Make sure to make the change before you make lint.", set()),
        ("`make <target>` is the shape", set()),
    ],
)
def test_the_scanner_reads_make_targets_only_from_code(markdown: str, expected: set[str]) -> None:
    assert _named_targets(markdown) == expected


def test_every_make_target_named_in_documentation_exists() -> None:
    defined = set(make_database.targets(REPO_ROOT))
    missing: dict[str, list[str]] = defaultdict(list)
    for path in _documentation():
        for target in _named_targets(path.read_text(encoding="utf-8")) - defined:
            missing[target].append(path.relative_to(REPO_ROOT).as_posix())

    assert dict(missing) == {}


def test_the_make_target_count_stays_within_the_ceiling() -> None:
    count = len(make_database.targets(REPO_ROOT))

    assert count <= TARGET_CEILING, (
        f"{count} make targets exceed the ceiling of {TARGET_CEILING}: consolidate or remove a target instead of "
        "adding one, or raise TARGET_CEILING with the owner's agreement"
    )
