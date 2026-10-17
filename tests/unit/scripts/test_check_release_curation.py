from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_release_curation.py"

spec = importlib.util.spec_from_file_location("check_release_curation", SCRIPT_PATH)
check_release_curation = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(check_release_curation)

CURATED_PROJECT_DEPENDENT_TESTS = {
    "tests/unit/scripts/test_check_complexity.py",
    "tests/unit/scripts/test_fast_lane_ceiling_check.py",
    "tests/unit/scripts/test_timing_policy_check.py",
}
RELEASE_SAFE_PROJECT_TESTS = {
    "tests/unit/scripts/test_check_makefile_inventory.py",
    "tests/unit/core/test_platform_registry_behavioral.py",
}


def _write_makefile(tmp_path: Path, recipe_lines: list[str]) -> Path:
    makefile = tmp_path / "Makefile"
    body = "\n".join(f"\t{line}" for line in recipe_lines)
    makefile.write_text(f"release-cut:\n{body}\n\nother-target:\n\techo hi\n", encoding="utf-8")
    return makefile


def test_parse_curation_list_handles_ignore_unmatch(tmp_path):
    makefile = _write_makefile(
        tmp_path,
        [
            "git rm -rf --ignore-unmatch _project _blog",
            "git rm -f --ignore-unmatch .mcp.json todo.config.yaml",
            "git add pyproject.toml",
        ],
    )
    paths = check_release_curation.parse_curation_list(makefile)
    assert paths == {"_project", "_blog", ".mcp.json", "todo.config.yaml"}


def test_parse_curation_list_accepts_target_prerequisites(tmp_path: Path) -> None:
    makefile = _write_makefile(tmp_path, ["git rm -rf --ignore-unmatch _project"])
    makefile.write_text(makefile.read_text().replace("release-cut:", "release-cut: guard"), encoding="utf-8")

    assert check_release_curation.parse_curation_list(makefile) == {"_project"}


def test_parse_curation_list_handles_legacy_prefixed_lines(tmp_path):
    makefile = _write_makefile(
        tmp_path,
        [
            "-git rm -rf _project",
            "-git rm -f .mcp.json",
        ],
    )
    paths = check_release_curation.parse_curation_list(makefile)
    assert paths == {"_project", ".mcp.json"}


def test_parse_curation_list_ignores_non_rm_lines(tmp_path):
    makefile = _write_makefile(
        tmp_path,
        [
            "git rm -rf --ignore-unmatch _project",
            "@LEFTOVER=$$(git ls-files _project _blog); \\",
            'if [ -n "$$LEFTOVER" ]; then exit 1; fi',
        ],
    )
    paths = check_release_curation.parse_curation_list(makefile)
    assert paths == {"_project"}


def test_real_makefile_curation_list_parses_to_paths_only():
    paths = check_release_curation.parse_curation_list(REPO_ROOT / "Makefile")
    assert paths, "expected a non-empty curation list from the repo Makefile"
    flags = {p for p in paths if p.startswith("-")}
    assert not flags, f"parser leaked flags into the curation list: {sorted(flags)}"


def _write_decision_doc(tmp_path: Path, ending: str) -> Path:
    doc = tmp_path / "single-repo-migration.md"
    doc.write_text(
        "- **`main` only**: `benchbox/`, `Makefile`.\n"
        "\n"
        "## Amendment — trailing entry\n"
        "\n"
        "- **`main` only** (extension to A3): `publication/`." + ending,
        encoding="utf-8",
    )
    return doc


def test_parse_main_only_allowlist_accepts_trailing_bullet_at_eof(tmp_path: Path) -> None:
    paths = check_release_curation.parse_main_only_allowlist(_write_decision_doc(tmp_path, "\n"))
    assert {"benchbox", "Makefile", "publication"} <= paths


def test_parse_main_only_allowlist_accepts_trailing_bullet_with_blank_line(tmp_path: Path) -> None:
    paths = check_release_curation.parse_main_only_allowlist(_write_decision_doc(tmp_path, "\n\n"))
    assert {"benchbox", "Makefile", "publication"} <= paths


def test_release_make_runtime_is_main_only_not_curated() -> None:
    main_only = check_release_curation.parse_main_only_allowlist(
        REPO_ROOT / "_project" / "decisions" / "single-repo-migration.md"
    )
    curated = check_release_curation.parse_curation_list(REPO_ROOT / "Makefile")

    assert "make" in main_only
    assert "make" not in curated


def test_curated_preview_release_paths_are_shipped_and_deferred_workflows_are_curated() -> None:
    main_only = check_release_curation.parse_main_only_allowlist(
        REPO_ROOT / "_project" / "decisions" / "single-repo-migration.md"
    )
    curated = check_release_curation.parse_curation_list(REPO_ROOT / "Makefile")

    assert main_only >= check_release_curation.REQUIRED_RELEASE_PATHS
    assert check_release_curation.REQUIRED_RELEASE_PATHS.isdisjoint(curated)
    assert curated >= check_release_curation.REQUIRED_CURATED_PATHS


def _copy_curated_make_runtime(destination: Path) -> None:
    shutil.copy2(REPO_ROOT / "Makefile", destination / "Makefile")
    shutil.copytree(REPO_ROOT / "make", destination / "make")


