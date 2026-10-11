from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from benchbox.cli.config import ConfigManager
from benchbox.core.tuning.interface import TuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLES_DIR = REPO_ROOT / "examples" / "tunings" / "snowflake"
PACKAGED_DIR = REPO_ROOT / "benchbox" / "core" / "tuning" / "templates" / "snowflake"
BENCHMARKS = ("tpch", "tpcds")

EXPECTED_CLUSTERING = {
    "tpch": {
        "LINEITEM": ["L_SHIPDATE", "L_SUPPKEY", "L_PARTKEY"],
        "ORDERS": ["O_ORDERDATE", "O_CUSTKEY", "O_ORDERKEY"],
    },
    "tpcds": {
        "STORE_SALES": ["SS_SOLD_DATE_SK", "SS_STORE_SK", "SS_PROMO_SK"],
        "STORE_RETURNS": ["SR_RETURNED_DATE_SK", "SR_STORE_SK", "SR_ITEM_SK"],
        "CATALOG_SALES": ["CS_SOLD_DATE_SK", "CS_SHIP_MODE_SK", "CS_ITEM_SK"],
        "CATALOG_RETURNS": ["CR_RETURNED_DATE_SK", "CR_ITEM_SK"],
        "WEB_SALES": ["WS_SOLD_DATE_SK", "WS_SHIP_MODE_SK", "WS_WEB_SITE_SK"],
        "WEB_RETURNS": ["WR_RETURNED_DATE_SK", "WR_ITEM_SK"],
    },
}


def _load_generator():
    path = REPO_ROOT / "scripts" / "generate_cloud_tpc_templates.py"
    spec = importlib.util.spec_from_file_location("generate_cloud_tpc_templates", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_template(path: Path):
    return ConfigManager().load_unified_tuning_config(path, platform="snowflake")


def _clustered_tables(config) -> dict[str, list[str]]:
    clustered = {}
    for name, tuning in config.table_tunings.items():
        columns = [column.name for column in tuning.get_columns_by_type(TuningType.CLUSTERING)]
        if columns:
            clustered[name] = columns
    return clustered


def test_snowflake_tuned_templates_exist_and_match_across_both_trees() -> None:
    for benchmark_id in BENCHMARKS:
        examples_path = EXAMPLES_DIR / f"{benchmark_id}_tuned.yaml"
        packaged_path = PACKAGED_DIR / f"{benchmark_id}_tuned.yaml"
        assert examples_path.is_file(), examples_path
        assert packaged_path.is_file(), packaged_path
        examples_payload = yaml.safe_load(examples_path.read_text(encoding="utf-8"))
        packaged_payload = yaml.safe_load(packaged_path.read_text(encoding="utf-8"))
        assert packaged_payload == examples_payload


@pytest.mark.parametrize("benchmark_id", BENCHMARKS)
def test_snowflake_tuned_templates_cluster_fact_tables_only(benchmark_id: str) -> None:
    clustered = _clustered_tables(_load_template(EXAMPLES_DIR / f"{benchmark_id}_tuned.yaml"))
    assert set(clustered) == set(EXPECTED_CLUSTERING[benchmark_id])
    for columns in clustered.values():
        assert len(columns) <= 3


@pytest.mark.parametrize("benchmark_id", BENCHMARKS)
def test_snowflake_tuned_template_keys_follow_sf1_cardinality_order(benchmark_id: str) -> None:
    clustered = _clustered_tables(_load_template(EXAMPLES_DIR / f"{benchmark_id}_tuned.yaml"))
    assert clustered == EXPECTED_CLUSTERING[benchmark_id]


def test_snowflake_rank_orders_date_before_foreign_keys_before_own_key() -> None:
    generator = _load_generator()
    candidates = [
        SimpleNamespace(table="STORE_SALES", column="SS_TICKET_NUMBER", roles=("join_locality",)),
        SimpleNamespace(table="STORE_SALES", column="SS_ITEM_SK", roles=("join_locality",)),
        SimpleNamespace(table="STORE_SALES", column="SS_SOLD_DATE_SK", roles=("temporal_partition",)),
        SimpleNamespace(table="STORE_SALES", column="SS_PROMO_SK", roles=("fact_dimension_join",)),
    ]
    ordered = sorted(candidates, key=lambda candidate: generator.snowflake_cluster_rank(candidate, "tpcds"))
    assert [candidate.column for candidate in ordered] == [
        "SS_SOLD_DATE_SK",
        "SS_PROMO_SK",
        "SS_ITEM_SK",
        "SS_TICKET_NUMBER",
    ]


def test_snowflake_render_table_caps_lineitem_clustering_at_three_lowest_cardinality_keys() -> None:
    generator = _load_generator()
    candidates = [
        SimpleNamespace(table="LINEITEM", column="L_SHIPDATE", type="DATE", roles=("temporal_partition",)),
        SimpleNamespace(table="LINEITEM", column="L_ORDERKEY", type="INTEGER", roles=("join_locality",)),
        SimpleNamespace(table="LINEITEM", column="L_PARTKEY", type="INTEGER", roles=("join_locality",)),
        SimpleNamespace(table="LINEITEM", column="L_SUPPKEY", type="INTEGER", roles=("join_locality",)),
    ]
    block = generator.render_table("snowflake", "tpch", "LINEITEM", list(reversed(candidates)))
    assert [entry["name"] for entry in block["clustering"]] == ["L_SHIPDATE", "L_SUPPKEY", "L_PARTKEY"]


def test_snowflake_render_table_leaves_dimension_tables_unclustered() -> None:
    generator = _load_generator()
    candidates = [
        SimpleNamespace(table="CUSTOMER", column="C_CUSTKEY", type="INTEGER", roles=("join_locality",)),
        SimpleNamespace(table="CUSTOMER", column="C_NATIONKEY", type="INTEGER", roles=("fact_dimension_join",)),
    ]
    block = generator.render_table("snowflake", "tpch", "CUSTOMER", candidates)
    assert block == {"table_name": "CUSTOMER"}


def _make_snowflake_adapter():
    from benchbox.platforms.snowflake import SnowflakeAdapter

    with patch("benchbox.platforms.snowflake.snowflake"):
        return SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            schema="PUBLIC",
            database="BENCHBOX",
        )


@pytest.mark.parametrize("benchmark_id", BENCHMARKS)
def test_snowflake_fake_driver_renders_cluster_by_for_fact_tables_only(benchmark_id: str) -> None:
    with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
        adapter = _make_snowflake_adapter()
    adapter.resolve_physical_table = Mock(side_effect=lambda table_name, connection: table_name)
    config = _load_template(EXAMPLES_DIR / f"{benchmark_id}_tuned.yaml")
    rendered: dict[str, str] = {}
    for tuning in config.table_tunings.values():
        cursor = Mock()
        cursor.fetchone.return_value = (None, False)
        connection = Mock()
        connection.cursor.return_value = cursor
        adapter.apply_table_tunings(tuning, connection)
        for call in cursor.execute.call_args_list:
            statement = call.args[0]
            if isinstance(statement, str) and statement.startswith("ALTER TABLE") and "CLUSTER BY" in statement:
                rendered[statement.split()[2]] = statement
    assert set(rendered) == set(EXPECTED_CLUSTERING[benchmark_id])
    for table, columns in EXPECTED_CLUSTERING[benchmark_id].items():
        assert rendered[table] == f"ALTER TABLE {table} CLUSTER BY ({', '.join(columns)})"
