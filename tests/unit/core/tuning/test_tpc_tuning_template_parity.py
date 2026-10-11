from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from benchbox.cli.config import ConfigManager
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload, build_tuning_payload
from benchbox.core.tuning.interface import TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.platform_capabilities import UNSUPPORTED, map_candidate_to_platform
from benchbox.core.tuning.profile_validation import (
    build_tuning_profile_metadata,
    candidate_key,
    validate_tuning_template,
)
from benchbox.core.tuning.workload_profiles import TEMPORAL_PARTITION, load_tpc_tuning_profile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
CHECKOUT_ROOT = REPO_ROOT
TUNING_ROOT = REPO_ROOT / "examples" / "tunings"


def _databricks_temporal_partition_candidates(benchmark_id: str) -> set[str]:
    return {
        candidate_key(candidate)
        for candidate in load_tpc_tuning_profile().required_candidates(benchmark_id)
        if TEMPORAL_PARTITION in candidate.roles
    }


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds", "ssb"])
def test_databricks_templates_map_every_candidate_except_temporal_partitions(benchmark_id: str) -> None:
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark=benchmark_id,
        platform="databricks",
        tuning_config=_load_tuning("databricks", benchmark_id),
    )

    unsupported = {
        mapping.candidate_key for mapping in result.mappings if mapping.platform_mapping.decision == UNSUPPORTED
    }
    assert result.is_valid, [issue.to_dict() for issue in result.issues]
    assert unsupported == _databricks_temporal_partition_candidates(benchmark_id)
    assert result.mapped_count + result.unsupported_count == result.required_count
    assert result.waived_count == 0
    reasons = {
        mapping.platform_mapping.reason
        for mapping in result.mappings
        if mapping.platform_mapping.decision == UNSUPPORTED
    }
    assert reasons, "temporal-partition exclusions must name the vendor reason"
    assert all("Databricks" in reason for reason in reasons)


def _duckdb_temporal_partition_candidates(benchmark_id: str) -> set[str]:
    return {
        candidate_key(candidate)
        for candidate in load_tpc_tuning_profile().required_candidates(benchmark_id)
        if TEMPORAL_PARTITION in candidate.roles
    }


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds", "ssb"])
def test_duckdb_templates_map_every_candidate_except_temporal_partitions(benchmark_id: str) -> None:
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark=benchmark_id,
        platform="duckdb",
        tuning_config=_load_tuning("duckdb", benchmark_id),
    )

    unsupported = {
        mapping.candidate_key for mapping in result.mappings if mapping.platform_mapping.decision == UNSUPPORTED
    }
    assert result.is_valid, [issue.to_dict() for issue in result.issues]
    assert unsupported == _duckdb_temporal_partition_candidates(benchmark_id)
    assert result.mapped_count + result.unsupported_count == result.required_count
    assert result.waived_count == 0


@pytest.mark.parametrize(
    ("filename", "benchmark_id"),
    [("tpch_liquid_tuned.yaml", "tpch"), ("tpcds_liquid_tuned.yaml", "tpcds")],
)
def test_databricks_liquid_templates_map_same_logical_profile_with_distinct_rendering(
    filename: str,
    benchmark_id: str,
) -> None:
    tuning_config = ConfigManager().load_unified_tuning_config(
        TUNING_ROOT / "databricks" / filename,
        platform="databricks",
    )
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark=benchmark_id,
        platform="databricks",
        tuning_config=tuning_config,
    )

    assert result.is_valid, [issue.to_dict() for issue in result.issues]
    assert result.mapped_count == result.required_count
    metadata = result.to_metadata()
    assert metadata["physical_rendering_id"] == "databricks_liquid_auto"
    assert "liquid_clustering_auto" in metadata["platform_physical_tuning_mechanisms"]
    assert "z_order" not in metadata["platform_physical_tuning_mechanisms"]
    assert "distribution" not in metadata["platform_physical_tuning_mechanisms"]


