from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, cast

try:
    from benchbox.utils.clock import elapsed_seconds, mono_time
    from benchbox.utils.dialect_utils import SQLTranslationError, translate_sql_query
except Exception as import_error:
    print(f"generator infrastructure import error: {type(import_error).__name__}: {import_error}", file=sys.stderr)
    raise SystemExit(2) from import_error

try:
    from benchbox._vendor import sqlglot as _sqlglot
    from benchbox._vendor.sqlglot.errors import ParseError
except Exception as import_error:
    print(f"generator SQLGlot import error: {type(import_error).__name__}: {import_error}", file=sys.stderr)
    raise SystemExit(2) from import_error
else:
    sqlglot_runtime = _sqlglot
    parse_error_type = ParseError

SHAPES = ("target_to_target", "postgres_to_target")
SCHEMA = "sqlglot-generator-failure-v1"
SUMMARY_SCHEMA = "sqlglot-generator-summary-v1"
REPLAY_COMMAND_TEMPLATE = (
    "uv run -- python _project/sqlglot-upstream/repros/generator.py "
    "--seed {seed} --source-dialect {source_dialect} --target-dialect {target_dialect} "
    "--failure-artifact {failure_artifact} --replay {failure_artifact}"
)


CLI_DESCRIPTION = "Deterministic, bounded SQLGlot translation-fuzzing pilot."


def _case_sql(rng: random.Random) -> str:
    table = rng.choice(("orders", "customers", "events", "lineitem", "products", "accounts"))
    column = rng.choice(("id", "amount", "created_at", "quantity", "status", "name"))
    projection = rng.choice(
        (
            column,
            f"{column}, COUNT(*) AS n",
            f"SUM({column}) AS total",
            f"{column}, {rng.choice(('id', 'amount', 'name'))}",
        )
    )
    predicate = rng.choice(
        (
            "1 = 1",
            f"{column} >= {rng.randint(0, 100)}",
            f"{column} IS NOT NULL",
            f"{column} <> '{rng.choice(('open', 'closed', 'active'))}'",
            f"{column} IN (1, 2, 3)",
        )
    )
    sql = f"SELECT {projection} FROM {table} WHERE {predicate}"
    if rng.random() < 0.65:
        sql += f" GROUP BY {column}"
    if rng.random() < 0.75:
        sql += f" ORDER BY {column} {rng.choice(('ASC', 'DESC'))}"
    if rng.random() < 0.55:
        sql += f" LIMIT {rng.randint(1, 100)}"
    return sql


def generate_case(seed: int, index: int) -> tuple[int, str]:
    case_seed = seed + index
    return case_seed, _case_sql(random.Random(case_seed))


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def run_shape(sql: str, source: str, target: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_dialect": source,
        "target_dialect": target,
        "status": "pass",
        "error": None,
        "error_type": None,
    }
    try:
        translated = translate_sql_query(sql, target, source_dialect=source, strict=True)
        result["translated_sql"] = translated
        if sqlglot_runtime is None:
            raise RuntimeError("SQLGlot is unavailable for target parse validation")
        sqlglot_runtime.parse_one(translated, read=target)
    except (SQLTranslationError, parse_error_type) as exc:
        result["status"] = "fail"
        result["error"] = _error(exc)
        result["error_type"] = type(exc).__name__
    return result


def evaluate(sql: str, source: str, target: str) -> dict[str, dict[str, Any]]:
    return {SHAPES[0]: run_shape(sql, target, target), SHAPES[1]: run_shape(sql, "postgres", target)}


def _source_valid(sql: str, target: str) -> bool:
    if sqlglot_runtime is None:
        return False
    try:
        sqlglot_runtime.parse_one(sql, read=target)
        sqlglot_runtime.parse_one(sql, read="postgres")
    except parse_error_type:
        return False
    return True


def _shrunk_candidates(sql: str) -> list[str]:
    parts = sql.split()
    return list(
        dict.fromkeys(
            [" ".join(parts[:n]) for n in range(len(parts) - 1, 0, -1)]
            + ["SELECT 1", "SELECT 1 FROM t", "SELECT id FROM t"]
        )
    )


def _outcome_signature(outcome: dict[str, Any]) -> tuple[Any, Any, Any]:
    return outcome["status"], outcome.get("error_type"), outcome.get("error")


