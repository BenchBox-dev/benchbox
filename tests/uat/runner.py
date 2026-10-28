from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from benchbox.core.results.query_status import documented_exclusions_cover_failures
from benchbox.core.results.submit_classification import (
    SubmitTerminalState,
    classify_result_path,
)
from tests.uat.artifact_hygiene import (
    assert_no_local_growth,
    configured_external_root,
    snapshot_local_runs,
)
from tests.uat.matrix import benchbox_run_argv, benchbox_run_official_argv
from tests.uat.throughput import resolve_official_result_path, validate_throughput_result
from tests.uat.timeouts import TimeoutResult, run_with_timeout

__all__ = [
    "CellResult",
    "LOAD_FAILURE_MARKER",
    "SubmitTerminalState",
    "classify_for_submit",
    "submit_state_is_cell_failure",
]

LOAD_FAILURE_MARKER = "BENCHBOX_LOAD_FAILURE_JSON="


@dataclass(frozen=True)
class CellResult:
    platform: str
    benchmark: str
    scale: float
    status: str
    exit_code: int
    elapsed_s: float
    log_path: Path
    result_path: Path | None
    submit_terminal_state: str = SubmitTerminalState.submittable.value
    throughput_check: str | None = None
    load_failure_path: Path | None = None


def last_nonempty_output_line(log_text: str) -> str | None:
    for line in reversed(log_text.splitlines()):
        stripped = line.strip()
        if stripped and not stripped.startswith("# "):
            return stripped
    return None


def classify_for_submit(result_json: Path | str | None) -> SubmitTerminalState:
    return classify_result_path(result_json)


def _failures_are_documented_exclusions(result_path: Path | None) -> bool:
    if result_path is None:
        return False
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not documented_exclusions_cover_failures(data):
        return False
    return all(
        str(row.get("status")).upper() == "FAILED"
        for row in data.get("queries", [])
        if isinstance(row, dict)
        and str(row.get("run_type") or "measurement").lower() == "measurement"
        and str(row.get("status")).upper() not in {"SUCCESS", "SKIPPED"}
    )


def submit_state_is_cell_failure(state: SubmitTerminalState | str) -> bool:
    normalized = state.value if isinstance(state, SubmitTerminalState) else str(state)
    return normalized in {
        SubmitTerminalState.query_failure.value,
        SubmitTerminalState.schema_violation.value,
        SubmitTerminalState.bundle_load_error.value,
        SubmitTerminalState.missing_manifest.value,
    }


def _default_log_path(log_dir: Path, platform: str, benchmark: str, scale: float, now: _dt.datetime) -> Path:
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    return log_dir / f"{platform}_{benchmark}_{scale}_{timestamp}.log"


DIAGNOSTIC_RERUN_TIMEOUT_S = 180