def test_duckdb_maps_logical_profile_to_sorting_not_databricks_clustering() -> None:
    tuning_config = _load_tuning("duckdb", "tpch")
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="duckdb",
        tuning_config=tuning_config,
    )

    supplier_key = next(mapping for mapping in result.mappings if mapping.candidate_key == "tpch.LINEITEM.L_SUPPKEY")
    assert supplier_key.mapped_tuning_types == ("sorting",)
    assert "sorting" in supplier_key.platform_mapping.physical_mechanisms
    assert "z_order" not in supplier_key.platform_mapping.physical_mechanisms


def test_dropped_low_evidence_candidates_fail_when_reintroduced() -> None:
    tuning_config = _load_tuning("duckdb", "tpcds")
    tuning_config.table_tunings["WEB_RETURNS"].sorting.append(
        TuningColumn(name="WR_WEB_PAGE_SK", type="INTEGER", order=2)
    )

    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpcds",
        platform="duckdb",
        tuning_config=tuning_config,
    )

    assert not result.is_valid
    assert any(issue.candidate_key == "tpcds.WEB_RETURNS.WR_WEB_PAGE_SK" for issue in result.issues)


def test_multi_mechanism_candidates_fail_when_one_mapping_type_is_lost() -> None:
    tuning_config = _load_tuning("databricks", "tpch")
    tuning_config.table_tunings["LINEITEM"].clustering = []

    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="databricks",
        tuning_config=tuning_config,
    )

    assert not result.is_valid
    assert any(
        issue.candidate_key == "tpch.LINEITEM.L_ORDERKEY" and "clustering" in issue.message for issue in result.issues
    )
    assert {
        unmapped["candidate"]: unmapped.get("missing_tuning_types", [])
        for unmapped in result.unmapped_logical_candidates
    }["tpch.LINEITEM.L_ORDERKEY"] == ["clustering"]


def test_databricks_z_order_mapping_does_not_report_distribution_as_physical_mechanism() -> None:
    tuning_config = _load_tuning("databricks", "tpch")
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="databricks",
        tuning_config=tuning_config,
    )

    assert result.to_metadata()["physical_rendering_id"] == "databricks_z_order"
    assert "z_order" in result.physical_mechanisms
    assert "distribution" not in result.physical_mechanisms


def test_future_platform_mappings_expose_distribution_limitations() -> None:
    profile = load_tpc_tuning_profile()
    order_key = next(
        candidate
        for candidate in profile.required_candidates("tpch")
        if candidate.table == "ORDERS" and candidate.column == "O_ORDERKEY"
    )

    bigquery = map_candidate_to_platform("bigquery", order_key)
    redshift = map_candidate_to_platform("redshift", order_key)
    snowflake = map_candidate_to_platform("snowflake", order_key)

    assert bigquery.tuning_types == ("clustering",)
    assert "no user-visible distribution key" in bigquery.reason
    assert redshift.tuning_types == ("distribution", "sorting")
    assert redshift.max_columns == 1
    assert snowflake.tuning_types == ("clustering",)
    assert "no user-managed distribution key" in snowflake.reason


def test_tuning_profile_metadata_exposes_comparison_semantics() -> None:
    metadata = build_tuning_profile_metadata(
        benchmark="tpch",
        platform="databricks",
        tuning_config=_load_tuning("databricks", "tpch"),
    )

    assert metadata is not None
    assert metadata["logical_tuning_profile_id"] == "tpc-v1"
    assert metadata["physical_rendering_id"] == "databricks_z_order"
    assert "z_order" in metadata["platform_physical_tuning_mechanisms"]
    coverage = metadata["logical_profile_coverage"]
    temporal = _databricks_temporal_partition_candidates("tpch")
    assert coverage["mapped_count"] + coverage["unsupported_count"] == coverage["required_count"]
    assert coverage["unsupported_count"] == len(temporal)
    assert {entry["candidate"] for entry in metadata["unmapped_logical_candidates"]} == temporal
    assert {entry["decision"] for entry in metadata["unmapped_logical_candidates"]} == {UNSUPPORTED}


