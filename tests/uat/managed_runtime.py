from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.uat import docker_assets
from tests.uat.clickhouse_memory import runtime_limit_matches_rung
from tests.uat.config import UATConfig
from tests.uat.preflight_budget import MemorySnapshot

STARROCKS_MEMORY_LIMIT_ENV_VAR = "STARROCKS_MEMORY_LIMIT"
STARROCKS_DEFAULT_MEMORY_LIMIT = "8g"
STARROCKS_READINESS = (
    "sh",
    "-c",
    'mysql -h127.0.0.1 -P9030 -uroot --skip-password -e "SHOW FRONTENDS\\G" | '
    'grep -q "Alive: true" && mysql -h127.0.0.1 -P9030 -uroot --skip-password '
    '-e "SHOW BACKENDS\\G" | grep -q "Alive: true"',
)

CEDARDB_READINESS = (
    "sh",
    "-c",
    'PGPASSWORD="$CEDAR_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$CEDAR_USER" -d "$CEDAR_DB" -tAc "SELECT 1"',
)
_COORDINATOR_QUERY_SCRIPT = """\
command -v curl >/dev/null 2>&1 || exit 127
r=$(curl -fsS -X POST -H "$USER_HEADER: benchbox" --data "SELECT 1" http://localhost:8080/v1/statement) || exit 1
i=0
while [ "$i" -lt 60 ]; do
  case "$r" in *'"error"'*) echo "$r" >&2; exit 1;; esac
  case "$r" in *'"data":[[1]]'*) exit 0;; esac
  n=$(echo "$r" | grep -o '"nextUri":"[^"]*"' | cut -d'"' -f4)
  [ -n "$n" ] || { echo "$r" >&2; exit 1; }
  sleep 1
  r=$(curl -fsS "http://localhost:8080/${n#*://*/}") || exit 1
  i=$((i+1))
done
exit 1
"""
PRESTO_READINESS = ("sh", "-c", f"USER_HEADER=X-Presto-User\n{_COORDINATOR_QUERY_SCRIPT}")
TRINO_READINESS = ("sh", "-c", f"USER_HEADER=X-Trino-User\n{_COORDINATOR_QUERY_SCRIPT}")

_APPLICATION_READINESS: dict[str, tuple[tuple[str, ...], float]] = {
    "starrocks": (STARROCKS_READINESS, 0.0),
    "cedardb": (CEDARDB_READINESS, 15.0),
    "presto": (PRESTO_READINESS, 60.0),
    "trino": (TRINO_READINESS, 60.0),
}

_PROBE_TOOL_OPTIONAL = frozenset({"cedardb", "presto", "trino"})
_COMMAND_NOT_FOUND_EXIT_CODES = frozenset({126, 127})

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
    entry = _APPLICATION_READINESS.get(spec.platform)
    if entry is None:
        return None
    argv = docker_assets.compose_ps_command(spec, project_name)
    argv[-2:] = ["exec", "-T", spec.services[0], *entry[0]]
    return argv


def _readiness_timeout_s(config: UATConfig, spec: docker_assets.DockerPlatformSpec) -> float:
    floor = _APPLICATION_READINESS[spec.platform][1]
    probe = config.execute.liveness_probe_timeout_s or 2.0
    return min(config.cleanup.docker_start_timeout_s, max(probe, floor))


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
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    command = _readiness_command(spec, project_name)
    if command is None:
        return None
    interval = max(retry_interval_s, 1.0)
    attempts = max(1, int(retry_window_s / interval) + 1)
    base_timeout = _readiness_timeout_s(config, spec)
    deadline = clock() + max(retry_window_s, base_timeout)
    result = None
    for attempt in range(attempts):
        result = docker_runner(
            command,
            dry_run=config.dry_run,
            timeout_s=max(1.0, min(base_timeout, deadline - clock())),
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
        if spec.platform in _PROBE_TOOL_OPTIONAL and result.returncode in _COMMAND_NOT_FOUND_EXIT_CODES:
            record_event(
                docker_events,
                log_dir=log_dir,
                platform=spec.platform,
                action=action,
                status="skipped",
                project_name=project_name,
                message=(
                    f"WARNING: query readiness probe tooling is missing in the {spec.platform} container "
                    f"(exit {result.returncode}); falling back to healthcheck and TCP readiness only"
                ),
            )
            return None
        if attempt + 1 < attempts and clock() < deadline:
            (sleep_fn or time.sleep)(interval)
        elif clock() >= deadline:
            break
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
