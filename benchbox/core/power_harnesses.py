from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhaseHarnessCapability:
    benchmark_id: str
    adapter_method: str


PowerHarnessCapability = PhaseHarnessCapability


@dataclass(frozen=True)
class CombinedHarnessCapability:
    benchmark_id: str
    label: str
    power_method: str
    throughput_method: str
    maintenance_method: str
    spec_sequence: tuple[str, ...] = ()


POWER_HARNESS_CAPABILITIES: tuple[PhaseHarnessCapability, ...] = (
    PhaseHarnessCapability("tpch", "_execute_tpch_power_test"),
    PhaseHarnessCapability("tpcds", "_execute_tpcds_power_test"),
)

THROUGHPUT_HARNESS_CAPABILITIES: tuple[PhaseHarnessCapability, ...] = (
    PhaseHarnessCapability("tpch", "_execute_tpch_throughput_test"),
    PhaseHarnessCapability("tpcds", "_execute_tpcds_throughput_test"),
)

MAINTENANCE_HARNESS_CAPABILITIES: tuple[PhaseHarnessCapability, ...] = (
    PhaseHarnessCapability("tpch", "_execute_tpch_maintenance_test"),
    PhaseHarnessCapability("tpcds", "_execute_tpcds_maintenance_test"),
)

COMBINED_HARNESS_CAPABILITIES: tuple[CombinedHarnessCapability, ...] = (
    CombinedHarnessCapability(
        "tpch", "TPC-H", "_execute_tpch_power_test", "_execute_tpch_throughput_test", "_execute_tpch_maintenance_test"
    ),
    CombinedHarnessCapability(
        "tpcds",
        "TPC-DS",
        "_execute_tpcds_power_test",
        "_execute_tpcds_throughput_test",
        "_execute_tpcds_maintenance_test",
        spec_sequence=("power", "throughput", "maintenance", "throughput", "maintenance"),
    ),
)

_POWER_HARNESS_BY_ID = {capability.benchmark_id: capability for capability in POWER_HARNESS_CAPABILITIES}
_THROUGHPUT_HARNESS_BY_ID = {capability.benchmark_id: capability for capability in THROUGHPUT_HARNESS_CAPABILITIES}
_MAINTENANCE_HARNESS_BY_ID = {capability.benchmark_id: capability for capability in MAINTENANCE_HARNESS_CAPABILITIES}
_COMBINED_HARNESS_BY_ID = {capability.benchmark_id: capability for capability in COMBINED_HARNESS_CAPABILITIES}


def resolve_power_harness(benchmark_id: str) -> PhaseHarnessCapability | None:
    return _POWER_HARNESS_BY_ID.get(benchmark_id)


def resolve_throughput_harness(benchmark_id: str) -> PhaseHarnessCapability | None:
    return _THROUGHPUT_HARNESS_BY_ID.get(benchmark_id)


def resolve_maintenance_harness(benchmark_id: str) -> PhaseHarnessCapability | None:
    return _MAINTENANCE_HARNESS_BY_ID.get(benchmark_id)


def resolve_combined_harness(benchmark_id: str) -> CombinedHarnessCapability | None:
    return _COMBINED_HARNESS_BY_ID.get(benchmark_id)
