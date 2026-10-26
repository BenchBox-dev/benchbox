from __future__ import annotations

import csv
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests.uat.config import UATConfig

from tests.uat import docker_assets
from tests.uat.matrix import (
    invalidate_reachability_cache_after_lifecycle_change,
    platform_is_reachable,
    resolve_platforms,
)
from tests.uat.phases import PhaseResult
from tests.uat.preflight_budget import (
    DiskBudget,
    DiskBudgetCoverage,
    DiskHeadroomCheck,
    DiskRootFreeSpace,
    free_space_gib as free_space_gib,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def automated_local_platforms() -> tuple[str, ...]:
    return tuple(
        sorted(
            platform
            for platform, spec in docker_assets.docker_platform_specs().items()
            if spec.managed_start_allowed and not spec.fixed_container_names
        )
    )


AUTOMATED_LOCAL_PLATFORMS: tuple[str, ...] = automated_local_platforms()

BringUpRunner = Callable[[str], int]
ReachabilityChecker = Callable[[str], bool]


@dataclass(frozen=True)
class PreflightResult(PhaseResult):
    free_space_gib: float
    docker_reachable: bool
    host_load_1m: float | None
    warnings: tuple[str, ...]
    local_platforms_checked: tuple[str, ...] = ()
    local_platforms_attempted: tuple[str, ...] = ()
    disk_budget_summary: str | None = None
    free_space_report: tuple[str, ...] = ()
    abort_kind: str | None = None


def docker_reachable() -> bool:
    try:
        cli = docker_assets.resolve_container_cli()
        subprocess.run(
            [cli, "ps"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.SubprocessError, docker_assets.DockerAssetError):
        return False


def docker_data_root() -> Path | None:
    try:
        cli = docker_assets.resolve_container_cli()
        completed = subprocess.run(
            [cli, "info", "--format", "{{.DockerRootDir}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError, docker_assets.DockerAssetError):
        return None

    value = completed.stdout.strip()
    if not value:
        return None
    root = Path(value).expanduser()
    return root if root.exists() else None


def host_load_1m() -> float | None:
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def requested_platforms_from_config(config: UATConfig) -> tuple[str, ...]:
    platform_groups_default = ("sql",) if config.platforms.uses_implicit_group_default else ()
    return tuple(
        resolve_platforms(
            groups=config.platforms.groups if config.platforms.groups is not None else platform_groups_default,
            include=config.platforms.include,
            exclude=config.platforms.exclude,
        )
    )


def preflight_kwargs_from_config(
    config: UATConfig,
    *,
    benchmark_runs_dir: str | Path | None = None,
) -> dict[str, Any]:
    if benchmark_runs_dir is None:
        from tests.uat.phases.execute import default_benchmark_runs_dir

        benchmark_runs_dir = default_benchmark_runs_dir(config)
    return {
        "free_space_path": config.preflight.free_space_path or str(benchmark_runs_dir),
        "free_space_min_gib": config.preflight.free_space_min_gib,
        "docker_required": config.preflight.docker_required or config.cleanup.docker_manage_platforms,
        "noisy_neighbor_warn_load": config.preflight.noisy_neighbor_warn_load,
        "local_platforms_check": config.preflight.local_platforms_check,
        "requested_platforms": requested_platforms_from_config(config),
        "benchmark_runs_dir": benchmark_runs_dir,
        "disk_budget_config": config,
        "docker_manage_platforms": config.cleanup.docker_manage_platforms,
    }


def run_preflight(
    *,
    free_space_path: str | Path = "~/Developer/benchmark_runs",
    free_space_min_gib: float = 5.0,
    docker_required: bool = False,
    noisy_neighbor_warn_load: float = 8.0,
    local_platforms_check: bool = False,
    requested_platforms: Iterable[str] = (),
    benchmark_runs_dir: str | Path | None = None,
    bring_up_runner: BringUpRunner | None = None,
    reachability_checker: ReachabilityChecker | None = None,
    disk_budget_config: UATConfig | None = None,
    docker_manage_platforms: bool = False,
) -> PreflightResult:
    free_gib = free_space_gib(free_space_path)
    docker_ok = docker_reachable()
    load_1m = host_load_1m()

    warnings: list[str] = []
    aborted = False
    abort_reason: str | None = None
    checked: tuple[str, ...] = ()
    attempted: tuple[str, ...] = ()
    disk_budget_summary = None
    budget_gate = None
    benchmark_runs_root = (
        Path(benchmark_runs_dir).expanduser() if benchmark_runs_dir is not None else Path(free_space_path)
    )
    docker_root = docker_data_root() if docker_manage_platforms and docker_ok else None
    disk_roots = collect_disk_roots(
        free_space_path=free_space_path,
        benchmark_runs_dir=benchmark_runs_root,
        docker_manage_platforms=docker_manage_platforms,
        docker_data_root=docker_root,
    )
    free_space_entries = read_disk_root_free_space(disk_roots, free_space_reader=free_space_gib)
    if disk_budget_config is not None:
        try:
            disk_budget_summary, budget_gate = estimate_disk_budget_summary_and_gate(
                disk_budget_config,
                free_space_entries,
                min_free_gib=free_space_min_gib,
            )
            warnings.extend(_disk_budget_coverage_warnings(budget_gate.coverage))
            disk_budget_summary = _disk_budget_report(
                disk_budget_summary, budget_gate, free_space_min_gib=free_space_min_gib
            )
        except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
            return PreflightResult(
                phase="preflight",
                free_space_gib=free_gib,
                docker_reachable=docker_ok,
                host_load_1m=load_1m,
                aborted=True,
                abort_reason=f"disk budget estimator failed: {type(exc).__name__}: {exc}",
                abort_kind="disk_floor",
                warnings=tuple(warnings),
                local_platforms_checked=(),
                local_platforms_attempted=(),
                disk_budget_summary=None,
                free_space_report=(),
            )
    if free_space_min_gib <= 0:
        required_gib = 0.0
    elif budget_gate is not None:
        required_gib = budget_gate.headroom.required_gib
    else:
        required_gib = free_space_min_gib
    free_space_report = format_free_space_report(
        free_space_entries,
        required_gib=required_gib,
        lower_bound=_required_is_lower_bound(budget_gate, required_gib),
    )

    abort_kind: str | None = None
    if free_space_min_gib > 0 and budget_gate is not None and budget_gate.headroom.shortfalls:
        aborted = True
        abort_reason = format_disk_headroom_failure(budget_gate.headroom, budget_gate.budget)
        abort_kind = "disk_floor"
    elif free_space_min_gib > 0:
        short_roots = tuple(root for root in free_space_entries if root.free_gib < free_space_min_gib)
        if short_roots:
            aborted = True
            details = "; ".join(
                f"{root.label} {root.path}: {root.free_gib:.1f} GiB free < cutoff {free_space_min_gib:.1f} GiB"
                for root in short_roots
            )
            abort_reason = f"free space gate failed: {details}"
            abort_kind = "disk_floor"
    if docker_required and not docker_ok:
        aborted = True
        abort_reason = (abort_reason or "") + ("; docker_required=true but `docker ps` is unreachable")
        if abort_kind is None:
            abort_kind = "docker_required"
    if load_1m is not None and load_1m > noisy_neighbor_warn_load:
        warnings.append(f"host load {load_1m:.1f} > {noisy_neighbor_warn_load:.1f}")
    if not docker_ok and not docker_required:
        warnings.append("docker not reachable (any docker platforms will skip)")
    if docker_manage_platforms and docker_ok and docker_root is None:
        warnings.append(
            "container data root is not host-visible (VM-backed engine); "
            "container images/volumes are neither budgeted nor free-space-checked"
        )

    if local_platforms_check and not aborted:
        checked, attempted, local_abort, local_warnings = _check_local_platforms(
            requested_platforms,
            bring_up_runner=bring_up_runner
            or (lambda platform: _run_make_bring_up(platform, benchmark_runs_dir=benchmark_runs_dir)),
            reachability_checker=reachability_checker or platform_is_reachable,
        )
        warnings.extend(local_warnings)
        if local_abort is not None:
            aborted = True
            abort_reason = local_abort
            abort_kind = "local_platform_unreachable"

    return PreflightResult(
        phase="preflight",
        free_space_gib=free_gib,
        docker_reachable=docker_ok,
        host_load_1m=load_1m,
        aborted=aborted,
        abort_reason=abort_reason,
        abort_kind=abort_kind,
        warnings=tuple(warnings),
        local_platforms_checked=checked,
        local_platforms_attempted=attempted,
        disk_budget_summary=disk_budget_summary,
        free_space_report=free_space_report,
    )


def _check_local_platforms(
    requested_platforms: Iterable[str],
    *,
    bring_up_runner: BringUpRunner,
    reachability_checker: ReachabilityChecker,
) -> tuple[tuple[str, ...], tuple[str, ...], str | None, tuple[str, ...]]:
    checked = tuple(dict.fromkeys(requested_platforms))
    attempted: list[str] = []
    warnings: list[str] = []
    automated = set(automated_local_platforms())
    for platform in checked:
        if reachability_checker(platform):
            continue
        if platform not in automated:
            return (
                checked,
                tuple(attempted),
                f"local platform {platform!r} is unreachable and has no automated UAT bring-up; "
                "see https://benchbox.dev/docs/operations/uat-local-provisioning.html",
                tuple(warnings),
            )
        attempted.append(platform)
        returncode = bring_up_runner(platform)
        invalidate_reachability_cache_after_lifecycle_change()
        if returncode != 0:
            return (
                checked,
                tuple(attempted),
                f"local platform {platform!r} is unreachable and `make uat-bring-up PLATFORM={platform}` "
                f"failed with exit {returncode}",
                tuple(warnings),
            )
        if not reachability_checker(platform):
            return (
                checked,
                tuple(attempted),
                f"local platform {platform!r} remains unreachable after `make uat-bring-up PLATFORM={platform}`",
                tuple(warnings),
            )
        warnings.append(f"local platform {platform!r} was unreachable; `make uat-bring-up` recovered it")
    return checked, tuple(attempted), None, tuple(warnings)


def _run_make_bring_up(platform: str, *, benchmark_runs_dir: str | Path | None = None) -> int:
    argv = ["make", "uat-bring-up", f"PLATFORM={platform}"]
    if benchmark_runs_dir is not None:
        argv.append(f"BENCHMARK_RUNS_DIR={Path(benchmark_runs_dir).expanduser()}")
    completed = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        check=False,
    )
    return completed.returncode


def estimate_disk_budget_summary(config: UATConfig) -> str:
    from tests.uat.preflight_budget import estimate_peak_disk, format_disk_budget

    return format_disk_budget(estimate_peak_disk(config))


@dataclass(frozen=True)
class DiskBudgetGate:
    budget: DiskBudget
    headroom: DiskHeadroomCheck
    coverage: DiskBudgetCoverage


def estimate_disk_budget_summary_and_gate(
    config: UATConfig,
    free_space_entries: Iterable[DiskRootFreeSpace],
    *,
    min_free_gib: float,
) -> tuple[str, DiskBudgetGate]:
    from tests.uat.preflight_budget import (
        assess_budget_coverage,
        check_disk_headroom,
        estimate_cells,
        estimate_peak_disk,
        format_disk_budget,
        largest_scale_cells,
        load_budget_table,
    )

    table = load_budget_table()
    summary = format_disk_budget(estimate_peak_disk(config))
    gated_cells = largest_scale_cells(config)
    budget = estimate_cells(gated_cells, table=table)
    headroom = check_disk_headroom(
        budget,
        free_space_entries,
        min_free_gib=min_free_gib,
        platform_chunking=config.execute.platform_chunking,
    )
    coverage = assess_budget_coverage(gated_cells, table=table)
    return summary, DiskBudgetGate(budget=budget, headroom=headroom, coverage=coverage)


def _disk_budget_coverage_warnings(coverage: DiskBudgetCoverage) -> tuple[str, ...]:
    if not coverage.is_lower_bound:
        return ()
    return (
        f"disk budget gate is a LOWER BOUND: {coverage.cells_with_rows} of {coverage.cells_total} "
        f"largest-scale cell(s) have an inventory row and "
        f"{coverage.cells_with_measured_database} of {coverage.cells_total} have a measured "
        "loaded-database footprint; real demand may exceed the gated figure",
    )


def _disk_budget_report(summary: str, gate: DiskBudgetGate, *, free_space_min_gib: float) -> str:
    from tests.uat.preflight_budget import format_budget_coverage, format_budget_verdict

    lines = [summary, format_budget_coverage(gate.coverage)]
    if free_space_min_gib <= 0:
        lines.append(
            "Disk budget verdict: gate disabled (preflight.free_space_min_gib <= 0); "
            "the estimate above is informational only and was not enforced"
        )
    elif not gate.headroom.shortfalls:
        lines.append(format_budget_verdict(gate.headroom, gate.coverage))
    return "\n".join(lines)


def _required_is_lower_bound(gate: DiskBudgetGate | None, required_gib: float) -> bool:
    return gate is not None and required_gib > 0 and gate.coverage.is_lower_bound


def collect_disk_roots(
    *,
    free_space_path: str | Path,
    benchmark_runs_dir: str | Path,
    docker_manage_platforms: bool = False,
    docker_data_root: str | Path | None = None,
) -> tuple[tuple[str, Path], ...]:
    runs_root = Path(benchmark_runs_dir).expanduser()
    roots: list[tuple[str, Path]] = [
        ("tmp", Path(tempfile.gettempdir()).expanduser()),
        ("output", runs_root),
        ("benchmark-data", runs_root / "datagen"),
    ]
    configured_free_space = Path(free_space_path).expanduser()
    if configured_free_space != runs_root:
        roots.append(("configured-free-space", configured_free_space))
    if docker_manage_platforms and docker_data_root is not None:
        roots.append(("docker-data", Path(docker_data_root).expanduser()))
    return tuple(roots)


def read_disk_root_free_space(
    roots: Iterable[tuple[str, Path]],
    *,
    free_space_reader: Callable[[str | Path], float] = free_space_gib,
) -> tuple[DiskRootFreeSpace, ...]:
    return tuple(DiskRootFreeSpace(label, path, free_space_reader(path)) for label, path in roots)


def format_free_space_report(
    entries: Iterable[DiskRootFreeSpace],
    *,
    required_gib: float,
    lower_bound: bool = False,
) -> tuple[str, ...]:
    marker = ">= " if lower_bound else ""
    return tuple(
        f"Free space: {entry.label:<21} {entry.free_gib:7.2f} GiB "
        f"(required {marker}{required_gib:.2f} GiB) {entry.path}"
        for entry in entries
    )


def format_disk_headroom_failure(check: DiskHeadroomCheck, budget: DiskBudget | None = None) -> str:
    from tests.uat.preflight_budget import format_disk_headroom_failure as _format

    return _format(check, budget)
