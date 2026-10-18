from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.uat import _cli as uat_cli
from tests.uat.gate_summary import PhaseAccounting
from tests.uat.phases import report
from tests.uat.runner import CellResult, SubmitTerminalState

pytestmark = pytest.mark.medium

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "report-accounting-skipped-unreachable-cells.jsonl"


def _cell(
    platform: str,
    benchmark: str,
    scale: float,
    status: str = "passed",
    submit_terminal_state: str = SubmitTerminalState.submittable.value,
) -> CellResult:
    return CellResult(
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        status=status,
        exit_code=0 if status in {"passed", "skipped", "skipped-unreachable"} else 1,
        elapsed_s=1.0 if status in {"passed", "failed", "timed-out"} else 0.0,
        log_path=Path(f"/tmp/{platform}_{benchmark}_{scale}.log"),
        result_path=Path(f"/tmp/{platform}_{benchmark}_{scale}.json") if status == "passed" else None,
        submit_terminal_state=submit_terminal_state,
    )


def test_report_cli_reconciles_skipped_and_unreachable_fixture(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(FIXTURE), "--output-tsv", str(output_tsv)])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 5
    assert payload["attempted"] == 3
    assert payload["skipped"] == 1
    assert payload["unreachable"] == 1
    assert payload["unreachable_is_estimated"] is True
    assert payload["startup_failed"] == 0
    assert payload["total_defined"] == 5
    assert payload["passed"] + payload["failed"] + payload["timed_out"] == payload["attempted"]
    assert (
        payload["attempted"] + payload["skipped"] + payload["unreachable"] + payload["startup_failed"]
        == payload["total_defined"]
    )

    text = output_tsv.read_text(encoding="utf-8")
    assert "attempted=3 skipped=1 unreachable=1 startup_failed=0 died_mid_platform=0 total_defined=5" in text
    assert "# UNREACHABLE_CELLS=1 release_gate_attention=required" in text


def test_report_counts_execute_unreachable_cells_outside_rows(tmp_path: Path):
    summary = report.write_report(
        [
            _cell("duckdb", "tpch", 0.01, status="passed"),
            _cell("duckdb", "tpch", 0.1, status="failed"),
            _cell("duckdb", "tpch", 1.0, status="timed-out"),
        ],
        output_path=tmp_path / "matrix_summary.tsv",
        compatibility_pruned_count=2,
        early_stop_pruned_count=1,
        skipped_unreachable_count=4,
    )

    assert summary.attempted_count == 3
    assert summary.skipped_count == 3
    assert summary.unreachable_count == 4
    assert summary.total_defined_count == 10
    assert summary.candidate_count == 10

    text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert (
        "compatibility_pruned=2 early_stop_pruned=1 attempted=3 skipped=3 "
        "unreachable=4 startup_failed=0 died_mid_platform=0 total_defined=10"
    ) in text
    assert "# UNREACHABLE_CELLS=4 release_gate_attention=required" in text


def test_report_threads_startup_failed_count_into_total_defined_and_exit_code(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
        skipped_unreachable_count=2,
        startup_failed_count=3,
    )

    assert summary.unreachable_count == 2
    assert summary.startup_failed_count == 3
    assert summary.total_defined_count == 6
    assert summary.exit_code() == 1

    text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "unreachable=2 startup_failed=3 died_mid_platform=0 total_defined=6" in text
    assert "release_accounting" in text and "startup_failed=3" in text
    assert "# STARTUP_FAILED_CELLS=3 release_gate_attention=required" in text


def test_report_exit_code_clean_when_startup_failed_count_zero(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
    )
    assert summary.startup_failed_count == 0
    assert summary.exit_code() == 0
    text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "STARTUP_FAILED_CELLS" not in text


