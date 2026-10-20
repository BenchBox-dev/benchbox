from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
from packaging.requirements import Requirement

from scripts.sync_sqlglot_runtime import check_runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTDB_DSN_ENV = "BENCHBOX_QUESTDB_DSN"
BASELINE = Path("scripts/sqlglot_runtime_baseline.json")
REQUIRED_CASES = {
    "postgres-extract-fractional-division",
    "postgres-integer-division",
    "sqlite-leap-day-interval",
    "sqlite-null-interval",
    "sqlite-unknown-extract",
    "sqlite-month-interval",
    "questdb-dialect",
    "duckdb-group-by-all",
    "duckdb-order-by-all",
    "mysql-percentile-decorator",
    "wrapper-sqlite-fractional-extract",
    "wrapper-sqlite-month-interval",
    "wrapper-duckdb-order-by-all",
}


def dependency_identity(root: Path, ref: str) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "show", f"{ref}:benchbox/_vendor/sqlglot.json"], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    lock = subprocess.check_output(["git", "show", f"{ref}:uv.lock"], cwd=root, text=True)
    packages = [package for package in tomllib.loads(lock)["package"] if package["name"] == "sqlglot"]
    if len(packages) != 1:
        raise ValueError("Base must identify one public or bundled SQLGlot runtime")
    return packages[0]


class EngineError(Exception):
    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


def _execute_sqlite(sql: str) -> list[list[Any]]:
    try:
        with sqlite3.connect(":memory:") as connection:
            return [list(row) for row in connection.execute(sql).fetchall()]
    except sqlite3.Error as error:
        raise EngineError(type(error).__name__) from error


def _execute_duckdb(sql: str) -> list[list[Any]]:
    try:
        with duckdb.connect(":memory:") as connection:
            return [list(row) for row in connection.execute(sql).fetchall()]
    except duckdb.Error as error:
        raise EngineError(type(error).__name__) from error


def _execute_questdb(sql: str) -> list[list[Any]]:
    import psycopg

    dsn = os.environ.get(QUESTDB_DSN_ENV)
    if not dsn:
        raise RuntimeError(f"Executing a QuestDB case requires a running QuestDB container; set {QUESTDB_DSN_ENV}")
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
            return [list(row) for row in connection.execute(sql).fetchall()]
    except psycopg.Error as error:
        if isinstance(error, psycopg.OperationalError) and not isinstance(error, psycopg.errors.SyntaxError):
            raise RuntimeError(f"QuestDB endpoint unavailable: {error}") from error
        raise EngineError(type(error).__name__) from error


EXECUTORS: dict[str, Callable[[str], list[list[Any]]]] = {
    "sqlite": _execute_sqlite,
    "duckdb": _execute_duckdb,
    "questdb": _execute_questdb,
}
ENTRIES = {"transpile", "wrapper"}


def case_platform(case: dict[str, Any]) -> str:
    return case.get("platform") or case["target"]


def result_shape(case: dict[str, Any], executors: dict[str, Callable[[str], list[list[Any]]]]) -> str:
    if case_platform(case) in executors:
        return "rows"
    return "native_percentile" if case["target"] == "mysql" else "dialect_available"


def load_baseline(
    root: Path, identity: dict[str, Any], executors: dict[str, Callable[[str], list[list[Any]]]] | None = None
) -> list[dict[str, Any]]:
    executors = EXECUTORS if executors is None else executors
    baseline = json.loads((root / BASELINE).read_text(encoding="utf-8"))
    if baseline["schema"] != 2 or baseline["identity"] != identity:
        raise ValueError("Runtime baseline identity must match the exact pinned wheel/source")
    cases = baseline["cases"]
    if (
        not isinstance(cases, list)
        or {case["id"] for case in cases} != REQUIRED_CASES
        or len(cases) != len(REQUIRED_CASES)
    ):
        raise ValueError("Runtime baseline must retain every named case exactly once")
    for case in cases:
        workaround = case["workaround"]
        path = workaround.get("path")
        retained = workaround["decision"] == "retain"
        path_exists = isinstance(path, str) and (root / path).is_file()
        if (
            not all(isinstance(case[key], str) and case[key] for key in ("id", "sql", "source", "target", "entry"))
            or case["entry"] not in ENTRIES
            or ("platform" in case and not (isinstance(case["platform"], str) and case["platform"]))
            or ("platform" in case and case["entry"] != "wrapper")
            or case["identify"] is not True
            or case["expected"]["outcome"] not in {"pass", "unsupported"}
            or workaround["decision"] not in {"retain", "fork-covered"}
            or (retained and not path_exists)
            or (path is not None and not path_exists)
            or not workaround["reason"]
        ):
            raise ValueError(f"Malformed runtime baseline case: {case.get('id')}")
        if case["expected"]["outcome"] == "pass":
            shape = result_shape(case, executors)
            if shape not in case["expected"] or (shape == "rows" and not isinstance(case["expected"]["rows"], list)):
                raise ValueError("Passing cases require the acknowledged result shape for their target")
            if shape != "rows" and workaround["decision"] != "retain":
                raise ValueError("A pass without database execution cannot retire a workaround")
        elif not isinstance(case["expected"].get("error_type"), str):
            raise ValueError("Unsupported cases require an explicit error type")
    return cases


