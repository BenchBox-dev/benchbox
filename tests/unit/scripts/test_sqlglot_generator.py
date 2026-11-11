from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = ROOT / "_project" / "sqlglot-upstream" / "repros" / "generator.py"


@pytest.fixture()
def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sqlglot_generator", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(generator: ModuleType, tmp_path: Path, *extra: str) -> tuple[list[str], object]:
    argv = [
        "--seed",
        "20260831",
        "--source-dialect",
        "postgres",
        "--target-dialect",
        "duckdb",
        "--failure-artifact",
        str(tmp_path / "failure.json"),
        *extra,
    ]
    return argv, generator.build_parser().parse_args(argv)


def _pass_outcomes(generator: ModuleType) -> dict[str, dict[str, object]]:
    return {
        "target_to_target": {"status": "pass", "error": None, "error_type": None},
        "postgres_to_target": {"status": "pass", "error": None, "error_type": None},
    }


def _failure_outcomes(generator: ModuleType, error_type: str = "ParseError") -> dict[str, dict[str, object]]:
    outcomes = _pass_outcomes(generator)
    outcomes["target_to_target"] = {
        "status": "fail",
        "error": f"{error_type}: synthetic",
        "error_type": error_type,
    }
    return outcomes


def _write_failure(generator: ModuleType, args: object, outcomes: dict[str, dict[str, object]]) -> dict[str, object]:
    artifact = generator._artifact(args, args.seed, 0, "SELECT 1", "SELECT 1", outcomes)
    generator._write_json(args.failure_artifact, artifact)
    return artifact


def test_evaluate_calls_both_shapes_with_strict_translation(
    generator: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, bool]] = []

    def translate(sql: str, target: str, *, source_dialect: str, strict: bool) -> str:
        calls.append((source_dialect, target, strict))
        return sql

    monkeypatch.setattr(generator, "translate_sql_query", translate)
    monkeypatch.setattr(generator.sqlglot_runtime, "parse_one", lambda sql, read: object())

    outcomes = generator.evaluate("SELECT 1", "postgres", "duckdb")

    assert calls == [("duckdb", "duckdb", True), ("postgres", "duckdb", True)]
    assert set(outcomes) == set(generator.SHAPES)


