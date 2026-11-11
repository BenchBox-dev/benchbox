from __future__ import annotations

import re
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
from benchbox.platforms.clickhouse import ClickHouseAdapter
from tests.unit.benchmarks.test_schema_pk_conformance import _spec_tpcds_keys

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("chdb_probe_satisfied"),
]

COMPOSITE_KEY_TABLES = [
    "store_sales",
    "store_returns",
    "catalog_sales",
    "catalog_returns",
    "web_sales",
    "web_returns",
    "inventory",
]


@pytest.fixture(autouse=True)
def clickhouse_dependencies():
    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        yield


@pytest.fixture(scope="module")
def untuned_tpcds_ddl(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    benchmark = TPCDSBenchmark(scale_factor=0.01, output_dir=tmp_path_factory.mktemp("tpcds"))
    with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient") as client_class:
        client = Mock()
        client_class.return_value = client
        adapter = ClickHouseAdapter(deployment_mode="server")
        adapter.tuning_enabled = False
        adapter.create_schema(benchmark, adapter.create_connection())
    statements = [str(call.args[0]) for call in client.execute.call_args_list]
    ddl: dict[str, str] = {}
    for statement in statements:
        match = re.match(r"\s*CREATE TABLE\s+(\w+)", statement, flags=re.IGNORECASE)
        if match:
            ddl[match.group(1).lower()] = statement
    return ddl


def _order_by(statement: str) -> str:
    match = re.search(r"ORDER BY\s+(\(.*\)|tuple\(\))\s*;?\s*$", statement, flags=re.IGNORECASE | re.DOTALL)
    assert match is not None, statement
    return match.group(1)


def test_untuned_run_builds_every_tpcds_table(untuned_tpcds_ddl: dict[str, str]) -> None:
    assert set(_spec_tpcds_keys()) <= set(untuned_tpcds_ddl)


@pytest.mark.parametrize("table", COMPOSITE_KEY_TABLES)
def test_untuned_merge_tree_order_by_follows_the_specification_composite_key(
    untuned_tpcds_ddl: dict[str, str], table: str
) -> None:
    key = _spec_tpcds_keys()[table]
    assert len(key) > 1
    assert _order_by(untuned_tpcds_ddl[table]) == f"({', '.join(key)})"


def test_untuned_merge_tree_order_by_is_empty_for_a_table_without_a_key(untuned_tpcds_ddl: dict[str, str]) -> None:
    assert _spec_tpcds_keys()["dbgen_version"] == ()
    assert _order_by(untuned_tpcds_ddl["dbgen_version"]) == "tuple()"


