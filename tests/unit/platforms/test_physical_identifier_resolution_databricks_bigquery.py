from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.cloud.exceptions import NotFound

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms import bigquery as bigquery_module
from benchbox.platforms.bigquery import BigQueryAdapter
from benchbox.platforms.databricks import DatabricksAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.cloud_import]

REPO_ROOT = Path(__file__).resolve().parents[3]
CREATE_TABLE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:`[^`]+`\.)*`[^`]+`)\s*\((.*)\)", re.S
)
COLUMN = re.compile(r"`(\w+)`\s+[A-Z]")


def _created_tables(statements: list[str]) -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    for statement in statements:
        match = CREATE_TABLE.search(statement)
        if match:
            tables[match.group(1).strip("`").replace("`", "").split(".")[-1]] = COLUMN.findall(match.group(2))
    return tables


class FakeCursor:
    def __init__(self, describe_rows=None):
        self.statements: list[str] = []
        self.describe_rows = describe_rows or [("Provider", "delta")]
        self.closed = False

    def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)

    def fetchall(self):
        return self.describe_rows

    def fetchone(self):
        return None

    def close(self):
        self.closed = True


class FakeDatabricksConnection:
    def __init__(self):
        self.cursors: list[FakeCursor] = []

    def cursor(self):
        cursor = FakeCursor()
        self.cursors.append(cursor)
        return cursor

    @property
    def statements(self) -> list[str]:
        return [statement for cursor in self.cursors for statement in cursor.statements]


@pytest.fixture
def databricks_adapter():
    with patch("benchbox.platforms.databricks.adapter.databricks_sql"):
        yield DatabricksAdapter(server_hostname="host.example", http_path="/sql/1.0/warehouses/x", access_token="t")


@pytest.fixture
def databricks_schema(databricks_adapter, tmp_path):
    connection = FakeDatabricksConnection()
    databricks_adapter.create_schema(TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path), connection)
    return databricks_adapter, _created_tables(connection.statements)


def _clustering(table: str, *columns: str) -> TableTuning:
    return TableTuning(
        table_name=table,
        clustering=[TuningColumn(name=name, type="INTEGER", order=index) for index, name in enumerate(columns, 1)],
    )


class TestDatabricksPhysicalIdentifiers:
    def test_logical_table_resolves_to_what_create_schema_made(self, databricks_schema):
        adapter, tables = databricks_schema
        assert "lineitem" in tables
        for logical in ("LINEITEM", "lineitem", "LineItem"):
            assert adapter.resolve_physical_table(logical) in tables
        assert adapter.resolve_physical_table("LINEITEM") == "lineitem"

    def test_logical_column_resolves_to_what_create_schema_made(self, databricks_schema):
        adapter, tables = databricks_schema
        for logical in ("L_ORDERKEY", "l_partkey"):
            assert adapter.resolve_physical_column("LINEITEM", logical) in tables["lineitem"]
        assert adapter.resolve_physical_column("LINEITEM", "L_ORDERKEY") == "l_orderkey"

    def test_zorder_statement_uses_the_physical_identifiers(self, databricks_adapter):
        unified = UnifiedTuningConfiguration()
        unified.table_tunings["LINEITEM"] = _clustering("LINEITEM", "L_ORDERKEY", "L_PARTKEY")
        connection = FakeDatabricksConnection()
        databricks_adapter.run_post_load_tunings("LINEITEM", unified, connection)
        assert "OPTIMIZE lineitem ZORDER BY (l_orderkey, l_partkey)" in connection.statements
        assert "DESCRIBE EXTENDED lineitem" in connection.statements

    def test_liquid_clustering_statement_uses_the_physical_identifiers(self, databricks_adapter):
        unified = UnifiedTuningConfiguration()
        unified.platform_optimizations.databricks_clustering_strategy = "liquid_clustering"
        unified.platform_optimizations.liquid_clustering_enabled = True
        unified.platform_optimizations.liquid_clustering_columns = ["L_SHIPDATE", "L_ORDERKEY"]
        databricks_adapter.unified_tuning_configuration = unified
        connection = FakeDatabricksConnection()
        databricks_adapter.apply_table_tunings(_clustering("LINEITEM", "L_PARTKEY"), connection)
        assert "ALTER TABLE lineitem CLUSTER BY (l_shipdate, l_orderkey)" in connection.statements
        assert not any("ZORDER" in statement for statement in connection.statements)


@dataclass(frozen=True)
class FakeTableRef:
    table_id: str


