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
from benchbox.core.tuning.platform_capabilities import (
    UNSUPPORTED,
    WAIVED,
    map_candidate_to_platform,
    snowflake_cluster_rank,
)
from benchbox.core.tuning.profile_validation import (
    build_tuning_profile_metadata,
    candidate_key,
    validate_tuning_template,
)
from benchbox.core.tuning.workload_profiles import TEMPORAL_PARTITION, load_tpc_tuning_profile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
CHECKOUT_ROOT = REPO_ROOT
TUNING_ROOT = REPO_ROOT / "examples" / "tunings"


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds", "ssb"])
def test_databricks_templates_map_every_candidate_including_temporal_locality(benchmark_id: str) -> None:
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark=benchmark_id,
        platform="databricks",
        tuning_config=_load_tuning("databricks", benchmark_id),
    )

    assert result.is_valid, [issue.to_dict() for issue in result.issues]
    assert result.unsupported_count == 0
    assert result.mapped_count + result.capped_count == result.required_count
    assert all(not mapping.capped for mapping in result.mappings if TEMPORAL_PARTITION in mapping.candidate.roles)
    assert result.waived_count == 0


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds"])
def test_databricks_zorder_templates_lead_fact_clustering_with_date_column(benchmark_id: str) -> None:
    temporal = {
        (candidate.table, candidate.column)
        for candidate in load_tpc_tuning_profile().required_candidates(benchmark_id)
        if TEMPORAL_PARTITION in candidate.roles
    }
    assert temporal
    for root in (TUNING_ROOT / "databricks", REPO_ROOT / "benchbox" / "core" / "tuning" / "templates" / "databricks"):
        payload = yaml.safe_load((root / f"{benchmark_id}_tuned.yaml").read_text(encoding="utf-8"))
        for table, column in temporal:
            clustering = payload["table_tunings"][table]["clustering"]
            assert clustering[0]["name"] == column, f"{root}/{benchmark_id} {table}"
            assert len(clustering) <= 4


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds"])
def test_databricks_packaged_templates_match_examples(benchmark_id: str) -> None:
    example = (TUNING_ROOT / "databricks" / f"{benchmark_id}_tuned.yaml").read_text(encoding="utf-8")
    packaged = (
        REPO_ROOT / "benchbox" / "core" / "tuning" / "templates" / "databricks" / f"{benchmark_id}_tuned.yaml"
    ).read_text(encoding="utf-8")
    assert example == packaged


def test_databricks_zorder_maps_temporal_candidates_to_clustering() -> None:
    for candidate in load_tpc_tuning_profile().required_candidates("tpcds"):
        if TEMPORAL_PARTITION not in candidate.roles:
            continue
        mapping = map_candidate_to_platform("databricks", candidate)
        assert mapping.decision != UNSUPPORTED
        assert mapping.tuning_types == ("clustering",)
        assert "z_order" in mapping.physical_mechanisms


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
    assert coverage["mapped_count"] + coverage["capped_count"] == coverage["required_count"]
    assert coverage["unsupported_count"] == 0
    assert metadata["unmapped_logical_candidates"] == []
    assert metadata["validation_status"] == "passed"


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


SNOWFLAKE_FACT_TABLES = {
    "tpch": {"LINEITEM", "ORDERS"},
    "tpcds": {
        "STORE_SALES",
        "STORE_RETURNS",
        "CATALOG_SALES",
        "CATALOG_RETURNS",
        "WEB_SALES",
        "WEB_RETURNS",
        "INVENTORY",
    },
}


@pytest.mark.parametrize(
    ("platform", "benchmark_id"),
    [
        ("snowflake", "tpch"),
        ("snowflake", "tpcds"),
    ],
)
def test_cloud_tpc_tuned_templates_certify_fact_tables_against_logical_profile(
    platform: str, benchmark_id: str
) -> None:
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
    assert result.unsupported_count == 0
    assert result.mapped_count + result.capped_count + result.waived_count == result.required_count
    fact_tables = SNOWFLAKE_FACT_TABLES[benchmark_id]
    for mapping in result.mappings:
        if mapping.candidate.table in fact_tables:
            assert not mapping.waived
            continue
        assert mapping.waived
        assert mapping.platform_mapping.reason
    mapped_per_table: dict[str, int] = {}
    required_per_table: dict[str, int] = {}
    for mapping in result.mappings:
        required_per_table[mapping.candidate.table] = required_per_table.get(mapping.candidate.table, 0) + 1
        if mapping.mapped:
            mapped_per_table[mapping.candidate.table] = mapped_per_table.get(mapping.candidate.table, 0) + 1
    for table, required in required_per_table.items():
        if table in fact_tables:
            assert mapped_per_table.get(table, 0) == min(3, required)
        else:
            assert mapped_per_table.get(table, 0) == 0
    assert result.to_metadata()["validation_status"] == "passed"


def test_generated_cloud_templates_match_checked_in_files() -> None:
    script = CHECKOUT_ROOT / "scripts" / "generate_cloud_tpc_templates.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        cwd=CHECKOUT_ROOT,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_snowflake_templates_carry_at_most_three_clustering_columns() -> None:
    for benchmark_id in ("tpch", "tpcds"):
        payload = yaml.safe_load((TUNING_ROOT / "snowflake" / f"{benchmark_id}_tuned.yaml").read_text(encoding="utf-8"))
        for table, block in payload.get("table_tunings", {}).items():
            clustering = block.get("clustering", []) or []
            assert len(clustering) <= 3, f"snowflake/{benchmark_id} {table}: {len(clustering)} clustering columns"


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