def _translate(case: dict[str, Any], sqlglot: Any) -> str:
    if case["entry"] == "transpile":
        return sqlglot.transpile(
            case["sql"],
            read=case["source"],
            write=case["target"],
            identify=case["identify"],
            unsupported_level=sqlglot.ErrorLevel.RAISE,
        )[0]
    from benchbox.utils.dialect_utils import SQLTranslationError, translate_sql_query

    try:
        return translate_sql_query(
            case["sql"],
            case["target"],
            source_dialect=case["source"],
            identify=case["identify"],
            strict=True,
        )
    except SQLTranslationError as error:
        raise (error.__cause__ or error) from error


def observe_case(
    case: dict[str, Any], executors: dict[str, Callable[[str], list[list[Any]]]] | None = None
) -> dict[str, Any]:
    from benchbox._vendor import sqlglot

    executors = EXECUTORS if executors is None else executors
    try:
        sql = _translate(case, sqlglot)
        if case["target"] == "mysql":
            expression = sqlglot.parse_one(sql, read="mysql")
            if expression.find(sqlglot.exp.Case) and expression.find(sqlglot.exp.Null):
                return {"outcome": "unsupported", "error_type": "SyntheticPercentileDecorator"}
            return {"outcome": "pass", "native_percentile": True}
        executor = executors.get(case_platform(case))
        if executor is None:
            return {"outcome": "pass", "dialect_available": True}
        return {"outcome": "pass", "rows": executor(sql)}
    except EngineError as error:
        return {"outcome": "unsupported", "error_type": error.error_type}
    except (sqlglot.errors.UnsupportedError, sqlglot.errors.ParseError, ValueError) as error:
        return {"outcome": "unsupported", "error_type": type(error).__name__}


def check(
    root: Path = REPO_ROOT,
    base_ref: str = "origin/develop",
    executors: dict[str, Callable[[str], list[list[Any]]]] | None = None,
) -> None:
    identity = check_runtime(root / "benchbox/_vendor")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if any(
        Requirement(requirement).name.lower().replace("_", "-") in {"sqlglot", "benchbox-sqlglot"}
        for requirement in project["project"]["dependencies"]
    ):
        raise ValueError("BenchBox must use only its bundled runtime, not an external SQLGlot requirement")
    cases = load_baseline(root, identity, executors)
    base = subprocess.check_output(["git", "merge-base", "HEAD", base_ref], cwd=root, text=True).strip()
    previous = dependency_identity(root, base)
    print(
        f"SQLGlot runtime identity {'changed' if previous != identity else 'unchanged'}; checking acknowledged outcomes"
    )
    for case in cases:
        actual = observe_case(case, executors)
        if actual != case["expected"]:
            raise ValueError(
                f"{case['id']}: expected {case['expected']}, observed {actual}; "
                f"review retirement decision at {case['workaround'].get('path', 'the removed workaround')}"
            )
        print(f"  {case['id']}: {actual['outcome']} ({case['workaround']['decision']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the bundled wheel identity and reviewed runtime outcomes.")
    parser.add_argument("--base-ref", default="origin/develop")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        check(base_ref=arguments.base_ref)
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as error:
        print(f"SQLGlot retirement check: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
