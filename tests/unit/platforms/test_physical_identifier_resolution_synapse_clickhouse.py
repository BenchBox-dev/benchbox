from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import benchbox.platforms.azure_synapse as synapse_module
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.platforms.azure_synapse import AzureSynapseAdapter
from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]

CLICKHOUSE_ADAPTERS = [
    pytest.param(ClickHouseLocalAdapter, {}, id="local"),
    pytest.param(ClickHouseServerAdapter, {}, id="server"),
    pytest.param(ClickHouseCloudAdapter, {"host": "h", "password": "p"}, id="cloud"),
]


class RecordingClient:
    def __init__(self, tables: list[str] | None = None, columns: dict[str, list[str]] | None = None) -> None:
        self.statements: list[str] = []
        self.tables = tables or []
        self.columns = columns or {}

    @property
    def tuning_statements(self) -> list[str]:
        return [s for s in self.statements if "system." not in s]

    def execute(self, statement: str, *args, **kwargs):
        self.statements.append(statement)
        if "FROM system.tables" in statement:
            return [(name,) for name in self.tables]
        if "FROM system.columns" in statement:
            table = re.search(r"table = '([^']+)'", statement).group(1)
            return [(name,) for name in self.columns.get(table, [])]
        return []


class RecordingCursor:
    def __init__(self, statements: list[str]) -> None:
        self.statements = statements

    def execute(self, statement: str, *args, **kwargs) -> None:
        self.statements.append(statement)

    def fetchall(self) -> list:
        return []

    def fetchone(self):
        return None

    def close(self) -> None:
        return None


class RecordingOdbcConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.statements)


@pytest.fixture
def tpch_benchmark(tmp_path):
    return TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.fixture
def clickhouse_factory(chdb_probe_satisfied):
    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        yield lambda cls, kwargs: cls(**kwargs)


@pytest.fixture
def synapse_adapter(monkeypatch):
    monkeypatch.setattr(synapse_module, "pyodbc", MagicMock())
    monkeypatch.setattr(synapse_module, "check_platform_dependencies", lambda platform, packages=None: (True, []))
    return AzureSynapseAdapter(server="test.sql.azuresynapse.net", username="admin", password="secret")


def _clickhouse_created_tables(statements: list[str]) -> dict[str, list[str]]:
    created: dict[str, list[str]] = {}
    for statement in statements:
        match = re.match(r"\s*CREATE TABLE\s+(\w+)\s*\((.*)\)\s*ENGINE", statement, re.IGNORECASE | re.DOTALL)
        if match:
            created[match.group(1)] = re.findall(r"^\s*(\w+)\s+[A-Za-z]", match.group(2), re.MULTILINE)
    return created


def _synapse_created_tables(statements: list[str]) -> dict[str, list[str]]:
    created: dict[str, list[str]] = {}
    for statement in statements:
        match = re.match(r"\s*CREATE TABLE\s+\[[^\]]+\]\.\[([^\]]+)\]\s*\((.*)\)", statement, re.IGNORECASE | re.DOTALL)
        if match:
            created[match.group(1)] = re.findall(r"\[([^\]]+)\]\s+[A-Za-z]", match.group(2))
    return created


def _lineitem_tuning(**kwargs) -> TableTuning:
    return TableTuning(table_name="LINEITEM", **kwargs)


