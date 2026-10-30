from __future__ import annotations

import datetime as _dt
import os
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tests.uat import docker_assets, managed_runtime
from tests.uat.cleanup import CellKey, can_prune, prune_database_dir, source_reuse_graph
from tests.uat.config import OutputConfig, UATConfig
from tests.uat.ladder import LadderRung, plan_ladder
from tests.uat.matrix import (
    invalidate_reachability_cache_after_lifecycle_change,
    platform_is_reachable,
    probe_platform_reachability,
)
from tests.uat.phases import PhaseResult
from tests.uat.phases.enumerate import (
    Cell,
    CompatibilityPrunedCell,
    enumerate_cells_with_pruning,
)
from tests.uat.preflight_budget import (
    MemorySnapshot,
    check_memory_headroom,
    format_memory_headroom_failure,
    free_space_gib as default_free_space_reader,
    read_memory_snapshot as default_free_memory_reader,
)
from tests.uat.runner import CellResult, run_cell

_DEFAULT_OUTPUT = OutputConfig()
_CONTAINER_LOG_CAPTURE_TIMEOUT_S = 60.0

BENCHBOX_OUTPUT_DIR_ENV_VAR = "BENCHBOX_OUTPUT_DIR"


def _resolve_output_base(template: str, default_template: str, *, explicit: bool) -> str:
    if explicit:
        return template
    override = os.environ.get(BENCHBOX_OUTPUT_DIR_ENV_VAR)
    if not override:
        return template
    default_base = _DEFAULT_OUTPUT.benchmark_runs_dir_template
    if not default_template.startswith(default_base):
        return template
    suffix = default_template[len(default_base) :]
    return override.rstrip("/") + suffix


@dataclass(frozen=True)
class DockerLifecycleEvent:
    platform: str
    action: str
    status: str
    project_name: str | None
    message: str
    result: docker_assets.DockerCommandResult | None = None
    free_space_gib: float | None = None


@dataclass(frozen=True)
class ExecuteOutcome(PhaseResult):
    results: tuple[CellResult, ...]
    pruned: tuple[Cell, ...]
    skipped_unreachable: tuple[Cell, ...]
    startup_failed: tuple[Cell, ...] = ()
    died_mid_platform: tuple[Cell, ...] = ()
    compatibility_pruned: tuple[CompatibilityPrunedCell, ...] = ()
    docker_events: tuple[DockerLifecycleEvent, ...] = ()
    abort_kind: str | None = None

    def exit_code(self) -> int:
        if self.aborted:
            return 2
        if not self.results:
            return 1
        if self.died_mid_platform:
            return 1
        return 0 if all(result.status == "passed" for result in self.results) else 1


class _PlatformDiedMidRun(Exception):
    def __init__(self, *, platform: str, remaining_cells: list[Cell]) -> None:
        super().__init__(f"{platform} stopped being reachable mid-run")
        self.platform = platform
        self.remaining_cells = remaining_cells


@dataclass(frozen=True)
class _DockerPlatformState:
    spec: docker_assets.DockerPlatformSpec | None = None
    project_name: str | None = None
    started: bool = False
    cleanup_status: str = "not-run"


DockerRunner = Callable[..., docker_assets.DockerCommandResult]
FreeSpaceReader = Callable[[str | Path], float]
FreeMemoryReader = Callable[[], MemorySnapshot]
SleepFn = Callable[[float], None]


