from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from benchbox.core.results.environment import ContainerEnvironment, NormalizedExecutionEnvironment
from benchbox.core.tuning import template_evidence
from benchbox.core.tuning.template_evidence import (
    EVIDENCE_STATES,
    evidence_state,
    evidence_state_for_run,
    evidence_warning,
    is_tuned_template_ref,
    load_registry,
    normalize_benchmark,
    template_cell,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGED_ROOT = REPO_ROOT / "benchbox" / "core" / "tuning" / "templates"
EXAMPLES_ROOT = REPO_ROOT / "examples" / "tunings"

REQUIRED_ENTRY_KEYS = ("measurements",)
REQUIRED_MEASUREMENT_KEYS = ("state", "scale_factor", "memory", "engine_version", "measured_on", "evidence")


def _measurement(state, *, scale_factor=1.0, memory=None, engine_version="v1"):
    if memory is None:
        memory = {"basis": "host_total_gb", "value": 16}
    return {
        "state": state,
        "scale_factor": scale_factor,
        "memory": memory,
        "engine_version": engine_version,
        "measured_on": "2026-10-11",
        "evidence": "test evidence",
    }


def _shipped_tuned_templates() -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    for root in (PACKAGED_ROOT, EXAMPLES_ROOT):
        for path in sorted(root.glob("*/*_tuned.yaml")):
            stem = path.stem
            if stem.endswith("_liquid_tuned"):
                benchmark = stem[: -len("_liquid_tuned")]
            else:
                benchmark = stem[: -len("_tuned")]
            found.append((path.parent.name.lower(), benchmark.lower(), path))
    return found


def test_registry_covers_every_shipped_tuned_template():
    registry = load_registry(refresh=True)
    missing = [
        f"{platform}/{benchmark} ({path})"
        for platform, benchmark, path in _shipped_tuned_templates()
        if not isinstance(registry.get(platform), dict) or not isinstance(registry[platform].get(benchmark), dict)
    ]
    assert _shipped_tuned_templates(), "no shipped tuned templates found"
    assert missing == []


def test_registry_has_no_entry_without_a_shipped_template():
    shipped = {(platform, benchmark) for platform, benchmark, _ in _shipped_tuned_templates()}
    registry = load_registry(refresh=True)
    stale = [
        f"{platform}/{benchmark}"
        for platform, entries in registry.items()
        if isinstance(entries, dict)
        for benchmark in entries
        if (platform, benchmark) not in shipped
    ]
    assert stale == []


def test_registry_entries_carry_tiered_evidence():
    registry = load_registry(refresh=True)
    assert registry, "registry is empty"
    for platform, entries in registry.items():
        assert isinstance(entries, dict)
        for benchmark, entry in entries.items():
            assert isinstance(entry, dict), f"{platform}/{benchmark}"
            assert set(entry) == set(REQUIRED_ENTRY_KEYS), f"{platform}/{benchmark}"
            assert isinstance(entry["measurements"], list), f"{platform}/{benchmark}"
            scopes = set()
            for measurement in entry["measurements"]:
                assert isinstance(measurement, dict), f"{platform}/{benchmark}"
                assert set(measurement) == set(REQUIRED_MEASUREMENT_KEYS), f"{platform}/{benchmark}"
                assert measurement["state"] in EVIDENCE_STATES, f"{platform}/{benchmark}"
                assert isinstance(measurement["scale_factor"], (int, float)) and not isinstance(
                    measurement["scale_factor"], bool
                )
                assert measurement["scale_factor"] > 0
                memory = measurement["memory"]
                assert isinstance(memory, dict), f"{platform}/{benchmark}"
                assert set(memory) == {"basis", "value"}, f"{platform}/{benchmark}"
                assert memory["basis"] in {"container_limit", "host_total_gb", "warehouse_size"}
                value = memory["value"]
                if memory["basis"] == "host_total_gb":
                    assert isinstance(value, (int, float)) and not isinstance(value, bool)
                    assert value > 0
                elif memory["basis"] == "warehouse_size":
                    assert isinstance(value, str) and value.strip(), f"{platform}/{benchmark}"
                else:
                    assert isinstance(value, (str, int, float)) and not isinstance(value, bool)
                    assert value.strip() if isinstance(value, str) else value > 0
                assert isinstance(measurement["engine_version"], str) and measurement["engine_version"].strip()
                assert isinstance(measurement["measured_on"], str) and measurement["measured_on"]
                assert isinstance(measurement["evidence"], str) and measurement["evidence"].strip()
                scope = (
                    measurement["scale_factor"],
                    memory["basis"],
                    memory["value"],
                    measurement["engine_version"],
                )
                assert scope not in scopes, f"duplicate evidence scope for {platform}/{benchmark}"
                scopes.add(scope)


def test_evidence_state_resolves_known_cells():
    assert evidence_state("duckdb", "tpch") == "unmeasured"
    assert evidence_state("DuckDB", "TPCH") == "unmeasured"
    assert evidence_state("databricks", "tpcds") == "unmeasured"
    assert evidence_state("snowflake", "tpch") == "unmeasured"
    assert evidence_state("clickhouse", "ssb") == "unmeasured"


def test_evidence_state_matches_scale_memory_and_engine(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {
            "duckdb": {
                "tpch": {
                    "measurements": [
                        _measurement(
                            "measured-benefit",
                            scale_factor=1.0,
                            memory={"basis": "container_limit", "value": 1024},
                            engine_version="v1",
                        ),
                        _measurement(
                            "measured-regression",
                            scale_factor=10.0,
                            memory={"basis": "container_limit", "value": 2048},
                            engine_version="v2",
                        ),
                    ]
                }
            }
        },
    )

    assert (
        evidence_state(
            "duckdb",
            "tpch",
            scale_factor=1.0,
            memory={"basis": "container_limit", "value": 1024},
            engine_version="v1",
        )
        == "measured-benefit"
    )
    assert (
        evidence_state(
            "duckdb",
            "tpch",
            scale_factor=10.0,
            memory={"basis": "container_limit", "value": 2048},
            engine_version="v2",
        )
        == "measured-regression"
    )
    assert (
        evidence_state(
            "duckdb",
            "tpch",
            scale_factor=10.0,
            memory={"basis": "container_limit", "value": 1024},
            engine_version="v2",
        )
        == "unmeasured"
    )
    assert (
        evidence_state(
            "duckdb",
            "tpch",
            scale_factor=10.0,
            memory={"basis": "container_limit", "value": 2048},
            engine_version="v1",
        )
        == "unmeasured"
    )


def test_evidence_state_normalizes_platform_aliases():
    assert evidence_state("clickhouse-local", "tpch") == "unmeasured"
    assert evidence_state("clickhouse-server", "tpch") == "unmeasured"
    assert evidence_state("chdb", "tpch") == "unmeasured"


def test_spark_does_not_inherit_databricks_template_evidence():
    assert evidence_state("spark", "tpch") is None
    assert template_cell("examples/tunings/databricks/tpch_liquid_tuned.yaml", "spark", "TPC-H") is None


def test_evidence_state_returns_none_off_registry():
    assert evidence_state("duckdb", "nosuchbench") is None
    assert evidence_state("nosuchplatform", "tpch") is None
    assert evidence_state(None, "tpch") is None
    assert evidence_state("duckdb", None) is None
    assert evidence_state("", "") is None


def test_evidence_state_ignores_unknown_state(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {"duckdb": {"tpch": {"measurements": [_measurement("measured")]}}},
    )

    assert (
        evidence_state(
            "duckdb",
            "tpch",
            scale_factor=1.0,
            memory={"basis": "host_total_gb", "value": 16},
            engine_version="v1",
        )
        == "unmeasured"
    )


def test_is_tuned_template_ref():
    assert is_tuned_template_ref("examples/tunings/duckdb/tpch_tuned.yaml")
    assert is_tuned_template_ref("examples/tunings/databricks/tpch_liquid_tuned.yaml")
    assert is_tuned_template_ref("tpch_tuned.yaml:abcdef1234567890")
    assert not is_tuned_template_ref("examples/tunings/duckdb/tpch_notuning.yaml")
    assert not is_tuned_template_ref("my_custom.yaml")
    assert not is_tuned_template_ref(None)
    assert not is_tuned_template_ref("")


def test_evidence_warning_exact_text_for_unmeasured_run():
    message = evidence_warning(
        "duckdb",
        "tpch",
        source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tunings_applied={"table_tunings": {}},
    )
    assert message == (
        "Tuned template duckdb/tpch has no measured benefit: the result shows "
        "the template was applied, not that it is faster than notuning."
    )


def test_evidence_warning_absent_without_tuned_template():
    assert (
        evidence_warning(
            "duckdb", "tpch", source_file="examples/tunings/duckdb/tpch_notuning.yaml", tunings_applied={"a": 1}
        )
        is None
    )
    assert (
        evidence_warning(
            "duckdb",
            "tpch",
            source_file="examples/tunings/duckdb/tpch_tuned.yaml",
            tunings_applied=None,
        )
        is None
    )
    assert (
        evidence_warning("duckdb", "tpch", source_file="examples/tunings/duckdb/tpch_tuned.yaml", tunings_applied={})
        is None
    )
    assert evidence_warning("nosuchplatform", "tpch", source_file="tpch_tuned.yaml", tunings_applied={"a": 1}) is None


@pytest.mark.parametrize("state", ("measured-benefit", "measured-neutral", "measured-regression"))
def test_evidence_warning_absent_for_matching_measured_template(state, monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {"duckdb": {"tpch": {"measurements": [_measurement(state)]}}},
    )
    assert (
        evidence_warning(
            "duckdb",
            "tpch",
            source_file="examples/tunings/duckdb/tpch_tuned.yaml",
            tunings_applied={"table_tunings": {}},
            execution_environment={
                "client_host": {"memory_gb": 16},
                "platform_runtime": {"runtime_type": "local_process"},
            },
            scale_factor=1.0,
            engine_version="v1",
        )
        is None
    )


def test_evidence_warning_for_unmeasured_tier(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {"duckdb": {"tpch": {"measurements": [_measurement("measured-benefit", scale_factor=1.0)]}}},
    )

    message = evidence_warning(
        "duckdb",
        "tpch",
        source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tunings_applied={"table_tunings": {}},
        scale_factor=10.0,
        execution_environment={
            "client_host": {"memory_gb": 16},
            "platform_runtime": {"runtime_type": "local_process"},
        },
        engine_version="v1",
    )

    assert message == (
        "Tuned template duckdb/tpch has no measured benefit: the result shows "
        "the template was applied, not that it is faster than notuning."
    )


def test_registry_file_matches_expected_template_count():
    raw = yaml.safe_load(
        (REPO_ROOT / "benchbox" / "core" / "tuning" / "profiles" / "template_evidence.yaml").read_text()
    )
    count = sum(len(entries) for entries in raw.values() if isinstance(entries, dict))
    assert count == len({(p, b) for p, b, _ in _shipped_tuned_templates()})


def test_normalize_benchmark_handles_display_names():
    assert normalize_benchmark("tpch") == "tpch"
    assert normalize_benchmark("TPC-H") == "tpch"
    assert normalize_benchmark("TPC-DS") == "tpcds"
    assert normalize_benchmark("star_schema") == "ssb"
    assert normalize_benchmark(None) is None
    assert normalize_benchmark("") is None


def test_evidence_state_resolves_display_benchmark_names():
    assert evidence_state("duckdb", "TPC-H") == "unmeasured"
    assert evidence_state("DuckDB", "TPC-H") == "unmeasured"
    assert evidence_state("databricks", "TPC-DS") == "unmeasured"


def test_template_cell_reads_cell_from_template_path():
    assert template_cell("examples/tunings/duckdb/tpch_tuned.yaml", "DuckDB", "TPC-H") == ("duckdb", "tpch")
    assert template_cell("examples/tunings/databricks/tpch_liquid_tuned.yaml", "Databricks", "TPC-H") == (
        "databricks",
        "tpch",
    )


def test_template_cell_falls_back_to_run_identity_for_bare_refs():
    assert template_cell("tpch_tuned.yaml:abcdef1234567890", "DuckDB", "TPC-H") == ("duckdb", "tpch")
    assert template_cell("tpch_tuned.yaml:abcdef1234567890", None, None) is None
    assert template_cell("my_custom.yaml", "duckdb", "tpch") is None


def test_template_cell_rejects_cell_mismatch_with_run_identity():
    assert template_cell("examples/tunings/duckdb/tpch_tuned.yaml", "databricks", "TPC-H") is None
    assert template_cell("examples/tunings/duckdb/tpch_tuned.yaml", "DuckDB", "TPC-DS") is None


def test_template_cell_rejects_same_named_custom_files():
    assert template_cell("/tmp/tpch_tuned.yaml", "DuckDB", "TPC-H") is None
    assert template_cell("mydir/tpch_tuned.yaml", "DuckDB", "TPC-H") is None


def test_template_cell_accepts_absolute_shipped_paths():
    assert template_cell("/repo/benchbox/core/tuning/templates/duckdb/tpch_tuned.yaml", "DuckDB", "TPC-H") == (
        "duckdb",
        "tpch",
    )


def test_evidence_state_for_run_matches_scope_and_console_warning_inputs(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {
            "duckdb": {
                "tpch": {
                    "measurements": [
                        _measurement(
                            "measured-benefit",
                            memory={"basis": "container_limit", "value": 1024},
                        )
                    ]
                }
            }
        },
    )
    execution_environment = NormalizedExecutionEnvironment(container=ContainerEnvironment(memory_limit=1024))

    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
            scale_factor=1.0,
            execution_environment=execution_environment,
            engine_version="v1",
        )
        == "measured-benefit"
    )
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
            scale_factor=1.0,
            execution_environment={"container": {"memory_limit": 2048}},
            engine_version="v1",
        )
        == "unmeasured"
    )
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied=None,
        )
        is None
    )