def shrink(
    sql: str,
    source: str,
    target: str,
    failing_shapes: set[str],
    *,
    deadline_started: float | None = None,
    deadline_seconds: float | None = None,
) -> str:
    baseline = evaluate(sql, source, target)
    if failing_shapes != {name for name in SHAPES if baseline[name]["status"] == "fail"}:
        raise ValueError("failing_shapes disagrees with the baseline outcome")
    signatures = {name: _outcome_signature(baseline[name]) for name in SHAPES}
    current = sql
    changed = True
    visited: set[str] = {current}
    iterations = 0
    budget = 256
    while changed and iterations < budget:
        if (
            deadline_started is not None
            and deadline_seconds is not None
            and elapsed_seconds(deadline_started) >= deadline_seconds
        ):
            break
        changed = False
        iterations += 1
        for candidate in _shrunk_candidates(current):
            if candidate in visited:
                continue
            visited.add(candidate)
            if not _source_valid(candidate, target):
                continue
            outcomes = evaluate(candidate, source, target)
            if all(_outcome_signature(outcomes[name]) == signatures[name] for name in SHAPES):
                current, changed = candidate, True
                break
    return current


def _artifact(
    args: argparse.Namespace, case_seed: int, index: int, sql: str, minimized: str, outcomes: dict[str, Any]
) -> dict[str, Any]:
    version = getattr(sqlglot_runtime, "__version__", "unavailable")
    return {
        "schema": SCHEMA,
        "id": _failure_id(args, index),
        "seed": args.seed,
        "case_index": index,
        "case_seed": case_seed,
        "sqlglot_version": version,
        "source_dialect": args.source_dialect,
        "target_dialect": args.target_dialect,
        "input_sql": sql,
        "minimized_sql": minimized,
        "failing_shapes": [name for name in SHAPES if outcomes[name]["status"] == "fail"],
        "outcomes": outcomes,
        "replay_command": REPLAY_COMMAND_TEMPLATE,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_failure_payload(data: object, args: argparse.Namespace) -> tuple[list[str], dict[str, Any]]:
    required = {
        "schema",
        "id",
        "seed",
        "case_index",
        "case_seed",
        "sqlglot_version",
        "source_dialect",
        "target_dialect",
        "input_sql",
        "minimized_sql",
        "failing_shapes",
        "outcomes",
        "replay_command",
    }
    if not isinstance(data, dict) or not required <= set(data) or data["schema"] != SCHEMA:
        raise ValueError("failure artifact schema or required field mismatch")
    for key in ("seed", "source_dialect", "target_dialect"):
        if data[key] != getattr(args, key):
            raise ValueError(f"failure artifact metadata mismatch for {key}")
    if data["sqlglot_version"] != getattr(sqlglot_runtime, "__version__", "unavailable"):
        raise ValueError("failure artifact sqlglot_version mismatch")
    failing = data["failing_shapes"]
    index = data["case_index"]
    outcomes = data["outcomes"]
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or not isinstance(data["case_seed"], int)
        or data["id"] != _failure_id(args, index)
        or data["case_seed"] != data["seed"] + index
        or data["replay_command"] != REPLAY_COMMAND_TEMPLATE
        or not isinstance(data["input_sql"], str)
        or not data["input_sql"]
        or not isinstance(data["minimized_sql"], str)
        or not data["minimized_sql"]
        or not isinstance(failing, list)
        or not failing
        or not set(failing) <= set(SHAPES)
        or not isinstance(outcomes, dict)
        or set(outcomes) != set(SHAPES)
    ):
        raise ValueError("failure artifact fields are invalid")
    recorded_failures: set[str] = set()
    for name in SHAPES:
        outcome = outcomes[name]
        if not isinstance(outcome, dict) or outcome.get("status") not in ("pass", "fail"):
            raise ValueError("failure artifact outcome is invalid")
        if outcome["status"] == "fail":
            if not isinstance(outcome.get("error_type"), str) or not isinstance(outcome.get("error"), str):
                raise ValueError("failure artifact failing outcome lacks an exact error signature")
            recorded_failures.add(name)
        elif outcome.get("error_type") is not None or outcome.get("error") is not None:
            raise ValueError("failure artifact passing outcome has an error signature")
    if set(failing) != recorded_failures:
        raise ValueError("failure artifact failing_shapes disagrees with outcomes")
    if not _source_valid(data["minimized_sql"], args.target_dialect):
        raise ValueError("failure artifact minimized_sql is not valid in both source dialects")
    return cast(list[str], failing), cast(dict[str, Any], outcomes)


def _validate_advisory_evidence(failure_path: Path, summary_path: Path, args: argparse.Namespace) -> None:
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    _validate_failure_payload(failure, args)
    if (
        not isinstance(summary, dict)
        or summary.get("schema") != SUMMARY_SCHEMA
        or summary.get("status") != "failure"
        or summary.get("case_index") != failure.get("case_index")
        or not isinstance(summary.get("cases_executed"), int)
        or summary["cases_executed"] < 1
    ):
        raise ValueError("summary evidence is incomplete")
    for key in ("seed", "source_dialect", "target_dialect"):
        if summary.get(key) != getattr(args, key) or failure.get(key) != getattr(args, key):
            raise ValueError(f"advisory evidence metadata mismatch for {key}")