def run_execute(
    config: UATConfig,
    *,
    log_dir: Path | None = None,
    benchmark_runs_dir: Path | None = None,
    databases_root: Path | None = None,
    cleanup_enabled: bool = True,
    runner=None,
    docker_runner: DockerRunner | None = None,
    free_space_checks_enabled: bool = False,
    free_space_path: Path | str | None = None,
    free_space_min_gib: float | None = None,
    free_space_reader: FreeSpaceReader | None = None,
    memory_reader: FreeMemoryReader | None = None,
    sleep_fn: SleepFn | None = None,
) -> ExecuteOutcome:
    if runner is None:
        runner = run_cell
    if docker_runner is None:
        docker_runner = docker_assets.run_docker_command
    if free_space_reader is None:
        free_space_reader = default_free_space_reader
    if memory_reader is None:
        memory_reader = default_free_memory_reader
    if sleep_fn is None:
        sleep_fn = time.sleep
    assert config.execute.parallel_platforms is False, "parallel_platforms must remain False — UAT W3 line 222"
    benchmark_runs_dir = (
        Path(benchmark_runs_dir).expanduser() if benchmark_runs_dir is not None else default_benchmark_runs_dir(config)
    )
    if free_space_path is None:
        free_space_path = config.preflight.free_space_path or benchmark_runs_dir
    if free_space_min_gib is None:
        free_space_min_gib = config.preflight.free_space_min_gib

    enumeration = enumerate_cells_with_pruning(config)
    cells = list(enumeration.cells)
    by_pb: dict[tuple[str, str], list[Cell]] = defaultdict(list)
    for cell in cells:
        by_pb[(cell.platform, cell.benchmark)].append(cell)
    for key in by_pb:
        by_pb[key].sort(key=lambda c: c.scale)

    by_pb = _reorder_for_topology(by_pb)
    by_platform = _group_by_platform(by_pb)

    results: list[CellResult] = []
    pruned: list[Cell] = []
    skipped_unreachable: list[Cell] = []
    startup_failed: list[Cell] = []
    died_mid_platform: list[Cell] = []
    docker_events: list[DockerLifecycleEvent] = []
    completed_pairs: set[tuple[str, str]] = set()
    already_pruned: set[tuple[str, str, float]] = set()
    last_completed_platform: str | None = None
    last_docker_cleanup_status = "not-run"
    abort_reason: str | None = None
    abort_kind: str | None = None

    for platform, platform_pairs in by_platform:
        _log_platform_chunk_start(config, platform=platform, platform_pairs=platform_pairs, log_dir=log_dir)
        platform_abort_reason, platform_abort_kind = _pre_start_abort_reason(
            config,
            platform=platform,
            free_space_checks_enabled=free_space_checks_enabled,
            free_space_path=free_space_path,
            free_space_min_gib=free_space_min_gib,
            free_space_reader=free_space_reader,
            memory_reader=memory_reader,
            last_completed_platform=last_completed_platform,
            docker_cleanup_status=last_docker_cleanup_status,
            log_dir=log_dir,
        )
        docker_state = _DockerPlatformState(cleanup_status=last_docker_cleanup_status)

        docker_startup_failed = False
        try:
            if platform_abort_reason is None:
                docker_state, startup_reason = _start_docker_platform_if_needed(
                    config,
                    platform=platform,
                    benchmark_runs_dir=benchmark_runs_dir,
                    docker_runner=docker_runner,
                    docker_events=docker_events,
                    log_dir=log_dir,
                    sleep_fn=sleep_fn,
                    memory_reader=memory_reader,
                )
                last_docker_cleanup_status = docker_state.cleanup_status
                if startup_reason is not None:
                    if docker_state.cleanup_status == "startup-failed":
                        startup_failed.extend(cell for _, pb_cells in platform_pairs for cell in pb_cells)
                        docker_startup_failed = True
                    else:
                        platform_abort_reason = startup_reason
                        platform_abort_kind = "docker_startup"
            if platform_abort_reason is None and not docker_startup_failed:
                try:
                    _run_or_skip_platform(
                        config,
                        platform=platform,
                        platform_pairs=platform_pairs,
                        by_pb=by_pb,
                        results=results,
                        pruned=pruned,
                        skipped_unreachable=skipped_unreachable,
                        died_mid_platform=died_mid_platform,
                        completed_pairs=completed_pairs,
                        already_pruned=already_pruned,
                        databases_root=databases_root,
                        cleanup_enabled=cleanup_enabled,
                        runner=runner,
                        docker_runner=docker_runner,
                        docker_events=docker_events,
                        log_dir=log_dir,
                        benchmark_runs_dir=benchmark_runs_dir,
                    )
                except Exception as exc:
                    _annotate_disk_floor_abort(
                        exc,
                        skipped_unreachable=skipped_unreachable,
                        startup_failed=startup_failed,
                        died_mid_platform=died_mid_platform,
                        compatibility_pruned=enumeration.compatibility_pruned,
                    )
                    raise
        finally:
            docker_state, teardown_abort_reason, teardown_abort_kind = _teardown_docker_platform_if_needed(
                config,
                platform=platform,
                docker_state=docker_state,
                benchmark_runs_dir=benchmark_runs_dir,
                docker_runner=docker_runner,
                docker_events=docker_events,
                free_space_checks_enabled=free_space_checks_enabled,
                free_space_path=free_space_path,
                free_space_min_gib=free_space_min_gib,
                free_space_reader=free_space_reader,
                log_dir=log_dir,
            )
            last_docker_cleanup_status = docker_state.cleanup_status
            if platform_abort_reason is None:
                platform_abort_reason = teardown_abort_reason
                platform_abort_kind = teardown_abort_kind

        if platform_abort_reason is not None:
            abort_reason = platform_abort_reason
            abort_kind = platform_abort_kind
            break
        _prune_platform_chunk_if_enabled(
            config,
            platform=platform,
            by_pb=by_pb,
            results=results,
            completed_pairs=completed_pairs,
            already_pruned=already_pruned,
            databases_root=databases_root,
            cleanup_enabled=cleanup_enabled,
            log_dir=log_dir,
        )
        last_completed_platform = platform

    return ExecuteOutcome(
        phase="execute",
        results=tuple(results),
        pruned=tuple(pruned),
        skipped_unreachable=tuple(skipped_unreachable),
        startup_failed=tuple(startup_failed),
        died_mid_platform=tuple(died_mid_platform),
        compatibility_pruned=enumeration.compatibility_pruned,
        docker_events=tuple(docker_events),
        aborted=abort_reason is not None,
        abort_reason=abort_reason,
        abort_kind=abort_kind,
    )