def test_top_level_import_failure_uses_infrastructure_exit() -> None:
    script = f"""
import builtins
import runpy

real_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name == 'benchbox.utils.clock':
        raise ImportError('synthetic bootstrap failure')
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked_import
runpy.run_path({str(GENERATOR_PATH)!r}, run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "generator infrastructure import error" in result.stderr


def test_campaign_executes_requested_unique_cases_deterministically(
    generator: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv, _ = _args(
        generator,
        tmp_path,
        "--cases",
        "1024",
        "--deadline-seconds",
        "300",
        "--summary-artifact",
        str(tmp_path / "summary.json"),
    )
    observed: list[str] = []

    def evaluate(sql: str, source: str, target: str) -> dict[str, dict[str, object]]:
        observed.append(sql)
        return _pass_outcomes(generator)

    monkeypatch.setattr(generator, "evaluate", evaluate)

    assert generator.main(argv) == 0
    assert len(observed) == len(set(observed)) == 1024
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "clean"
    assert summary["cases_executed"] == 1024
    assert summary["attempts"] >= 1024


def test_equivalent_failure_artifacts_are_byte_identical(generator: ModuleType, tmp_path: Path) -> None:
    _, args = _args(generator, tmp_path)
    outcomes = _failure_outcomes(generator)
    artifact = generator._artifact(args, args.seed + 7, 7, "SELECT 1", "SELECT 1", outcomes)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    generator._write_json(first, artifact)
    generator._write_json(second, copy.deepcopy(artifact))

    assert first.read_bytes() == second.read_bytes()


def test_shrink_skips_invalid_candidates_and_preserves_error_signature(
    generator: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluated: list[str] = []

    def evaluate(sql: str, source: str, target: str) -> dict[str, dict[str, object]]:
        evaluated.append(sql)
        if "BROKEN" in sql:
            return _failure_outcomes(generator, "SQLTranslationError")
        return _pass_outcomes(generator)

    monkeypatch.setattr(generator, "evaluate", evaluate)
    monkeypatch.setattr(generator, "_shrunk_candidates", lambda sql: ["INVALID", "SELECT BROKEN", "SELECT 1"])
    monkeypatch.setattr(generator, "_source_valid", lambda sql, target: sql != "INVALID")

    minimized = generator.shrink("SELECT BROKEN FROM t", "postgres", "duckdb", {"target_to_target"})

    assert minimized == "SELECT BROKEN"
    assert "INVALID" not in evaluated


def test_shrink_cannot_cycle_between_equivalent_candidates(
    generator: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def evaluate(sql: str, source: str, target: str) -> dict[str, dict[str, object]]:
        nonlocal calls
        calls += 1
        return _failure_outcomes(generator)

    monkeypatch.setattr(generator, "evaluate", evaluate)
    monkeypatch.setattr(generator, "_source_valid", lambda sql, target: True)

    minimized = generator.shrink("SELECT id FROM t", "postgres", "duckdb", {"target_to_target"})

    assert minimized
    assert calls <= 10


def test_deadline_incomplete_is_infrastructure_error(
    generator: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv, _ = _args(
        generator,
        tmp_path,
        "--cases",
        "2",
        "--deadline-seconds",
        "1",
        "--summary-artifact",
        str(tmp_path / "summary.json"),
    )
    monkeypatch.setattr(generator, "mono_time", lambda: 0.0)
    monkeypatch.setattr(generator, "elapsed_seconds", lambda started: 2.0)

    assert generator.main(argv) == 2
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "deadline_incomplete"
    assert summary["cases_executed"] == 0


@pytest.mark.parametrize("failure_site", ["evaluate", "write"])
def test_unexpected_runtime_and_writer_errors_never_use_discovery_exit(
    generator: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_site: str
) -> None:
    argv, _ = _args(generator, tmp_path, "--cases", "1")
    if failure_site == "evaluate":
        monkeypatch.setattr(generator, "evaluate", lambda *unused: (_ for _ in ()).throw(RuntimeError("boom")))
    else:
        monkeypatch.setattr(generator, "evaluate", lambda *unused: _failure_outcomes(generator))
        monkeypatch.setattr(generator, "shrink", lambda *unused: "SELECT 1")
        monkeypatch.setattr(generator, "_write_json", lambda *unused: (_ for _ in ()).throw(OSError("read-only")))

    assert generator.main(argv) == 2


def test_replay_distinguishes_exact_reproduction_from_changed_signature(
    generator: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv, args = _args(
        generator,
        tmp_path,
        "--summary-artifact",
        str(tmp_path / "summary.json"),
        "--replay",
        str(tmp_path / "failure.json"),
    )
    _write_failure(generator, args, _failure_outcomes(generator, "ParseError"))
    monkeypatch.setattr(generator, "_source_valid", lambda sql, target: True)
    monkeypatch.setattr(generator, "evaluate", lambda *unused: _failure_outcomes(generator, "ParseError"))

    assert generator.main(argv) == 1
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["status"] == "reproduced"

    monkeypatch.setattr(generator, "evaluate", lambda *unused: _failure_outcomes(generator, "SQLTranslationError"))
    assert generator.main(argv) == 3
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["status"] == "still_failing_changed"


def test_explicit_missing_replay_is_infrastructure_error(generator: ModuleType, tmp_path: Path) -> None:
    argv, _ = _args(generator, tmp_path, "--replay", str(tmp_path / "missing.json"))

    assert generator.main(argv) == 2


def test_advisory_evidence_validation_fails_closed(
    generator: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv, args = _args(
        generator,
        tmp_path,
        "--summary-artifact",
        str(tmp_path / "summary.json"),
        "--validate-advisory-evidence",
    )
    _write_failure(generator, args, _failure_outcomes(generator))
    generator._write_json(args.summary_artifact, generator._summary(args, "failure", 1, case_index=0))

    assert generator.main(argv) == 0
    summary = json.loads(args.summary_artifact.read_text(encoding="utf-8"))
    summary["seed"] += 1
    generator._write_json(args.summary_artifact, summary)
    assert generator.main(argv) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "wrong"),
        ("id", "case-wrong"),
        ("case_seed", -1),
        ("sqlglot_version", "0.0.0"),
        ("replay_command", "python generator.py"),
        ("failing_shapes", ["postgres_to_target"]),
    ],
)
def test_replay_rejects_inconsistent_artifact_metadata(
    generator: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    _, args = _args(generator, tmp_path)
    artifact = _write_failure(generator, args, _failure_outcomes(generator))
    artifact[field] = value
    generator._write_json(args.failure_artifact, artifact)
    monkeypatch.setattr(generator, "_source_valid", lambda sql, target: True)

    assert generator._load_replay(args.failure_artifact, args)[0] == 2


@pytest.mark.parametrize(
    "option,value",
    [
        ("--source-dialect", "sqlite"),
        ("--cases", "0"),
        ("--deadline-seconds", "0"),
    ],
)
def test_invalid_campaign_contract_is_infrastructure_error(
    generator: ModuleType, tmp_path: Path, option: str, value: str
) -> None:
    argv, _ = _args(generator, tmp_path)
    if option in argv:
        argv[argv.index(option) + 1] = value
    else:
        argv.extend((option, value))

    assert generator.main(argv) == 2