def test_evidence_state_for_run_requires_captured_memory_scope(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {"duckdb": {"tpch": {"measurements": [_measurement("measured-benefit")]}}},
    )

    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
            scale_factor=1.0,
            execution_environment={},
            engine_version="v1",
        )
        == "unmeasured"
    )
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
            scale_factor=1.0,
            execution_environment={"platform_runtime": {"runtime_type": "local_process"}},
            system_profile={"memory_gb": 16},
            engine_version="v1",
        )
        == "measured-benefit"
    )
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
            scale_factor=1.0,
            execution_environment={"container": {"memory_limit": 1024}},
            system_profile={"memory_gb": 16},
            engine_version="v1",
        )
        == "unmeasured"
    )

    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
            scale_factor=1.0,
            execution_environment={"container": {"memory_limit": None}},
            system_profile={"memory_gb": 16},
            engine_version="v1",
        )
        == "unmeasured"
    )

    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
            scale_factor=1.0,
            execution_environment={"platform_runtime": {"runtime_type": "remote_server"}},
            system_profile={"memory_gb": 16},
            engine_version="v1",
        )
        == "unmeasured"
    )


MANAGED_CLOUD = {"platform_runtime": {"runtime_type": "managed_cloud"}}
X_SMALL = {
    "warehouse_size": "X-Small",
    "warehouse_size_source": "observed",
    "multi_cluster_warehouse": False,
    "max_cluster_count": 1,
    "source": "observed",
}