def test_duckdb_tuning_profile_metadata_reports_temporal_partitions_as_unsupported() -> None:
    metadata = build_tuning_profile_metadata(
        benchmark="tpch",
        platform="duckdb",
        tuning_config=_load_tuning("duckdb", "tpch"),
    )

    assert metadata is not None
    assert metadata["logical_tuning_profile_id"] == "tpc-v1"
    assert metadata["physical_rendering_id"] == "duckdb"
    assert "sorting" in metadata["platform_physical_tuning_mechanisms"]
    coverage = metadata["logical_profile_coverage"]
    temporal = _duckdb_temporal_partition_candidates("tpch")
    assert coverage["mapped_count"] + coverage["unsupported_count"] == coverage["required_count"]
    assert coverage["unsupported_count"] == len(temporal)
    assert {entry["candidate"] for entry in metadata["unmapped_logical_candidates"]} == temporal
    assert {entry["decision"] for entry in metadata["unmapped_logical_candidates"]} == {UNSUPPORTED}


def test_tuning_profile_metadata_is_omitted_for_basic_constraints_fallback() -> None:
    metadata = build_tuning_profile_metadata(
        benchmark="tpch",
        platform="duckdb",
        tuning_config=UnifiedTuningConfiguration(),
    )

    assert metadata is None


def test_tuning_profile_metadata_normalizes_display_benchmark_names() -> None:
    metadata = build_tuning_profile_metadata(
        benchmark="TPC-H Benchmark",
        platform="duckdb",
        tuning_config=_load_tuning("duckdb", "tpch"),
    )

    assert metadata is not None
    assert metadata["logical_tuning_profile_id"] == "tpc-v1"
    assert metadata["logical_profile_coverage"]["unmapped_count"] == len(_duckdb_temporal_partition_candidates("tpch"))


def test_tuning_companion_payload_carries_logical_profile_metadata() -> None:
    metadata = build_tuning_profile_metadata(
        benchmark="tpch",
        platform="duckdb",
        tuning_config=_load_tuning("duckdb", "tpch"),
    )
    result = SimpleNamespace(
        execution_id="run-1",
        tunings_applied={"configuration": {"tuning_mode": "tuned"}},
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash="abc123",
        tuning_validation_status="applied_unverified",
        execution_metadata={"tuning_profile": metadata},
    )

    payload = build_tuning_payload(result)

    assert payload is not None
    assert payload["logical_profile"]["logical_tuning_profile_id"] == "tpc-v1"
    assert payload["logical_profile"]["logical_profile_coverage"]["unmapped_count"] == len(
        _duckdb_temporal_partition_candidates("tpch")
    )


def test_result_payload_platform_tuning_summary_carries_logical_profile_metadata() -> None:
    metadata = build_tuning_profile_metadata(
        benchmark="tpch",
        platform="duckdb",
        tuning_config=_load_tuning("duckdb", "tpch"),
    )
    result = BenchmarkResults(
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=0.01,
        execution_id="run-1",
        timestamp=datetime(2026, 5, 26),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "run_type": "measurement",
            }
        ],
        tunings_applied={"configuration": {"tuning_mode": "tuned"}},
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash="abc123",
        tuning_validation_status="applied_unverified",
        execution_metadata={"tuning_profile": metadata},
    )

    payload = build_result_payload(result)

    logical_profile = payload["platform"]["tuning"]["logical_profile"]
    assert logical_profile["id"] == "tpc-v1"
    assert logical_profile["physical_rendering_id"] == "duckdb"
    assert "sorting" in logical_profile["physical_mechanisms"]
    assert logical_profile["coverage"]["unmapped_count"] == len(_duckdb_temporal_partition_candidates("tpch"))


