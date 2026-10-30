from __future__ import annotations

import datetime as _dt
import json
import os
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from tests.uat.phases.report import atomic_write_text, terminal_state
from tests.uat.runner import CellResult

FAILURE_TAIL_LINES = 50
FAILURE_TAIL_CHARS = 12_000
FAILED_QUERY_MAX_COUNT = 50
FAILED_QUERY_ERROR_CHARS = 500
_NON_FAILED_QUERY_STATUSES = frozenset({"SUCCESS", "SKIPPED", "PASSED", "OK"})

CELLS_FINALIZED_SUFFIX = ".finalized"
CELLS_INPROGRESS_SUFFIX = ".inprogress"


class SourceInfo(Protocol):
    commit_sha: str
    commit_short_sha: str
    dirty: bool


def cells_accounting_path(cells_jsonl: Path) -> Path:
    return cells_jsonl.with_name(cells_jsonl.name + ".accounting.json")


def cells_finalized_path(cells_jsonl: Path) -> Path:
    return cells_jsonl.with_name(cells_jsonl.name + CELLS_FINALIZED_SUFFIX)


def cells_inprogress_path(cells_jsonl: Path) -> Path:
    return cells_jsonl.with_name(cells_jsonl.name + CELLS_INPROGRESS_SUFFIX)


def _render_cell_row(cell: CellResult, *, source_info: SourceInfo) -> str:
    cell_terminal_state = terminal_state(cell)
    failed_queries = _failed_query_details(cell)
    failure_tail = _persist_cell_failure_context(
        cell, terminal_state=cell_terminal_state, failed_queries=failed_queries
    )
    return (
        json.dumps(
            {
                "platform": cell.platform,
                "benchmark": cell.benchmark,
                "scale": cell.scale,
                "status": cell.status,
                "terminal_state": cell_terminal_state,
                "submit_terminal_state": cell.submit_terminal_state,
                "timed_out": cell.status == "timed-out",
                "exit_code": cell.exit_code,
                "elapsed_s": cell.elapsed_s,
                "log_path": str(cell.log_path),
                "result_path": (str(cell.result_path) if cell.result_path else None),
                "load_failure_path": (str(cell.load_failure_path) if cell.load_failure_path else None),
                "throughput_check": cell.throughput_check,
                "failure_tail": failure_tail,
                "failed_queries": failed_queries,
                "source_commit_sha": source_info.commit_sha,
                "source_commit_short_sha": source_info.commit_short_sha,
                "source_dirty": source_info.dirty,
            }
        )
        + "\n"
    )


class CellStreamWriter:
    def __init__(self, path: Path, *, source_info: SourceInfo) -> None:
        self._path = path
        self._source_info = source_info
        self.count = 0
        path.unlink(missing_ok=True)
        cells_finalized_path(path).unlink(missing_ok=True)
        write_cells_inprogress_marker(path)

    def append(self, cell: CellResult) -> None:
        line = _render_cell_row(cell, source_info=self._source_info)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self.count += 1