class FakeDatasetRef:
    def table(self, name: str) -> FakeTableRef:
        return FakeTableRef(name)


class FakeQueryJob:
    def result(self):
        return []


class FakeBigQueryClient:
    def __init__(self):
        self.queries: list[str] = []
        self.get_table_ids: list[str] = []
        self.tables: dict[str, list[str]] = {}
        self.clustering: dict[str, list[str]] = {}

    def dataset(self, dataset_id):
        return FakeDatasetRef()

    def get_dataset(self, ref):
        return SimpleNamespace()

    def query(self, statement, *args, **kwargs):
        self.queries.append(statement)
        return FakeQueryJob()

    def get_table(self, ref: FakeTableRef):
        self.get_table_ids.append(ref.table_id)
        if ref.table_id not in self.tables:
            raise NotFound(f"table {ref.table_id} not found")
        return SimpleNamespace(
            time_partitioning=None,
            clustering_fields=self.clustering.get(ref.table_id, []),
            schema=[SimpleNamespace(name=name) for name in self.tables[ref.table_id]],
        )


@pytest.fixture
def bigquery_adapter():
    with (
        patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
        patch.object(bigquery_module, "bigquery"),
    ):
        yield BigQueryAdapter(project_id="proj", dataset_id="ds")


@pytest.fixture
def bigquery_schema(bigquery_adapter, tmp_path):
    client = FakeBigQueryClient()
    bigquery_adapter.create_schema(TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path), client)
    client.tables = _created_tables(client.queries)
    return bigquery_adapter, client


class TestBigQueryPhysicalIdentifiers:
    def test_logical_table_resolves_to_what_create_schema_made(self, bigquery_schema):
        adapter, client = bigquery_schema
        assert "LINEITEM" in client.tables
        for logical in ("LINEITEM", "lineitem", "LineItem"):
            assert adapter.resolve_physical_table(logical) in client.tables
        assert adapter.resolve_physical_table("lineitem") == "LINEITEM"

    def test_catalog_resolution_reuses_the_exact_case_fallback(self, bigquery_adapter):
        client = FakeBigQueryClient()
        client.tables = {"customer": ["c_custkey"]}
        assert bigquery_adapter.resolve_physical_table("customer", client) == "customer"
        assert client.get_table_ids == ["CUSTOMER", "customer"]

    def test_logical_column_resolves_to_what_create_schema_made(self, bigquery_schema):
        adapter, client = bigquery_schema
        for logical in ("L_ORDERKEY", "l_partkey"):
            assert adapter.resolve_physical_column("LINEITEM", logical, client) in client.tables["LINEITEM"]
            assert adapter.resolve_physical_column("LINEITEM", logical) in client.tables["LINEITEM"]
        assert adapter.resolve_physical_column("LINEITEM", "L_ORDERKEY", client) == "l_orderkey"

    def test_tuning_inspects_the_table_create_schema_made(self, bigquery_schema):
        adapter, client = bigquery_schema
        client.clustering["LINEITEM"] = ["l_orderkey", "l_partkey"]
        client.get_table_ids.clear()
        with patch.object(adapter.logger, "warning") as warning:
            adapter.apply_table_tunings(_clustering("lineitem", "L_ORDERKEY", "L_PARTKEY"), client)
        assert client.get_table_ids[0] == "LINEITEM"
        assert not any("needs recreation" in str(call) or "differs" in str(call) for call in warning.call_args_list)
        assert not any("Could not verify" in str(call) for call in warning.call_args_list)

    def test_tuning_flags_a_real_clustering_difference(self, bigquery_schema):
        adapter, client = bigquery_schema
        client.clustering["LINEITEM"] = ["l_shipdate"]
        with patch.object(adapter.logger, "warning") as warning:
            adapter.apply_table_tunings(_clustering("LINEITEM", "L_ORDERKEY"), client)
        assert any("differs from desired tuning" in str(call) for call in warning.call_args_list)


class TestNoCasingHacksInTuningBuilders:
    @pytest.mark.parametrize("relative", ["benchbox/platforms/databricks/adapter.py", "benchbox/platforms/bigquery.py"])
    @pytest.mark.parametrize("hack", ["table_tuning.table_name.upper()", "table_tuning.table_name.lower()"])
    def test_builders_do_not_fold_the_logical_table_name(self, relative, hack):
        assert hack not in (REPO_ROOT / relative).read_text(encoding="utf-8")