def _start_docker_platform_if_needed(
    config: UATConfig,
    *,
    platform: str,
    benchmark_runs_dir: Path,
    docker_runner: DockerRunner,
    docker_events: list[DockerLifecycleEvent],
    log_dir: Path | None,
    sleep_fn: SleepFn,
    memory_reader: FreeMemoryReader,
) -> tuple[_DockerPlatformState, str | None]:
    if not docker_assets.is_docker_platform(platform):
        return _DockerPlatformState(), None

    spec = docker_assets.docker_platform_spec(platform)
    if not config.cleanup.docker_manage_platforms:
        _record_docker_event(
            docker_events,
            log_dir=log_dir,
            platform=platform,
            action="manage",
            status="disabled",
            project_name=None,
            message="cleanup.docker_manage_platforms=false; probing externally managed stack only",
        )
        return _DockerPlatformState(spec=spec, cleanup_status="disabled-external"), None

    project_name = docker_assets.compose_project_name(
        config.name,
        platform,
        config.cleanup.docker_project_prefix,
    )
    try:
        docker_assets.validate_managed_start_allowed(
            spec,
            config.cleanup.docker_fixed_container_name_policy,
        )
        compose_env = managed_runtime.compose_environment(config, spec, benchmark_runs_dir)
    except docker_assets.DockerAssetError as exc:
        return _DockerPlatformState(spec=spec, project_name=project_name), str(exc)

    up_result = docker_runner(
        docker_assets.compose_up_command(
            spec,
            project_name,
            start_timeout_s=config.cleanup.docker_start_timeout_s,
        ),
        dry_run=config.dry_run,
        timeout_s=config.cleanup.docker_start_timeout_s,
        cwd=docker_assets.REPO_ROOT,
        env=compose_env,
    )
    _record_docker_event(
        docker_events,
        log_dir=log_dir,
        platform=platform,
        action="up",
        status="ok" if up_result.succeeded else "failed",
        project_name=project_name,
        message=_docker_result_message(up_result),
        result=up_result,
    )
    if not up_result.succeeded:
        _capture_container_logs(
            config,
            spec=spec,
            project_name=project_name,
            platform=platform,
            docker_runner=docker_runner,
            docker_events=docker_events,
            log_dir=log_dir,
            benchmark_runs_dir=benchmark_runs_dir,
        )
        state = _DockerPlatformState(
            spec=spec,
            project_name=project_name,
            started=True,
            cleanup_status="startup-failed",
        )
        return (
            state,
            f"UAT-managed Docker startup failed for {platform} project {project_name}: "
            f"{_docker_result_message(up_result)}",
        )
    invalidate_reachability_cache_after_lifecycle_change()

    if not config.dry_run:
        readiness_reason = _check_docker_platform_readiness(
            config,
            spec=spec,
            project_name=project_name,
            platform=platform,
            docker_runner=docker_runner,
            docker_events=docker_events,
            log_dir=log_dir,
            benchmark_runs_dir=benchmark_runs_dir,
            sleep_fn=sleep_fn,
            memory_reader=memory_reader,
        )
        if readiness_reason is not None:
            _record_docker_event(
                docker_events,
                log_dir=log_dir,
                platform=platform,
                action="readiness",
                status="failed",
                project_name=project_name,
                message=readiness_reason,
            )
            _capture_container_logs(
                config,
                spec=spec,
                project_name=project_name,
                platform=platform,
                docker_runner=docker_runner,
                docker_events=docker_events,
                log_dir=log_dir,
                benchmark_runs_dir=benchmark_runs_dir,
            )
            state = _DockerPlatformState(
                spec=spec,
                project_name=project_name,
                started=True,
                cleanup_status="startup-failed",
            )
            return state, readiness_reason

    state = _DockerPlatformState(
        spec=spec,
        project_name=project_name,
        started=True,
        cleanup_status="started",
    )
    return state, None


def _capture_container_logs(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    platform: str,
    docker_runner: DockerRunner,
    docker_events: list[DockerLifecycleEvent],
    log_dir: Path | None,
    benchmark_runs_dir: Path,
) -> None:
    if config.dry_run:
        return
    result = docker_runner(
        docker_assets.compose_logs_command(spec, project_name),
        dry_run=False,
        timeout_s=_CONTAINER_LOG_CAPTURE_TIMEOUT_S,
        cwd=docker_assets.REPO_ROOT,
        env=managed_runtime.compose_environment(config, spec, benchmark_runs_dir),
    )
    artifact = f"container-logs-{platform}.log"
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / artifact).open("a", encoding="utf-8") as fh:
            fh.write(f"# {result.command} exit={result.returncode}\n{result.stdout}\n{result.stderr}\n")
    _record_docker_event(
        docker_events,
        log_dir=log_dir,
        platform=platform,
        action="logs",
        status="ok" if result.succeeded else "failed",
        project_name=project_name,
        message=f"container logs written to {artifact}" if result.succeeded else _docker_result_message(result),
        result=result,
    )


def _check_docker_platform_readiness(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    platform: str,
    docker_runner: DockerRunner,
    docker_events: list[DockerLifecycleEvent],
    log_dir: Path | None,
    benchmark_runs_dir: Path,
    sleep_fn: SleepFn,
    memory_reader: FreeMemoryReader,
) -> str | None:
    sleep_fn(config.cleanup.docker_settle_s)

    ps_result = docker_runner(
        docker_assets.compose_ps_command(spec, project_name),
        dry_run=False,
        timeout_s=config.cleanup.docker_start_timeout_s,
        cwd=docker_assets.REPO_ROOT,
        env=managed_runtime.compose_environment(config, spec, benchmark_runs_dir),
    )
    _record_docker_event(
        docker_events,
        log_dir=log_dir,
        platform=platform,
        action="ps",
        status="ok" if ps_result.succeeded else "failed",
        project_name=project_name,
        message=_docker_result_message(ps_result),
        result=ps_result,
    )
    if not ps_result.succeeded:
        return (
            f"UAT readiness check for {platform} project {project_name} could not run `compose ps` "
            f"{config.cleanup.docker_settle_s}s after `up --wait` reported success: "
            f"{_docker_result_message(ps_result)}"
        )
    if not docker_assets.compose_ps_service_rows(ps_result.stdout):
        return (
            f"UAT-managed Docker readiness check failed for {platform} project {project_name}: "
            f"`compose ps -a` listed no services {config.cleanup.docker_settle_s}s "
            "after `up --wait` reported success"
        )
    unhealthy = docker_assets.compose_ps_unhealthy_services(ps_result.stdout)
    if unhealthy:
        return (
            f"UAT-managed Docker readiness check failed for {platform} project {project_name}: "
            f"service(s) {', '.join(unhealthy)} not ready {config.cleanup.docker_settle_s}s "
            "after `up --wait` reported success"
        )

    if not probe_platform_reachability(platform, timeout_s=config.execute.liveness_probe_timeout_s or 2.0):
        endpoint = docker_assets.host_reachability_endpoint(platform) or platform
        return (
            f"UAT-managed Docker readiness check failed for {platform} project {project_name}: "
            f"reachability probe to {endpoint} failed {config.cleanup.docker_settle_s}s after `up --wait` reported success"
        )
    application_reason = managed_runtime.check_application_readiness(
        config,
        spec=spec,
        project_name=project_name,
        docker_runner=docker_runner,
        docker_events=docker_events,
        record_event=_record_docker_event,
        log_dir=log_dir,
        benchmark_runs_dir=benchmark_runs_dir,
        retry_window_s=max(0.0, config.cleanup.docker_start_timeout_s - config.cleanup.docker_settle_s),
        retry_interval_s=config.cleanup.docker_settle_s,
        sleep_fn=sleep_fn,
    )
    if application_reason is not None:
        return application_reason
    if platform == "starrocks" and not config.dry_run:
        resource_reason = managed_runtime.reconcile_starrocks_resources(
            config,
            spec=spec,
            project_name=project_name,
            docker_runner=docker_runner,
            docker_events=docker_events,
            record_event=_record_docker_event,
            log_dir=log_dir,
        )
        if resource_reason is not None:
            return resource_reason
    return managed_runtime.check_memory_admission(
        config,
        spec=spec,
        project_name=project_name,
        platform=platform,
        docker_runner=docker_runner,
        docker_events=docker_events,
        record_event=_record_docker_event,
        log_dir=log_dir,
        memory_reader=memory_reader,
    )


