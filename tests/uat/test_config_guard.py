from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.uat import config_guard

pytestmark = pytest.mark.medium

GATE_CONFIG = """\
name: gate
execute:
  per_cell_timeout_s: 1200
  per_cell_memory_limit_gib: 13
  early_stop_on_failure: false
validate:
  validator_clean_rate_floor: 1.0
report:
  cross_scale_coverage_min_pairs: 32
"""

COMPOSE = """\
services:
  db:
    image: repo/db:1
    mem_limit: ${DB_MEMORY_LIMIT:-8g}
    healthcheck:
      retries: 20
      start_period: 120s
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=guard", "-c", "user.email=guard@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests/uat/configs").mkdir(parents=True)
    (tmp_path / "docker/db").mkdir(parents=True)
    (tmp_path / "tests/uat/configs/gate.yaml").write_text(GATE_CONFIG, encoding="utf-8")
    (tmp_path / "docker/db/docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_unchanged_tree_has_no_guarded_changes(repo):
    assert config_guard.changed_guarded_values(repo, "HEAD") == []


def test_non_guarded_edits_pass(repo):
    gate = repo / "tests/uat/configs/gate.yaml"
    gate.write_text(
        GATE_CONFIG.replace("early_stop_on_failure: false", "early_stop_on_failure: true")
        + "  infrastructure_retry_job_budget_s: 19800\n",
        encoding="utf-8",
    )
    compose = repo / "docker/db/docker-compose.yml"
    compose.write_text(COMPOSE.replace("repo/db:1", "repo/db:1@sha256:" + "cd" * 32), encoding="utf-8")

    assert config_guard.changed_guarded_values(repo, "HEAD") == []


@pytest.mark.parametrize(
    ("relative", "old", "new", "key"),
    [
        (
            "tests/uat/configs/gate.yaml",
            "per_cell_timeout_s: 1200",
            "per_cell_timeout_s: 2400",
            "execute.per_cell_timeout_s",
        ),
        (
            "tests/uat/configs/gate.yaml",
            "per_cell_memory_limit_gib: 13",
            "per_cell_memory_limit_gib: 26",
            "execute.per_cell_memory_limit_gib",
        ),
        (
            "tests/uat/configs/gate.yaml",
            "validator_clean_rate_floor: 1.0",
            "validator_clean_rate_floor: 0.5",
            "validate.validator_clean_rate_floor",
        ),
        (
            "tests/uat/configs/gate.yaml",
            "cross_scale_coverage_min_pairs: 32",
            "cross_scale_coverage_min_pairs: 16",
            "report.cross_scale_coverage_min_pairs",
        ),
        ("docker/db/docker-compose.yml", "DB_MEMORY_LIMIT:-8g", "DB_MEMORY_LIMIT:-4g", "services.db.mem_limit"),
        ("docker/db/docker-compose.yml", "retries: 20", "retries: 2", "services.db.healthcheck.retries"),
    ],
)
def test_changed_guarded_value_is_reported(repo, relative, old, new, key):
    path = repo / relative
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    changes = config_guard.changed_guarded_values(repo, "HEAD")

    assert [(c.path, c.key) for c in changes] == [(relative, key)]


def test_removed_and_added_guarded_keys_are_reported(repo):
    gate = repo / "tests/uat/configs/gate.yaml"
    gate.write_text(
        GATE_CONFIG.replace("  per_cell_memory_limit_gib: 13\n", "") + "  liveness_probe_timeout_s: 9\n",
        encoding="utf-8",
    )

    changes = config_guard.changed_guarded_values(repo, "HEAD")

    assert {(c.key, c.base, c.head) for c in changes} == {
        ("execute.per_cell_memory_limit_gib", 13, "<absent>"),
        ("report.liveness_probe_timeout_s", "<absent>", 9),
    }


def test_deleted_guarded_file_is_reported(repo):
    (repo / "tests/uat/configs/gate.yaml").unlink()

    keys = {c.key for c in config_guard.changed_guarded_values(repo, "HEAD")}

    assert "execute.per_cell_timeout_s" in keys


def test_cli_exit_code_reflects_changes(repo, capsys):
    assert config_guard.main(["--root", str(repo), "--base", "HEAD"]) == 0
    gate = repo / "tests/uat/configs/gate.yaml"
    gate.write_text(GATE_CONFIG.replace("1200", "600"), encoding="utf-8")

    assert config_guard.main(["--root", str(repo), "--base", "HEAD"]) == 1
    assert "execute.per_cell_timeout_s: 1200 -> 600" in capsys.readouterr().err