def _append_diagnostic_rerun(log_fh, argv: list[str], *, timeout_s: int, env: dict[str, str]) -> None:
    log_fh.write("[uat] verbose diagnostic re-run (--quiet suppressed the original error):\n")
    log_fh.flush()
    try:
        rerun = run_with_timeout(
            argv,
            timeout_s=timeout_s,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        text = _decode_process_output(rerun.stdout)
        if text:
            log_fh.write(text if text.endswith("\n") else text + "\n")
        log_fh.write(f"[uat] diagnostic re-run exit_code={rerun.exit_code} timed_out={rerun.timed_out}\n")
    except Exception as exc:
        log_fh.write(f"[uat] diagnostic re-run error {type(exc).__name__}: {exc}\n")


def _extract_load_failure(log_text: str) -> dict[str, object] | None:

    for line in log_text.splitlines():
        if not line.startswith(LOAD_FAILURE_MARKER):
            continue
        raw_payload = line[len(LOAD_FAILURE_MARKER) :].strip()
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _extract_load_failure_from_path(log_path: Path) -> dict[str, object] | None:

    try:
        with log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith(LOAD_FAILURE_MARKER):
                    continue
                raw_payload = line[len(LOAD_FAILURE_MARKER) :].strip()
                try:
                    payload = json.loads(raw_payload)
                except (TypeError, ValueError):
                    return None
                return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _write_load_failure_sidecar(
    *,
    log_path: Path,
    platform: str,
    benchmark: str,
    scale: float,
    payload: dict[str, object],
) -> Path:

    sidecar = log_path.with_suffix(".load_failure.json")
    artifact = dict(payload)
    artifact.update(
        {
            "platform": platform,
            "benchmark": benchmark,
            "scale": scale,
            "log_path": str(log_path),
        }
    )
    temp_path = sidecar.with_name(sidecar.name + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as fh:
            json.dump(artifact, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, sidecar)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return sidecar


def _materialize_load_failure_sidecar(
    *,
    log_path: Path,
    platform: str,
    benchmark: str,
    scale: float,
    runs_dir: Path,
    result_path: Path | None,
) -> tuple[Path | None, Path | None]:

    payload = _extract_load_failure_from_path(log_path)
    if payload is None:
        return result_path, None

    marker_result = payload.get("result_json")
    if marker_result:
        candidate = _resolve_result_path(str(marker_result), runs_dir)
        if candidate.suffix.lower() == ".json" and candidate.exists():
            result_path = candidate
            payload["result_json"] = str(candidate)
        else:
            payload["result_json"] = None
    else:
        payload["result_json"] = str(result_path) if result_path is not None else None
    sidecar = _write_load_failure_sidecar(
        log_path=log_path,
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        payload=payload,
    )
    return result_path, sidecar


def _diagnostic_rerun_argv(
    *,
    official: bool,
    platform: str,
    benchmark: str,
    scale: float,
    phases: str,
    compression: str | None,
    runs_dir: Path,
    extra_args,
    local_managed_platform: bool,
    streams: int | None,
    seed: int | None,
) -> list[str]:
    output_args = ("--output", str(runs_dir / "datagen"), *extra_args)
    if official:
        return benchbox_run_official_argv(
            platform,
            benchmark,
            scale,
            phases=phases,
            streams=streams,
            seed=seed,
            extra_args=output_args,
            local_managed_platform=local_managed_platform,
            quiet=False,
        )
    return benchbox_run_argv(
        platform,
        benchmark,
        scale,
        phases=phases,
        compression=compression,
        extra_args=output_args,
        local_managed_platform=local_managed_platform,
        quiet=False,
    )


def _resolve_cell_result_path(
    *,
    official: bool,
    stdout_text: str,
    runs_dir: Path,
) -> Path | None:
    if official:
        result_path = resolve_official_result_path(
            runs_dir / "results",
            emitted_path=last_nonempty_output_line(stdout_text),
        )
        if result_path is not None and (result_path.suffix.lower() != ".json" or not result_path.exists()):
            return None
        return result_path

    result_path_str = last_nonempty_output_line(stdout_text)
    result_path = _resolve_result_path(result_path_str, runs_dir) if result_path_str else None
    if result_path is not None and (result_path.suffix.lower() != ".json" or not result_path.exists()):
        return None
    return result_path


def run_cell(
    platform: str,
    benchmark: str,
    scale: float,
    *,
    timeout_s: int = 600,
    phases: str = "load,power",
    compression: str | None = None,
    log_dir: Path | str | None = None,
    benchmark_runs_dir: Path | str | None = None,
    extra_args=(),
    local_managed_platform: bool = False,
    official: bool = False,
    streams: int | None = None,
    seed: int | None = None,
    now: _dt.datetime | None = None,
) -> CellResult:
    if official and streams is None:
        raise ValueError("run_cell(official=True) requires `streams`")
    now = now or _dt.datetime.now()
    log_dir = Path(log_dir) if log_dir is not None else _default_log_dir(now)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = _default_log_path(log_dir, platform, benchmark, scale, now)
    runs_dir = _default_benchmark_runs_dir() if benchmark_runs_dir is None else Path(benchmark_runs_dir).expanduser()
    external_root = configured_external_root(output=runs_dir)
    local_snapshot = snapshot_local_runs() if external_root is not None else None
    if official:
        argv = benchbox_run_official_argv(
            platform,
            benchmark,
            scale,
            phases=phases,
            streams=streams,
            seed=seed,
            extra_args=("--output", str(runs_dir / "datagen"), *extra_args),
            local_managed_platform=local_managed_platform,
        )
    else:
        argv = benchbox_run_argv(
            platform,
            benchmark,
            scale,
            phases=phases,
            compression=compression,
            extra_args=("--output", str(runs_dir / "datagen"), *extra_args),
            local_managed_platform=local_managed_platform,
        )

    with log_path.open("w", encoding="utf-8") as log_fh:
        log_fh.write(f"# {' '.join(argv)}\n")
        env = os.environ.copy()
        env["BENCHBOX_OUTPUT_DIR"] = str(runs_dir)
        log_fh.write(f"# BENCHBOX_OUTPUT_DIR={runs_dir}\n")
        log_fh.flush()
        stderr_start_size = log_path.stat().st_size
        timeout_result = run_with_timeout(
            argv,
            timeout_s=timeout_s,
            stdout=subprocess.PIPE,
            stderr=log_fh,
            env=env,
        )
        log_fh.flush()
        stderr_has_content = _log_region_has_nonempty_content(log_path, stderr_start_size)
        stdout_text = _decode_process_output(timeout_result.stdout)
        stderr_text = _decode_process_output(timeout_result.stderr)
        if stderr_text:
            log_fh.write(stderr_text)
            stderr_has_content = stderr_has_content or bool(stderr_text.strip())
        if stdout_text:
            log_fh.write(stdout_text)
        if timeout_result.timed_out:
            log_fh.write(f"# UAT_TIMEOUT timeout_s={timeout_s} exit_code={timeout_result.exit_code}\n")
        elif timeout_result.exit_code != 0 and not stdout_text.strip() and not stderr_has_content:
            rerun_argv = _diagnostic_rerun_argv(
                official=official,
                platform=platform,
                benchmark=benchmark,
                scale=scale,
                phases=phases,
                compression=compression,
                runs_dir=runs_dir,
                extra_args=extra_args,
                local_managed_platform=local_managed_platform,
                streams=streams,
                seed=seed,
            )
            _append_diagnostic_rerun(
                log_fh,
                rerun_argv,
                timeout_s=min(timeout_s, DIAGNOSTIC_RERUN_TIMEOUT_S),
                env=env,
            )

    if external_root is not None and local_snapshot is not None:
        assert_no_local_growth(local_snapshot, external_root)

    result_path = _resolve_cell_result_path(
        official=official,
        stdout_text=stdout_text,
        runs_dir=runs_dir,
    )

    result_path, load_failure_path = _materialize_load_failure_sidecar(
        log_path=log_path,
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        runs_dir=runs_dir,
        result_path=result_path,
    )
    status = _classify(timeout_result)
    submit_state = classify_for_submit(result_path) if result_path is not None else SubmitTerminalState.missing_manifest
    exit_code = timeout_result.exit_code
    if status == "passed" and submit_state_is_cell_failure(submit_state):
        status = "failed"
        exit_code = exit_code or 1
    elif (
        status == "failed"
        and submit_state is SubmitTerminalState.query_failure
        and _failures_are_documented_exclusions(result_path)
    ):
        status = "passed"
        exit_code = 0
    throughput_check: str | None = None
    if official and streams is not None:
        if result_path is not None and result_path.exists():
            try:
                result_json = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                throughput_check = f"could not read result JSON: {exc}"
                if status == "passed":
                    status = "failed"
                    exit_code = exit_code or 1
            else:
                ok, reason = validate_throughput_result(
                    result_json,
                    requested_streams=streams,
                    platform=platform,
                    benchmark=benchmark,
                    scale=scale,
                )
                throughput_check = reason
                if not ok and status == "passed":
                    status = "failed"
                    exit_code = exit_code or 1
        elif status == "timed-out":
            throughput_check = "not validated: cell timed out"
        else:
            throughput_check = "no result JSON resolved for throughput validation"
            if status == "passed":
                status = "failed"
                exit_code = exit_code or 1
    return CellResult(
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        status=status,
        exit_code=exit_code,
        elapsed_s=timeout_result.elapsed_s,
        log_path=log_path,
        result_path=result_path,
        submit_terminal_state=submit_state.value,
        throughput_check=throughput_check,
        load_failure_path=load_failure_path,
    )


def _resolve_result_path(result_path_str: str, runs_dir: Path) -> Path:
    path = Path(result_path_str).expanduser()
    if path.is_absolute() or path.exists():
        return path
    if len(path.parts) >= 2 and path.parts[0] == "benchmark_runs":
        return runs_dir.parent / path
    return runs_dir / path


def _decode_process_output(output: object) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def _log_region_has_nonempty_content(path: Path, start_offset: int, *, chunk_size: int = 65536) -> bool:
    with path.open("rb") as fh:
        fh.seek(start_offset)
        while chunk := fh.read(chunk_size):
            if chunk.strip():
                return True
    return False


def _classify(timeout_result: TimeoutResult) -> str:
    if timeout_result.timed_out:
        return "timed-out"
    if timeout_result.exit_code == 0:
        return "passed"
    return "failed"


def _default_log_dir(now: _dt.datetime) -> Path:
    runs_root = Path(
        os.environ.get(
            "BENCHBOX_OUTPUT_DIR",
            str(Path.home() / "Developer" / "benchmark_runs"),
        )
    )
    return runs_root / "logs" / f"uat_{now.strftime('%Y%m%d')}"


def _default_benchmark_runs_dir() -> Path:
    return Path(
        os.environ.get(
            "BENCHBOX_OUTPUT_DIR",
            str(Path.home() / "Developer" / "benchmark_runs"),
        )
    ).expanduser()