def _teardown_docker_platform_if_needed(
    config: UATConfig,
    *,
    platform: str,
    docker_state: _DockerPlatformState,
    benchmark_runs_dir: Path,
    docker_runner: DockerRunner,
    docker_events: list[DockerLifecycleEvent],
    free_space_checks_enabled: bool,
    free_space_path: Path | str,
    free_space_min_gib: float,
    free_space_reader: FreeSpaceReader,
    log_dir: Path | None,
) -> tuple[_DockerPlatformState, str | None, str | None]:
    if not docker_state.started or docker_state.spec is None or docker_state.project_name is None:
        return docker_state, None, None

    stack_started_successfully = docker_state.cleanup_status == "started"

    cleanup_status, cleanup_abort_reason = _run_docker_teardown(
        config,
        platform=platform,
        docker_state=docker_state,
        benchmark_runs_dir=benchmark_runs_dir,
        docker_runner=docker_runner,
        docker_events=docker_events,
        log_dir=log_dir,
    )
    state = _DockerPlatformState(
        spec=docker_state.spec,
        project_name=docker_state.project_name,
        started=docker_state.started,
        cleanup_status=cleanup_status,
    )
    if cleanup_abort_reason is not None:
        if stack_started_successfully:
            return state, cleanup_abort_reason, "docker_teardown"
        _record_docker_event(
            docker_events,
            log_dir=log_dir,
            platform=platform,
            action="down-policy",
            status="advance-after-startup-failed",
            project_name=docker_state.project_name,
            message=(
                "Teardown also failed for a stack whose own startup already failed; "
                f"advancing per FAIL-and-advance policy instead of a global abort: {cleanup_abort_reason}"
            ),
        )
    free_space_abort_reason = _free_space_abort_reason(
        enabled=free_space_checks_enabled,
        path=free_space_path,
        min_gib=free_space_min_gib,
        reader=free_space_reader,
        last_completed_platform=platform,
        docker_cleanup_status=cleanup_status,
        context=f"after Docker teardown for platform {platform}",
        log_dir=log_dir,
    )
    if free_space_abort_reason is not None:
        return state, free_space_abort_reason, "disk_floor"
    return state, None, None


def _run_docker_teardown(
    config: UATConfig,
    *,
    platform: str,
    docker_state: _DockerPlatformState,
    benchmark_runs_dir: Path,
    docker_runner: DockerRunner,
    docker_events: list[DockerLifecycleEvent],
    log_dir: Path | None,
) -> tuple[str, str | None]:
    assert docker_state.spec is not None
    assert docker_state.project_name is not None
    if config.cleanup.docker_platform_switch == "off":
        _record_docker_event(
            docker_events,
            log_dir=log_dir,
            platform=platform,
            action="down",
            status="off",
            project_name=docker_state.project_name,
            message="cleanup.docker_platform_switch=off; UAT-managed Docker teardown skipped",
        )
        return "off", None

    try:
        compose_env = managed_runtime.compose_environment(config, docker_state.spec, benchmark_runs_dir)
    except docker_assets.DockerAssetError:
        compose_env = {"BENCHBOX_DATA_DIR": str(docker_assets.REPO_ROOT)}

    down_result = docker_runner(
        docker_assets.compose_down_command(
            docker_state.spec,
            docker_state.project_name,
            config.cleanup.docker_platform_switch,
        ),
        dry_run=config.dry_run,
        timeout_s=config.cleanup.docker_start_timeout_s,
        cwd=docker_assets.REPO_ROOT,
        env=compose_env,
    )
    cleanup_status = "ok" if down_result.succeeded else "failed"
    _record_docker_event(
        docker_events,
        log_dir=log_dir,
        platform=platform,
        action="down",
        status=cleanup_status,
        project_name=docker_state.project_name,
        message=_docker_result_message(down_result),
        result=down_result,
    )
    if (
        config.cleanup.docker_platform_switch in {"volumes", "images"}
        and docker_assets.resolve_container_cli() == "mocker"
    ):
        removed_volumes = docker_assets.sweep_leaked_mocker_volumes(
            docker_state.project_name,
            docker_state.spec,
            runner=docker_runner,
            dry_run=config.dry_run,
        )
        _record_docker_event(
            docker_events,
            log_dir=log_dir,
            platform=platform,
            action="volume-sweep",
            status="ok",
            project_name=docker_state.project_name,
            message=(
                f"removed {len(removed_volumes)} leaked mocker named volume(s): {', '.join(removed_volumes)}"
                if removed_volumes
                else "no leaked mocker named volumes found"
            ),
        )
    if down_result.succeeded:
        return cleanup_status, None
    return (
        cleanup_status,
        f"UAT-managed Docker cleanup failed for {platform} project {docker_state.project_name}: "
        f"{_docker_result_message(down_result)}",
    )


