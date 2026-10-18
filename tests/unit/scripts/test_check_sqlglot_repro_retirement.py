from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.check_sqlglot_repro_retirement import BASELINE, REPO_ROOT, check, load_baseline
from scripts.sync_sqlglot_runtime import check_runtime

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def guarded_repository(tmp_path: Path) -> Path:
    runtime = tmp_path / "benchbox/_vendor"
    runtime.mkdir(parents=True)
    for path in (REPO_ROOT / "benchbox/_vendor").iterdir():
        if path.suffix in {".zip", ".json", ".txt"}:
            shutil.copyfile(path, runtime / path.name)
    baseline = json.loads((REPO_ROOT / BASELINE).read_text(encoding="utf-8"))
    for case in baseline["cases"]:
        document = tmp_path / case["workaround"]["path"]
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text("Recorded workaround decision", encoding="utf-8")
    (tmp_path / BASELINE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / BASELINE).write_text(json.dumps(baseline), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="fixture"\ndependencies=[]\n', encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "benchbox", "pyproject.toml", "scripts"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--quiet", "-m", "Fixture baseline"], check=True)
    return tmp_path


def change_baseline(root: Path, transform) -> None:
    path = root / BASELINE
    data = json.loads(path.read_text(encoding="utf-8"))
    transform(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_acknowledged_passes_do_not_repeat_retirement_failure(guarded_repository: Path) -> None:
    check(guarded_repository, "HEAD")
    check(guarded_repository, "HEAD")


def test_same_version_changed_source_requires_baseline_review(guarded_repository: Path) -> None:
    identity = check_runtime(guarded_repository / "benchbox/_vendor")
    identity["fork_sha"] = "0" * 40
    with pytest.raises(ValueError, match="identity"):
        load_baseline(guarded_repository, identity)


@pytest.mark.parametrize("field", ["source", "target", "call_shape", "expected", "workaround"])
def test_incomplete_baseline_fails_closed(guarded_repository: Path, field: str) -> None:
    change_baseline(guarded_repository, lambda data: data["cases"][0].pop(field))
    with pytest.raises((ValueError, KeyError)):
        check(guarded_repository, "HEAD")


def test_missing_case_fails_closed(guarded_repository: Path) -> None:
    change_baseline(guarded_repository, lambda data: data["cases"].pop())
    with pytest.raises(ValueError, match="every named case"):
        check(guarded_repository, "HEAD")


def test_wrong_execution_result_requires_retirement_review(guarded_repository: Path) -> None:
    def alter(data):
        case = next(case for case in data["cases"] if case["id"] == "postgres-integer-division")
        case["expected"] = {"outcome": "pass", "rows": [[1]]}

    change_baseline(guarded_repository, alter)
    with pytest.raises(ValueError, match="postgres-integer-division"):
        check(guarded_repository, "HEAD")


def test_unsupported_case_becoming_executable_requires_review(guarded_repository: Path) -> None:
    def alter(data):
        case = next(case for case in data["cases"] if case["id"] == "sqlite-unknown-extract")
        case["sql"] = "SELECT EXTRACT(YEAR FROM DATE '2024-02-29')"

    change_baseline(guarded_repository, alter)
    with pytest.raises(ValueError, match="sqlite-unknown-extract"):
        check(guarded_repository, "HEAD")


def test_external_dependency_cannot_replace_bundled_runtime(guarded_repository: Path) -> None:
    (guarded_repository / "pyproject.toml").write_text(
        '[project]\nname="fixture"\ndependencies=["sqlglot>=30"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="external SQLGlot"):
        check(guarded_repository, "HEAD")