def write_cells_inprogress_marker(cells_jsonl: Path) -> Path:
    marker = cells_inprogress_path(cells_jsonl)
    atomic_write_text(
        marker,
        json.dumps({"started_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}) + "\n",
    )
    return marker


def write_cells_finalized_marker(cells_jsonl: Path, *, row_count: int) -> Path:
    marker = cells_finalized_path(cells_jsonl)
    atomic_write_text(
        marker,
        json.dumps(
            {
                "finalized_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "row_count": int(row_count),
            }
        )
        + "\n",
    )
    cells_inprogress_path(cells_jsonl).unlink(missing_ok=True)
    return marker


def cells_are_finalized(cells_jsonl: Path) -> bool:
    return cells_finalized_path(cells_jsonl).exists()


def cells_run_incomplete(cells_jsonl: Path) -> bool:
    return cells_inprogress_path(cells_jsonl).exists() and not cells_finalized_path(cells_jsonl).exists()


def read_cells_finalized_marker(cells_jsonl: Path) -> dict[str, object] | None:
    marker = cells_finalized_path(cells_jsonl)
    if not marker.exists():
        return None
    try:
        with marker.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_cells_jsonl(
    path: Path,
    cells: Iterable[CellResult],
    *,
    source_info: SourceInfo,
    skipped_unreachable_count: int = 0,
    startup_failed_count: int = 0,
    died_mid_platform_count: int = 0,
    compatibility_pruned_count: int = 0,
    early_stop_pruned_count: int = 0,
    registry_pruned_count: int = 0,
    disk_gate_disabled: bool = False,
    memory_gate_disabled: bool = False,
    container_engine: str | None = None,
    finalize: bool = True,
) -> None:
    lines = [_render_cell_row(cell, source_info=source_info) for cell in cells]
    atomic_write_text(path, "".join(lines))

    accounting_path = cells_accounting_path(path)
    accounting_text = (
        json.dumps(
            {
                "skipped_unreachable_count": int(skipped_unreachable_count),
                "startup_failed_count": int(startup_failed_count),
                "died_mid_platform_count": int(died_mid_platform_count),
                "compatibility_pruned_count": int(compatibility_pruned_count),
                "early_stop_pruned_count": int(early_stop_pruned_count),
                "registry_pruned_count": int(registry_pruned_count),
                "disk_gate_disabled": bool(disk_gate_disabled),
                "memory_gate_disabled": bool(memory_gate_disabled),
                "container_engine": container_engine,
            }
        )
        + "\n"
    )
    atomic_write_text(accounting_path, accounting_text)

    if finalize:
        write_cells_finalized_marker(path, row_count=len(lines))


def read_cells_jsonl(path: Path) -> list[CellResult]:
    cells: list[CellResult] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            payload = json.loads(line)
            cells.append(
                CellResult(
                    platform=payload["platform"],
                    benchmark=payload["benchmark"],
                    scale=float(payload["scale"]),
                    status=payload["status"],
                    exit_code=int(payload.get("exit_code", 0)),
                    elapsed_s=float(payload.get("elapsed_s", 0.0)),
                    log_path=Path(payload.get("log_path", "")),
                    result_path=(Path(payload["result_path"]) if payload.get("result_path") else None),
                    submit_terminal_state=payload.get("submit_terminal_state", "submittable"),
                    throughput_check=payload.get("throughput_check"),
                    load_failure_path=(
                        Path(payload["load_failure_path"]) if payload.get("load_failure_path") else None
                    ),
                )
            )
    return cells


def read_accounting_sidecar(cells_jsonl: Path) -> tuple[dict[str, object], bool]:
    sidecar = cells_accounting_path(cells_jsonl)
    if not sidecar.exists():
        return {}, False
    try:
        with sidecar.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return {}, False
        return payload, True
    except (OSError, ValueError, TypeError):
        return {}, False


def coerce_accounting_count(value: object, default: int = 0) -> int:
    return coerce_accounting_count_with_validity(value, default)[0]


def coerce_accounting_count_with_validity(value: object, default: int = 0) -> tuple[int, bool]:
    try:
        return int(value), True
    except (TypeError, ValueError):
        return default, False


def read_skipped_unreachable_sidecar(cells_jsonl: Path) -> tuple[int, bool]:
    payload, present = read_accounting_sidecar(cells_jsonl)
    return coerce_accounting_count(payload.get("skipped_unreachable_count", 0)), present


def _failed_query_details(cell: CellResult) -> list[dict[str, str]]:
    if cell.status == "passed" or cell.result_path is None:
        return []
    try:
        data = json.loads(Path(cell.result_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    errors_by_query: dict[str, str] = {}
    for entry in data.get("errors") or []:
        if isinstance(entry, dict) and entry.get("phase") == "query":
            message = str(entry.get("message") or "").strip()
            if message:
                errors_by_query.setdefault(str(entry.get("query_id", "")), message)
    details: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").strip().upper() in _NON_FAILED_QUERY_STATUSES:
            continue
        query_id = str(row.get("id") if row.get("id") is not None else row.get("query_id", ""))
        error = errors_by_query.get(query_id, "")
        details.append(
            {
                "query_id": query_id,
                "status": str(row.get("status") or ""),
                "error": error[:FAILED_QUERY_ERROR_CHARS],
            }
        )
        if len(details) >= FAILED_QUERY_MAX_COUNT:
            break
    return details


def _persist_cell_failure_context(
    cell: CellResult, *, terminal_state: str, failed_queries: list[dict[str, str]] | None = None
) -> str:
    if cell.status == "passed" and cell.result_path is not None:
        return ""
    log_path = Path(cell.log_path)
    tail = _cell_log_tail(log_path)
    if log_path.exists():
        if not _cell_log_has_marker(log_path):
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"# UAT_TERMINAL_STATE terminal_state={terminal_state} "
                    f"status={cell.status} exit_code={cell.exit_code} "
                    f"result_path={cell.result_path or ''}\n"
                )
                for failed in failed_queries or ():
                    error = " ".join(failed["error"].split())
                    fh.write(f"# UAT_FAILED_QUERY id={failed['query_id']} status={failed['status']} error={error}\n")
                fh.write(f"# UAT_FAILURE_TAIL_START max_lines={FAILURE_TAIL_LINES}\n")
                fh.write((tail or "(no subprocess output captured)") + "\n")
                fh.write("# UAT_FAILURE_TAIL_END\n")
    return tail


def _cell_log_tail(log_path: Path) -> str:
    if not log_path.exists():
        return ""
    lines: deque[str] = deque(maxlen=FAILURE_TAIL_LINES)
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if line.startswith("# UAT_"):
                break
            if line.startswith("# "):
                continue
            if line.strip():
                lines.append(line)
    tail = "\n".join(lines)
    if len(tail) > FAILURE_TAIL_CHARS:
        return tail[-FAILURE_TAIL_CHARS:]
    return tail


def _cell_log_has_marker(log_path: Path) -> bool:
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("# UAT_TERMINAL_STATE "):
                return True
    return False