@pytest.mark.parametrize(("adapter_cls", "kwargs"), CLICKHOUSE_ADAPTERS)
class TestClickHousePhysicalIdentifiers:
    def test_resolver_matches_the_identifiers_create_schema_emits(
        self, clickhouse_factory, adapter_cls, kwargs, tpch_benchmark
    ):
        adapter = clickhouse_factory(adapter_cls, kwargs)
        client = RecordingClient()
        adapter.create_schema(tpch_benchmark, client)
        created = _clickhouse_created_tables(client.statements)

        assert adapter.resolve_physical_table("LINEITEM") in created
        assert adapter.resolve_physical_table("lineitem") in created
        physical_table = adapter.resolve_physical_table("LINEITEM")
        for logical in ("L_ORDERKEY", "l_shipdate"):
            assert adapter.resolve_physical_column("LINEITEM", logical) in created[physical_table]

    def test_untuned_clickhouse_ddl_is_unchanged_by_the_policy(
        self, clickhouse_factory, adapter_cls, kwargs, tpch_benchmark
    ):
        adapter = clickhouse_factory(adapter_cls, kwargs)
        client = RecordingClient()
        adapter.create_schema(tpch_benchmark, client)

        lineitem = next(s for s in client.statements if re.match(r"\s*CREATE TABLE lineitem\b", s))
        assert lineitem.startswith("CREATE TABLE lineitem (")
        assert "l_orderkey INTEGER NOT NULL" in lineitem

    def test_apply_table_tunings_never_optimizes(self, clickhouse_factory, adapter_cls, kwargs):
        adapter = clickhouse_factory(adapter_cls, {**kwargs, "optimize_after_load": True, "tuning_enabled": True})
        client = RecordingClient()
        tuning = _lineitem_tuning(
            sorting=[TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1)],
            clustering=[TuningColumn(name="L_SHIPDATE", type="DATE", order=1)],
        )

        adapter.apply_table_tunings(tuning, client)

        assert client.tuning_statements == []

    def test_post_load_optimize_targets_the_physical_table(self, clickhouse_factory, adapter_cls, kwargs):
        adapter = clickhouse_factory(adapter_cls, {**kwargs, "optimize_after_load": True, "tuning_enabled": True})
        client = RecordingClient()

        adapter.apply_post_load_tunings("LINEITEM", SimpleNamespace(table_tunings={}), client)

        assert client.tuning_statements == ["OPTIMIZE TABLE lineitem FINAL"]

    def test_post_load_optimize_is_off_by_default(self, clickhouse_factory, adapter_cls, kwargs):
        adapter = clickhouse_factory(adapter_cls, kwargs)
        client = RecordingClient()

        adapter.apply_post_load_tunings("LINEITEM", SimpleNamespace(table_tunings={}), client)

        assert client.tuning_statements == []

    def test_post_load_optimize_names_the_table_the_schema_created(
        self, clickhouse_factory, adapter_cls, kwargs, tpch_benchmark
    ):
        adapter = clickhouse_factory(adapter_cls, {**kwargs, "optimize_after_load": True, "tuning_enabled": True})
        client = RecordingClient()
        adapter.create_schema(tpch_benchmark, client)
        created = _clickhouse_created_tables(client.statements)
        client.statements.clear()

        adapter.apply_post_load_tunings("LINEITEM", SimpleNamespace(table_tunings={}), client)

        targets = {re.match(r"OPTIMIZE TABLE (\S+) FINAL", s).group(1) for s in client.tuning_statements}
        assert targets
        assert targets <= set(created)

    def test_mixed_case_schema_is_resolved_through_the_catalog(self, clickhouse_factory, adapter_cls, kwargs):
        adapter = clickhouse_factory(adapter_cls, {**kwargs, "optimize_after_load": True, "tuning_enabled": True})
        client = RecordingClient(
            tables=["DimDate", "DimCustomer", "lineitem"],
            columns={"DimCustomer": ["SK_CustomerID", "CustomerID"]},
        )

        assert adapter.resolve_physical_table("DIMCUSTOMER", client) == "DimCustomer"
        assert adapter.resolve_physical_table("DimCustomer", client) == "DimCustomer"
        assert adapter.resolve_physical_table("LINEITEM", client) == "lineitem"
        assert adapter.resolve_physical_column("DimCustomer", "sk_customerid", client) == "SK_CustomerID"

        adapter.apply_post_load_tunings("DimCustomer", SimpleNamespace(table_tunings={}), client)

        assert client.tuning_statements == ["OPTIMIZE TABLE DimCustomer FINAL"]

    def test_unreadable_catalog_falls_back_to_the_adapter_policy(self, clickhouse_factory, adapter_cls, kwargs):
        adapter = clickhouse_factory(adapter_cls, kwargs)

        class Failing:
            def execute(self, *args, **kwargs):
                raise RuntimeError("catalog unavailable")

        assert adapter.resolve_physical_table("LINEITEM", Failing()) == "lineitem"
        assert adapter.resolve_physical_column("LINEITEM", "L_ORDERKEY", Failing()) == "l_orderkey"

    def test_mixed_case_logical_names_resolve_to_the_same_table(self, clickhouse_factory, adapter_cls, kwargs):
        adapter = clickhouse_factory(adapter_cls, kwargs)

        assert adapter.resolve_physical_table("LineItem") == "lineitem"
        assert adapter.resolve_physical_column("LINEITEM", "L_OrderKey") == "l_orderkey"


class TestSynapsePhysicalIdentifiers:
    def test_resolver_matches_the_identifiers_create_schema_emits(self, synapse_adapter, tpch_benchmark):
        connection = RecordingOdbcConnection()
        synapse_adapter.create_schema(tpch_benchmark, connection)
        created = _synapse_created_tables(connection.statements)

        physical_table = synapse_adapter.resolve_physical_table("LINEITEM")
        assert physical_table in created
        assert synapse_adapter.resolve_physical_table("lineitem") == physical_table
        for logical in ("L_ORDERKEY", "l_shipdate"):
            assert synapse_adapter.resolve_physical_column("LINEITEM", logical) in created[physical_table]

    def test_apply_table_tunings_refreshes_statistics_on_the_physical_table(self, synapse_adapter, tpch_benchmark):
        connection = RecordingOdbcConnection()
        synapse_adapter.create_schema(tpch_benchmark, connection)
        created = _synapse_created_tables(connection.statements)
        connection.statements.clear()
        tuning = _lineitem_tuning(distribution=[TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1)])

        synapse_adapter.apply_table_tunings(tuning, connection)

        assert connection.statements == ["UPDATE STATISTICS [dbo].[lineitem]"]
        assert "lineitem" in created

    def test_tuning_clause_uses_physical_column_names(self, synapse_adapter):
        tuning = _lineitem_tuning(
            distribution=[TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1)],
            partitioning=[TuningColumn(name="L_SHIPDATE", type="DATE", order=1)],
        )

        clause = synapse_adapter.generate_tuning_clause(tuning)

        assert clause == (
            "WITH (DISTRIBUTION = HASH([l_orderkey]), "
            "PARTITION ([l_shipdate] RANGE RIGHT FOR VALUES ()), "
            "CLUSTERED COLUMNSTORE INDEX)"
        )

    def test_ctas_sort_is_disabled(self, synapse_adapter):
        columns = [
            TuningColumn(name="L_SHIPDATE", type="DATE", order=1),
            TuningColumn(name="L_ORDERKEY", type="INTEGER", order=2),
        ]

        with patch.object(synapse_adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            sql = synapse_adapter._build_ctas_sort_sql("LINEITEM", columns)

        assert sql is None


class TestTuningBuildersDoNotFoldCaseByHand:
    @pytest.mark.parametrize(
        "relative", ["benchbox/platforms/azure_synapse.py", "benchbox/platforms/clickhouse/tuning.py"]
    )
    @pytest.mark.parametrize("forbidden", ["table_tuning.table_name.upper()", "table_tuning.table_name.lower()"])
    def test_source_has_no_ad_hoc_case_folding_of_the_table_name(self, relative, forbidden):
        assert forbidden not in (REPO_ROOT / relative).read_text()