def _summary(args: argparse.Namespace, status: str, cases_executed: int, **extra: Any) -> dict[str, Any]:
    return {
        "schema": SUMMARY_SCHEMA,
        "status": status,
        "cases_executed": cases_executed,
        "seed": args.seed,
        "source_dialect": args.source_dialect,
        "target_dialect": args.target_dialect,
        "sqlglot_version": getattr(sqlglot_runtime, "__version__", "unavailable"),
        **extra,
    }


def _failure_id(args: argparse.Namespace, index: int) -> str:
    return f"{args.source_dialect}-to-{args.target_dialect}-seed-{args.seed}-case-{index}"


def _load_replay(path: Path, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        failing, outcomes_recorded = _validate_failure_payload(data, args)
        outcomes = evaluate(data["minimized_sql"], args.source_dialect, args.target_dialect)
        reproduced = bool(failing) and all(
            all(
                outcomes[name].get(key) == outcomes_recorded[name].get(key) for key in ("status", "error_type", "error")
            )
            for name in SHAPES
        )
        current_failed = {name for name in SHAPES if outcomes[name]["status"] == "fail"}
        status = "reproduced" if reproduced else ("still_failing_changed" if current_failed else "resolved")
        exit_status = 1 if reproduced else (3 if current_failed else 0)
        return exit_status, _summary(args, status, 1, outcomes=outcomes)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"replay artifact error: {_error(exc)}", file=sys.stderr)
        return 2, _summary(args, "error", 0, error=_error(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--source-dialect", required=True)
    parser.add_argument("--target-dialect", required=True)
    parser.add_argument("--failure-artifact", type=Path, required=True)
    parser.add_argument("--replay", type=Path, help="Explicit failure artifact to replay")
    parser.add_argument("--validate-advisory-evidence", action="store_true")
    parser.add_argument("--cases", type=int, default=1024)
    parser.add_argument("--deadline-seconds", type=float, default=300)
    parser.add_argument("--summary-artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.source_dialect not in ("postgres", args.target_dialect):
            raise ValueError("source-dialect must be postgres or target-dialect")
        if args.cases <= 0 or args.deadline_seconds <= 0:
            raise ValueError("cases and deadline-seconds must be positive")
        if args.replay and not args.replay.is_file():
            raise ValueError("--replay path does not exist or is not a regular file")
        if args.validate_advisory_evidence:
            if args.summary_artifact is None:
                raise ValueError("--validate-advisory-evidence requires --summary-artifact")
            _validate_advisory_evidence(args.failure_artifact, args.summary_artifact, args)
            return 0
        if args.replay is not None:
            status, summary = _load_replay(args.replay, args)
            if args.summary_artifact:
                _write_json(args.summary_artifact, summary)
            return status
        if sqlglot_runtime is None:
            raise RuntimeError("SQLGlot is required")
        started, seen, executed, attempts = mono_time(), set(), 0, 0
        attempt_cap = max(args.cases * 20, args.cases + 100)
        while executed < args.cases and attempts < attempt_cap:
            if elapsed_seconds(started) >= args.deadline_seconds:
                break
            index = attempts
            attempts += 1
            case_seed, sql = generate_case(args.seed, index)
            if sql in seen:
                continue
            seen.add(sql)
            executed += 1
            outcomes = evaluate(sql, args.source_dialect, args.target_dialect)
            if any(outcome["status"] == "fail" for outcome in outcomes.values()):
                failing = {name for name in SHAPES if outcomes[name]["status"] == "fail"}
                minimized = shrink(
                    sql,
                    args.source_dialect,
                    args.target_dialect,
                    failing,
                    deadline_started=started,
                    deadline_seconds=args.deadline_seconds,
                )
                _write_json(
                    args.failure_artifact,
                    _artifact(
                        args,
                        case_seed,
                        index,
                        sql,
                        minimized,
                        evaluate(minimized, args.source_dialect, args.target_dialect),
                    ),
                )
                if args.summary_artifact:
                    _write_json(
                        args.summary_artifact, _summary(args, "failure", executed, attempts=attempts, case_index=index)
                    )
                    _validate_advisory_evidence(args.failure_artifact, args.summary_artifact, args)
                return 1
        complete = executed == args.cases
        incomplete_status = (
            "deadline_incomplete" if elapsed_seconds(started) >= args.deadline_seconds else "attempt_cap_incomplete"
        )
        if args.summary_artifact:
            _write_json(
                args.summary_artifact,
                _summary(
                    args,
                    "clean" if complete else incomplete_status,
                    executed,
                    requested_cases=args.cases,
                    attempts=attempts,
                ),
            )
        return 0 if complete else 2
    except Exception as exc:
        print(f"generator infrastructure error: {_error(exc)}", file=sys.stderr)
        if args.summary_artifact:
            try:
                _write_json(args.summary_artifact, _summary(args, "error", 0, error=_error(exc)))
            except Exception as summary_exc:
                print(f"summary artifact error: {_error(summary_exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