def test_curated_release_make_runtime_executes_help_and_inventory(tmp_path: Path) -> None:
    _copy_curated_make_runtime(tmp_path)

    help_result = subprocess.run(
        ["make", "--no-print-directory", "help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    inventory_result = subprocess.run(
        ["make", "--no-print-directory", "makefile-inventory-check"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "makefile-inventory-check" in help_result.stdout
    assert inventory_result.returncode == 0, inventory_result.stderr
    assert "Makefile inventory OK:" in inventory_result.stdout
    assert "public, default=test" in inventory_result.stdout


@pytest.mark.parametrize(
    "target",
    ["complexity-check", "platform-manifest-check", "blind-spots-list"],
)
def test_curated_release_development_targets_fail_with_explicit_policy(tmp_path: Path, target: str) -> None:
    _copy_curated_make_runtime(tmp_path)

    result = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "requires the BenchBox development tree" in result.stderr
    assert "No such file or directory" not in result.stderr


def test_curated_release_cut_guard_allows_only_the_matching_develop_descendant(tmp_path: Path) -> None:
    _copy_curated_make_runtime(tmp_path)
    subprocess.run(["git", "init", "-b", "develop"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "release-test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "Makefile", "make"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "v9.9.9"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "develop"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "README.md").write_text("develop advanced\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "advance develop"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "v9.9.9"], cwd=tmp_path, check=True, capture_output=True)

    allowed = subprocess.run(
        ["make", "--no-print-directory", ".release-cut-tree-required", "VERSION=9.9.9"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    rejected = subprocess.run(
        ["make", "--no-print-directory", ".release-cut-tree-required", "VERSION=9.9.8"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert allowed.returncode == 0, allowed.stderr
    assert rejected.returncode == 2
    assert "unless resuming v9.9.8 after curation" in rejected.stderr


def test_every_project_dependent_make_recipe_is_declared_development_only(tmp_path: Path) -> None:
    assert check_release_curation.development_tree_target_findings(REPO_ROOT) == []

    _copy_curated_make_runtime(tmp_path)
    with (tmp_path / "make" / "documentation.mk").open("a", encoding="utf-8") as stream:
        stream.write("\nforgotten-project-target:\n\tpython _project/scripts/forgotten.py\n")

    assert check_release_curation.development_tree_target_findings(tmp_path) == [
        "Make target 'forgotten-project-target' references _project/ but is not declared development-only"
    ]


def test_curated_release_make_runtime_fails_closed_when_module_is_omitted(tmp_path: Path) -> None:
    _copy_curated_make_runtime(tmp_path)
    (tmp_path / "make" / "help.mk").unlink()

    result = subprocess.run(
        ["make", "--no-print-directory", "help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "make/help.mk" in result.stderr
    assert "No such file or directory" in result.stderr


def _release_curation_recipes() -> tuple[str, str]:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    target = re.search(r"^release-cut:[^\n]*\n((?:[ \t].*\n|\n)+)", text, re.MULTILINE)
    assert target is not None
    body = target.group(1)
    removals = "\n".join(line for line in body.splitlines() if line.startswith("\tgit rm "))
    guard = re.search(r"\t@LEFTOVER=.*?\n\tfi", body, re.DOTALL)
    assert removals and guard is not None
    return removals, guard.group(0)


@pytest.mark.parametrize("relative", sorted(CURATED_PROJECT_DEPENDENT_TESTS))
def test_release_curation_guard_rejects_retained_project_test(tmp_path: Path, relative: str) -> None:
    retained = tmp_path / relative
    retained.parent.mkdir(parents=True)
    retained.write_text("# accidentally retained development test\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", relative], cwd=tmp_path, check=True, capture_output=True)
    _, guard = _release_curation_recipes()
    (tmp_path / "guard.mk").write_text("guard:\n" + guard + "\n", encoding="utf-8")

    result = subprocess.run(
        ["make", "--no-print-directory", "-f", "guard.mk", "guard"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "release curation incomplete" in result.stderr
    assert relative in result.stderr


def test_curated_release_removes_dangling_project_test_and_runs_retained_tests(
    tmp_path: Path,
) -> None:
    curated = check_release_curation.parse_curation_list(REPO_ROOT / "Makefile")
    assert curated.isdisjoint(RELEASE_SAFE_PROJECT_TESTS)

    _copy_curated_make_runtime(tmp_path)
    shutil.copytree(REPO_ROOT / "benchbox", tmp_path / "benchbox")
    for relative in sorted(CURATED_PROJECT_DEPENDENT_TESTS):
        project_test = tmp_path / relative
        project_test.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, project_test)
    project_scripts = tmp_path / "_project" / "scripts"
    project_scripts.mkdir(parents=True)
    for name in ["fast_lane_ceiling_check.py", "timing_policy_check.py", "timing_audit.py"]:
        shutil.copy2(REPO_ROOT / "_project" / "scripts" / name, project_scripts / name)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "tests", "_project"], cwd=tmp_path, check=True, capture_output=True)
    removals, guard = _release_curation_recipes()
    (tmp_path / "curate.mk").write_text("curate:\n" + removals + "\n" + guard + "\n", encoding="utf-8")
    curation = subprocess.run(
        ["make", "--no-print-directory", "-f", "curate.mk", "curate"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert curation.returncode == 0, curation.stdout + curation.stderr
    assert not project_scripts.exists()

    for relative in sorted(RELEASE_SAFE_PROJECT_TESTS):
        retained_test = tmp_path / relative
        retained_test.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, retained_test)

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_XDIST", "COV_CORE")) and key != "PYTEST_CURRENT_TEST"
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", "0", "-p", "no:cacheprovider", "-m", "fast", "tests"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout


def test_release_cut_stages_only_tracked_version_files() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nrelease-cut:", 1)[1].split("\n\n", 1)[0]
    staged = [line.split()[2:] for line in recipe.splitlines() if line.strip().startswith("git add ")]
    assert staged, "release-cut must stage the version files before its release commit"
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=REPO_ROOT, text=True).splitlines())
    missing = [path for paths in staged for path in paths if path not in tracked]
    assert not missing, f"release-cut stages untracked paths: {missing}"