def _run_or_skip_platform(
    config: UATConfig,
    *,
    platform: str,
    platform_pairs: list[tuple[str, list[Cell]]],
    by_pb: dict[tuple[str, str], list[Cell]],
    results: list[CellResult],
    pruned: list[Cell],
    skipped_unreachable: list[Cell],
    died_mid_platform: list[Cell],
    completed_pairs: set[tuple[str, str]],
    already_pruned: set[tuple[str, str, float]],
    databases_root: Path | None,
    cleanup_enabled: bool,
    runner,
    docker_runner: DockerRunner,
    docker_events: list[DockerLifecycleEvent],
    log_dir: Path | None,
    benchmark_runs_dir: Path,
) -> None:
    platform_cells = [cell for _, pb_cells in platform_pairs for cell in pb_cells]
    if config.execute.skip_unreachable and not platform_is_reachable(platform):
        skipped_unreachable.extend(platform_cells)
        return
    liveness_armed = (
        config.execute.liveness_probe_timeout_s > 0
        and not config.dry_run
        and probe_platform_reachability(platform, timeout_s=config.execute.liveness_probe_timeout_s)
    )
    application_liveness_project_name = None
    if config.cleanup.docker_manage_platforms and docker_assets.is_docker_platform(platform):
        application_liveness_project_name = docker_assets.compose_project_name(
            config.name,
            platform,
            config.cleanup.docker_project_prefix,
        )
    for index, (benchmark, pb_cells) in enumerate(platform_pairs):
        try:
            _run_platform_benchmark(
                config,
                platform=platform,
                benchmark=benchmark,
                pb_cells=pb_cells,
                by_pb=by_pb,
                results=results,
                pruned=pruned,
                completed_pairs=completed_pairs,
                already_pruned=already_pruned,
                databases_root=databases_root,
                cleanup_enabled=cleanup_enabled,
                runner=runner,
                docker_runner=docker_runner,
                docker_events=docker_events,
                log_dir=log_dir,
                benchmark_runs_dir=benchmark_runs_dir,
                liveness_armed=liveness_armed,
                application_liveness_project_name=application_liveness_project_name,
            )
        except _PlatformDiedMidRun as died:
            died_mid_platform.extend(died.remaining_cells)
            for _later_benchmark, later_cells in platform_pairs[index + 1 :]:
                died_mid_platform.extend(later_cells)
            return


def _run_platform_benchmark(
    config: UATConfig,
    *,
    platform: str,
    benchmark: str,
    pb_cells: list[Cell],
    by_pb: dict[tuple[str, str], list[Cell]],
    results: list[CellResult],
    pruned: list[Cell],
    completed_pairs: set[tuple[str, str]],
    already_pruned: set[tuple[str, str, float]],
    databases_root: Path | None,
    cleanup_enabled: bool,
    runner,
    docker_runner: DockerRunner,
    docker_events: list[DockerLifecycleEvent],
    log_dir: Path | None,
    benchmark_runs_dir: Path,
    liveness_armed: bool,
    application_liveness_project_name: str | None,
) -> None:
    ladder_rungs = [c.scale for c in pb_cells]
    observed: list[LadderRung] = []
    for index, cell in enumerate(pb_cells):
        _, pruned_rungs = plan_ladder(
            ladder_rungs,
            observed,
            early_stop_after_s=config.execute.early_stop_after_s,
            early_stop_on_failure=config.execute.early_stop_on_failure,
        )
        if cell.scale in pruned_rungs:
            pruned.append(cell)
            continue
        if liveness_armed and not probe_platform_reachability(
            platform, timeout_s=config.execute.liveness_probe_timeout_s
        ):
            endpoint = docker_assets.host_reachability_endpoint(platform) or platform
            append_lifecycle_log(
                log_dir,
                f"[liveness] {platform}/{benchmark}: probe to {endpoint} failed before cell "
                f"scale={cell.scale}; stack was reachable when the platform started. "
                f"Recording this and every remaining {platform} cell as died-mid-platform.",
            )
            raise _PlatformDiedMidRun(platform=platform, remaining_cells=list(pb_cells[index:]))
        if liveness_armed and application_liveness_project_name is not None:
            application_reason = managed_runtime.check_application_readiness(
                config,
                spec=docker_assets.docker_platform_spec(platform),
                project_name=application_liveness_project_name,
                docker_runner=docker_runner,
                docker_events=docker_events,
                record_event=_record_docker_event,
                log_dir=log_dir,
                benchmark_runs_dir=benchmark_runs_dir,
                action="application-liveness",
            )
            if application_reason is not None:
                append_lifecycle_log(
                    log_dir,
                    f"[liveness] {platform}/{benchmark}: application probe failed before cell "
                    f"scale={cell.scale}; stack was reachable when the platform started. "
                    f"Recording this and every remaining {platform} cell as died-mid-platform: {application_reason}",
                )
                raise _PlatformDiedMidRun(platform=platform, remaining_cells=list(pb_cells[index:]))
        cell_result = runner(
            cell.platform,
            cell.benchmark,
            cell.scale,
            timeout_s=config.execute.per_cell_timeout_s,
            memory_limit_gib=config.execute.per_cell_memory_limit_gib,
            phases=config.execute.phases_arg,
            compression=config.execute.compression,
            extra_args=config.execute.extra_args,
            local_managed_platform=config.cleanup.docker_manage_platforms
            and docker_assets.is_docker_platform(cell.platform),
            log_dir=log_dir,
            benchmark_runs_dir=benchmark_runs_dir,
            official=config.execute.official,
            streams=config.execute.streams,
            seed=config.execute.seed,
        )
        results.append(cell_result)
        observed.append(
            LadderRung(
                scale=cell.scale,
                elapsed_s=cell_result.elapsed_s,
                passed=(cell_result.status == "passed"),
            )
        )

    completed_pairs.add((platform, benchmark))

    if cleanup_enabled and databases_root is not None:
        _maybe_prune_completed(
            platform=platform,
            by_pb=by_pb,
            results=results,
            completed_pairs=completed_pairs,
            already_pruned=already_pruned,
            databases_root=databases_root,
            dry_run=config.dry_run,
        )