def _load_tuning(platform: str, benchmark: str):
    return ConfigManager().load_unified_tuning_config(
        TUNING_ROOT / platform / f"{benchmark}_tuned.yaml",
        platform=platform,
    )


@pytest.mark.parametrize(
    ("platform", "benchmark_id"),
    [
        ("snowflake", "tpch"),
        ("snowflake", "tpcds"),
    ],
)
def test_cloud_tpc_tuned_templates_certify_against_logical_profile(platform: str, benchmark_id: str) -> None:
    tuning_config = ConfigManager().load_unified_tuning_config(
        TUNING_ROOT / platform / f"{benchmark_id}_tuned.yaml",
        platform=platform,
    )
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark=benchmark_id,
        platform=platform,
        tuning_config=tuning_config,
    )

    assert result.is_valid, [issue.to_dict() for issue in result.issues]

    assert result.mapped_count + result.capped_count == result.required_count
    assert result.unsupported_count == 0
    assert result.waived_count == 0


def test_generated_cloud_templates_match_checked_in_files() -> None:
    script = CHECKOUT_ROOT / "scripts" / "generate_cloud_tpc_templates.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        cwd=CHECKOUT_ROOT,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_snowflake_templates_carry_at_most_four_clustering_columns() -> None:
    for benchmark_id in ("tpch", "tpcds"):
        payload = yaml.safe_load((TUNING_ROOT / "snowflake" / f"{benchmark_id}_tuned.yaml").read_text(encoding="utf-8"))
        for table, block in payload.get("table_tunings", {}).items():
            clustering = block.get("clustering", []) or []
            assert len(clustering) <= 4, f"snowflake/{benchmark_id} {table}: {len(clustering)} clustering columns"


def test_bigquery_and_redshift_templates_stay_out_of_the_certified_set() -> None:
    for platform in ("bigquery", "redshift"):
        for benchmark_id in ("tpch", "tpcds"):
            assert not (TUNING_ROOT / platform / f"{benchmark_id}_tuned.yaml").exists(), (
                f"{platform}/{benchmark_id}_tuned.yaml is checked in but its layouts are preview-only; "
                "wire the adapter rendering first, then re-certify"
            )


def test_bigquery_cap_overflow_is_capped_not_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.core.tuning import profile_validation
    from benchbox.core.tuning.workload_profiles import (
        ACCEPTED,
        WorkloadTuningCandidate,
        WorkloadTuningProfile,
    )

    candidates = tuple(
        WorkloadTuningCandidate(
            benchmark="tpch",
            table="ORDERS",
            column=f"C_CLUSTER_{index}",
            type="INTEGER",
            roles=("join_locality",),
            query_count=3,
            query_ids=("Q3", "Q5", "Q10"),
            status=ACCEPTED,
            rationale="synthetic cap-overflow fixture",
            evidence_source="unit-test",
        )
        for index in range(7)
    )
    profile = WorkloadTuningProfile(
        id="synthetic-cap",
        version="test",
        description="synthetic BigQuery cap-overflow fixture",
        candidates_by_benchmark={"tpch": candidates},
    )
    tuning_config = SimpleNamespace(
        table_tunings={
            "ORDERS": SimpleNamespace(
                table_name="ORDERS",
                clustering=[
                    SimpleNamespace(name=f"C_CLUSTER_{index}", type="INTEGER", order=index + 1) for index in range(4)
                ],
            )
        }
    )
    monkeypatch.setattr(
        profile_validation,
        "_rendering_verified_tuning_types",
        lambda _platform, _config: frozenset({"clustering"}),
    )
    result = validate_tuning_template(
        profile=profile,
        benchmark="tpch",
        platform="bigquery",
        tuning_config=tuning_config,
    )

    assert result.is_valid
    clustered = [m for m in result.mappings if "clustering" in m.mapped_tuning_types]
    assert len(clustered) == 4


