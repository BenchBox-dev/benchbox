from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchbox.utils.resource_limits import MemorySnapshot
from tests.uat import docker_assets
from tests.uat.clickhouse_memory import runtime_limit_matches_rung
from tests.uat.config import UATConfig

STARROCKS_MEMORY_LIMIT_ENV_VAR = "STARROCKS_MEMORY_LIMIT"
STARROCKS_DEFAULT_MEMORY_LIMIT = "4g"
STARROCKS_READINESS = (
    "sh",
    "-c",
    'mysql -h127.0.0.1 -P9030 -uroot --skip-password -e "SHOW FRONTENDS\\G" | '
    'grep -q "Alive: true" && mysql -h127.0.0.1 -P9030 -uroot --skip-password '
    '-e "SHOW BACKENDS\\G" | grep -q "Alive: true"',
)

DockerRunner = Callable[..., docker_assets.DockerCommandResult]
RecordEvent = Callable[..., None]


def resolve_starrocks_memory_limit(
    configured: str | None = STARROCKS_DEFAULT_MEMORY_LIMIT, *, env: dict[str, str] | None = None
) -> tuple[str, int]:
    environment = os.environ if env is None else env
    raw = environment.get(STARROCKS_MEMORY_LIMIT_ENV_VAR, configured)
    if raw is None:
        raise docker_assets.DockerAssetError("StarRocks memory request is required")
    if not isinstance(raw, str) or not raw.strip():
        raise docker_assets.DockerAssetError("StarRocks memory request must be a non-empty string")
    normalized = raw.strip()
    return normalized, docker_assets.parse_memory_bytes(normalized)


def compose_environment(config: UATConfig, spec: docker_assets.DockerPlatformSpec, runs_dir: Path) -> dict[str, str]:
    starrocks_limit = (
        resolve_starrocks_memory_limit(config.preflight.starrocks_memory_limit)[0]
        if spec.platform == "starrocks"
        else None
    )
    return docker_assets.compose_environment(
        spec,
        benchmark_runs_dir=runs_dir,
        memory_limit=config.preflight.clickhouse_memory_limit,
        starrocks_memory_limit=starrocks_limit,
    )


def _result_message(result: docker_assets.DockerCommandResult) -> str:
    if result.dry_run:
        return "dry-run: command recorded but not executed"
    if result.succeeded:
        return "command completed successfully"
    return result.error or result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"


def _readiness_command(spec: docker_assets.DockerPlatformSpec, project_name: str) -> list[str] | None:
    if spec.platform != "starrocks":
        return None
    argv = docker_assets.compose_ps_command(spec, project_name)
    argv[-2:] = ["exec", "-T", spec.services[0], *STARROCKS_READINESS]
    return argv


def check_application_readiness(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    docker_runner: DockerRunner,
    docker_events: list[Any],
    record_event: RecordEvent,
    log_dir: Path | None,
    benchmark_runs_dir: Path,
    action: str = "application-readiness",
    retry_window_s: float = 0.0,
    retry_interval_s: float = 0.0,
    sleep_fn: Callable[[float], None] | None = None,
) -> str | None:
    command = _readiness_command(spec, project_name)
    if command is None:
        return None
    interval = max(retry_interval_s, 1.0)
    attempts = max(1, int(retry_window_s / interval) + 1)
    result = None
    for attempt in range(attempts):
        result = docker_runner(
            command,
            dry_run=config.dry_run,
            timeout_s=min(config.cleanup.docker_start_timeout_s, config.execute.liveness_probe_timeout_s or 2.0),
            cwd=docker_assets.REPO_ROOT,
            env=compose_environment(config, spec, benchmark_runs_dir),
        )
        record_event(
            docker_events,
            log_dir=log_dir,
            platform=spec.platform,
            action=action,
            status="ok" if result.succeeded else "failed",
            project_name=project_name,
            message=f"attempt={attempt + 1}/{attempts} {_result_message(result)}",
            result=result,
        )
        if result.succeeded:
            return None
        if attempt + 1 < attempts:
            (sleep_fn or time.sleep)(interval)
    assert result is not None
    return f"UAT-managed Docker application readiness check failed for {spec.platform} project {project_name}: {_result_message(result)}"


