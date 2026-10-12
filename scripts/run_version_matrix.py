#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import psutil

from benchbox.utils.clock import elapsed_seconds, mono_time
from scripts.version_matrix_specs import GIB, SPECS, Cell, MatrixSpec, round_order, scale_argument, scale_token

RUNTIME_DISTRIBUTIONS = ("polars-runtime-32", "polars-runtime-64", "polars-runtime-compat")
OOM_EXIT_CODES = (-9, 137)
CRITICAL_PRESSURE = 4
CRITICAL_SAMPLES = 3

CLI_DESCRIPTION = (
    "Run a reproducible engine version matrix.\n"
    "\n"
    "The matrix is operator-run: it generates each benchmark dataset once, then\n"
    "loads and measures it with every package version in the selected engine spec.\n"
    "Each power cell is a separate BenchBox invocation, repeated per the spec.\n"
    "Artifacts stay outside the checkout and are recorded in ``matrix-manifest.json``;\n"
    "analyze them with ``scripts/analyze_version_matrix.py``.\n"
    "\n"
    "Run from the BenchBox checkout with ``uv run --no-sync`` because the runner\n"
    "changes the installed engine package between subprocesses while the project\n"
    "lock stays unchanged.\n"
)


@dataclass(frozen=True)
class Step:
    kind: str
    benchmark: str | None = None
    scale: float | None = None
    version: str | None = None
    repetition: int | None = None


def plan_steps(spec: MatrixSpec) -> list[Step]:
    steps = [Step("generate", benchmark, scale) for benchmark, scale in spec.benchmarks]
    for version in spec.versions:
        steps.append(Step("install", version=version))
        for benchmark, scale in spec.benchmarks:
            steps.append(Step("load", benchmark, scale, version))
            steps.extend(
                Step("power", benchmark, scale, version, repetition) for repetition in range(1, spec.repetitions + 1)
            )
    return steps


def _command_text(command: list[str]) -> str:
    return shlex.join(command)


def _append_log(log_path: Path, text: str) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    dry_run: bool,
) -> tuple[int, str, float]:
    if dry_run:
        print(f"$ {_command_text(command)}")
        return 0, "", 0.0

    _append_log(log_path, f"$ {_command_text(command)}\n")
    started = mono_time()
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    elapsed = elapsed_seconds(started)
    output = (completed.stdout or "") + (completed.stderr or "")
    _append_log(log_path, output)
    _append_log(log_path, f"[exit={completed.returncode} elapsed_s={elapsed:.3f}]\n")
    return completed.returncode, output, elapsed


def _find_result_path(output: str, *, cwd: Path) -> Path:
    for line in reversed(output.splitlines()):
        candidate = Path(line.strip())
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if candidate.is_file() and candidate.suffix == ".json":
            return candidate.resolve()
    raise RuntimeError("BenchBox completed without emitting an existing result JSON path")


def install_command(spec: MatrixSpec, version: str) -> list[str]:
    return [
        "uv",
        "pip",
        "install",
        "--prerelease=allow",
        "--python",
        sys.executable,
        f"{spec.driver_package}=={version}",
    ]


def _clear_databases(spec: MatrixSpec, output_dir: Path, benchmark: str, scale: float) -> None:
    database_dir = output_dir / "databases" / f"{benchmark}_sf{scale_token(scale)}"
    if not database_dir.exists():
        return
    for suffix in spec.cleared_database_suffixes:
        for path in database_dir.glob(f"*{suffix}"):
            if path.is_file() or path.is_symlink():
                path.unlink()


def benchbox_command(
    spec: MatrixSpec,
    *,
    phase: str,
    benchmark: str,
    scale: float,
    version: str | None = None,
) -> list[str]:
    command = [
        "uv",
        "run",
        "--no-sync",
        "--",
        "benchbox",
        "run",
        "--non-interactive",
        "--quiet",
        "--compression",
        spec.compression,
        "--phases",
        phase,
        "--benchmark",
        benchmark,
        "--scale",
        scale_argument(scale),
    ]
    if phase != "generate":
        command[7:7] = ["--platform", spec.platform]
    command.extend(spec.driver_platform_options(version))
    return command