def _warehouse_registry(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {
            "snowflake": {
                "tpch": {
                    "measurements": [
                        _measurement(
                            "measured-benefit",
                            scale_factor=10.0,
                            memory={"basis": "warehouse_size", "value": "X-Small"},
                            engine_version="10.37.102",
                        )
                    ]
                }
            }
        },
    )


def _snowflake_state(*, execution_environment=MANAGED_CLOUD, platform_compute=X_SMALL, engine_version="10.37.102"):
    return evidence_state_for_run(
        "examples/tunings/snowflake/tpch_tuned.yaml",
        "Snowflake",
        "TPC-H",
        tunings_applied={"table_tunings": {}},
        scale_factor=10.0,
        execution_environment=execution_environment,
        engine_version=engine_version,
        platform_compute=platform_compute,
    )


def test_managed_cloud_run_matches_warehouse_size_entry(monkeypatch):
    _warehouse_registry(monkeypatch)
    assert _snowflake_state() == "measured-benefit"


@pytest.mark.parametrize(
    "platform_compute",
    [
        {**X_SMALL, "warehouse_size": "Small"},
        {**X_SMALL, "multi_cluster_warehouse": True},
        {**X_SMALL, "multi_cluster_warehouse": "true"},
        {**X_SMALL, "max_cluster_count": 3},
        {**X_SMALL, "warehouse_size_source": "requested"},
        {**X_SMALL, "source": "observed", "warehouse_size_source": "requested"},
        {key: value for key, value in X_SMALL.items() if key != "warehouse_size_source"},
        {key: value for key, value in X_SMALL.items() if key != "warehouse_size"},
        {**X_SMALL, "warehouse_size": "  "},
        None,
    ],
)
def test_managed_cloud_run_without_matching_single_warehouse_stays_unmeasured(monkeypatch, platform_compute):
    _warehouse_registry(monkeypatch)
    assert _snowflake_state(platform_compute=platform_compute) == "unmeasured"


