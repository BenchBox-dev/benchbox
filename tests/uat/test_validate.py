from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.uat.phases import validate

pytestmark = pytest.mark.medium


def _minimal_bundle(
    *,
    platform: str = "duckdb",
    benchmark: str = "tpch",
    compliance_class: str | None = None,
) -> dict:
    benchmark_payload = {"id": benchmark, "scale_factor": 0.01}
    if compliance_class is not None:
        benchmark_payload["compliance_class"] = compliance_class
    return {
        "version": "2.0",
        "run": {
            "id": "uat-validate-test",
            "timestamp": "2026-05-21T00:00:00Z",
            "total_duration_ms": 100.0,
        },
        "benchmark": benchmark_payload,
        "platform": {"name": platform},
        "summary": {"validation": "passed", "queries": {"total": 22, "passed": 22, "failed": 0}},
        "phases": {"validation": {"status": "PASSED"}},
        "queries": [{"id": f"Q{i}", "ms": 1.0} for i in range(1, 23)],
    }


def _write_bundle(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_tsv(path: Path, statuses: list[str]) -> None:
    lines = [validate.TSV_HEADER]
    for i, status in enumerate(statuses):
        lines.append(f"duckdb\ttpch\t0.01\t/tmp/r{i}.json\t{status}\t0\t0\t")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_run_validate_writes_rollup_and_computes_counts_without_subprocess(tmp_path: Path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    _write_bundle(results_dir / "clean.json", _minimal_bundle())
    _write_bundle(results_dir / "warning.json", _minimal_bundle(platform="MysteryDB"))
    _write_bundle(results_dir / "error.json", {"version": "2.0"})
    _write_bundle(
        results_dir / "refused.json",
        _minimal_bundle(benchmark="tpcds", compliance_class="unofficial_subscale"),
    )

    out = validate.run_validate(results_dir, output_tsv=tmp_path / "rollup.tsv", floor=0.80)

    assert out.clean_count == 1
    assert out.warning_count == 1
    assert out.error_count == 1
    assert out.refused_count == 1
    assert out.total == 4
    assert out.clean_rate == pytest.approx(1 / 3)
    assert out.floor_breached is True
    assert out.exit_code() == 1

    text = out.rollup_tsv_path.read_text(encoding="utf-8")
    assert "clean.json\tclean\t0\t0" in text
    assert "warning.json\twarning_only\t0\t1" in text
    assert "error.json\terror\t" in text
    assert "refused.json\trefused-by-cli\t0\t0\tcompliance_class=unofficial_subscale" in text
    assert "# overall: total=4 submittable=3 clean=1" in text
    assert "# platform=duckdb: total=2 submittable=1 clean=1 validator_clean_rate=100.0%" in text
    assert "# platform=MysteryDB: total=1 submittable=1 clean=0 validator_clean_rate=0.0%" in text
    assert "# benchmark=tpch: total=2 submittable=2 clean=1 validator_clean_rate=50.0%" in text
    assert "# benchmark=tpcds: total=1 submittable=0 clean=0 validator_clean_rate=n/a" in text


def test_run_validate_all_clean_passes_floor(tmp_path: Path):
    result_path = _write_bundle(tmp_path / "clean.json", _minimal_bundle())

    out = validate.run_validate([result_path], output_tsv=tmp_path / "rollup.tsv", floor=0.80)

    assert out.clean_count == 1
    assert out.clean_rate == 1.0
    assert out.floor_breached is False
    assert out.exit_code() == 0


def test_run_validate_excludes_refused_from_denominator(tmp_path: Path):
    clean = _write_bundle(tmp_path / "clean.json", _minimal_bundle())
    refused = _write_bundle(
        tmp_path / "refused.json",
        _minimal_bundle(benchmark="tpcds", compliance_class="unofficial_nonstandard"),
    )

    out = validate.run_validate([clean, refused], output_tsv=tmp_path / "rollup.tsv", floor=0.80)

    assert out.refused_count == 1
    assert out.clean_rate == 1.0
    assert out.exit_code() == 0


def test_run_validate_missing_results_dir_returns_aborted_result(tmp_path: Path):
    out = validate.run_validate(
        tmp_path / "missing-results",
        output_tsv=tmp_path / "rollup.tsv",
    )

    assert out.aborted is True
    assert out.exit_code() == 2
    assert "results path not found" in (out.abort_reason or "")


def test_run_validate_explicit_missing_file_is_error_row(tmp_path: Path):
    out = validate.run_validate([tmp_path / "missing.json"], output_tsv=tmp_path / "rollup.tsv")

    assert out.aborted is False
    assert out.error_count == 1
    assert out.exit_code() == 1
    assert "File not found" in out.rollup_tsv_path.read_text(encoding="utf-8")


def test_run_validate_overwrites_stale_rollup(tmp_path: Path):
    stale_tsv = tmp_path / "rollup.tsv"
    stale_tsv.write_text("stale\n", encoding="utf-8")
    result_path = _write_bundle(tmp_path / "clean.json", _minimal_bundle())

    out = validate.run_validate([result_path], output_tsv=stale_tsv)

    assert out.exit_code() == 0
    assert stale_tsv.read_text(encoding="utf-8").startswith(validate.TSV_HEADER)
    assert "stale" not in stale_tsv.read_text(encoding="utf-8")


def test_parse_validator_status_by_path_round_trip(tmp_path: Path):
    tsv = tmp_path / "rollup.tsv"
    _write_tsv(tsv, ["clean", "warning_only", "error"])
    status_by_path = validate.parse_validator_status_by_path(tsv)
    keys = {str(p) for p in status_by_path}
    assert str(Path("/tmp/r0.json").resolve()) in keys
    assert status_by_path[Path("/tmp/r0.json").resolve()] == "clean"
    assert status_by_path[Path("/tmp/r1.json").resolve()] == "warning_only"
    assert status_by_path[Path("/tmp/r2.json").resolve()] == "error"


def test_parse_validator_status_by_path_normalizes_relative_paths(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tsv = tmp_path / "rollup.tsv"
    tsv.write_text(
        f"{validate.TSV_HEADER}\nduckdb\ttpch\t0.01\tbenchmark_runs/results/r0.json\terror\t0\t0\t\n",
        encoding="utf-8",
    )

    status_by_path = validate.parse_validator_status_by_path(tsv)

    assert status_by_path == {(tmp_path / "benchmark_runs" / "results" / "r0.json").resolve(): "error"}


def test_parse_validator_status_by_path_handles_missing_columns(tmp_path: Path):
    tsv = tmp_path / "rollup.tsv"
    tsv.write_text("foo\tbar\n", encoding="utf-8")
    assert validate.parse_validator_status_by_path(tsv) == {}


_HARNESS_STATEMENT = "SET threads TO 1"
_PRIVATE_STATEMENT = "COPY orders FROM '/home/runner/private/orders.csv'"


def _export_local_result(directory: Path, statement: str) -> Path:
    from datetime import datetime

    from benchbox.core.results.exporter import ResultExporter
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    result = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="duckdb",
        execution_id="uat-validate-privacy",
        timestamp=datetime(2026, 10, 9, 12, 0, 0),
        duration_seconds=3.0,
        total_queries=1,
        successful_queries=1,
        query_results=[{"query_id": "Q1", "execution_time": 1.0, "status": "SUCCESS", "rows_returned": 4}],
        validation_status="PASSED",
        validation_details={},
        applied_tuning_ledger={
            "status": "not_applicable",
            "statements": [{"statement": statement, "phase": "session", "status": "harness_default"}],
        },
    )
    return ResultExporter(output_dir=directory, anonymize=False).export_result(result, ["json"])["json"]


_CLICKHOUSE_DDL = "CREATE TABLE orders (o_id Int64) ENGINE = MergeTree() ORDER BY o_id"


@pytest.mark.parametrize("statement", [_HARNESS_STATEMENT, _CLICKHOUSE_DDL, _PRIVATE_STATEMENT])
def test_run_validate_checks_redacted_publish_form_of_local_tuning_statements(tmp_path: Path, statement: str):
    from benchbox.validation.bundle import validate_bundles

    source = _export_local_result(tmp_path / "results", statement)
    assert any("unanonymized tuning" in error for error in validate_bundles([source])[0].errors)

    out = validate.run_validate(source.parent, output_tsv=tmp_path / "rollup.tsv", floor=0.0)

    text = out.rollup_tsv_path.read_text(encoding="utf-8")
    assert "unanonymized tuning" not in text
    assert statement not in text
    assert str(source) in text


def test_run_validate_still_rejects_unredactable_bundle_with_tuning_statements(tmp_path: Path, monkeypatch):
    bundle = _minimal_bundle()
    bundle["platform"]["tuning"] = {
        "applied": {"statements": [{"statement": _PRIVATE_STATEMENT, "phase": "ddl", "status": "executed"}]}
    }
    _write_bundle(tmp_path / "raw.json", bundle)
    monkeypatch.setattr(validate, "_redacted_publish_source", lambda path: (None, None))

    out = validate.run_validate(tmp_path, output_tsv=tmp_path / "rollup.tsv", floor=0.0)

    assert out.error_count == 1
    assert "unanonymized tuning statement" in out.rollup_tsv_path.read_text(encoding="utf-8")


def test_run_validate_redacts_companion_only_tuning_statements(tmp_path: Path):
    source = _export_local_result(tmp_path / "results", "SET threads TO 1")
    payload = json.loads(source.read_text(encoding="utf-8"))
    applied = payload["platform"]["tuning"].pop("applied")
    payload["platform"].pop("tuning")
    source.write_text(json.dumps(payload), encoding="utf-8")
    companion = source.with_name(f"{source.stem}.applied.json")
    companion.write_text(json.dumps({"statements": applied["statements"]}), encoding="utf-8")

    out = validate.run_validate(source.parent, output_tsv=tmp_path / "rollup.tsv", floor=0.0)

    assert "unanonymized tuning" not in out.rollup_tsv_path.read_text(encoding="utf-8")