def _reorder_for_topology(
    by_pb: dict[tuple[str, str], list[Cell]],
) -> dict[tuple[str, str], list[Cell]]:
    consumer_to_sources: dict[str, list[str]] = {}
    for source, consumers in source_reuse_graph().items():
        for c in consumers:
            if c == source:
                continue
            consumer_to_sources.setdefault(c, []).append(source)

    by_platform: dict[str, list[tuple[str, list[Cell]]]] = {}
    for (platform, benchmark), pb_cells in by_pb.items():
        by_platform.setdefault(platform, []).append((benchmark, pb_cells))

    out: dict[tuple[str, str], list[Cell]] = {}
    for platform, pairs in by_platform.items():
        bench_to_cells = dict(pairs)
        bench_order = [b for b, _ in pairs]
        sorted_benches = _topological_sort(bench_order, consumer_to_sources)
        for bench in sorted_benches:
            out[(platform, bench)] = bench_to_cells[bench]
    return out


def _group_by_platform(
    by_pb: dict[tuple[str, str], list[Cell]],
) -> list[tuple[str, list[tuple[str, list[Cell]]]]]:
    grouped: list[tuple[str, list[tuple[str, list[Cell]]]]] = []
    index: dict[str, list[tuple[str, list[Cell]]]] = {}
    for (platform, benchmark), pb_cells in by_pb.items():
        if platform not in index:
            bucket: list[tuple[str, list[Cell]]] = []
            index[platform] = bucket
            grouped.append((platform, bucket))
        index[platform].append((benchmark, pb_cells))
    return grouped


def _topological_sort(
    benchmarks: list[str],
    consumer_to_sources: dict[str, list[str]],
) -> list[str]:
    bench_set = set(benchmarks)
    pending = set(benchmarks)
    dependents: dict[str, list[str]] = defaultdict(list)
    indegree = dict.fromkeys(benchmarks, 0)
    out: list[str] = []
    for consumer in benchmarks:
        for src in consumer_to_sources.get(consumer, ()):
            if src in bench_set:
                dependents[src].append(consumer)
                indegree[consumer] += 1

    while pending:
        ready = next((b for b in benchmarks if b in pending and indegree[b] == 0), None)
        if ready is None:
            ready = next(b for b in benchmarks if b in pending)
        pending.remove(ready)
        out.append(ready)
        for dependent in dependents.get(ready, ()):
            indegree[dependent] -= 1
    return out


def _log_platform_chunk_start(
    config: UATConfig,
    *,
    platform: str,
    platform_pairs: list[tuple[str, list[Cell]]],
    log_dir: Path | None,
) -> None:
    if not config.execute.platform_chunking:
        return
    append_lifecycle_log(
        log_dir,
        f"[platform-chunk] start platform={platform} "
        f"benchmarks={','.join(benchmark for benchmark, _ in platform_pairs)} "
        "mode=one-platform-at-a-time",
    )


def _prune_platform_chunk_if_enabled(
    config: UATConfig,
    *,
    platform: str,
    by_pb: dict[tuple[str, str], list[Cell]],
    results: list[CellResult],
    completed_pairs: set[tuple[str, str]],
    already_pruned: set[tuple[str, str, float]],
    databases_root: Path | None,
    cleanup_enabled: bool,
    log_dir: Path | None,
) -> None:
    if not config.execute.platform_chunking or not cleanup_enabled or databases_root is None:
        return
    _maybe_prune_completed(
        platform=platform,
        by_pb=by_pb,
        results=results,
        completed_pairs=completed_pairs,
        already_pruned=already_pruned,
        databases_root=databases_root,
        dry_run=config.dry_run,
    )
    append_lifecycle_log(
        log_dir,
        f"[platform-chunk] prune platform={platform} via=reuse-aware helpers scope=per-platform-benchmark-scale",
    )


