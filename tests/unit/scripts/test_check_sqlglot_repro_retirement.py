from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.check_sqlglot_repro_retirement import (
    BASELINE,
    EXECUTORS,
    REPO_ROOT,
    _execute_questdb,
    check,
    load_baseline,
    observe_case,
)
from scripts.sync_sqlglot_runtime import check_runtime

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def questdb_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(EXECUTORS, "questdb", lambda sql: [[1]])


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


@pytest.mark.parametrize("field", ["source", "target", "entry", "expected", "workaround"])
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
        case = next(case for case in data["cases"] if case["id"] == "sqlite-month-interval")
        case["sql"] = "SELECT DATE '2024-02-28' + INTERVAL '1' DAY"

    change_baseline(guarded_repository, alter)
    with pytest.raises(ValueError, match="sqlite-month-interval"):
        check(guarded_repository, "HEAD")


def test_external_dependency_cannot_replace_bundled_runtime(guarded_repository: Path) -> None:
    (guarded_repository / "pyproject.toml").write_text(
        '[project]\nname="fixture"\ndependencies=["sqlglot>=30"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="external SQLGlot"):
        check(guarded_repository, "HEAD")


def baseline_case(case_id: str) -> dict:
    baseline = json.loads((REPO_ROOT / BASELINE).read_text(encoding="utf-8"))
    return next(case for case in baseline["cases"] if case["id"] == case_id)


def test_wrapper_output_change_fails_while_direct_transpile_passes(
    guarded_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchbox.utils.dialect_utils as dialect_utils

    direct = baseline_case("postgres-extract-fractional-division")
    wrapped = baseline_case("wrapper-sqlite-fractional-extract")
    assert observe_case(direct) == direct["expected"]
    assert observe_case(wrapped) == wrapped["expected"]

    monkeypatch.setattr(dialect_utils, "_fix_sqlite_unsupported_syntax", lambda sql: "SELECT 0")
    assert observe_case(direct) == direct["expected"]
    assert observe_case(wrapped) == {"outcome": "pass", "rows": [[0]]}
    with pytest.raises(ValueError, match="wrapper-sqlite-fractional-extract"):
        check(guarded_repository, "HEAD")


def test_wrapper_failure_reports_underlying_error_type() -> None:
    case = {**baseline_case("questdb-dialect"), "target": "not_a_dialect"}
    assert case["entry"] == "wrapper"
    assert observe_case(case) == {"outcome": "unsupported", "error_type": "ValueError"}


def test_unknown_entry_fails_closed(guarded_repository: Path) -> None:
    change_baseline(guarded_repository, lambda data: data["cases"][0].update(entry="translate_ddl"))
    with pytest.raises(ValueError, match="Malformed"):
        check(guarded_repository, "HEAD")


def test_platform_requires_wrapper_entry(guarded_repository: Path) -> None:
    change_baseline(guarded_repository, lambda data: data["cases"][0].update(platform="sqlite"))
    with pytest.raises(ValueError, match="Malformed"):
        check(guarded_repository, "HEAD")


def flip_questdb(data: dict, *, drop_path: bool) -> None:
    case = next(case for case in data["cases"] if case["id"] == "questdb-dialect")
    case.update(target="postgres", platform="questdb", entry="wrapper", expected={"outcome": "pass", "rows": [[1]]})
    case["workaround"] = {"decision": "fork-covered", "reason": "Covered by the fork under QuestDB execution."}
    if not drop_path:
        case["workaround"]["path"] = "benchbox/utils/dialect_utils.py"


@pytest.mark.parametrize("drop_path", [True, False])
def test_questdb_case_flips_to_fork_covered_under_executor(guarded_repository: Path, drop_path: bool) -> None:
    change_baseline(guarded_repository, lambda data: flip_questdb(data, drop_path=drop_path))
    seen: list[str] = []

    def questdb(sql: str) -> list[list[int]]:
        seen.append(sql)
        return [[1]]

    check(guarded_repository, "HEAD", {**EXECUTORS, "questdb": questdb})
    assert seen == ["SELECT 1"]


def test_questdb_rows_must_match_executor_output(guarded_repository: Path) -> None:
    change_baseline(guarded_repository, lambda data: flip_questdb(data, drop_path=True))
    with pytest.raises(ValueError, match="questdb-dialect"):
        check(guarded_repository, "HEAD", {**EXECUTORS, "questdb": lambda sql: [[2]]})


def test_questdb_without_endpoint_fails_closed(guarded_repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_QUESTDB_DSN", raising=False)
    monkeypatch.setitem(EXECUTORS, "questdb", _execute_questdb)
    with pytest.raises(RuntimeError, match="BENCHBOX_QUESTDB_DSN"):
        check(guarded_repository, "HEAD")


def test_fork_covered_without_executor_is_rejected(guarded_repository: Path) -> None:
    change_baseline(guarded_repository, lambda data: flip_questdb(data, drop_path=True))
    executors = {name: executor for name, executor in EXECUTORS.items() if name != "questdb"}
    with pytest.raises(ValueError, match="Passing cases require"):
        check(guarded_repository, "HEAD", executors)


def test_retained_workaround_requires_existing_path(guarded_repository: Path) -> None:
    change_baseline(guarded_repository, lambda data: data["cases"][5]["workaround"].pop("path"))
    with pytest.raises(ValueError, match="Malformed"):
        check(guarded_repository, "HEAD")


def test_missing_declared_path_is_malformed_even_when_fork_covered(guarded_repository: Path) -> None:
    change_baseline(guarded_repository, lambda data: data["cases"][0]["workaround"].update(path="gone.py"))
    with pytest.raises(ValueError, match="Malformed"):
        check(guarded_repository, "HEAD")


def test_mysql_workaround_path_names_real_code() -> None:
    path = baseline_case("mysql-percentile-decorator")["workaround"]["path"]
    assert "PERCENTILE_CONT" in (REPO_ROOT / path).read_text(encoding="utf-8")


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def execute(self, sql: str):
        return self

    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]


def test_questdb_executor_waits_for_the_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    attempts: list[int] = []

    def connect(dsn: str, **options):
        attempts.append(1)
        if len(attempts) < 3:
            raise psycopg.OperationalError("connection refused")
        return _FakeConnection()

    monkeypatch.setenv("BENCHBOX_QUESTDB_DSN", "postgresql://fixture")
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr("scripts.check_sqlglot_repro_retirement.time.sleep", lambda seconds: None)
    assert _execute_questdb("SELECT 1") == [[1]]
    assert len(attempts) == 3


def test_questdb_executor_gives_up_after_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    def connect(dsn: str, **options):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setenv("BENCHBOX_QUESTDB_DSN", "postgresql://fixture")
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr("scripts.check_sqlglot_repro_retirement.time.sleep", lambda seconds: None)
    monkeypatch.setattr("scripts.check_sqlglot_repro_retirement.QUESTDB_READY_SECONDS", -1)
    with pytest.raises(RuntimeError, match="endpoint unavailable"):
        _execute_questdb("SELECT 1")