def test_report_cli_reads_startup_failed_sidecar(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "passed",
                "exit_code": 0,
                "elapsed_s": 1.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cells_jsonl.with_name("cells.jsonl.accounting.json").write_text(
        json.dumps({"skipped_unreachable_count": 0, "startup_failed_count": 2}), encoding="utf-8"
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["startup_failed"] == 2
    assert payload["unreachable"] == 0
    assert payload["attempted"] == 1
    assert payload["total_defined"] == 3
    text = output_tsv.read_text(encoding="utf-8")
    assert "# STARTUP_FAILED_CELLS=2 release_gate_attention=required" in text


def test_report_cli_reads_skipped_unreachable_sidecar(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        "\n".join(
            json.dumps(
                {
                    "platform": "duckdb",
                    "benchmark": "tpch",
                    "scale": 0.01,
                    "status": "passed",
                    "exit_code": 0,
                    "elapsed_s": 1.0,
                    "log_path": "/tmp/a.log",
                    "result_path": "/tmp/a.json",
                }
            )
            for _ in range(2)
        )
        + "\n",
        encoding="utf-8",
    )
    cells_jsonl.with_name("cells.jsonl.accounting.json").write_text(
        json.dumps({"skipped_unreachable_count": 3}), encoding="utf-8"
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["unreachable"] == 3
    assert payload["unreachable_is_estimated"] is False
    assert payload["attempted"] == 2
    assert payload["total_defined"] == 5
    text = output_tsv.read_text(encoding="utf-8")
    assert "# UNREACHABLE_CELLS=3 release_gate_attention=required" in text


def test_report_cli_without_sidecar_defaults_unreachable_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "passed",
                "exit_code": 0,
                "elapsed_s": 1.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["unreachable"] == 0
    assert payload["unreachable_is_estimated"] is True
    assert payload["total_defined"] == 1


def test_report_cli_malformed_sidecar_falls_back_to_estimated(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "passed",
                "exit_code": 0,
                "elapsed_s": 1.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sidecar = cells_jsonl.with_name(cells_jsonl.name + ".accounting.json")
    sidecar.write_text(
        json.dumps({"skipped_unreachable_count": None, "startup_failed_count": "abc"}),
        encoding="utf-8",
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["unreachable"] == 0
    assert payload["startup_failed"] == 0
    assert payload["unreachable_is_estimated"] is True
    assert "unreachable_is_estimated=true" in output_tsv.read_text(encoding="utf-8")


def test_report_cli_valid_zero_sidecar_is_not_estimated(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "passed",
                "exit_code": 0,
                "elapsed_s": 1.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cells_jsonl.with_name("cells.jsonl.accounting.json").write_text(
        json.dumps({"skipped_unreachable_count": 0, "startup_failed_count": 0}), encoding="utf-8"
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    assert uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["unreachable_is_estimated"] is False


def test_report_cli_non_mapping_sidecar_treated_as_absent(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "passed",
                "exit_code": 0,
                "elapsed_s": 1.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sidecar = cells_jsonl.with_name(cells_jsonl.name + ".accounting.json")
    sidecar.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["unreachable"] == 0
    assert payload["unreachable_is_estimated"] is True


def test_write_report_exit_code_zero_when_everything_attempted_passed(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
    )
    assert summary.exit_code() == 0


def test_write_report_exit_code_nonzero_on_fail_count(tmp_path: Path):
    summary = report.write_report(
        [
            _cell("duckdb", "tpch", 0.01, status="passed"),
            _cell("duckdb", "tpch", 0.1, status="failed"),
        ],
        output_path=tmp_path / "matrix_summary.tsv",
    )
    assert summary.fail_count == 1
    assert summary.exit_code() == 1


def test_write_report_exit_code_nonzero_on_timeout_count(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="timed-out")],
        output_path=tmp_path / "matrix_summary.tsv",
    )
    assert summary.timeout_count == 1
    assert summary.exit_code() == 1


def test_write_report_exit_code_nonzero_on_unreachable_count(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
        skipped_unreachable_count=2,
    )
    assert summary.unreachable_count == 2
    assert summary.fail_count == 0
    assert summary.timeout_count == 0
    assert summary.exit_code() == 1


def test_write_report_exit_code_aborted_still_wins_over_clean_run(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
        run_status="ABORTED",
    )
    assert summary.exit_code() == 2


def test_write_report_threads_registry_pruned_count_into_total_defined(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
        compatibility_pruned_count=2,
        registry_pruned_count=3,
    )

    assert summary.registry_pruned_count == 3
    assert summary.skipped_count == 5
    assert summary.total_defined_count == 6

    text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "registry_pruned=3" in text


def test_write_report_counts_unvalidated_cells_without_affecting_exit_code(tmp_path: Path):
    summary = report.write_report(
        [
            _cell("polars-df", "tpch", 0.01, status="passed", submit_terminal_state="unvalidated"),
            _cell("datafusion-df", "tpch", 0.01, status="passed", submit_terminal_state="unvalidated"),
            _cell("duckdb", "tpch", 0.01, status="passed"),
        ],
        output_path=tmp_path / "matrix_summary.tsv",
    )

    assert summary.unvalidated_count == 2
    assert summary.pass_count == 3
    assert summary.attempted_count == 3
    assert summary.total_defined_count == 3
    assert summary.exit_code() == 0

    text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "# UNVALIDATED_CELLS=2 release_gate_attention=required" in text


def test_write_report_omits_unvalidated_footer_line_when_zero(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
    )
    assert summary.unvalidated_count == 0
    text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "UNVALIDATED_CELLS" not in text


def test_write_report_unvalidated_count_ignores_non_passed_cells(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="failed", submit_terminal_state="unvalidated")],
        output_path=tmp_path / "matrix_summary.tsv",
    )
    assert summary.unvalidated_count == 0
    assert summary.fail_count == 1


def test_phase_accounting_carries_unvalidated_and_defaults_to_zero():
    default = PhaseAccounting()
    assert default.unvalidated == 0

    accounting = PhaseAccounting(attempted=5, passed=5, total_defined=5, unvalidated=2)
    assert accounting.unvalidated == 2
    from dataclasses import asdict

    assert asdict(accounting)["unvalidated"] == 2


def test_report_cli_marks_failed_cell_with_result_path_not_submittable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "failed",
                "exit_code": 1,
                "elapsed_s": 2.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
                "submit_terminal_state": "submittable",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 1
    text = output_tsv.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "failed\tfailed:submittable\t" in lines[1]
    capsys.readouterr()


def test_report_cli_reads_throughput_check_back_from_cells_jsonl(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "failed",
                "exit_code": 1,
                "elapsed_s": 2.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
                "submit_terminal_state": "submittable",
                "throughput_check": "throughput stream count mismatch: requested 3, executed 1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 1
    lines = output_tsv.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t")[-1] == "throughput_check"
    assert lines[1].split("\t")[-1] == "throughput stream count mismatch: requested 3, executed 1"
    capsys.readouterr()


def test_report_cli_throughput_check_defaults_to_none_when_absent(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_jsonl.write_text(
        json.dumps(
            {
                "platform": "duckdb",
                "benchmark": "tpch",
                "scale": 0.01,
                "status": "passed",
                "exit_code": 0,
                "elapsed_s": 1.0,
                "log_path": "/tmp/a.log",
                "result_path": "/tmp/a.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_tsv = tmp_path / "matrix_summary.tsv"

    exit_code = uat_cli.main(["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(output_tsv)])

    assert exit_code == 0
    lines = output_tsv.read_text(encoding="utf-8").splitlines()
    assert lines[1].split("\t")[-1] == ""
    capsys.readouterr()


def test_report_threads_died_mid_platform_count_into_total_defined_and_exit_code(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
        died_mid_platform_count=171,
    )

    assert summary.died_mid_platform_count == 171
    assert summary.total_defined_count == 172
    assert summary.exit_code() == 1

    text = (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
    assert "startup_failed=0 died_mid_platform=171 total_defined=172" in text
    assert "# DIED_MID_PLATFORM_CELLS=171 release_gate_attention=required" in text


def test_report_exit_code_clean_when_died_mid_platform_count_zero(tmp_path: Path):
    summary = report.write_report(
        [_cell("duckdb", "tpch", 0.01, status="passed")],
        output_path=tmp_path / "matrix_summary.tsv",
        died_mid_platform_count=0,
    )
    assert summary.exit_code() == 0
    assert "DIED_MID_PLATFORM_CELLS" not in (tmp_path / "matrix_summary.tsv").read_text(encoding="utf-8")
