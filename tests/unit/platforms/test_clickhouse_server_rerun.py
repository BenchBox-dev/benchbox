import re
from unittest.mock import patch

import pytest

from benchbox import TPCH
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms.clickhouse import ClickHouseAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

DATABASE = "benchbox_tpch_sf001"
TPCH_ROWS = {
    "region": 5,
    "nation": 25,
    "supplier": 100,
    "part": 2000,
    "partsupp": 8000,
    "customer": 1500,
    "orders": 15000,
    "lineitem": 60000,
}


class _ServerError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class _FakeServer:
    def __init__(self) -> None:
        self.databases: dict[str, dict[str, int]] = {"default": {}}
        self.statements: list[str] = []
        self.current = "default"

    def seed(self, database: str = DATABASE, rows: dict[str, int] | None = None) -> None:
        self.databases[database] = dict(TPCH_ROWS if rows is None else rows)

    def statements_matching(self, pattern: str) -> list[str]:
        return [statement for statement in self.statements if re.match(pattern, statement.strip(), re.IGNORECASE)]

    def run(self, query: str, database: str) -> list[tuple]:
        self.statements.append(query)
        text = query.strip()
        lowered = text.lower()
        if lowered == "show databases":
            return [(name,) for name in self.databases]
        if lowered.startswith("create database"):
            self.databases.setdefault(re.search(r"`(\w+)`", text).group(1), {})
            return []
        if lowered.startswith("drop database"):
            self.databases.pop(text.split()[-1], None)
            return []
        match = re.match(r"select count\(\) from system\.tables where database = '(\w+)'", text, re.IGNORECASE)
        if match:
            return [(len(self.databases.get(match.group(1), {})),)]
        if "from system.tables" in lowered:
            return [(name,) for name in self.databases.get(database, {})]
        if "benchbox_tuning_metadata" in lowered:
            raise _ServerError("Code: 60. DB::Exception: Unknown table benchbox_tuning_metadata", 60)
        match = re.match(r"select count\(\*\) from (\w+)$", lowered)
        if match:
            return [(self.databases[database].get(match.group(1), 0),)]
        match = re.match(r"create table (?:if not exists )?(\w+)", lowered)
        if match:
            name = match.group(1)
            if name in self.databases[database]:
                raise _ServerError(f"Code: 57. DB::Exception: Table {database}.{name} already exists", 57)
            self.databases[database][name] = 0
        return []


class _FakeClient:
    server: _FakeServer

    def __init__(self, *args, database: str = "default", **kwargs) -> None:
        self.database = database

    def execute(self, query, *args, **kwargs):
        return self.server.run(query, self.database)

    def disconnect(self) -> None:
        return None


@pytest.fixture
def server():
    fake = _FakeServer()
    fake.databases.pop(DATABASE, None)
    client_class = type("BoundFakeClient", (_FakeClient,), {"server": fake})
    with (
        patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.clickhouse.setup.ClickHouseClient", client_class),
    ):
        yield fake


@pytest.fixture
def tpch(tmp_path):
    return TPCH(scale_factor=0.01, output_dir=tmp_path)


def _adapter(tpch, **overrides) -> ClickHouseAdapter:
    config = {"deployment_mode": "server", "database": DATABASE, "scale_factor": 0.01, **overrides}
    adapter = ClickHouseAdapter(**config)
    adapter.benchmark_instance = tpch
    adapter.scale_factor = 0.01
    return adapter


def _create_statements(server: _FakeServer) -> list[str]:
    return server.statements_matching(r"create table")


def test_notuning_rerun_reuses_a_valid_existing_database(server, tpch):
    server.seed()
    adapter = _adapter(tpch)

    adapter.create_connection()

    assert adapter.database_was_reused is True
    assert server.statements_matching(r"drop database") == []
    assert _create_statements(server) == []


def test_notuning_rerun_recreates_the_database_with_force(server, tpch):
    server.seed()
    adapter = ClickHouseAdapter.from_config(
        {
            "deployment_mode": "server",
            "database": DATABASE,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "force_recreate": True,
        }
    )
    adapter.benchmark_instance = tpch
    adapter.scale_factor = 0.01
    assert adapter.force_recreate is True

    connection = adapter.create_connection()
    adapter.create_schema(tpch, connection)

    assert adapter.database_was_reused is False
    assert len(server.statements_matching(r"drop database")) == 1
    assert len(_create_statements(server)) == len(TPCH_ROWS)


def test_notuning_rerun_refuses_an_unusable_database_without_force(server, tpch):
    server.seed(rows={**TPCH_ROWS, "lineitem": 7})
    adapter = _adapter(tpch)

    with pytest.raises(RuntimeError, match="--force"):
        adapter.create_connection()

    assert server.statements_matching(r"drop database") == []
    assert _create_statements(server) == []
    assert server.databases[DATABASE]["lineitem"] == 7


def test_create_schema_refuses_before_any_create_when_tables_already_exist(server, tpch):
    server.seed()
    adapter = _adapter(tpch)
    connection = adapter.create_connection()
    adapter.database_was_reused = False

    with pytest.raises(RuntimeError, match="--force") as excinfo:
        adapter.create_schema(tpch, connection)

    assert "already exists" not in str(excinfo.value)
    assert "no table was created" in str(excinfo.value)
    assert _create_statements(server) == []


def test_tuned_rerun_still_drops_and_recreates_without_force(server, tpch):
    server.seed(rows={**TPCH_ROWS, "lineitem": 7})
    adapter = _adapter(tpch, tuning_enabled=True, unified_tuning_configuration=UnifiedTuningConfiguration())

    connection = adapter.create_connection()
    adapter.create_schema(tpch, connection)

    assert adapter.database_was_reused is False
    assert len(server.statements_matching(r"drop database")) == 1
    assert len(_create_statements(server)) == len(TPCH_ROWS)


@pytest.mark.parametrize(
    ("data_source", "run_benchmark", "own_database"),
    [
        ("tpcds", "tpcds_obt", "benchbox_tpcds_obt_sf001"),
        ("tpch", "write_primitives", "benchbox_write_primitives_sf001"),
    ],
)
def test_benchmark_sharing_data_with_another_gets_its_own_database(server, data_source, run_benchmark, own_database):
    shared_database = f"benchbox_{data_source}_sf001"
    server.databases[shared_database] = {"loaded_by_data_benchmark": 10}
    config = {"deployment_mode": "server", "benchmark": data_source, "scale_factor": 0.01}

    shared = ClickHouseAdapter.from_config(config)
    own = ClickHouseAdapter.from_config({**config, "run_benchmark_name": run_benchmark})

    assert shared.database == shared_database
    assert own.database == own_database
    assert own._server_database_table_count(own.database) == 0
    assert server.databases[shared_database] == {"loaded_by_data_benchmark": 10}
    assert server.statements_matching(r"drop database") == []