def reconcile_starrocks_resources(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    docker_runner: DockerRunner,
    docker_events: list[Any],
    record_event: RecordEvent,
    log_dir: Path | None,
) -> str | None:
    if config.dry_run or spec.platform != "starrocks" or docker_assets.resolve_container_cli() != "mocker":
        return None
    selected, selected_bytes = resolve_starrocks_memory_limit(config.preflight.starrocks_memory_limit)
    stats_result = docker_runner(
        docker_assets.compose_stats_command(spec, project_name),
        dry_run=False,
        timeout_s=min(config.cleanup.docker_start_timeout_s, 30),
        cwd=docker_assets.REPO_ROOT,
        env=compose_environment(config, spec, Path("/tmp")),
    )
    runtime_limit = docker_assets.parse_runtime_memory_limit(stats_result.stdout)
    if (
        stats_result.succeeded
        and runtime_limit is not None
        and runtime_limit_matches_rung(runtime_limit, selected_bytes / (1024**3), requested_bytes=selected_bytes)
    ):
        record_event(
            docker_events,
            log_dir=log_dir,
            platform=spec.platform,
            action="resource-reconcile",
            status="ok",
            project_name=project_name,
            message=f"requested={selected} runtime_limit_bytes={runtime_limit} already matches",
            result=stats_result,
        )
        return None
    result = docker_runner(
        [
            docker_assets.resolve_container_cli(),
            "update",
            "--memory",
            selected,
            f"{project_name}-{spec.services[0]}-1",
        ],
        dry_run=False,
        timeout_s=min(config.cleanup.docker_start_timeout_s, 30),
        cwd=docker_assets.REPO_ROOT,
        env=compose_environment(config, spec, Path("/tmp")),
    )
    record_event(
        docker_events,
        log_dir=log_dir,
        platform=spec.platform,
        action="resource-reconcile",
        status="ok" if result.succeeded else "failed",
        project_name=project_name,
        message=_result_message(result),
        result=result,
    )
    if result.succeeded:
        return None
    return f"UAT-managed StarRocks resource reconciliation failed for {project_name}: {_result_message(result)}"


def check_memory_admission(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    platform: str,
    docker_runner: DockerRunner,
    docker_events: list[Any],
    record_event: RecordEvent,
    log_dir: Path | None,
    memory_reader: Callable[[], MemorySnapshot],
) -> str | None:
    if platform not in {"clickhouse-server", "starrocks"}:
        return None
    if platform == "starrocks":
        selected_value, selected_bytes = resolve_starrocks_memory_limit(config.preflight.starrocks_memory_limit)
        reserve = 0.0
    else:
        selected_value, selected_bytes = docker_assets.resolve_clickhouse_memory_limit(
            config.preflight.clickhouse_memory_limit
        )
        reserve = config.preflight.docker_memory_reserve_gib
    result = docker_runner(
        docker_assets.compose_stats_command(spec, project_name),
        dry_run=False,
        timeout_s=config.cleanup.docker_start_timeout_s,
        cwd=docker_assets.REPO_ROOT,
        env=compose_environment(config, spec, Path("/tmp")),
    )
    runtime_limit = docker_assets.parse_runtime_memory_limit(result.stdout)
    record_event(
        docker_events,
        log_dir=log_dir,
        platform=platform,
        action="memory-admission",
        status="ok" if result.succeeded and runtime_limit is not None else "failed",
        project_name=project_name,
        message=(f"requested={selected_value} requested_bytes={selected_bytes} runtime_limit_bytes={runtime_limit}"),
        result=result,
    )
    if not result.succeeded:
        return f"{platform} runtime memory admission failed for {project_name}: stats could not be read: {_result_message(result)}"
    if runtime_limit is None:
        return f"{platform} runtime memory admission failed for {project_name}: stats did not report a memory limit"
    if not runtime_limit_matches_rung(runtime_limit, selected_bytes / (1024**3), requested_bytes=selected_bytes):
        return f"{platform} runtime memory admission failed for {project_name}: requested {selected_value} ({selected_bytes} bytes), runtime reported {runtime_limit} bytes"
    if platform != "clickhouse-server":
        return None
    snapshot = memory_reader()
    required = selected_bytes / (1024**3) + reserve
    if snapshot.free_gib is None:
        return f"ClickHouse runtime memory admission failed for {project_name}: host available memory could not be measured"
    if snapshot.free_gib < required:
        return f"ClickHouse runtime memory admission failed for {project_name}: {snapshot.free_gib:.2f} GiB available < {required:.2f} GiB required"
    return None
