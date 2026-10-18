from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER = REPO_ROOT / "docs" / "internal" / "dev-loop-property-ledger.md"

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS_TEST_DIR = REPO_ROOT / "tests" / "unit" / "workflows"
SCRIPTS_TEST_DIR = REPO_ROOT / "tests" / "unit" / "scripts"
RELEASE_TEST_DIR = REPO_ROOT / "tests" / "unit" / "release"
UNIT_DIR = REPO_ROOT / "tests" / "unit"
SCRIPTS_DIR = REPO_ROOT / "scripts"
PROJECT_SCRIPTS_DIR = REPO_ROOT / "_project" / "scripts"
HOOKS_FILE = REPO_ROOT / ".pre-commit-config.yaml"

SECTION_DIRS = {
    "### `.github/workflows/`": WORKFLOW_DIR,
    "### `tests/unit/workflows/`": WORKFLOWS_TEST_DIR,
    "### `tests/unit/scripts/` (complete)": SCRIPTS_TEST_DIR,
    "### `tests/unit/release/`": RELEASE_TEST_DIR,
    "### `tests/unit/test_auto_merge_*`": UNIT_DIR,
    "### `tests/unit/test_release_*`": UNIT_DIR,
    "### `scripts/`": SCRIPTS_DIR,
    "### `_project/scripts/`": PROJECT_SCRIPTS_DIR,
}
NON_FILE_SECTIONS = {"### `.pre-commit-config.yaml` hooks"}


def _ledger_text() -> str:
    assert LEDGER.is_file(), f"ledger missing: {LEDGER}"
    return LEDGER.read_text(encoding="utf-8")


