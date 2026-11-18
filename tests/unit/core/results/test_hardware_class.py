from __future__ import annotations

import pytest

from benchbox.core.results.hardware_class import (
    BASIS_ATTESTED_COUNT_RESOLVED,
    BASIS_ATTESTED_MODEL,
    BASIS_CONTAINER_HOST_VENDOR,
    BASIS_COUNT_AMBIGUOUS,
    BASIS_COUNT_RESOLVED,
    BASIS_MANAGED,
    BASIS_MODEL_AND_COUNT,
    BASIS_MODEL_MISSING,
    BASIS_MODEL_UNKNOWN,
    CLASS_MANAGED,
    CLASS_UNCLASSIFIED,
    CPU_CLASS_ROWS,
    HARDWARE_TABLE_VERSION,
    TIER_EDGES,
    classify_hardware,
    table_rows,
    tier_for_score,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    ("score", "tier"),
    [(0.5, "T0"), (2.49, "T0"), (2.5, "T1"), (9.99, "T1"), (10.0, "T2"), (39.9, "T2"), (40.0, "T3"), (160.0, "T4")],
)
def test_tier_edges_are_log_bands_four_wide(score: float, tier: str) -> None:
    assert tier_for_score(score) == tier
    assert TIER_EDGES == (2.5, 10.0, 40.0, 160.0, 640.0)


@pytest.mark.parametrize(
    ("model", "count", "tier", "score"),
    [
        ("AMD EPYC 7763 64-Core Processor", 4, "T1", 4.0),
        ("Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz", 4, "T1", 4.32),
        ("AMD EPYC 9V74 80-Core Processor", 4, "T1", 5.24),
        ("INTEL(R) XEON(R) PLATINUM 8573C", 4, "T1", 5.32),
        ("Intel(R) Xeon(R) 6973P-C", 4, "T1", 6.56),
        ("AMD EPYC 9V45 96-Core Processor", 4, "T1", 6.72),
        ("Apple M4", 10, "T2", 22.7),
    ],
)
def test_corpus_cpus_land_in_the_documented_tier(model: str, count: int, tier: str, score: float) -> None:
    result = classify_hardware(cpu_model=model, cpu_count=count)

    assert (result.hardware_class, result.tier, result.score) == (tier, tier, score)
    assert result.basis == BASIS_MODEL_AND_COUNT
    assert result.table_version == HARDWARE_TABLE_VERSION


def test_two_cpu_models_with_different_per_thread_speed_can_split_on_core_count() -> None:
    small = classify_hardware(cpu_model="AMD EPYC 7763 64-Core Processor", cpu_count=4)
    large = classify_hardware(cpu_model="AMD EPYC 7763 64-Core Processor", cpu_count=16)

    assert (small.hardware_class, large.hardware_class) == ("T1", "T2")


def test_missing_count_resolves_only_when_every_shipped_count_shares_a_tier() -> None:
    resolved = classify_hardware(cpu_model="Apple M4", cpu_count=None)
    ambiguous = classify_hardware(cpu_model="AMD EPYC 7763 64-Core Processor", cpu_count=None)

    assert (resolved.hardware_class, resolved.score, resolved.basis) == ("T2", None, BASIS_COUNT_RESOLVED)
    assert (ambiguous.hardware_class, ambiguous.basis) == (CLASS_UNCLASSIFIED, BASIS_COUNT_AMBIGUOUS)


def test_container_with_a_limit_uses_the_host_factor_times_the_limit() -> None:
    with_host_model = classify_hardware(cpu_model="Apple M4", cpu_count=4, execution_class="container")
    vendor_only = classify_hardware(cpu_vendor="Apple", cpu_count=4, execution_class="container")
    host_os_only = classify_hardware(container_host_os="macOS", cpu_count=4, execution_class="container")
    other_vendor = classify_hardware(
        cpu_vendor="Intel", container_host_os="macOS", cpu_count=4, execution_class="container"
    )

    assert with_host_model.score == 9.08
    assert (vendor_only.hardware_class, vendor_only.score, vendor_only.basis) == (
        "T1",
        9.08,
        BASIS_CONTAINER_HOST_VENDOR,
    )
    assert (host_os_only.hardware_class, host_os_only.basis) == (CLASS_UNCLASSIFIED, BASIS_MODEL_MISSING)
    assert (other_vendor.hardware_class, other_vendor.basis) == (CLASS_UNCLASSIFIED, BASIS_MODEL_MISSING)


def test_a_container_on_an_unknown_host_is_unclassified() -> None:
    result = classify_hardware(cpu_count=4, execution_class="container")

    assert (result.hardware_class, result.basis) == (CLASS_UNCLASSIFIED, BASIS_MODEL_MISSING)


def test_missing_model_is_unclassified_unless_an_owner_attestation_supplies_it() -> None:
    bare = classify_hardware(cpu_model=None, cpu_count=None)
    attested = classify_hardware(cpu_model=None, cpu_count=None, attested_cpu_model="Apple M4")
    recorded_wins = classify_hardware(
        cpu_model="AMD EPYC 7763 64-Core Processor", cpu_count=4, attested_cpu_model="Apple M4"
    )

    assert (bare.hardware_class, bare.basis) == (CLASS_UNCLASSIFIED, BASIS_MODEL_MISSING)
    assert (attested.hardware_class, attested.basis) == ("T2", BASIS_ATTESTED_COUNT_RESOLVED)
    assert recorded_wins.hardware_class == "T1"
    assert classify_hardware(attested_cpu_model="Apple M4", cpu_count=10).basis == BASIS_ATTESTED_MODEL


def test_unknown_models_are_never_guessed() -> None:
    result = classify_hardware(cpu_model="Apple M4 Max", cpu_count=16)

    assert (result.hardware_class, result.basis, result.tier) == (CLASS_UNCLASSIFIED, BASIS_MODEL_UNKNOWN, None)


@pytest.mark.parametrize("runtime", ["managed_cloud", "serverless"])
def test_managed_services_are_one_class_whatever_the_client_cpu(runtime: str) -> None:
    result = classify_hardware(cpu_model="Apple M4", cpu_count=10, runtime_type=runtime)

    assert (result.hardware_class, result.tier, result.basis) == (CLASS_MANAGED, None, BASIS_MANAGED)


def test_a_remote_server_run_does_not_inherit_the_client_cpu() -> None:
    result = classify_hardware(cpu_model="Apple M4", cpu_count=10, runtime_type="remote_server")

    assert result.hardware_class == CLASS_UNCLASSIFIED


def test_every_row_records_source_retrieval_date_and_estimate_flag() -> None:
    for row in table_rows():
        assert row["source"] and row["retrieved"] == "2026-11-17"
        assert isinstance(row["estimated"], bool)
        assert row["estimated"] == (row["stand_in"] is not None or row["cpu"] == "Apple M4")
    assert len(CPU_CLASS_ROWS) == len(table_rows())
