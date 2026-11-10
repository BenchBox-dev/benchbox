from __future__ import annotations

import json
from pathlib import Path

PASS = "pass"
TIMEOUT = "timeout"
OOM = "oom"
DOWNLOAD = "download"
SINGLESTORE_1712 = "singlestore_1712"
WRONG_RESULTS = "wrong_results"
CRASH = "crash"
ABSORBED = "absorbed_timeout"


def cell_row(platform: str, benchmark: str, scale: float, kind: str) -> dict:
    row: dict = {
        "platform": platform,
        "benchmark": benchmark,
        "scale": scale,
        "status": "passed",
        "terminal_state": "passed",
        "submit_terminal_state": "submittable",
        "exit_code": 0,
        "failure_tail": "",
        "failed_queries": [],
        "attempt": 1,
        "retry_signature": None,
        "retry_outcome": None,
    }
    failed = {"status": "failed", "exit_code": 1}
    if kind == TIMEOUT:
        row.update(status="timed-out", terminal_state="timeout", exit_code=124)
    elif kind == OOM:
        row.update(status="failed", terminal_state="killed", exit_code=137)
    elif kind == DOWNLOAD:
        row.update(failed, terminal_state="no_json_nonzero", failure_tail="DownloadError: download failed after 3")
    elif kind == SINGLESTORE_1712:
        row.update(
            failed,
            terminal_state="failed:query_failure",
            submit_terminal_state="query_failure",
            failed_queries=[{"query_id": "Q1", "status": "ERROR", "error": "ERROR 1712 (HY000): Leaf Error"}],
        )
    elif kind == WRONG_RESULTS:
        row.update(
            failed,
            terminal_state="failed:query_failure",
            submit_terminal_state="query_failure",
            failed_queries=[{"query_id": "Q1", "status": "ERROR", "error": "row count mismatch"}],
        )
    elif kind == CRASH:
        row.update(failed, terminal_state="no_json_nonzero", failure_tail="Traceback: KeyError")
    elif kind == ABSORBED:
        row.update(attempt=2, retry_signature="timeout", retry_outcome="retry_passed")
    return row


def write_run(
    history: Path,
    name: str,
    completed_at: str,
    rows: list[dict],
    *,
    verdict: str = "green",
    dry_run: bool = False,
    validator_errors: tuple[tuple[str, str, float], ...] = (),
) -> Path:
    run_dir = history / name
    run_dir.mkdir(parents=True)
    (run_dir / "cells.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (run_dir / "uat_gate_summary.json").write_text(
        json.dumps({"completed_at": completed_at, "verdict": verdict, "dry_run": dry_run}), encoding="utf-8"
    )
    if validator_errors:
        header = "platform\tbenchmark\tscale\tresult_path\tvalidator_status\terror_count\twarning_count\tfirst_error\n"
        lines = [f"{p}\t{b}\t{s}\t/x.json\terror\t1\t0\tbad\n" for p, b, s in validator_errors]
        (run_dir / "validator_rollup.tsv").write_text(header + "".join(lines), encoding="utf-8")
    return run_dir


def write_history(history: Path, cells: dict[tuple[str, str, float], list[str]]) -> None:
    runs = max(len(kinds) for kinds in cells.values())
    for index in range(runs):
        rows = [
            cell_row(platform, benchmark, scale, kinds[index])
            for (platform, benchmark, scale), kinds in cells.items()
            if index < len(kinds)
        ]
        failed = any(row["status"] != "passed" for row in rows)
        write_run(
            history,
            f"run-{index:02d}",
            f"2026-10-{index + 1:02d}T00:00:00+00:00",
            rows,
            verdict="red" if failed else "green",
        )
