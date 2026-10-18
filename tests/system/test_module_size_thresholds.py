from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.medium


MAX_LINES_DEFAULT = 1_200

ALLOWLIST_HEADROOM = 25

ALLOWLIST = {
    Path("benchbox/cli/commands/run.py"): 2_977,
    Path("benchbox/core/tpcds/benchmark/runner.py"): 2_115,
    Path("benchbox/core/tpcdi/generator/data.py"): 317,
}

MODULE_PATHS = [
    Path("benchbox/cli/commands/run.py"),
    Path("benchbox/cli/run_platform_resolution.py"),
    Path("benchbox/cli/app.py"),
    Path("benchbox/core/tpcds/benchmark/runner.py"),
    Path("benchbox/core/tpcds/generator/manager.py"),
    Path("benchbox/core/tpcdi/etl/pipeline.py"),
    Path("benchbox/core/tpcdi/generator/data.py"),
    Path("benchbox/platforms/clickhouse/adapter.py"),
]


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _allowlist_snippet(path: Path, line_count: int) -> str:
    return f'    Path(\n        "{path.as_posix()}"\n    ): {line_count},  # <justification -- why this module must exceed the default>'


def test_runtime_modules_respect_size_limits() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    violations = []

    for relative_path in MODULE_PATHS:
        full_path = repo_root / relative_path
        if not full_path.exists():
            raise AssertionError(f"Tracked module {relative_path} is missing; update the size guard list")

        line_count = _count_lines(full_path)
        if relative_path in ALLOWLIST:
            limit = ALLOWLIST[relative_path] + ALLOWLIST_HEADROOM
        else:
            limit = MAX_LINES_DEFAULT

        if line_count > limit:
            violations.append((relative_path, line_count, limit))

    if violations:
        details = "\n".join(f"{path} has {lines} lines (limit {limit})" for path, lines, limit in violations)
        snippets = "\n".join(_allowlist_snippet(path, lines) for path, lines, _limit in violations)
        raise AssertionError(
            "Runtime module size guardrails tripped:\n"
            + details
            + "\nReduce the module size, or -- if the size is justified -- paste this into "
            "ALLOWLIST in tests/system/test_module_size_thresholds.py with a real "
            "justification (ALLOWLIST_HEADROOM is added automatically on top; no `make "
            "guards-fix` regen exists for this guard, it is always a reviewed hand edit):\n" + snippets
        )


def test_allowlist_headroom_applies_only_to_allowlisted_entries() -> None:
    sample_path = next(iter(ALLOWLIST))
    documented_lines = ALLOWLIST[sample_path]

    assert documented_lines + ALLOWLIST_HEADROOM > documented_lines
    assert ALLOWLIST_HEADROOM < MAX_LINES_DEFAULT, "headroom must stay small relative to the default budget"

    untracked_path = Path("benchbox/cli/app.py")
    assert untracked_path not in ALLOWLIST
    effective_limit = (
        ALLOWLIST[untracked_path] + ALLOWLIST_HEADROOM if untracked_path in ALLOWLIST else MAX_LINES_DEFAULT
    )
    assert effective_limit == MAX_LINES_DEFAULT