@pytest.mark.parametrize(
    ("benchmark_id", "table", "expected_capped_columns"),
    [
        ("tpch", "LINEITEM", {"L_ORDERKEY"}),
        ("tpcds", "STORE_SALES", {"SS_ITEM_SK", "SS_CUSTOMER_SK", "SS_TICKET_NUMBER"}),
    ],
)
def test_snowflake_cap_counts_candidates_in_rank_order(
    benchmark_id: str, table: str, expected_capped_columns: set[str]
) -> None:
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark=benchmark_id,
        platform="snowflake",
        tuning_config=_load_tuning("snowflake", benchmark_id),
    )
    table_mappings = [mapping for mapping in result.mappings if mapping.candidate.table == table]
    ranks = [snowflake_cluster_rank(mapping.candidate) for mapping in table_mappings]
    assert ranks == sorted(ranks)
    mapped = [mapping.candidate.column for mapping in table_mappings if mapping.mapped]
    capped = {mapping.candidate.column for mapping in table_mappings if mapping.capped}
    assert len(mapped) == 3
    assert [mapping.candidate.column for mapping in table_mappings][:3] == mapped
    assert expected_capped_columns <= capped
    assert not any(not (mapping.mapped or mapping.capped) for mapping in table_mappings)


def test_failed_validation_status_and_reasons_reach_result_bundle() -> None:
    config = UnifiedTuningConfiguration(table_tunings={})
    metadata = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="duckdb",
        tuning_config=config,
    ).to_metadata()
    assert metadata["validation_status"] == "failed"
    result = BenchmarkResults(
        benchmark_name="TPC-H",
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

    logical_profile = build_result_payload(result)["platform"]["tuning"]["logical_profile"]

    assert logical_profile["validation_status"] == "failed"
    assert logical_profile["unmapped_candidates"]
    assert all(entry["reason"] for entry in logical_profile["unmapped_candidates"])


def test_profile_fact_tables_drive_snowflake_waivers_for_any_benchmark() -> None:
    profile = load_tpc_tuning_profile()
    for candidate in profile.required_candidates("ssb"):
        mapping = map_candidate_to_platform("snowflake", candidate)
        if candidate.table == "LINEORDER":
            assert candidate.fact_table
            assert mapping.decision == "mapped"
        else:
            assert not candidate.fact_table
            assert mapping.decision == WAIVED
            assert mapping.reason


def test_profile_loader_requires_fact_tables(tmp_path: Path) -> None:
    from benchbox.core.tuning.workload_profiles import load_workload_tuning_profile

    payload = yaml.safe_load((REPO_ROOT / "benchbox" / "core" / "tuning" / "profiles" / "tpc.yaml").read_text("utf-8"))
    del payload["profile"]["benchmarks"]["tpch"]["fact_tables"]
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="fact_tables"):
        load_workload_tuning_profile(path)


def test_validator_rejects_waivers_the_profile_does_not_support(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    from benchbox.core.tuning import profile_validation

    real_map = profile_validation.map_candidate_to_platform

    def waive_everything(platform, candidate, **kwargs):
        mapping = real_map(platform, candidate, **kwargs)
        return dataclasses.replace(mapping, decision=WAIVED, tuning_types=(), reason="waived for the test")

    monkeypatch.setattr(profile_validation, "map_candidate_to_platform", waive_everything)
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="snowflake",
        tuning_config=_load_tuning("snowflake", "tpch"),
    )

    assert not result.is_valid
    flagged = {issue.candidate_key for issue in result.issues}
    fact_candidates = {
        candidate_key(candidate)
        for candidate in load_tpc_tuning_profile().required_candidates("tpch")
        if candidate.fact_table
    }
    assert fact_candidates <= flagged


def test_snowflake_template_clustering_a_waived_dimension_table_fails_validation() -> None:
    tuning_config = _load_tuning("snowflake", "tpch")
    tuning_config.table_tunings["CUSTOMER"].clustering = [TuningColumn(name="C_CUSTKEY", type="INTEGER", order=1)]

    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="snowflake",
        tuning_config=tuning_config,
    )

    assert not result.is_valid
    assert result.to_metadata()["validation_status"] == "failed"
    assert [issue.candidate_key for issue in result.issues] == ["tpch.CUSTOMER.C_CUSTKEY"]
    assert "waived" in result.issues[0].message


def test_snowflake_template_keeps_unwaived_dimension_tables_unclustered_and_within_cap() -> None:
    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="snowflake",
        tuning_config=_load_tuning("snowflake", "tpch"),
    )
    assert result.is_valid
    waived_tables = {mapping.candidate.table for mapping in result.mappings if mapping.waived}
    assert waived_tables == {"CUSTOMER", "PART", "PARTSUPP", "SUPPLIER"}

    over_cap = _load_tuning("snowflake", "tpch")
    over_cap.table_tunings["LINEITEM"].clustering.append(TuningColumn(name="L_LINENUMBER", type="INTEGER", order=4))
    capped = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="snowflake",
        tuning_config=over_cap,
    )
    assert not capped.is_valid
    assert any("allows at most 3" in issue.message for issue in capped.issues)


def test_snowflake_template_clustering_a_waived_table_on_a_non_candidate_column_fails_validation() -> None:
    tuning_config = _load_tuning("snowflake", "tpch")
    tuning_config.table_tunings["CUSTOMER"].clustering = [TuningColumn(name="C_COMMENT", type="VARCHAR", order=1)]

    result = validate_tuning_template(
        profile=load_tpc_tuning_profile(),
        benchmark="tpch",
        platform="snowflake",
        tuning_config=tuning_config,
    )

    assert not result.is_valid
    assert [issue.candidate_key for issue in result.issues] == ["tpch.CUSTOMER.C_COMMENT"]