def _section_rows(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("### "):
            current = line if line in SECTION_DIRS else None
            if current is not None:
                sections[current] = []
        elif current is not None and line.startswith("|"):
            sections[current].append(line)
    return sections


def _covered(entry: str, section: str, sections: dict[str, list[str]]) -> bool:
    rows = sections.get(section, [])
    if any(entry in row for row in rows):
        return True
    return any("ledger-catch-all:" in row for row in rows)


def _workflow_files() -> list[str]:
    return sorted(p.name for p in WORKFLOW_DIR.glob("*.yml"))


CUTOVER_FILES = REPO_ROOT / "tests" / "unit" / "ledger_cutover_files.txt"


def _cutover_files(directory: Path, *, recursive: bool = True, pattern: str = "*") -> list[str]:
    entries = CUTOVER_FILES.read_text(encoding="utf-8").split()
    selected: list[str] = []
    for entry in entries:
        path = REPO_ROOT / entry
        try:
            relative = path.relative_to(directory)
        except ValueError:
            continue
        if not path.is_file() or not path.match(pattern):
            continue
        if not recursive and len(relative.parts) != 1:
            continue
        selected.append(relative.as_posix())
    return sorted(selected)


def test_cutover_lists_every_test_with_a_ledger_row() -> None:
    sections = _section_rows(_ledger_text())
    cutover = set(CUTOVER_FILES.read_text(encoding="utf-8").split())
    enrolled = [
        ("### `tests/unit/workflows/`", WORKFLOWS_TEST_DIR.glob("test_*.py"), WORKFLOWS_TEST_DIR),
        ("### `tests/unit/scripts/` (complete)", SCRIPTS_TEST_DIR.rglob("test_*"), SCRIPTS_TEST_DIR),
        ("### `tests/unit/release/`", RELEASE_TEST_DIR.glob("test_*.py"), RELEASE_TEST_DIR),
        ("### `tests/unit/test_auto_merge_*`", UNIT_DIR.glob("test_auto_merge_*.py"), UNIT_DIR),
        ("### `tests/unit/test_release_*`", UNIT_DIR.glob("test_release_*.py"), UNIT_DIR),
    ]
    missing = [
        path.relative_to(REPO_ROOT).as_posix()
        for section, paths, directory in enrolled
        for path in paths
        if any(f"`{path.relative_to(directory).as_posix()}`" in row for row in sections.get(section, []))
        and path.relative_to(REPO_ROOT).as_posix() not in cutover
    ]
    assert not missing, f"tests with a ledger row missing from {CUTOVER_FILES.name}: {missing}"


def test_ledger_lists_every_workflow() -> None:
    text = _ledger_text()
    missing = [name for name in _workflow_files() if name not in text]
    assert not missing, f"workflows missing from ledger: {missing}"


def test_ledger_lists_every_workflows_test() -> None:
    text = _ledger_text()
    names = _cutover_files(WORKFLOWS_TEST_DIR, recursive=False)
    assert names, "expected enrolled tests in tests/unit/workflows/"
    missing = [name for name in names if name not in text]
    assert not missing, f"tests/unit/workflows files missing from ledger: {missing}"


def test_ledger_lists_every_scripts_test() -> None:
    text = _ledger_text()
    sections = _section_rows(text)
    section = "### `tests/unit/scripts/` (complete)"
    names = _cutover_files(SCRIPTS_TEST_DIR, pattern="test_*")
    assert any("/" in name for name in names), "expected nested suites under tests/unit/scripts/"
    missing = [name for name in names if not _covered(name, section, sections)]
    assert not missing, f"tests/unit/scripts files missing from ledger: {missing}"


def test_ledger_lists_every_release_test() -> None:
    text = _ledger_text()
    names = _cutover_files(RELEASE_TEST_DIR, recursive=False)
    assert names, "expected enrolled tests in tests/unit/release/"
    missing = [name for name in names if name not in text]
    assert not missing, f"tests/unit/release files missing from ledger: {missing}"


def test_ledger_lists_auto_merge_and_release_tests() -> None:
    text = _ledger_text()
    names = _cutover_files(UNIT_DIR, recursive=False, pattern="test_auto_merge_*.py")
    names += _cutover_files(UNIT_DIR, recursive=False, pattern="test_release_*.py")
    assert names, "expected auto-merge and release tests in tests/unit/"
    missing = [name for name in names if name not in text]
    assert not missing, f"auto-merge/release tests missing from ledger: {missing}"


def test_ledger_lists_every_script() -> None:
    text = _ledger_text()
    sections = _section_rows(text)
    top_level = sorted(p.name for p in SCRIPTS_DIR.glob("*.py"))
    project_level = sorted(p.name for p in PROJECT_SCRIPTS_DIR.glob("*.py"))
    assert top_level and project_level, "expected scripts in scripts/ and _project/scripts/"
    missing = [name for name in top_level if not _covered(name, "### `scripts/`", sections)]
    missing += [name for name in project_level if not _covered(name, "### `_project/scripts/`", sections)]
    assert not missing, f"scripts missing from ledger: {missing}"


def test_ledger_covers_only_existing_files() -> None:
    text = _ledger_text()
    sections = _section_rows(text)
    orphaned = []
    for section, source_dir in SECTION_DIRS.items():
        if section in NON_FILE_SECTIONS:
            continue
        for row in sections.get(section, []):
            match = re.search(r"`([^`]+)`", row)
            if not match:
                continue
            entry = match.group(1)
            if "ledger-catch-all:" in entry:
                continue
            if "*" in entry:
                continue
            section_candidate = source_dir / entry
            if section_candidate.is_file():
                continue
            candidate = REPO_ROOT / entry
            if candidate.is_file():
                continue
            orphaned.append(f"{section} {entry}")
    assert not orphaned, f"ledger entries with no file on disk: {orphaned}"


def test_ledger_lists_every_pre_commit_hook() -> None:
    text = _ledger_text()
    hook_ids = re.findall(r"^\s*-\s*id:\s*(\S+)", HOOKS_FILE.read_text(encoding="utf-8"), re.M)
    assert hook_ids, "expected hooks in .pre-commit-config.yaml"
    missing = [hook for hook in dict.fromkeys(hook_ids) if hook not in text]
    assert not missing, f"pre-commit hooks missing from ledger: {missing}"


def test_ledger_records_keep_traps() -> None:
    text = _ledger_text()
    traps = [
        "detect_self_binding.py",
        "rp_scoped_check.py",
        "timing_audit.py",
        "timing_policy_check.py",
        "test_workflow_expression_contexts.py",
        "ruleset_drift_check.py",
        "validate_submission.py",
    ]
    missing = [name for name in traps if name not in text]
    assert not missing, f"KEEP traps missing from ledger: {missing}"