@pytest.mark.parametrize(
    ("second_distribution", "include_sort"),
    [(False, True), (True, True), (True, False)],
    ids=["locality-only", "distribution-capped", "capped-with-missing-sort"],
)
def test_redshift_sorting_is_not_capped_by_the_distkey_limit(
    monkeypatch: pytest.MonkeyPatch, second_distribution: bool, include_sort: bool
) -> None:
    from benchbox.core.tuning import profile_validation
    from benchbox.core.tuning.workload_profiles import (
        ACCEPTED,
        WorkloadTuningCandidate,
        WorkloadTuningProfile,
    )

    profile = WorkloadTuningProfile(
        id="synthetic-redshift-cap",
        version="test",
        description="synthetic Redshift DISTKEY-scope fixture",
        candidates_by_benchmark={
            "tpch": (
                WorkloadTuningCandidate(
                    benchmark="tpch",
                    table="ORDERS",
                    column="O_ORDERKEY",
                    type="INTEGER",
                    roles=("distribution_candidate", "join_locality"),
                    query_count=3,
                    query_ids=("Q3", "Q5", "Q10"),
                    status=ACCEPTED,
                    rationale="synthetic DISTKEY-scope fixture",
                    evidence_source="unit-test",
                ),
                WorkloadTuningCandidate(
                    benchmark="tpch",
                    table="ORDERS",
                    column="O_CUSTKEY",
                    type="INTEGER",
                    roles=(("distribution_candidate",) if second_distribution else ()) + ("join_locality",),
                    query_count=3,
                    query_ids=("Q3", "Q5", "Q10"),
                    status=ACCEPTED,
                    rationale="synthetic DISTKEY-scope fixture",
                    evidence_source="unit-test",
                ),
            )
        },
    )
    tuning_config = SimpleNamespace(
        table_tunings={
            "ORDERS": SimpleNamespace(
                table_name="ORDERS",
                distribution=[SimpleNamespace(name="O_ORDERKEY", type="INTEGER", order=1)],
                sorting=[
                    SimpleNamespace(name="O_ORDERKEY", type="INTEGER", order=1),
                    SimpleNamespace(name="O_CUSTKEY", type="INTEGER", order=2),
                ],
            )
        },
        platform_optimizations=SimpleNamespace(sorted_ingestion_mode="force"),
    )
    monkeypatch.setattr(
        profile_validation,
        "_rendering_verified_tuning_types",
        lambda _platform, _config: frozenset({"distribution", "sorting"}),
    )
    if not include_sort:
        tuning_config.table_tunings["ORDERS"].sorting.pop()
    result = validate_tuning_template(
        profile=profile,
        benchmark="tpch",
        platform="redshift",
        tuning_config=tuning_config,
    )

    custkey = next(m for m in result.mappings if m.candidate_key == "tpch.ORDERS.O_CUSTKEY")
    if not include_sort:
        assert not result.is_valid
        assert any("sorting" in issue.message for issue in result.issues)
        metadata = result.to_metadata()
        assert metadata["logical_profile_coverage"]["unmapped_count"] == 1
        assert metadata["logical_profile_coverage"]["capped_count"] == 0
        assert metadata["unmapped_logical_candidates"][0]["candidate"] == custkey.candidate_key
        assert metadata["unmapped_logical_candidates"][0]["missing_tuning_types"] == ["sorting"]
        assert not custkey.capped
    elif second_distribution:
        assert result.is_valid, [issue.to_dict() for issue in result.issues]
        assert result.mapped_count + result.capped_count == result.required_count
        assert custkey.capped
        assert custkey.mapped_tuning_types == ("sorting",)
        assert result.to_metadata()["unmapped_logical_candidates"] == []
    else:
        assert result.is_valid, [issue.to_dict() for issue in result.issues]
        assert result.mapped_count == result.required_count
        assert custkey.mapped
        assert custkey.mapped_tuning_types == ("sorting",)
