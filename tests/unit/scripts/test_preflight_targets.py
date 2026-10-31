from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SCRIPT = Path(__file__).resolve().parents[3] / "_project" / "scripts" / "preflight_targets.py"
spec = importlib.util.spec_from_file_location("preflight_targets", SCRIPT)
assert spec is not None and spec.loader is not None
pt = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pt
spec.loader.exec_module(pt)


def _git(repo: Path, *args: str) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, rel: str, text: str = "x = 1\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(root, *args)
    _write(root, "benchbox/core/engine.py")
    _write(root, "benchbox/core/__init__.py", "")
    _write(root, "benchbox/platforms/other.py")
    _write(root, "scripts/tool.py")
    _write(root, "tests/unit/core/test_engine.py")
    _write(root, "tests/integration/test_engine.py")
    _write(root, "tests/unit/test_tool.py")
    _write(root, "tests/unit/test_unrelated.py")
    _write(root, "docs/guide.md", "# guide\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    _git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(root, "checkout", "-b", "feature")
    return root


def _commit_all(repo: Path, message: str = "change") -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def test_source_module_maps_to_every_matching_test_file(repo: Path) -> None:
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert pt.map_tests(repo, paths) == ["tests/integration/test_engine.py", "tests/unit/core/test_engine.py"]


def test_changed_test_file_maps_to_itself(repo: Path) -> None:
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _commit_all(repo)

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/test_unrelated.py"]


def test_scripts_module_maps_to_its_test(repo: Path) -> None:
    _write(repo, "scripts/tool.py", "x = 2\n")
    _commit_all(repo)

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/test_tool.py"]


def test_unmapped_changes_select_no_tests(repo: Path) -> None:
    _write(repo, "benchbox/platforms/other.py", "x = 2\n")
    _write(repo, "benchbox/core/__init__.py", "x = 2\n")
    _write(repo, "docs/guide.md", "# changed\n")
    _commit_all(repo)

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == []


def test_uncommitted_and_untracked_changes_are_included(repo: Path) -> None:
    _write(repo, "benchbox/core/engine.py", "x = 3\n")
    _write(repo, "tests/unit/test_new.py")
    _git(repo, "add", "tests/unit/test_new.py")
    _write(repo, "scripts/untracked.py")

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["benchbox/core/engine.py", "scripts/untracked.py", "tests/unit/test_new.py"]
    assert pt.changed_python(repo, paths) == paths


def test_deleted_source_still_selects_its_remaining_tests(repo: Path) -> None:
    (repo / "benchbox/core/engine.py").unlink()
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["benchbox/core/engine.py"]
    assert pt.changed_python(repo, paths) == []
    assert pt.map_tests(repo, paths) == ["tests/integration/test_engine.py", "tests/unit/core/test_engine.py"]


def test_deleted_test_file_is_not_selected(repo: Path) -> None:
    (repo / "tests/unit/test_unrelated.py").unlink()
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["tests/unit/test_unrelated.py"]
    assert pt.map_tests(repo, paths) == []


def test_renamed_source_keeps_the_old_path_for_test_mapping(repo: Path) -> None:
    _git(repo, "mv", "benchbox/core/engine.py", "benchbox/core/motor.py")
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert paths == ["benchbox/core/engine.py", "benchbox/core/motor.py"]
    assert pt.map_tests(repo, paths) == ["tests/integration/test_engine.py", "tests/unit/core/test_engine.py"]


def test_project_scripts_module_maps_to_its_test(repo: Path) -> None:
    _write(repo, "_project/scripts/audit.py")
    _write(repo, "tests/unit/scripts/test_audit.py")
    _commit_all(repo)
    _write(repo, "_project/scripts/audit.py", "x = 2\n")

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/scripts/test_audit.py"]


def test_cli_prints_selection_and_reports_missing_base(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)

    assert pt.main(["python", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out.split() == ["benchbox/core/engine.py"]
    assert pt.main(["tests", "--repo-root", str(repo), "--base-ref", "origin/missing"]) == 2


def test_changed_test_helper_selects_the_tests_that_import_it(repo: Path) -> None:
    _write(repo, "tests/fixtures/helpers.py")
    _write(repo, "tests/fixtures/__init__.py", "")
    _write(repo, "tests/unit/test_absolute.py", "from tests.fixtures.helpers import x\n")
    _write(repo, "tests/unit/test_package.py", "from tests.fixtures import helpers\n")
    _write(repo, "tests/fixtures/test_relative.py", "from .helpers import x\n")
    _commit_all(repo, "helpers")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _write(repo, "tests/fixtures/helpers.py", "x = 2\n")
    _write(repo, "tests/fixtures/__init__.py", "y = 1\n")

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == [
        "tests/fixtures/test_relative.py",
        "tests/unit/test_absolute.py",
        "tests/unit/test_package.py",
    ]


def test_changed_conftest_selects_its_subtree_up_to_the_limit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(repo, "tests/unit/core/conftest.py")
    _write(repo, "tests/conftest.py")

    assert pt.map_tests(repo, ["tests/unit/core/conftest.py"]) == ["tests/unit/core/test_engine.py"]
    monkeypatch.setattr(pt, "CONFTEST_SUBTREE_LIMIT", 1)
    assert pt.map_tests(repo, ["tests/conftest.py"]) == []


def test_platform_source_selects_its_smoke_tests_and_guards(repo: Path) -> None:
    for path in (
        "tests/integration/platforms/test_clickhouse_smoke.py",
        "tests/integration/platforms/test_lakesail_stub_smoke.py",
        "tests/integration/platforms/test_local_platforms_smoke.py",
        "tests/system/test_module_size_thresholds.py",
        "tests/uat/test_connection_registry_drift.py",
    ):
        _write(repo, path)

    assert pt.map_tests(repo, ["benchbox/platforms/clickhouse/adapter.py", "benchbox/platforms/lakesail.py"]) == [
        "tests/integration/platforms/test_clickhouse_smoke.py",
        "tests/integration/platforms/test_lakesail_stub_smoke.py",
        "tests/system/test_module_size_thresholds.py",
        "tests/uat/test_connection_registry_drift.py",
    ]
    assert pt.map_tests(repo, ["benchbox/platforms/base/adapter.py"]) == [
        "tests/integration/platforms/test_local_platforms_smoke.py",
        "tests/system/test_module_size_thresholds.py",
        "tests/uat/test_connection_registry_drift.py",
    ]
    assert pt.map_tests(repo, ["scripts/tool.py"]) == ["tests/unit/test_tool.py"]


def test_source_guard_tests_exist() -> None:
    root = SCRIPT.parents[2]
    missing = [test for tests in pt.SOURCE_GUARD_TESTS.values() for test in tests if not (root / test).is_file()]

    assert missing == []


@pytest.mark.parametrize(
    "path",
    [
        "_project/scripts/oracle_reviewers/cli.py",
        "_project/scripts/oracle_reviewers/runner.py",
        "_project/scripts/oracle_review_check.py",
        "_project/scripts/oracle_gate.py",
        "_project/scripts/soundness_merge_digest.py",
        "scripts/pr_arm.py",
        "tests/integration/oracle/harness.py",
        "tests/integration/oracle/fake_github.py",
        "tests/fixtures/oracle_reviewers/captures/ci_attempt.json",
        "tests/fixtures/oracle_reviewers/omp_multi_turn_review.jsonl",
        "tests/fixtures/oracle_reviewers/provenance.json",
        ".github/workflows/oracle-verdict.yml",
        ".github/workflows/oracle-gate.yml",
        ".github/oracle-reviewers.yml",
        ".github/oracle-omp-config.yml",
    ],
)
def test_an_oracle_change_selects_every_scenario_harness_test(path: str) -> None:
    selected = pt.map_tests(SCRIPT.parents[2], [path])

    assert set(pt.ORACLE_SCENARIO_TESTS) <= set(selected)


def test_the_scenario_harness_list_names_every_harness_test_file() -> None:
    root = SCRIPT.parents[2]
    found = sorted(path.relative_to(root).as_posix() for path in (root / "tests/integration/oracle").glob("test_*.py"))

    assert list(pt.ORACLE_SCENARIO_TESTS) == found


def test_platform_smoke_mapping_needs_the_whole_platform_name(repo: Path) -> None:
    for path in (
        "tests/integration/platforms/test_cloud_spark_smoke.py",
        "tests/integration/platforms/test_presto_smoke.py",
        "tests/integration/platforms/test_starrocks_smoke_resource_heavy.py",
    ):
        _write(repo, path)

    assert pt.map_tests(repo, ["benchbox/platforms/cloud_shared.py"]) == []
    assert pt.map_tests(repo, ["benchbox/platforms/presto_trino_utils.py", "benchbox/platforms/starrocks/x.py"]) == [
        "tests/integration/platforms/test_presto_smoke.py",
    ]


def test_workflow_change_selects_workflow_tests_and_tests_naming_the_file(repo: Path) -> None:
    _write(repo, "tests/unit/workflows/test_every_workflow.py")
    _write(repo, "tests/system/test_names_ci.py", "WORKFLOW = '.github/workflows/ci.yml'\n")
    _write(repo, "tests/system/test_names_bare.py", "NAME = 'ci.yml'\n")
    _write(repo, "tests/system/test_names_other.py", "NAME = 'foo-ci.yml'\n")
    _write(repo, "tests/system/test_names_suffix.py", "NAME = 'ci.yml.bak'\n")
    _write(repo, "tests/system/test_names_nothing.py", "NAME = 'unrelated'\n")

    assert pt.map_tests(repo, [".github/workflows/ci.yml"]) == [
        "tests/system/test_names_bare.py",
        "tests/system/test_names_ci.py",
        "tests/unit/workflows/test_every_workflow.py",
    ]


def test_composite_action_change_selects_workflow_tests_and_tests_naming_the_action(repo: Path) -> None:
    _write(repo, "tests/unit/workflows/test_every_workflow.py")
    _write(repo, "tests/system/test_uses_action.py", "USES = './.github/actions/setup-env'\n")
    _write(repo, "tests/system/test_uses_longer_action.py", "USES = './.github/actions/setup-env-extra'\n")
    _write(repo, "tests/system/test_uses_nothing.py", "USES = 'other'\n")

    assert pt.map_tests(repo, [".github/actions/setup-env/action.yml"]) == [
        "tests/system/test_uses_action.py",
        "tests/unit/workflows/test_every_workflow.py",
    ]


def test_python_file_in_a_composite_action_selects_workflow_tests(repo: Path) -> None:
    _write(repo, "tests/unit/workflows/test_every_workflow.py")
    _write(repo, "tests/system/test_uses_action.py", "USES = './.github/actions/setup-env'\n")

    assert pt.map_tests(repo, [".github/actions/setup-env/resolve.py"]) == [
        "tests/system/test_uses_action.py",
        "tests/unit/workflows/test_every_workflow.py",
    ]


def test_oracle_workflow_change_still_selects_the_scenario_harness(repo: Path) -> None:
    for test in pt.ORACLE_SCENARIO_TESTS:
        _write(repo, test)
    _write(repo, "tests/unit/workflows/test_every_workflow.py")

    assert set(pt.ORACLE_SCENARIO_TESTS) <= set(pt.map_tests(repo, [".github/workflows/oracle-gate.yml"]))


def test_changed_test_package_init_selects_its_package_and_importers(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(repo, "tests/fixtures/__init__.py", "")
    _write(repo, "tests/fixtures/test_inside.py")
    _write(repo, "tests/unit/test_user.py", "from tests.fixtures import data\n")

    assert pt.map_tests(repo, ["tests/fixtures/__init__.py"]) == [
        "tests/fixtures/test_inside.py",
        "tests/unit/test_user.py",
    ]
    monkeypatch.setattr(pt, "CONFTEST_SUBTREE_LIMIT", 0)
    assert pt.map_tests(repo, ["tests/fixtures/__init__.py"]) == []


def test_conftest_limit_matches_the_heavy_tier_carve_out() -> None:
    from scripts import heavy_tier_needed

    assert pt.CONFTEST_SUBTREE_LIMIT == heavy_tier_needed.BROAD_CONFTEST_MODULES


def test_source_change_selects_tests_that_import_the_module(repo: Path) -> None:
    _write(repo, "tests/unit/test_via_import.py", "from benchbox.core import engine\n")
    _write(repo, "tests/unit/test_via_dotted.py", "import benchbox.core.engine as e\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "importers")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)

    selected = pt.map_tests(repo, pt.changed_paths(repo, "origin/develop"))

    assert "tests/unit/test_via_import.py" in selected
    assert "tests/unit/test_via_dotted.py" in selected
    assert "tests/unit/test_unrelated.py" not in selected


def test_base_change_selects_test_benchbox_through_patched_subclass_module(repo: Path) -> None:
    _write(repo, "benchbox/base.py", "class BaseBenchmark:\n    pass\n")
    _write(
        repo,
        "benchbox/core/tpch.py",
        "from benchbox.base import BaseBenchmark\n\n\nclass TPCH(BaseBenchmark):\n    pass\n",
    )
    _write(repo, "benchbox/__init__.py", "from benchbox.core.tpch import TPCH\n")
    _write(
        repo,
        "tests/test_benchbox.py",
        "from unittest import mock\n\nfrom benchbox import TPCH\n\n\n"
        "def test_x():\n    with mock.patch('benchbox.core.tpch.TPCH.generate'):\n        pass\n",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _write(repo, "benchbox/base.py", "class BaseBenchmark:\n    x = 1\n")
    _commit_all(repo)

    selected = pt.map_tests(repo, pt.changed_paths(repo, "origin/develop"))

    assert selected == ["tests/test_benchbox.py"]


def test_importing_the_package_alone_does_not_select_for_a_submodule_change(repo: Path) -> None:
    _write(repo, "benchbox/__init__.py", "")
    _write(repo, "tests/unit/test_pkg.py", "import benchbox\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "pkg")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _write(repo, "benchbox/core/other.py", "x = 2\n")
    _commit_all(repo)

    assert pt.map_tests(repo, pt.changed_paths(repo, "origin/develop")) == []


def test_monkeypatch_setattr_target_selects_the_patching_test(repo: Path) -> None:
    _write(
        repo,
        "tests/unit/test_patcher.py",
        "def test_x(monkeypatch):\n    monkeypatch.setattr('benchbox.core.engine.run', None)\n",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "patcher")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)

    assert "tests/unit/test_patcher.py" in pt.map_tests(repo, pt.changed_paths(repo, "origin/develop"))


def test_platform_branching_source_triggers_linux_run_of_mapped_tests(repo: Path) -> None:
    _write(repo, "benchbox/core/engine.py", "import sys\nIS_LINUX = sys.platform.startswith('linux')\n")
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert pt.linux_tests(repo, paths) == ["tests/integration/test_engine.py", "tests/unit/core/test_engine.py"]


def test_changed_test_with_platform_skip_triggers_linux_run(repo: Path) -> None:
    _write(
        repo,
        "tests/unit/test_unrelated.py",
        "import sys\nimport pytest\n\n@pytest.mark.skipif(sys.platform == 'win32', reason='posix')\ndef test_a():\n    pass\n",
    )
    _commit_all(repo)

    assert pt.linux_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/test_unrelated.py"]


@pytest.mark.parametrize(
    "snippet", ["if os.name == 'nt':\n    pass\n", "x = platform.system()\n", "if IS_WINDOWS:\n    pass\n"]
)
def test_other_platform_branch_forms_trigger(repo: Path, snippet: str) -> None:
    _write(repo, "scripts/tool.py", snippet)
    _commit_all(repo)

    assert pt.linux_tests(repo, pt.changed_paths(repo, "origin/develop")) == ["tests/unit/test_tool.py"]


def test_change_without_platform_branching_does_not_trigger_linux_run(repo: Path) -> None:
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _write(repo, "docs/guide.md", "sys.platform in prose\n")
    _commit_all(repo)

    paths = pt.changed_paths(repo, "origin/develop")

    assert pt.map_tests(repo, paths)
    assert pt.linux_tests(repo, paths) == []


def test_untouched_platform_branching_file_does_not_trigger(repo: Path) -> None:
    _write(repo, "benchbox/platforms/other.py", "import sys\nIS_LINUX = sys.platform == 'linux'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)

    assert pt.linux_tests(repo, pt.changed_paths(repo, "origin/develop")) == []


def test_linux_kind_cli_prints_tests_only_when_triggered(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "scripts/tool.py", "import sys\nx = sys.platform\n")
    _commit_all(repo)

    assert pt.main(["linux", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out.split() == ["tests/unit/test_tool.py"]