def _maybe_prune_completed(
    *,
    platform: str,
    by_pb: dict[tuple[str, str], list[Cell]],
    results: list[CellResult],
    completed_pairs: set[tuple[str, str]],
    already_pruned: set[tuple[str, str, float]],
    databases_root: Path,
    dry_run: bool,
) -> None:
    pending_keys = [
        CellKey(c.platform, c.benchmark, c.scale)
        for (p, b), pb in by_pb.items()
        if (p, b) not in completed_pairs
        for c in pb
    ]
    completed_keys_this_platform = [
        CellKey(r.platform, r.benchmark, r.scale) for r in results if r.platform == platform
    ]
    benches_done_on_platform = [b for (p, b) in completed_pairs if p == platform]
    for prev_bench in benches_done_on_platform:
        scales_for_prev = {c.scale for c in by_pb.get((platform, prev_bench), [])}
        for scale in scales_for_prev:
            key = (platform, prev_bench, scale)
            if key in already_pruned:
                continue
            decision = can_prune(
                prev_bench,
                platform=platform,
                scale=scale,
                pending_cells=pending_keys,
                completed_cells=completed_keys_this_platform,
            )
            if decision.safe_to_prune:
                prune_database_dir(
                    databases_root,
                    platform=platform,
                    benchmark=prev_bench,
                    scale=scale,
                    dry_run=dry_run,
                )
                already_pruned.add(key)


def _annotate_disk_floor_abort(
    exc: BaseException,
    *,
    skipped_unreachable: list[Cell],
    startup_failed: list[Cell],
    died_mid_platform: list[Cell],
    compatibility_pruned: tuple[CompatibilityPrunedCell, ...],
) -> None:
    if not hasattr(exc, "skipped_unreachable_count"):
        try:
            exc.skipped_unreachable_count = len(skipped_unreachable)
        except (AttributeError, TypeError):
            pass
    if not hasattr(exc, "startup_failed_count"):
        try:
            exc.startup_failed_count = len(startup_failed)
        except (AttributeError, TypeError):
            pass
    if not hasattr(exc, "died_mid_platform_count"):
        try:
            exc.died_mid_platform_count = len(died_mid_platform)
        except (AttributeError, TypeError):
            pass
    if not hasattr(exc, "compatibility_pruned"):
        try:
            exc.compatibility_pruned = compatibility_pruned
        except (AttributeError, TypeError):
            pass


def _pre_start_abort_reason(
    config: UATConfig,
    *,
    platform: str,
    free_space_checks_enabled: bool,
    free_space_path: Path | str,
    free_space_min_gib: float,
    free_space_reader: FreeSpaceReader,
    memory_reader: FreeMemoryReader,
    last_completed_platform: str | None,
    docker_cleanup_status: str,
    log_dir: Path | None,
) -> tuple[str | None, str | None]:
    context = f"before starting platform {platform}"
    disk_reason = _free_space_abort_reason(
        enabled=free_space_checks_enabled,
        path=free_space_path,
        min_gib=free_space_min_gib,
        reader=free_space_reader,
        last_completed_platform=last_completed_platform,
        docker_cleanup_status=docker_cleanup_status,
        context=context,
        log_dir=log_dir,
    )
    if disk_reason is not None:
        return disk_reason, "disk_floor"
    memory_reason = _free_memory_abort_reason(
        config=config,
        platform=platform,
        reader=memory_reader,
        context=context,
        log_dir=log_dir,
    )
    if memory_reason is not None:
        return memory_reason, "memory_floor"
    return None, None


def _free_space_abort_reason(
    *,
    enabled: bool,
    path: Path | str,
    min_gib: float,
    reader: FreeSpaceReader,
    last_completed_platform: str | None,
    docker_cleanup_status: str,
    context: str,
    log_dir: Path | None,
) -> str | None:
    if not enabled or min_gib <= 0:
        return None
    free_gib = reader(path)
    append_lifecycle_log(
        log_dir,
        f"[free-space] {context}: {free_gib:.2f} GiB free at {path} "
        f"(threshold {min_gib:.2f} GiB, docker_cleanup_status={docker_cleanup_status})",
    )
    if free_gib >= min_gib:
        return None
    last_platform = last_completed_platform or "none"
    return (
        f"free space {free_gib:.2f} GiB < cutoff {min_gib:.2f} GiB at {path} "
        f"{context}; last_completed_platform={last_platform}; "
        f"docker_cleanup_status={docker_cleanup_status}"
    )