def _load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Result bundle is not an object: {path}")
    return payload


def _record(
    *,
    output_dir: Path,
    result_path: Path,
    phase: str,
    benchmark: str,
    scale: float,
    requested_version: str | None,
    repetition: int | None,
    elapsed_s: float,
) -> dict[str, Any]:
    payload = _load_payload(result_path)
    platform = payload.get("platform") if isinstance(payload.get("platform"), dict) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    queries = summary.get("queries") if isinstance(summary.get("queries"), dict) else {}
    return {
        "path": result_path.relative_to(output_dir).as_posix(),
        "phase": phase,
        "benchmark": benchmark,
        "scale": scale,
        "requested_version": requested_version,
        "repetition": repetition,
        "elapsed_s": round(elapsed_s, 3),
        "platform_version": (
            execution.get("driver_version_resolved")
            or execution.get("driver_resolved_version")
            or platform.get("client_version")
            or platform.get("version")
        ),
        "client_version": platform.get("client_version"),
        "driver_resolved_version": execution.get("driver_version_resolved") or execution.get("driver_resolved_version"),
        "driver_actual_version": execution.get("driver_version_actual") or execution.get("driver_actual_version"),
        "validation": summary.get("validation"),
        "query_total": queries.get("total"),
        "query_failed": queries.get("failed"),
    }


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_freeze(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in text.splitlines():
        name, separator, version = line.strip().partition("==")
        if separator:
            packages[name.lower().replace("_", "-")] = version
    return packages


def verify_installation(packages: dict[str, str], version: str) -> dict[str, Any]:
    if packages.get("polars") != version:
        raise RuntimeError(f"expected polars=={version}, found {packages.get('polars')}")
    runtimes = {name: value for name, value in packages.items() if name.startswith("polars-runtime-")}
    stale = {name: value for name, value in runtimes.items() if value != version}
    if stale:
        raise RuntimeError(f"stale Polars runtime distributions for {version}: {stale}")
    return {"polars": version, "runtimes": runtimes, "pyarrow": packages.get("pyarrow")}


def check_pins(expected: dict[str, Any], current: dict[str, Any]) -> None:
    changed = sorted(key for key in expected if expected[key] != current.get(key))
    if changed:
        raise RuntimeError(f"pinned value changed mid-matrix: {', '.join(changed)}")


def classify_outcome(
    spec: MatrixSpec,
    *,
    exit_code: int,
    timed_out: bool,
    safety_abort: bool,
    result_ok: bool,
    peak_rss_gib: float,
    swap_growth_gib: float,
) -> str:
    if safety_abort:
        return "resource-policy-exceeded"
    if timed_out:
        return "timeout"
    if exit_code in OOM_EXIT_CODES:
        return "oom-killed"
    if exit_code != 0 or not result_ok:
        return "execution-failure"
    if peak_rss_gib > spec.rss_limit_gib or swap_growth_gib > spec.swap_limit_gib:
        return "resource-policy-exceeded"
    return "completed"


def generate_command(benchmark: str, scale: float) -> list[str]:
    return [
        "uv",
        "run",
        "--no-sync",
        "--",
        "benchbox",
        "run",
        "--non-interactive",
        "--quiet",
        "--phases",
        "generate",
        "--benchmark",
        benchmark,
        "--scale",
        scale_argument(scale),
    ]


def cell_options(spec: MatrixSpec, benchmark: str, scale: float, engine: str) -> list[str]:
    return [
        "--platform",
        spec.platform,
        "--benchmark",
        benchmark,
        "--scale",
        scale_argument(scale),
        "--tuning",
        "notuning",
        "--ignore-memory-warnings",
        "--platform-option",
        "rechunk=false",
        "--execution-engine",
        engine,
    ]


def prime_command(spec: MatrixSpec, benchmark: str, scale: float) -> list[str]:
    base = ["uv", "run", "--no-sync", "--", "benchbox", "run", "--non-interactive", "--quiet", "--phases", "load"]
    return [*base, *cell_options(spec, benchmark, scale, "default")]


def cell_command(spec: MatrixSpec, cell: Cell) -> list[str]:
    base = [
        "uv",
        "run",
        "--no-sync",
        "--",
        "benchbox",
        "run",
        "--non-interactive",
        "--quiet",
        "--iterations",
        str(spec.iterations),
    ]
    return [*base, *cell_options(spec, cell.benchmark, spec.scales[cell.benchmark], cell.engine)]


def plan_lines(spec: MatrixSpec, *, shuffle_seed: int, first_round: int, rounds: int) -> list[str]:
    lines = [
        f"# {rounds} rounds x {len(spec.cells())} cells = {rounds * len(spec.cells())} invocations "
        f"(shuffle seed {shuffle_seed}, first round {first_round})"
    ]
    lines.extend(f"$ {shlex.join(generate_command(name, scale))}" for name, scale in spec.benchmarks)
    lines.extend(f"$ {shlex.join(prime_command(spec, name, scale))}" for name, scale in spec.benchmarks)
    for round_index in range(first_round, first_round + rounds):
        order = round_order(spec, shuffle_seed, round_index)
        for position, cell in enumerate(order, start=1):
            lines.append(
                f"round {round_index} position {position}/{len(order)} {cell.cell_id}: install polars=={cell.version}; "
                f"$ {shlex.join(cell_command(spec, cell))}"
            )
    return lines


def run_matrix(spec: MatrixSpec, *, output_dir: Path, repo_root: Path, dry_run: bool) -> dict[str, Any]:
    if output_dir == repo_root or repo_root in output_dir.parents:
        raise ValueError(f"output directory must be outside the checkout: {output_dir}")
    if not dry_run:
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError(f"output directory must be new or empty: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "matrix.log"
    env = os.environ.copy()
    env["BENCHBOX_OUTPUT_DIR"] = str(output_dir)
    env["PYTHONUNBUFFERED"] = "1"
    records: list[dict[str, Any]] = []

    for step in plan_steps(spec):
        if step.kind == "install":
            assert step.version is not None
            status, output, _ = _run_command(
                install_command(spec, step.version), cwd=repo_root, env=env, log_path=log_path, dry_run=dry_run
            )
            if status:
                raise RuntimeError(
                    f"Could not install {spec.display_name} {step.version}; see {log_path}\n{output[-2000:]}"
                )
            continue
        assert step.benchmark is not None and step.scale is not None
        if step.kind == "load" and not dry_run:
            _clear_databases(spec, output_dir, step.benchmark, step.scale)
        command = benchbox_command(
            spec, phase=step.kind, benchmark=step.benchmark, scale=step.scale, version=step.version
        )
        status, output, elapsed_s = _run_command(command, cwd=repo_root, env=env, log_path=log_path, dry_run=dry_run)
        if status:
            raise RuntimeError(
                f"BenchBox {step.kind} failed for {step.benchmark} v{step.version or '-'}; see {log_path}"
            )
        if dry_run:
            continue
        records.append(
            _record(
                output_dir=output_dir,
                result_path=_find_result_path(output, cwd=repo_root),
                phase=step.kind,
                benchmark=step.benchmark,
                scale=step.scale,
                requested_version=step.version,
                repetition=step.repetition,
                elapsed_s=elapsed_s,
            )
        )
        with (output_dir / "matrix-records.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(records[-1], sort_keys=True) + "\n")

    manifest = {
        "schema_version": "1",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "versions": list(spec.versions),
        "benchmarks": [{"id": name, "scale": scale} for name, scale in spec.benchmarks],
        "repetitions": spec.repetitions,
        "compression": spec.compression,
        "expected_runs": spec.expected_benchmark_runs,
        "records": records,
    }
    if not dry_run:
        _write_json(output_dir / "matrix-manifest.json", manifest)
    return manifest


def _run_logged(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path) -> tuple[int, str, float]:
    _append_log(log_path, f"$ {shlex.join(command)}\n")
    started = mono_time()
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    elapsed = elapsed_seconds(started)
    output = (completed.stdout or "") + (completed.stderr or "")
    _append_log(log_path, output)
    _append_log(log_path, f"[exit={completed.returncode} elapsed_s={elapsed:.3f}]\n")
    return completed.returncode, completed.stdout or "", elapsed


def _find_optional_result_path(output: str, *, cwd: Path) -> Path | None:
    for line in reversed(output.splitlines()):
        candidate = Path(line.strip())
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if candidate.is_file() and candidate.suffix == ".json":
            return candidate.resolve()
    return None


def swap_out_bytes() -> int:
    page = os.sysconf("SC_PAGE_SIZE")
    text = subprocess.run(["vm_stat"], capture_output=True, text=True, check=False).stdout
    for line in text.splitlines():
        if line.startswith("Swapouts"):
            return int(line.split(":")[1].strip().rstrip(".")) * page
    return 0


def pressure_level() -> int:
    text = subprocess.run(
        ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return int(text) if text else 1


def directory_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def host_state(path: Path) -> dict[str, Any]:
    load = os.getloadavg()
    return {
        "load_1m": round(load[0], 2),
        "pressure_level": pressure_level(),
        "free_disk_gib": round(shutil.disk_usage(path).free / GIB, 1),
        "swap_out_bytes": swap_out_bytes(),
    }


def host_description() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_gib": round(psutil.virtual_memory().total / GIB, 1),
        "python": platform.python_version(),
    }


def _kill_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def monitor(
    spec: MatrixSpec, process: subprocess.Popen[str], spill_dir: Path, swap_start: int, pressure_start: int
) -> dict[str, Any]:
    root = psutil.Process(process.pid)
    started = mono_time()
    peak_rss = 0
    peak_spill = 0
    max_pressure = pressure_start
    critical = 0
    samples = 0
    timed_out = False
    safety_abort = False
    while process.poll() is None:
        try:
            total = 0
            for member in [root, *root.children(recursive=True)]:
                try:
                    total += member.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            peak_rss = max(peak_rss, total)
        except psutil.NoSuchProcess:
            pass
        if samples % 5 == 0:
            peak_spill = max(peak_spill, directory_bytes(spill_dir))
            level = pressure_level()
            max_pressure = max(max_pressure, level)
            critical = critical + 1 if level >= CRITICAL_PRESSURE else 0
            if critical >= CRITICAL_SAMPLES or swap_out_bytes() - swap_start > spec.safety_swap_gib * GIB:
                safety_abort = True
                _kill_group(process)
                break
        samples += 1
        if elapsed_seconds(started) > spec.cell_timeout_s:
            timed_out = True
            _kill_group(process)
            break
        time.sleep(1)
    process.wait()
    return {
        "peak_rss_bytes": peak_rss,
        "peak_spill_bytes": max(peak_spill, directory_bytes(spill_dir)),
        "max_pressure": max_pressure,
        "timed_out": timed_out,
        "safety_abort": safety_abort,
    }


def extract_result(payload: dict[str, Any], spec: MatrixSpec, cell: Cell) -> dict[str, Any]:
    platform_info = payload.get("platform") if isinstance(payload.get("platform"), dict) else {}
    config = platform_info.get("config") if isinstance(platform_info.get("config"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    counts = summary.get("queries") if isinstance(summary.get("queries"), dict) else {}
    queries: dict[str, dict[str, Any]] = {}
    for query in payload.get("queries", []):
        if isinstance(query, dict) and query.get("run_type") == "measurement":
            queries[f"{query.get('stream')}:{query.get('id')}"] = {
                "rows": query.get("rows"),
                "status": query.get("status"),
                "ms": query.get("ms"),
            }
    receipt = platform_info.get("execution_engine")
    if not isinstance(receipt, dict):
        receipt = config.get("execution_engine") if isinstance(config.get("execution_engine"), dict) else {}
    requested = receipt.get("requested", config.get("engine_requested"))
    expected = spec.query_counts[cell.benchmark]
    checks = {
        "rechunk_effective_false": config.get("rechunk_effective") is False,
        "engine_matches": requested == cell.engine,
        "polars_version_matches": platform_info.get("version") == cell.version,
        "query_count_matches": counts.get("total") == expected and len(queries) == expected,
        "no_failed_queries": counts.get("failed") == 0,
    }
    return {
        "validation": summary.get("validation"),
        "query_total": counts.get("total"),
        "query_failed": counts.get("failed"),
        "rechunk_effective": config.get("rechunk_effective"),
        "execution_engine": receipt,
        "engine_requested": requested,
        "collect_engine_argument": (receipt.get("applied_native") or {})
        .get("collect_kwargs", {})
        .get("engine", config.get("collect_engine_argument")),
        "polars_version": platform_info.get("version"),
        "polars_runtime_package": config.get("polars_runtime_package"),
        "polars_runtime_version": config.get("polars_runtime_version"),
        "queries": queries,
        "checks": checks,
        "result_ok": all(checks.values()),
    }


def _git(repo_root: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=repo_root, capture_output=True, text=True, check=True).stdout.strip()


def current_pins(repo_root: Path, datagen_root: Path, spec: MatrixSpec) -> dict[str, Any]:
    manifests = {}
    for name, _ in spec.benchmarks:
        for path in sorted(datagen_root.glob(f"{name}_sf*/_datagen_manifest.json")):
            manifests[path.parent.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "benchbox_commit": _git(repo_root, "rev-parse", "HEAD"),
        "pyarrow": metadata.version("pyarrow"),
        "thread_count": spec.thread_count,
        "datagen_manifests": manifests,
    }


def prepare_cell_environment(
    cell: Cell, spec: MatrixSpec, *, repo_root: Path, env: dict[str, str], log_path: Path
) -> dict[str, Any]:
    _run_logged(
        ["uv", "pip", "uninstall", "--python", sys.executable, "polars", *RUNTIME_DISTRIBUTIONS],
        cwd=repo_root,
        env=env,
        log_path=log_path,
    )
    status, _, _ = _run_logged(
        ["uv", "pip", "install", "--python", sys.executable, f"polars=={cell.version}"],
        cwd=repo_root,
        env=env,
        log_path=log_path,
    )
    if status:
        raise RuntimeError(f"could not install polars=={cell.version}; see {log_path}")
    status, frozen, _ = _run_logged(
        ["uv", "pip", "freeze", "--python", sys.executable], cwd=repo_root, env=env, log_path=log_path
    )
    if status:
        raise RuntimeError(f"could not list installed packages; see {log_path}")
    installed = verify_installation(parse_freeze(frozen), cell.version)
    probe = [sys.executable, "-c", "import polars as pl;print(pl.__version__);print(pl.thread_pool_size())"]
    status, probed, _ = _run_logged(probe, cwd=repo_root, env=env, log_path=log_path)
    reported = probed.split()
    if status or reported != [cell.version, str(spec.thread_count)]:
        raise RuntimeError(f"Polars reports {reported}, expected {[cell.version, str(spec.thread_count)]}")
    installed["thread_pool_size"] = spec.thread_count
    return installed


def run_cell(
    spec: MatrixSpec,
    cell: Cell,
    *,
    round_index: int,
    position: int,
    output_dir: Path,
    repo_root: Path,
    env: dict[str, str],
    log_path: Path,
) -> dict[str, Any]:
    installed = prepare_cell_environment(cell, spec, repo_root=repo_root, env=env, log_path=log_path)
    spill_dir = output_dir / "spill" / f"round{round_index}-{cell.benchmark}-{cell.version}-{cell.setup}"
    if spill_dir.exists():
        shutil.rmtree(spill_dir)
    spill_dir.mkdir(parents=True)
    cell_env = {**env, "POLARS_TEMP_DIR": str(spill_dir), "POLARS_MAX_THREADS": str(spec.thread_count)}
    command = cell_command(spec, cell)
    _append_log(log_path, f"$ {shlex.join(command)}\n")
    before = host_state(output_dir)
    started_at = datetime.now(tz=timezone.utc).isoformat()
    started = mono_time()
    with log_path.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=cell_env,
            stdout=subprocess.PIPE,
            stderr=handle,
            text=True,
            start_new_session=True,
        )
        stdout_lines: list[str] = []
        reader = threading.Thread(target=lambda: stdout_lines.extend(process.stdout or []), daemon=True)
        reader.start()
        observed = monitor(spec, process, spill_dir, before["swap_out_bytes"], before["pressure_level"])
        reader.join(timeout=30)
    elapsed = elapsed_seconds(started)
    after = host_state(output_dir)
    output = "".join(stdout_lines)
    _append_log(log_path, output)
    _append_log(log_path, f"[exit={process.returncode} elapsed_s={elapsed:.3f}]\n")
    result_path = _find_optional_result_path(output, cwd=repo_root)
    extracted: dict[str, Any] = {}
    if result_path is not None:
        try:
            extracted = extract_result(json.loads(result_path.read_text(encoding="utf-8")), spec, cell)
        except (OSError, ValueError, TypeError):
            extracted = {}
    swap_growth_gib = max(0, after["swap_out_bytes"] - before["swap_out_bytes"]) / GIB
    peak_rss_gib = observed["peak_rss_bytes"] / GIB
    outcome = classify_outcome(
        spec,
        exit_code=process.returncode,
        timed_out=observed["timed_out"],
        safety_abort=observed["safety_abort"],
        result_ok=bool(extracted.get("result_ok")),
        peak_rss_gib=peak_rss_gib,
        swap_growth_gib=swap_growth_gib,
    )
    shutil.rmtree(spill_dir, ignore_errors=True)
    return {
        "round": round_index,
        "position": position,
        "benchmark": cell.benchmark,
        "version": cell.version,
        "setup": cell.setup,
        "engine": cell.engine,
        "scale": spec.scales[cell.benchmark],
        "started_at": started_at,
        "elapsed_s": round(elapsed, 3),
        "exit_code": process.returncode,
        "outcome": outcome,
        "host_safety_abort": observed["safety_abort"],
        "peak_rss_gib": round(peak_rss_gib, 3),
        "swap_out_growth_gib": round(swap_growth_gib, 3),
        "spill_peak_gib": round(observed["peak_spill_bytes"] / GIB, 3),
        "pressure_before": before["pressure_level"],
        "pressure_after": after["pressure_level"],
        "pressure_max": observed["max_pressure"],
        "load_1m_before": before["load_1m"],
        "free_disk_before_gib": before["free_disk_gib"],
        "free_disk_after_gib": after["free_disk_gib"],
        "installed": installed,
        "result_path": result_path.relative_to(output_dir).as_posix()
        if result_path is not None and output_dir in result_path.parents
        else None,
        **{key: value for key, value in extracted.items() if key != "result_ok"},
    }


def run_polars_matrix(
    spec: MatrixSpec,
    *,
    output_dir: Path,
    repo_root: Path,
    shuffle_seed: int,
    first_round: int,
    rounds: int,
    qualification: Path,
) -> dict[str, Any]:
    if output_dir == repo_root or repo_root in output_dir.parents:
        raise ValueError(f"output directory must be outside the checkout: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory must be new or empty: {output_dir}")
    if _git(repo_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("the checkout has uncommitted changes")
    qualification_sha = hashlib.sha256(qualification.read_bytes()).hexdigest()
    if qualification_sha != spec.qualification_sha256:
        raise ValueError(f"qualification file hash {qualification_sha} differs from the spec")
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(qualification, output_dir / "qualification.json")
    log_path = output_dir / "matrix.log"
    env = os.environ.copy()
    env["BENCHBOX_OUTPUT_DIR"] = str(output_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["POLARS_MAX_THREADS"] = str(spec.thread_count)

    for name, scale in spec.benchmarks:
        status, _, _ = _run_logged(generate_command(name, scale), cwd=repo_root, env=env, log_path=log_path)
        if status:
            raise RuntimeError(f"data generation failed for {name}; see {log_path}")
    first_cell = Cell(spec.benchmark_ids[0], spec.baseline_version, "A")
    prepare_cell_environment(first_cell, spec, repo_root=repo_root, env=env, log_path=log_path)
    for name, scale in spec.benchmarks:
        status, _, _ = _run_logged(prime_command(spec, name, scale), cwd=repo_root, env=env, log_path=log_path)
        if status:
            raise RuntimeError(f"Parquet conversion failed for {name}; see {log_path}")

    pins = current_pins(repo_root, output_dir / "datagen", spec)
    manifest: dict[str, Any] = {
        "schema_version": "1",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "spec": spec.snapshot(),
        "shuffle_seed": shuffle_seed,
        "workload_seed": None,
        "first_round": first_round,
        "rounds": rounds,
        "pins": pins,
        "host": host_description(),
        "round_order": {},
        "records": [],
        "complete": False,
    }
    for round_index in range(first_round, first_round + rounds):
        order = round_order(spec, shuffle_seed, round_index)
        manifest["round_order"][str(round_index)] = [cell.cell_id for cell in order]
        for position, cell in enumerate(order, start=1):
            record = run_cell(
                spec,
                cell,
                round_index=round_index,
                position=position,
                output_dir=output_dir,
                repo_root=repo_root,
                env=env,
                log_path=log_path,
            )
            manifest["records"].append(record)
            _write_json(output_dir / "matrix-manifest.json", manifest)
            check_pins(pins, current_pins(repo_root, output_dir / "datagen", spec))
    manifest["complete"] = True
    _write_json(output_dir / "matrix-manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None, *, spec: MatrixSpec | None = None, description: str = CLI_DESCRIPTION) -> int:
    parser = argparse.ArgumentParser(description=description)
    if spec is None:
        parser.add_argument("--engine", choices=sorted(SPECS), required=True, help="Engine spec to run")
    parser.add_argument("--output-dir", type=Path, required=True, help="New output root outside the checkout")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned commands without running them")
    parser.add_argument("--shuffle-seed", type=int, default=None, help="Seed for the per-round cell order only")
    parser.add_argument("--first-round", type=int, default=1, help="Index of the first round")
    parser.add_argument("--rounds", type=int, default=None, help="Number of rounds to run")
    parser.add_argument("--qualification", type=Path, default=None, help="Qualification file named by the spec")
    args = parser.parse_args(argv)
    selected = spec or SPECS[args.engine]
    repo_root = REPO_ROOT
    output_dir = args.output_dir.expanduser().resolve()
    if selected.setup_versions:
        rounds = args.rounds if args.rounds is not None else selected.effective_rounds
        if args.dry_run:
            if args.shuffle_seed is None:
                parser.error("--shuffle-seed is required for a setup-matrix dry run")
            print(
                "\n".join(
                    plan_lines(selected, shuffle_seed=args.shuffle_seed, first_round=args.first_round, rounds=rounds)
                )
            )
            return 0
        if args.shuffle_seed is None or args.qualification is None:
            parser.error("--shuffle-seed and --qualification are required unless --dry-run is given")
        try:
            manifest = run_polars_matrix(
                selected,
                output_dir=output_dir,
                repo_root=repo_root,
                shuffle_seed=args.shuffle_seed,
                first_round=args.first_round,
                rounds=rounds,
                qualification=args.qualification.expanduser().resolve(),
            )
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"{selected.display_name} matrix complete: {len(manifest['records'])} runs")
        print(f"Manifest: {output_dir / 'matrix-manifest.json'}")
        return 0
    try:
        manifest = run_matrix(selected, output_dir=output_dir, repo_root=repo_root, dry_run=args.dry_run)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"{selected.display_name} matrix {'planned' if args.dry_run else 'complete'}: "
        f"{len(manifest['records']) if not args.dry_run else manifest['expected_runs']} runs"
    )
    if not args.dry_run:
        print(f"Manifest: {output_dir / 'matrix-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