def test_managed_cloud_run_on_other_engine_version_stays_unmeasured(monkeypatch):
    _warehouse_registry(monkeypatch)
    assert _snowflake_state(engine_version="10.38.0") == "unmeasured"
    assert _snowflake_state(engine_version=None) == "unmeasured"


def test_warehouse_size_applies_only_to_managed_cloud_runs(monkeypatch):
    _warehouse_registry(monkeypatch)
    assert _snowflake_state(execution_environment={"platform_runtime": {"runtime_type": "remote_server"}}) == (
        "unmeasured"
    )
    assert _snowflake_state(execution_environment={}) == "unmeasured"


def test_platform_compute_does_not_change_local_or_container_scope(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {
            "duckdb": {
                "tpch": {
                    "measurements": [
                        _measurement("measured-benefit", memory={"basis": "container_limit", "value": 1024}),
                        _measurement("measured-neutral"),
                    ]
                }
            }
        },
    )
    common = {
        "tunings_applied": {"table_tunings": {}},
        "scale_factor": 1.0,
        "engine_version": "v1",
        "platform_compute": X_SMALL,
    }
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            execution_environment={"container": {"memory_limit": 1024}},
            **common,
        )
        == "measured-benefit"
    )
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            execution_environment={"platform_runtime": {"runtime_type": "local_process"}},
            system_profile={"memory_gb": 16},
            **common,
        )
        == "measured-neutral"
    )


@pytest.mark.parametrize(
    ("benchmark_key", "scale_factor", "state"),
    [
        ("tpch", 100.0, "measured-neutral"),
        ("tpch", 10.0, "measured-neutral"),
        ("tpcds", 100.0, "measured-benefit"),
        ("tpcds", 10.0, "measured-neutral"),
    ],
)
def test_shipped_registry_reports_snowflake_campaign_state(benchmark_key, scale_factor, state):
    load_registry(refresh=True)
    arguments = {
        "tunings_applied": {"table_tunings": {}},
        "scale_factor": scale_factor,
        "execution_environment": MANAGED_CLOUD,
        "engine_version": "10.37.102",
        "platform_compute": X_SMALL,
    }
    source_file = f"examples/tunings/snowflake/{benchmark_key}_tuned.yaml"
    assert evidence_state_for_run(source_file, "Snowflake", benchmark_key, **arguments) == state
    assert evidence_warning("Snowflake", benchmark_key, source_file=source_file, **arguments) is None
    assert (
        evidence_state_for_run(source_file, "Snowflake", benchmark_key, **{**arguments, "platform_compute": None})
        == "unmeasured"
    )