def _free_memory_abort_reason(
    *,
    config: UATConfig,
    platform: str,
    reader: FreeMemoryReader,
    context: str,
    log_dir: Path | None,
) -> str | None:
    min_gib = config.preflight.free_memory_min_gib
    if min_gib <= 0 or not config.cleanup.docker_manage_platforms or not docker_assets.is_docker_platform(platform):
        return None

    try:
        engine = docker_assets.resolve_container_cli()
    except docker_assets.DockerAssetError as exc:
        engine = f"unresolved ({exc})"
    vm_request = _describe_platform_vm_request(platform)
    request_gib: float | None = None
    if platform == "clickhouse-server":
        try:
            _, request_bytes = docker_assets.resolve_clickhouse_memory_limit(config.preflight.clickhouse_memory_limit)
        except docker_assets.DockerAssetError as exc:
            return f"ClickHouse memory request is not admissible {context}: {exc}"
        request_gib = request_bytes / (1024**3)
        vm_request = f"clickhouse-server={request_gib:.3f} GiB"
    elif platform == "starrocks":
        try:
            _, request_bytes = managed_runtime.resolve_starrocks_memory_limit(config.preflight.starrocks_memory_limit)
        except docker_assets.DockerAssetError as exc:
            return f"StarRocks memory request is not admissible {context}: {exc}"
        request_gib = request_bytes / (1024**3)
        vm_request = f"starrocks={request_gib:.3f} GiB"

    snapshot = reader()
    required_gib = min_gib
    if request_gib is not None:
        required_gib = max(min_gib, request_gib + config.preflight.docker_memory_reserve_gib)
    check = check_memory_headroom(snapshot, min_free_gib=required_gib)
    swap_note = f", swap {snapshot.swap_used_percent:.1f}% used" if snapshot.swap_used_percent is not None else ""
    if snapshot.free_gib is None and request_gib is not None:
        append_lifecycle_log(
            log_dir,
            f"[free-memory] {context}: engine={engine} vm_request={vm_request} "
            f"host available memory could not be measured; request-aware gate requires "
            f"{required_gib:.2f} GiB (reserve={config.preflight.docker_memory_reserve_gib:.2f} GiB); refusing startup",
        )
        return (
            f"memory request gate failed: host available memory could not be measured for {platform}; "
            f"{required_gib:.2f} GiB ({vm_request} + {config.preflight.docker_memory_reserve_gib:.2f} GiB reserve) "
            f"is required {context} (engine={engine})"
        )
    if snapshot.free_gib is None:
        append_lifecycle_log(
            log_dir,
            f"[free-memory] {context}: engine={engine} vm_request={vm_request} "
            "free memory could not be measured on this host; gate skipped",
        )
        return None

    append_lifecycle_log(
        log_dir,
        f"[free-memory] {context}: engine={engine} vm_request={vm_request} "
        f"{snapshot.free_gib:.2f} GiB available (threshold {required_gib:.2f} GiB{swap_note}; "
        f"reserve={config.preflight.docker_memory_reserve_gib:.2f} GiB)",
    )
    if not check.shortfall:
        return None
    return f"{format_memory_headroom_failure(check)} {context} (engine={engine}, vm_request={vm_request})"


def _describe_platform_vm_request(platform: str) -> str:
    try:
        spec = docker_assets.docker_platform_spec(platform)
    except docker_assets.DockerAssetError:
        return "unknown (no compose spec)"
    try:
        limits = docker_assets.compose_declared_memory_limits(spec)
    except Exception as exc:
        return f"unknown (could not read declared limits: {exc})"
    if not limits:
        return "no declared memory limit (engine default)"
    return ", ".join(f"{service}={limit}" for service, limit in sorted(limits.items()))


def _record_docker_event(
    events: list[DockerLifecycleEvent],
    *,
    log_dir: Path | None,
    platform: str,
    action: str,
    status: str,
    project_name: str | None,
    message: str,
    result: docker_assets.DockerCommandResult | None = None,
    free_space_gib: float | None = None,
) -> None:
    event = DockerLifecycleEvent(
        platform=platform,
        action=action,
        status=status,
        project_name=project_name,
        message=message,
        result=result,
        free_space_gib=free_space_gib,
    )
    events.append(event)
    command = f" command={result.command}" if result is not None else ""
    append_lifecycle_log(
        log_dir,
        f"[docker] platform={platform} action={action} status={status} project={project_name}"
        f"{command} message={message}",
    )


def _docker_result_message(result: docker_assets.DockerCommandResult) -> str:
    if result.dry_run:
        return "dry-run: command recorded but not executed"
    if result.succeeded:
        return "command completed successfully"
    detail = result.error or result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    if result.timed_out:
        detail = result.error or detail
    return detail


def append_lifecycle_log(log_dir: Path | None, line: str) -> None:
    if log_dir is None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "uat_lifecycle.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{_dt.datetime.now().astimezone().isoformat(timespec='seconds')} {line}\n")


def default_log_dir(config: UATConfig, now: _dt.datetime | None = None) -> Path:
    now = now or _dt.datetime.now()
    template = _resolve_output_base(
        config.output.logs_dir_template,
        _DEFAULT_OUTPUT.logs_dir_template,
        explicit="logs_dir_template" in config.output.explicitly_set,
    )
    rendered = (
        template.replace("{date}", now.strftime("%Y%m%d"))
        .replace("{time}", now.strftime("%H%M%S"))
        .replace("{name}", config.name)
    )
    path = Path(rendered).expanduser()
    if "{time}" in template:
        suffix = 2
        while path.exists():
            path = path.with_name(f"{path.name}-{suffix}")
            suffix += 1
    return path


def reserve_default_log_dir(config: UATConfig, now: _dt.datetime | None = None) -> Path:
    now = now or _dt.datetime.now()
    template = _resolve_output_base(
        config.output.logs_dir_template,
        _DEFAULT_OUTPUT.logs_dir_template,
        explicit="logs_dir_template" in config.output.explicitly_set,
    )
    candidate = default_log_dir(config, now=now)
    if "{time}" not in template:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    base = candidate
    suffix = 2
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = base.with_name(f"{base.name}-{suffix}")
            suffix += 1


def default_benchmark_runs_dir(config: UATConfig, now: _dt.datetime | None = None) -> Path:
    now = now or _dt.datetime.now()
    template = _resolve_output_base(
        config.output.benchmark_runs_dir_template,
        _DEFAULT_OUTPUT.benchmark_runs_dir_template,
        explicit="benchmark_runs_dir_template" in config.output.explicitly_set,
    )
    rendered = template.replace("{date}", now.strftime("%Y%m%d")).replace("{name}", config.name)
    return Path(rendered).expanduser()


def default_submissions_dir(config: UATConfig, now: _dt.datetime | None = None) -> Path:
    now = now or _dt.datetime.now()
    template = _resolve_output_base(
        config.output.submissions_dir_template,
        _DEFAULT_OUTPUT.submissions_dir_template,
        explicit="submissions_dir_template" in config.output.explicitly_set,
    )
    rendered = template.replace("{date}", now.strftime("%Y%m%d")).replace("{name}", config.name)
    return Path(rendered).expanduser()
