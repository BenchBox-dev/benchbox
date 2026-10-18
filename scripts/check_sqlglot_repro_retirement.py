from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import duckdb
from packaging.requirements import Requirement

from scripts.sync_sqlglot_runtime import check_runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("_project/sqlglot-upstream/runtime-baseline.json")
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


def load_baseline(root: Path, identity: dict[str, Any]) -> list[dict[str, Any]]:
    baseline = json.loads((root / BASELINE).read_text(encoding="utf-8"))
    if baseline["schema"] != 1 or baseline["identity"] != identity:
        raise ValueError("Runtime baseline identity must match the exact pinned wheel/source")
    cases = baseline["cases"]
    if (
        not isinstance(cases, list)
        or {case["id"] for case in cases} != REQUIRED_CASES
        or len(cases) != len(REQUIRED_CASES)
    ):
        raise ValueError("Runtime baseline must retain every named case exactly once")
    for case in cases:
        if (
            not all(isinstance(case[key], str) and case[key] for key in ("id", "sql", "source", "target", "call_shape"))
            or case["call_shape"] != "transpile"
            or case["identify"] is not True
            or case["expected"]["outcome"] not in {"pass", "unsupported"}
            or case["workaround"]["decision"] not in {"retain", "fork-covered"}
            or not (root / case["workaround"]["path"]).is_file()
            or not case["workaround"]["reason"]
        ):
            raise ValueError(f"Malformed runtime baseline case: {case.get('id')}")
        if case["expected"]["outcome"] == "pass":
            shape = {"sqlite": "rows", "duckdb": "rows", "mysql": "native_percentile"}.get(
                case["target"], "dialect_available"
            )
            if shape not in case["expected"] or (shape == "rows" and not isinstance(case["expected"]["rows"], list)):
                raise ValueError("Passing cases require the acknowledged result shape for their target")
        elif not isinstance(case["expected"].get("error_type"), str):
            raise ValueError("Unsupported cases require an explicit error type")
    return cases


def observe_case(case: dict[str, Any]) -> dict[str, Any]:
    from benchbox._vendor import sqlglot

    try:
        sql = sqlglot.transpile(
            case["sql"],
            read=case["source"],
            write=case["target"],
            identify=case["identify"],
            unsupported_level=sqlglot.ErrorLevel.RAISE,
        )[0]
        if case["target"] not in {"sqlite", "duckdb", "mysql"}:
            return {"outcome": "pass", "dialect_available": True}
        if case["target"] == "mysql":
            expression = sqlglot.parse_one(sql, read="mysql")
            if expression.find(sqlglot.exp.Case) and expression.find(sqlglot.exp.Null):
                return {"outcome": "unsupported", "error_type": "SyntheticPercentileDecorator"}
            return {"outcome": "pass", "native_percentile": True}
        if case["target"] == "duckdb":
            with duckdb.connect(":memory:") as connection:
                rows = [list(row) for row in connection.execute(sql).fetchall()]
        else:
            with sqlite3.connect(":memory:") as connection:
                rows = [list(row) for row in connection.execute(sql).fetchall()]
        return {"outcome": "pass", "rows": rows}
    except (
        sqlglot.errors.UnsupportedError,
        sqlglot.errors.ParseError,
        ValueError,
        sqlite3.Error,
        duckdb.Error,
    ) as error:
        return {"outcome": "unsupported", "error_type": type(error).__name__}


def check(root: Path = REPO_ROOT, base_ref: str = "origin/develop") -> None:
    identity = check_runtime(root / "benchbox/_vendor")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if any(
        Requirement(requirement).name.lower().replace("_", "-") in {"sqlglot", "benchbox-sqlglot"}
        for requirement in project["project"]["dependencies"]
    ):
        raise ValueError("BenchBox must use only its bundled runtime, not an external SQLGlot requirement")
    cases = load_baseline(root, identity)
    base = subprocess.check_output(["git", "merge-base", "HEAD", base_ref], cwd=root, text=True).strip()
    previous = dependency_identity(root, base)
    print(
        f"SQLGlot runtime identity {'changed' if previous != identity else 'unchanged'}; checking acknowledged outcomes"
    )
    for case in cases:
        actual = observe_case(case)
        if actual != case["expected"]:
            raise ValueError(
                f"{case['id']}: expected {case['expected']}, observed {actual}; "
                f"review retirement decision at {case['workaround']['path']}"
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
