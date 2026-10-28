from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tests.uat.compatibility import CompatibilityRule, compatibility_rule_for
from tests.uat.config import UATConfig
from tests.uat.matrix import (
    BenchmarkInfo,
    filter_scales_by_registry,
    load_benchmarks,
    missing_benchmarks_from_include,
    missing_platforms_from_include,
    resolve_benchmarks,
    resolve_platforms,
)

REGISTRY_PRUNE_STATUS = "pruned-registry"
BENCHMARK_NOT_IN_REGISTRY_RULE_ID = "benchmark-not-in-registry"
PLATFORM_NOT_IN_REGISTRY_RULE_ID = "platform-not-in-registry"


@dataclass(frozen=True)
class Cell:
    platform: str
    benchmark: str
    scale: float


@dataclass(frozen=True)
class CompatibilityPrunedCell:
    platform: str
    benchmark: str
    scale: float
    rule_id: str
    status: str
    reason: str
    evidence: str


@dataclass(frozen=True)
class EnumerationResult:
    cells: tuple[Cell, ...]
    compatibility_pruned: tuple[CompatibilityPrunedCell, ...]

    @property
    def candidate_count(self) -> int:
        return len(self.cells) + len(self.compatibility_pruned)


def is_registry_prune(cell: CompatibilityPrunedCell) -> bool:
    return cell.status == REGISTRY_PRUNE_STATUS


def count_pruned_by_kind(pruned: Iterable[CompatibilityPrunedCell]) -> tuple[int, int]:
    rows = tuple(pruned)
    registry = sum(1 for cell in rows if is_registry_prune(cell))
    return len(rows) - registry, registry


def enumerate_cells(
    config: UATConfig,
    *,
    benchmarks: dict[str, BenchmarkInfo] | None = None,
) -> list[Cell]:
    return list(enumerate_cells_with_pruning(config, benchmarks=benchmarks).cells)


def resolved_platforms(config: UATConfig) -> list[str]:
    platform_groups_default = ("sql",) if config.platforms.uses_implicit_group_default else ()
    return resolve_platforms(
        groups=config.platforms.groups if config.platforms.groups is not None else platform_groups_default,
        include=config.platforms.include,
        exclude=config.platforms.exclude,
    )


def enumerate_cells_with_pruning(
    config: UATConfig,
    *,
    benchmarks: dict[str, BenchmarkInfo] | None = None,
) -> EnumerationResult:
    benchmarks = benchmarks if benchmarks is not None else load_benchmarks()

    benchmark_groups_default = ("all",) if config.benchmarks.uses_implicit_group_default else ()
    platform_list = resolved_platforms(config)
    benchmark_list = resolve_benchmarks(
        groups=config.benchmarks.groups if config.benchmarks.groups is not None else benchmark_groups_default,
        include=config.benchmarks.include,
        exclude=config.benchmarks.exclude,
        benchmarks=benchmarks,
    )

    requested = list(config.scales.requested_rungs)

    cells: list[Cell] = []
    compatibility_pruned: list[CompatibilityPrunedCell] = []
    include_release_gate_runtime_envelopes = config.compatibility.release_gate_runtime_envelopes

    missing_benchmarks = missing_benchmarks_from_include(config.benchmarks.include, benchmarks)
    missing_platforms = missing_platforms_from_include(config.platforms.include)
    accounting_platforms = [*platform_list, *missing_platforms]
    accounting_benchmarks = [*benchmark_list, *missing_benchmarks]
    for missing_benchmark in missing_benchmarks:
        for platform in accounting_platforms:
            compatibility_pruned.extend(
                _registry_absent_rows(platform=platform, benchmark=missing_benchmark, requested=requested)
            )

    for missing_platform in missing_platforms:
        for benchmark in accounting_benchmarks:
            compatibility_pruned.extend(
                _registry_absent_platform_rows(platform=missing_platform, benchmark=benchmark, requested=requested)
            )

    for platform in platform_list:
        for benchmark in benchmark_list:
            info = benchmarks.get(benchmark)
            if info is None:
                compatibility_pruned.extend(
                    _registry_absent_rows(platform=platform, benchmark=benchmark, requested=requested)
                )
                continue
            rule = compatibility_rule_for(
                platform,
                benchmark,
                info,
                include_release_gate_runtime_envelopes=include_release_gate_runtime_envelopes,
            )
            if rule is not None:
                compatibility_pruned.extend(
                    _pruned_rows_for_rule(platform=platform, benchmark=benchmark, requested=requested, rule=rule)
                )
                continue
            filtered = filter_scales_by_registry(benchmark, requested, info=info)
            dropped = [scale for scale in requested if scale not in filtered]
            uses_min_scale_fallback = not filtered and config.scales.override is None and info.min_scale is not None
            if uses_min_scale_fallback:
                filtered = [info.min_scale]
            elif dropped:
                compatibility_pruned.extend(
                    _ladder_pruned_rows(platform=platform, benchmark=benchmark, dropped=dropped)
                )
            for scale in filtered:
                cells.append(Cell(platform=platform, benchmark=benchmark, scale=scale))
    return EnumerationResult(cells=tuple(cells), compatibility_pruned=tuple(compatibility_pruned))


def _registry_absent_rows(
    *,
    platform: str,
    benchmark: str,
    requested: list[float],
) -> list[CompatibilityPrunedCell]:
    return [
        CompatibilityPrunedCell(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            rule_id=BENCHMARK_NOT_IN_REGISTRY_RULE_ID,
            status=REGISTRY_PRUNE_STATUS,
            reason=(
                f"Benchmark {benchmark!r} was requested in the config but is not present in the "
                "benchmark registry (typo'd or removed benchmark id)."
            ),
            evidence="tests.uat.matrix.load_benchmarks() BENCHMARK_METADATA keys",
        )
        for scale in requested
    ]


def _registry_absent_platform_rows(
    *,
    platform: str,
    benchmark: str,
    requested: list[float],
) -> list[CompatibilityPrunedCell]:
    return [
        CompatibilityPrunedCell(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            rule_id=PLATFORM_NOT_IN_REGISTRY_RULE_ID,
            status=REGISTRY_PRUNE_STATUS,
            reason=(
                f"Platform {platform!r} was requested in the config but is not present in the "
                "platform registry (typo'd or removed platform id)."
            ),
            evidence="tests.uat.matrix.known_platform_ids() PlatformRegistry SQL/dataframe platforms",
        )
        for scale in requested
    ]


def _ladder_pruned_rows(
    *,
    platform: str,
    benchmark: str,
    dropped: list[float],
) -> list[CompatibilityPrunedCell]:
    return [
        CompatibilityPrunedCell(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            rule_id=f"uat.ladder.{benchmark}.scale-not-in-registry",
            status=REGISTRY_PRUNE_STATUS,
            reason=(
                f"scale {scale} is outside {benchmark}'s registered scale_options and was dropped "
                "by the scale-ladder registry filter."
            ),
            evidence="tests.uat.matrix.filter_scales_by_registry against benchmark registry scale_options",
        )
        for scale in dropped
    ]


def _pruned_rows_for_rule(
    *,
    platform: str,
    benchmark: str,
    requested: list[float],
    rule: CompatibilityRule,
) -> list[CompatibilityPrunedCell]:
    return [
        CompatibilityPrunedCell(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            rule_id=rule.rule_id,
            status=rule.status,
            reason=rule.reason,
            evidence=rule.evidence,
        )
        for scale in requested
    ]
