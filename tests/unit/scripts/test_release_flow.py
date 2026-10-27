from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"
UV_LOCK_CHECKER = REPO_ROOT / "_project/scripts/check_uv_lock_revision.py"
SOUNDNESS_PATHS = REPO_ROOT / "_project/scripts/soundness_paths.py"
RELEASE_TREE_SKIP = "_project scripts are not in this checkout (release tree)"

spec = importlib.util.spec_from_file_location("release_flow", SCRIPTS / "release_flow.py")
release_flow = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = release_flow
spec.loader.exec_module(release_flow)

VERSION = "1.2.3"

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.3] - 2026-01-01

### Added

- A hand-curated entry that describes a user-visible change.

## [1.2.2] - 2025-12-01

### Added

- Earlier entry.
"""

LOCK = """version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "benchbox"
version = "{version}"
source = {{ editable = "." }}
"""


def write_tree(root: Path, version: str = VERSION) -> Path:

    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("update_version.py", "generate_changelog_entry.py", "release_flow.py", "changelog_fragments.py"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    (root / "benchbox" / "utils").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "benchbox"\nversion = "{version}"\nrequires-python = ">=3.11"\n\n'
        '[tool.ruff]\ntarget-version = "py311"\n',
        encoding="utf-8",
    )
    (root / "benchbox" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "README.md").write_text(f"# BenchBox\n\nCurrent release: v{version}.\n", encoding="utf-8")
    (root / "docs" / "README.md").write_text(f"Current release: `v{version}`. More text.\n", encoding="utf-8")
    (root / "benchbox" / "utils" / "VERSION_MANAGEMENT.md").write_text(
        f"Current release: `v{version}`.\n", encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (root / "uv.lock").write_text(LOCK.format(version=version), encoding="utf-8")
    return root


def ok_runner(argv, cwd):
    return 0, ""


def failing_runner(script_name: str):
    def runner(argv, cwd):
        if any(Path(part).name == script_name for part in argv):
            return 1, f"{script_name} failed"
        return 0, ""

    return runner


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = write_tree(tmp_path)
    git(root, "init", "-q")
    git(root, "add", "--", "uv.lock")
    git(root, "commit", "-q", "-m", "Initial lock revision")
    git(root, "tag", "v1.2.2")
    return root


def test_check_passes_when_every_element_is_present(tree: Path) -> None:
    assert release_flow.run_checks(tree, VERSION, ok_runner) == []


@pytest.mark.parametrize(
    ("relative_path", "needle", "replacement", "expected"),
    [
        ("pyproject.toml", f'version = "{VERSION}"', 'version = "1.2.4"', "pyproject.toml"),
        ("benchbox/__init__.py", VERSION, "1.2.4", "benchbox/__init__.py"),
        ("README.md", f"v{VERSION}", "v1.2.4", "README.md"),
        ("docs/README.md", f"v{VERSION}", "v1.2.4", "docs/README.md"),
        ("benchbox/utils/VERSION_MANAGEMENT.md", f"v{VERSION}", "v1.2.4", "VERSION_MANAGEMENT.md"),
        ("uv.lock", f'version = "{VERSION}"', 'version = "1.2.4"', "uv.lock"),
    ],
)
def test_check_fails_when_a_version_marker_is_stale(
    tree: Path, relative_path: str, needle: str, replacement: str, expected: str
) -> None:
    path = tree / relative_path
    path.write_text(path.read_text(encoding="utf-8").replace(needle, replacement, 1), encoding="utf-8")

    problems = release_flow.run_checks(tree, VERSION, ok_runner)

    assert any(expected in problem for problem in problems), problems


@pytest.mark.parametrize("relative_path", ["README.md", "uv.lock"])
def test_check_fails_when_a_version_marker_is_missing(tree: Path, relative_path: str) -> None:
    (tree / relative_path).write_text("nothing here\n", encoding="utf-8")

    problems = release_flow.run_checks(tree, VERSION, ok_runner)

    assert any(relative_path in problem for problem in problems), problems


def test_check_compares_lock_version_as_pep440(tmp_path: Path) -> None:
    root = write_tree(tmp_path, "1.3.0-rc.1")
    (root / "uv.lock").write_text(LOCK.format(version="1.3.0rc1"), encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG.replace("1.2.3", "1.3.0-rc.1", 1), encoding="utf-8")

    assert release_flow.check_version_markers(root, "1.3.0-rc.1") == []


def test_check_fails_when_the_changelog_entry_is_missing(tree: Path) -> None:
    (tree / "CHANGELOG.md").write_text(CHANGELOG.replace("## [1.2.3]", "## [1.2.0]"), encoding="utf-8")

    problems = release_flow.run_checks(tree, VERSION, ok_runner)

    assert any("no '## [1.2.3]" in problem for problem in problems), problems


def test_check_fails_when_the_changelog_entry_is_an_uncurated_draft(tree: Path) -> None:
    draft = CHANGELOG.replace(
        "- A hand-curated entry that describes a user-visible change.",
        "- add a thing (#123)",
    )
    (tree / "CHANGELOG.md").write_text(draft, encoding="utf-8")

    problems = release_flow.run_checks(tree, VERSION, ok_runner)

    assert any("verbatim commit subjects" in problem for problem in problems), problems


@pytest.mark.parametrize("invalid_date", ["TBD", "2026-02-30", "2026-1-1"])
def test_check_rejects_an_invalid_changelog_date(tree: Path, invalid_date: str) -> None:
    (tree / "CHANGELOG.md").write_text(CHANGELOG.replace("2026-01-01", invalid_date), encoding="utf-8")
    assert any("valid YYYY-MM-DD" in problem for problem in release_flow.check_changelog(tree, VERSION))


@pytest.mark.skipif(not UV_LOCK_CHECKER.exists(), reason=RELEASE_TREE_SKIP)
def test_check_rejects_a_committed_lock_revision_downgrade(tree: Path) -> None:
    scripts = tree / "_project" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(UV_LOCK_CHECKER, scripts)
    (tree / "uv.lock").write_text(LOCK.format(version=VERSION).replace("revision = 3", "revision = 2"))
    git(tree, "add", "--", "uv.lock")
    git(tree, "commit", "-q", "-m", "Downgrade lock revision")
    git(tree, "tag", "v1.2.3")
    problems = release_flow.run_checks(tree, VERSION)
    assert any("DOWNGRADE" in problem for problem in problems), problems


def test_run_checks_can_skip_the_network_dependent_freshness_check(tree: Path) -> None:
    seen: list[str] = []

    def runner(argv, cwd):
        seen.append(" ".join(str(a) for a in argv))
        return 0, ""

    release_flow.run_checks(tree, VERSION, runner, skip_upstream_freshness=True)
    assert not any("check_sqlglot_upstream_freshness" in call for call in seen)
    assert not any("check_sqlglot_advisories" in call for call in seen)
    release_flow.run_checks(tree, VERSION, runner)
    assert any("check_sqlglot_upstream_freshness" in call for call in seen)
    assert any("check_sqlglot_advisories" in call for call in seen)


def test_check_pins_an_explicit_lock_baseline(tree: Path) -> None:
    expected = subprocess.check_output(["git", "rev-parse", "refs/tags/v1.2.2"], cwd=tree, text=True).strip()
    seen = []

    def runner(argv, cwd):
        seen.append(list(argv))
        return 0, ""

    assert release_flow.check_uv_lock_revision(tree, runner, "refs/tags/v1.2.2") == []
    assert seen[0][-2:] == ["--baseline-ref", expected]
    assert release_flow.check_uv_lock_revision(tree, runner, "refs/tags/missing")
    assert len(seen) == 1


@pytest.mark.skipif(not UV_LOCK_CHECKER.exists(), reason=RELEASE_TREE_SKIP)
def test_lock_baseline_cannot_be_replaced_to_hide_a_committed_downgrade(tree: Path) -> None:
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tree, text=True).strip()
    scripts = tree / "_project/scripts"
    scripts.mkdir(parents=True)
    shutil.copy(UV_LOCK_CHECKER, scripts)
    (tree / "uv.lock").write_text(LOCK.format(version=VERSION).replace("revision = 3", "revision = 2"))
    git(tree, "add", "--", "uv.lock")
    git(tree, "commit", "-q", "-m", "Downgrade lock revision")
    git(tree, "replace", base, "HEAD")
    problems = release_flow.check_uv_lock_revision(tree, release_flow.run_command, base)
    assert any("DOWNGRADE" in problem for problem in problems), problems


@pytest.mark.parametrize(
    "path",
    [
        "scripts/release_flow.py",
        "scripts/update_version.py",
        "scripts/generate_changelog_entry.py",
        "scripts/check_release_curation.py",
        "scripts/check_dependency_bounds.py",
        "scripts/check_sqlglot_upstream_freshness.py",
        "scripts/check_sqlglot_advisories.py",
        "scripts/sqlglot_refresh_waiver.json",
        "_project/scripts/check_uv_lock_revision.py",
        "_project/decisions/single-repo-migration.md",
    ],
)
@pytest.mark.skipif(not SOUNDNESS_PATHS.exists(), reason=RELEASE_TREE_SKIP)
def test_release_enforcement_requires_external_review_for_each_dependency(path: str) -> None:
    spec = importlib.util.spec_from_file_location("release_soundness_paths", SOUNDNESS_PATHS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.is_soundness_path(path)


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_make_release_check_refuses_stale_lock_without_modifying_it(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    subprocess.run(["uv", "lock", "--offline"], cwd=tree, check=True, capture_output=True)
    original = (tree / "uv.lock").read_bytes()
    project = tree / "pyproject.toml"
    project.write_text(project.read_text(encoding="utf-8").replace('version = "1.2.3"', 'version = "1.2.4"'))
    result = subprocess.run(
        ["make", "-f", str(REPO_ROOT / "Makefile"), "release-check", "VERSION=1.2.4"],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "lockfile" in result.stderr and "--locked" in result.stderr, result.stdout + result.stderr
    assert (tree / "uv.lock").read_bytes() == original


@pytest.mark.parametrize(
    "version,release_date",
    [
        ("bad-version", None),
        (VERSION, "2026-02-30"),
    ],
)
def test_invalid_preparation_inputs_leave_files_unchanged(tree: Path, version: str, release_date: str | None) -> None:
    write_fragment(tree)
    before = {path: path.read_bytes() for path in tree.rglob("*") if path.is_file() and ".git" not in path.parts}
    assert release_flow.prep(tree, version, release_date, release_flow.run_command) != 0
    assert {path: path.read_bytes() for path in before} == before


def test_missing_preparation_marker_fails_before_any_write(tree: Path) -> None:
    write_fragment(tree)
    (tree / "docs/README.md").unlink()
    before = {path: path.read_bytes() for path in tree.rglob("*") if path.is_file() and ".git" not in path.parts}
    assert release_flow.prep(tree, "1.2.4", None, release_flow.run_command) != 0
    assert {path: path.read_bytes() for path in before} == before


def test_invalid_fragment_aborts_preparation_before_any_marker_or_changelog_write(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_fragment(tree, "feat-good.json")
    bad = write_fragment(tree, "feat-bad.json", {"schema": 1, "entries": [{"category": "added", "text": "short"}]})
    before = {path: path.read_bytes() for path in tree.rglob("*") if path.is_file() and ".git" not in path.parts}

    assert release_flow.prep(tree, "1.2.4", "2026-02-03", release_flow.run_command) != 0

    assert {path: path.read_bytes() for path in before} == before
    captured = capsys.readouterr()
    assert bad.as_posix() in captured.err + captured.out
    assert "release-prep failed at: changelog fragment validation" in captured.err


@pytest.mark.parametrize(
    ("script_name", "label"),
    [
        ("check_uv_lock_revision.py", "uv.lock revision"),
        ("check_release_curation.py", "release curation"),
        ("check_dependency_bounds.py", "dependency bounds"),
        ("check_sqlglot_upstream_freshness.py", "sqlglot upstream freshness"),
        ("check_sqlglot_advisories.py", "sqlglot advisories"),
    ],
)
def test_check_fails_when_a_delegated_check_fails(tree: Path, script_name: str, label: str) -> None:
    problems = release_flow.run_checks(tree, VERSION, failing_runner(script_name))

    assert len(problems) == 1
    assert problems[0].startswith(label)


def test_dependency_bounds_check_blocks_on_cap_reached(tree: Path) -> None:
    seen: list[list[str]] = []

    def runner(argv, cwd):
        seen.append(list(argv))
        return 0, ""

    release_flow.run_checks(tree, VERSION, runner)

    bounds = next(argv for argv in seen if Path(argv[1]).name == "check_dependency_bounds.py")
    assert "--fail-on=cap-reached" in bounds


def test_check_rejects_an_invalid_version(tree: Path) -> None:
    assert release_flow.run_checks(tree, "not-a-version", ok_runner)


def test_cli_check_reports_all_problems_and_exits_nonzero(tree: Path) -> None:
    (tree / "README.md").write_text("no marker\n", encoding="utf-8")
    (tree / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "release_flow.py"), "check", "--version", VERSION, "--root", str(tree)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "README.md" in result.stderr
    assert "CHANGELOG.md" in result.stderr


def write_fragment(root: Path, name: str = "feat-fixture.json", data: dict | None = None) -> Path:
    path = root / "changelog.d" / name
    path.parent.mkdir(exist_ok=True)
    payload = (
        data
        if data is not None
        else {"schema": 1, "entries": [{"category": "added", "breaking": False, "text": FRAGMENT_TEXT}]}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("uv") is None or shutil.which("git") is None, reason="needs uv and git")
@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
def test_prep_updates_every_marker_and_compiles_the_fragments(tmp_path: Path, version: str) -> None:
    root = write_tree(tmp_path, "1.2.2")
    (root / "CHANGELOG.md").write_text(CHANGELOG.split("## [1.2.3]")[0] + "## [1.2.2] - 2025-12-01\n", encoding="utf-8")
    write_fragment(root)
    write_fragment(root, "chore-fixture.json", {"schema": 1, "internal": "Test scaffolding only."})

    subprocess.run(["uv", "lock", "--offline"], cwd=root, check=True, capture_output=True)
    git(root, "init", "-q")
    git(root, "add", "--all")
    git(root, "commit", "-q", "-m", "feat: add a fixture feature (#7)")
    git(root, "tag", "v1.2.2")
    before = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    assert before == ""

    code = release_flow.prep(root, version, "2026-02-03", release_flow.run_command)

    assert code == 0
    changed = set(
        subprocess.run(
            ["git", "diff", "--name-only"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.split()
    )
    assert changed == {
        "pyproject.toml",
        "uv.lock",
        "CHANGELOG.md",
        "benchbox/__init__.py",
        "README.md",
        "docs/README.md",
        "benchbox/utils/VERSION_MANAGEMENT.md",
        "changelog.d/feat-fixture.json",
        "changelog.d/chore-fixture.json",
    }
    assert not list((root / "changelog.d").glob("*.json"))
    assert release_flow.check_version_markers(root, version) == []
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}] - 2026-02-03" in changelog
    assert f"- {FRAGMENT_TEXT}" in changelog
    assert "Test scaffolding only" not in changelog
    assert release_flow.check_changelog(root, version) == []


def test_prep_stops_at_the_first_failing_step(tree: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv, cwd):
        calls.append(list(argv))
        if Path(argv[1]).name == "generate_changelog_entry.py":
            return 0, ""
        return 3, "boom"

    assert release_flow.prep(tree, VERSION, None, runner) == 3
    assert len(calls) == 2
    assert Path(calls[0][1]).name == "generate_changelog_entry.py"
    assert "--dry-run" in calls[0]
    assert Path(calls[1][1]).name == "update_version.py"


def test_prep_validates_fragments_first_and_compiles_them_last(tree: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv, cwd):
        calls.append(list(argv))
        return 0, ""

    assert release_flow.prep(tree, VERSION, "2026-02-03", runner) == 0

    scripts = [Path(argv[1]).name if argv[0] != "uv" else "uv" for argv in calls]
    assert scripts == [
        "generate_changelog_entry.py",
        "update_version.py",
        "update_version.py",
        "uv",
        "generate_changelog_entry.py",
    ]
    validate, compile_ = calls[0], calls[-1]
    assert "--dry-run" in validate
    assert "--dry-run" not in compile_
    for argv in (validate, compile_):
        assert "--compile-fragments" in argv
        assert argv[argv.index("--version") + 1] == VERSION
        assert argv[argv.index("--release-date") + 1] == "2026-02-03"
        assert "--since-ref" not in argv


def test_pre_tag_check_fails_while_any_fragment_is_pending(tree: Path) -> None:
    assert release_flow.run_checks(tree, VERSION, ok_runner, pre_tag=True) == []
    write_fragment(tree, "feat-pending.json")
    write_fragment(tree, "chore-pending.json", {"schema": 1, "internal": "Test scaffolding only."})

    assert release_flow.run_checks(tree, VERSION, ok_runner) == []
    problems = release_flow.run_checks(tree, VERSION, ok_runner, pre_tag=True)
    assert len(problems) == 1
    assert "changelog.d" in problems[0]
    assert "chore-pending.json" in problems[0]
    assert "feat-pending.json" in problems[0]


def test_cli_pre_tag_flag_reaches_the_pending_fragment_check(tree: Path) -> None:
    write_fragment(tree)

    def check(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "release_flow.py"),
                "check",
                "--version",
                VERSION,
                "--root",
                str(tree),
                "--skip-upstream-freshness",
                *extra,
            ],
            capture_output=True,
            text=True,
        )

    assert "changelog.d:" not in check().stderr
    pre_tag = check("--pre-tag")
    assert pre_tag.returncode == 1
    assert "changelog.d: 1 fragment(s) still pending" in pre_tag.stderr


@pytest.fixture
def candidate_tree(tmp_path: Path) -> tuple[Path, str]:
    root = write_tree(tmp_path)
    git(root, "init", "-q")
    paths = [str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]
    git(root, "add", "--", *paths)
    git(root, "commit", "-q", "-m", "Initial release contents")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return root, base


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "benchbox/__init__.py",
        "uv.lock",
        "README.md",
        "docs/README.md",
        "benchbox/utils/VERSION_MANAGEMENT.md",
    ],
)
def test_candidate_selection_catches_each_split_version_bump(candidate_tree: tuple[Path, str], path: str) -> None:
    root, base = candidate_tree
    file = root / path
    file.write_text(file.read_text(encoding="utf-8").replace(VERSION, "1.2.4"))
    selected, _version, freshness_required, identity_changed = release_flow.select_candidate(root, base)
    assert selected
    assert freshness_required
    assert identity_changed
    assert release_flow.run_checks(root, _version, ok_runner, baseline_ref=base)


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        ("CHANGELOG.md", "A hand-curated entry", "Updated release note"),
        ("uv.lock", "revision = 3", "revision = 2"),
        ("uv.lock", "version = 1\n", "version = 2\n"),
    ],
)
def test_candidate_selection_catches_release_section_and_lock_schema(
    candidate_tree: tuple[Path, str],
    path: str,
    old: str,
    new: str,
) -> None:
    root, base = candidate_tree
    file = root / path
    file.write_text(file.read_text(encoding="utf-8").replace(old, new))
    assert release_flow.select_candidate(root, base)[0]


def test_candidate_selection_preserves_ordinary_prose_dependency_and_source_edits(
    candidate_tree: tuple[Path, str],
) -> None:
    root, base = candidate_tree
    readme = root / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nInstallation instructions improved.\n")
    project = root / "pyproject.toml"
    project.write_text(
        project.read_text(encoding="utf-8").replace("[tool.ruff]", 'dependencies = ["packaging>=24"]\n\n[tool.ruff]')
    )
    (root / "benchbox/new_module.py").write_text('"""Ordinary module."""\n')
    changelog = root / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace("Earlier entry.", "Clarified older entry."))
    assert release_flow.select_candidate(root, base) == (False, VERSION, False, False)


@pytest.mark.parametrize(
    "path",
    [
        "scripts/release_flow.py",
        "scripts/changelog_fragments.py",
        "scripts/check_dependency_bounds.py",
        "_project/decisions/single-repo-migration.md",
        ".github/workflows/ci.yml",
        "Makefile",
    ],
)
def test_candidate_selection_includes_committed_enforcement_changes(
    candidate_tree: tuple[Path, str], path: str
) -> None:
    root, base = candidate_tree
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        file.read_text(encoding="utf-8") + "\n# Updated enforcement.\n" if file.exists() else "# Updated enforcement.\n"
    )
    git(root, "add", "--", path)
    git(root, "commit", "-q", "-m", "Update release enforcement")
    assert release_flow.select_candidate(root, base) == (True, VERSION, False, False)


@pytest.mark.parametrize(
    "path",
    [
        "scripts/check_sqlglot_upstream_freshness.py",
        "scripts/check_sqlglot_advisories.py",
        "scripts/sqlglot_refresh_waiver.json",
        "benchbox/_vendor/sqlglot.json",
    ],
)
def test_candidate_selection_requires_freshness_when_the_gate_itself_changes(
    candidate_tree: tuple[Path, str], path: str
) -> None:
    root, base = candidate_tree
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(file.read_text(encoding="utf-8") + "\n" if file.exists() else "\n")
    git(root, "add", "--", path)
    git(root, "commit", "-q", "-m", "Update freshness gate")
    assert release_flow.select_candidate(root, base) == (True, VERSION, True, False)


@pytest.mark.parametrize(
    ("required", "identity", "expected_skip", "expected_pre_tag"),
    [
        ("true", "true", "", "1"),
        ("false", "false", "SKIP_UPSTREAM_FRESHNESS=1", ""),
        ("true", "false", "", ""),
        ("", "", "SKIP_UPSTREAM_FRESHNESS=1", ""),
    ],
)
def test_ci_release_check_step_passes_skip_and_pre_tag_from_the_selection_outputs(
    tmp_path: Path, required: str, identity: str, expected_skip: str, expected_pre_tag: str
) -> None:
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    check = next(step for step in workflow["jobs"]["ci-paths"]["steps"] if step.get("name") == "Check release content")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "make"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CALLS"\n')
    fake.chmod(0o755)
    calls = tmp_path / "calls.txt"
    env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CALLS": str(calls),
        "RELEASE_VERSION": VERSION,
        "EVENT_BASE_SHA": "a" * 40,
        "UPSTREAM_FRESHNESS_REQUIRED": required,
        "RELEASE_IDENTITY_CHANGED": identity,
    }
    step_script = tmp_path / "step.sh"
    step_script.write_text(check["run"])
    subprocess.run(["sh", "-c", '. "$1"', "sh", str(step_script)], env=env, check=True)
    args = calls.read_text(encoding="utf-8").split()
    assert args[0] == "release-check"
    assert [a for a in args if a.startswith("SKIP_UPSTREAM_FRESHNESS=")] == [
        expected_skip or "SKIP_UPSTREAM_FRESHNESS="
    ]
    assert [a for a in args if a.startswith("RELEASE_PRE_TAG=")] == [f"RELEASE_PRE_TAG={expected_pre_tag}"]


@pytest.mark.parametrize("base", ["HEAD", "", "0" * 40])
def test_candidate_selection_rejects_missing_or_moving_baseline(candidate_tree: tuple[Path, str], base: str) -> None:
    with pytest.raises(ValueError, match="event base"):
        release_flow.select_candidate(candidate_tree[0], base)


def test_candidate_selection_rejects_missing_baseline_contents(candidate_tree: tuple[Path, str]) -> None:
    root, _base = candidate_tree
    git(root, "rm", "--", "README.md")
    git(root, "commit", "-q", "-m", "Remove baseline marker")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    (root / "README.md").write_text(f"Current release: v{VERSION}.\n")
    with pytest.raises(ValueError, match="baseline README"):
        release_flow.select_candidate(root, base)


def test_candidate_selection_cli_is_stdlib_only_and_preserves_lock(
    candidate_tree: tuple[Path, str], tmp_path: Path
) -> None:
    root, base = candidate_tree
    original = (root / "uv.lock").read_bytes()
    output = tmp_path / "output.txt"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(Path(root).resolve() / "scripts/release_flow.py"),
            "select",
            "--base-sha",
            base,
            "--github-output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        output.read_text(encoding="utf-8")
        == f"release-check-needed=false\nrelease-version={VERSION}\nupstream-freshness-required=false\nrelease-identity-changed=false\n"
    )
    assert (root / "uv.lock").read_bytes() == original


def test_candidate_check_is_required_on_pull_requests_without_changing_legacy_publisher() -> None:
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    classifier = workflow["jobs"]["ci-paths"]
    assert classifier.get("if") == "${{ !(github.event.action == 'edited' && !github.event.changes.base) }}"
    steps = classifier["steps"]
    selection = next(step for step in steps if step.get("id") == "release")
    assert selection["env"]["EVENT_BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert 'git fetch --no-tags origin "$EVENT_BASE_SHA"' in selection["run"]
    check = next(step for step in steps if step.get("name") == "Check release content")
    assert check["if"] == "steps.release.outputs.release-check-needed == 'true'"
    assert check["env"]["UPSTREAM_FRESHNESS_REQUIRED"] == "${{ steps.release.outputs.upstream-freshness-required }}"
    assert 'SKIP_UPSTREAM_FRESHNESS="$skip"' in check["run"]
    assert check["env"]["RELEASE_IDENTITY_CHANGED"] == "${{ steps.release.outputs.release-identity-changed }}"
    assert 'RELEASE_PRE_TAG="$pre_tag"' in check["run"]
    assert 'if [ "$RELEASE_IDENTITY_CHANGED" = "true" ]; then pre_tag=1; fi' in check["run"]
    assert 'if [ "$UPSTREAM_FRESHNESS_REQUIRED" != "true" ]; then skip=1; fi' in check["run"]
    core = workflow["jobs"]["core"]
    assert "ci-paths" in core["needs"]
    aggregate = next(step for step in core["steps"] if step.get("id") == "aggregate")
    assert "--always ci-paths" in aggregate["run"]
    assert "origin/release" in (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")


def test_publish_and_release_branch_workflows_run_the_advisory_check() -> None:
    import yaml

    release = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    job = release["jobs"]["sqlglot-upstream-freshness"]
    assert any("scripts/check_sqlglot_advisories.py" in step.get("run", "") for step in job["steps"])
    assert "sqlglot-upstream-freshness" in release["jobs"]["build"]["needs"]

    test = yaml.safe_load((REPO_ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8"))
    runs = [step.get("run", "") for step in test["jobs"]["release-readiness"]["steps"]]
    assert any("scripts/check_sqlglot_advisories.py" in run for run in runs)
    assert any("scripts/check_sqlglot_upstream_freshness.py" in run for run in runs)


def test_make_targets_exist_and_legacy_release_targets_are_kept() -> None:
    def dry_run(target: str, version: str = VERSION) -> str:
        return subprocess.run(
            ["make", "-n", target, f"VERSION={version}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    assert "release_flow.py check --version" in dry_run("release-check")
    assert "--skip-upstream-freshness" not in dry_run("release-check")
    skipped = subprocess.run(
        ["make", "-n", "release-check", f"VERSION={VERSION}", "SKIP_UPSTREAM_FRESHNESS=1"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "--skip-upstream-freshness" in skipped
    assert "release_flow.py prep --version" in dry_run("release-prep")
    assert "release_finalize.py" in dry_run("release-finalize")
    assert "release_cut_start.sh" in dry_run("release-cut")
    assert "--pre-tag" not in dry_run("release-check")
    assert (
        "--pre-tag"
        in subprocess.run(
            ["make", "-n", "release-check", f"VERSION={VERSION}", "RELEASE_PRE_TAG=1"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert "--since-ref" not in dry_run("release-prep")


def test_release_v2_tag_time_release_check_confirms_the_tagged_tree_has_no_fragments() -> None:
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/release-v2.yml").read_text(encoding="utf-8"))
    checks = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "make release-check" in step.get("run", "")
    ]
    assert len(checks) == 1
    assert 'make release-check VERSION="$version" RELEASE_PRE_TAG=1' in checks[0]["run"]


def test_selection_reports_release_identity_separately_from_enforcement_changes(
    candidate_tree: tuple[Path, str],
) -> None:
    root, base = candidate_tree
    changelog = root / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace("A hand-curated entry", "A changed entry"))
    assert release_flow.select_candidate(root, base) == (True, VERSION, True, False)
    git(root, "checkout", "-q", "--", "CHANGELOG.md")
    lock = root / "uv.lock"
    lock.write_text(lock.read_text(encoding="utf-8").replace("revision = 3", "revision = 2"))
    assert release_flow.select_candidate(root, base) == (True, VERSION, True, False)
    git(root, "checkout", "-q", "--", "uv.lock")

    enforcement = root / "scripts" / "changelog_fragments.py"
    enforcement.write_text("# Updated enforcement.\n")
    git(root, "add", "--", "scripts/changelog_fragments.py")
    git(root, "commit", "-q", "-m", "Update fragment enforcement")
    assert release_flow.select_candidate(root, base) == (True, VERSION, False, False)


FRAGMENT_TEXT = "Prep compiles fragment entries into the dated section of the changelog."
