from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.platforms.redshift import RedshiftAdapter
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.cloud_import]

PLATFORMS_DIR = Path(__file__).resolve().parents[3] / "benchbox" / "platforms"

CREATE_TABLE_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"?[A-Za-z_][A-Za-z0-9_]*\"?)\s*\((.*)\)",
    re.IGNORECASE | re.DOTALL,
)
COLUMN_PATTERN = re.compile(r"(?:^|,)\s*(\"?[A-Za-z_][A-Za-z0-9_]*\"?)\s+[A-Za-z]+", re.DOTALL)


class RecordingCursor:
    def __init__(self, row=None):
        self.statements: list[str] = []
        self.row = row

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def close(self):
        pass


class RecordingConnection:
    def __init__(self, row=None):
        self.recorder = RecordingCursor(row)

    def cursor(self):
        return self.recorder


def fold_unquoted(identifier: str, fold) -> str:
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1]
    return fold(identifier)


def created_identifiers(statements: list[str], table: str, fold) -> tuple[str, dict[str, str]]:
    for statement in statements:
        match = CREATE_TABLE_PATTERN.search(statement)
        if match and fold_unquoted(match.group(1), fold).lower() == table.lower():
            columns = {}
            for column in COLUMN_PATTERN.findall(match.group(2)):
                physical = fold_unquoted(column, fold)
                columns[physical.lower()] = physical
            return fold_unquoted(match.group(1), fold), columns
    raise AssertionError(f"no CREATE TABLE for {table} in {statements}")


def clustering_tuning(table_name: str, column_name: str) -> TableTuning:
    return TableTuning(table_name=table_name, clustering=[TuningColumn(name=column_name, type="INTEGER", order=1)])


@pytest.fixture
def tpch(tmp_path):
    return TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.fixture
def snowflake_adapter():
    with (
        patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.snowflake.snowflake"),
    ):
        yield SnowflakeAdapter(account="account", username="user", password="password")


@pytest.fixture
def redshift_adapter():
    with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=10):
        yield RedshiftAdapter(
            host="cluster.redshift.amazonaws.com",
            port=5439,
            database="db",
            username="user",
            password="password",
        )


class TestSnowflakePhysicalIdentifiers:
    def test_logical_table_resolves_to_what_create_schema_made(self, snowflake_adapter, tpch):
        connection = RecordingConnection()
        snowflake_adapter.create_schema(tpch, connection)
        table, _ = created_identifiers(connection.recorder.statements, "lineitem", str.upper)
        for logical in ("LINEITEM", "lineitem", "LineItem"):
            assert snowflake_adapter.resolve_physical_table(logical, connection) == table

    def test_logical_column_resolves_to_what_create_schema_made(self, snowflake_adapter, tpch):
        connection = RecordingConnection()
        snowflake_adapter.create_schema(tpch, connection)
        _, columns = created_identifiers(connection.recorder.statements, "lineitem", str.upper)
        for logical in ("L_ORDERKEY", "l_orderkey"):
            assert snowflake_adapter.resolve_physical_column("LINEITEM", logical, connection) == columns["l_orderkey"]

    def test_clustering_statements_use_the_physical_identifiers(self, snowflake_adapter):
        connection = RecordingConnection()
        snowflake_adapter.apply_table_tunings(clustering_tuning("lineitem", "l_orderkey"), connection)
        assert connection.recorder.statements[-1] == "ALTER TABLE LINEITEM CLUSTER BY (l_orderkey)"
        snowflake_adapter.apply_post_load_tunings("lineitem", None, connection)
        assert connection.recorder.statements[-1] == "ALTER TABLE LINEITEM RESUME RECLUSTER"

    def test_run_scoped_reset_clears_pending_recluster(self, snowflake_adapter):
        connection = RecordingConnection()
        snowflake_adapter.apply_table_tunings(clustering_tuning("lineitem", "l_orderkey"), connection)
        assert snowflake_adapter._pending_resume_recluster() == {"LINEITEM"}
        snowflake_adapter._reset_run_scoped_state()
        assert snowflake_adapter._pending_resume_recluster() == set()
        statements_before = len(connection.recorder.statements)
        assert snowflake_adapter.apply_post_load_tunings("lineitem", None, connection) is False
        assert len(connection.recorder.statements) == statements_before

    def test_a_refused_recluster_resume_fails_the_maintenance_phase(self, snowflake_adapter):
        connection = RecordingConnection()
        snowflake_adapter.apply_table_tunings(clustering_tuning("lineitem", "l_orderkey"), connection)

        def refuse(sql, params=None):
            raise RuntimeError("clustering not permitted")

        connection.recorder.execute = refuse
        config = SimpleNamespace(table_tunings={"lineitem": clustering_tuning("lineitem", "l_orderkey")})
        snowflake_adapter.run_post_load_tunings("lineitem", config, connection)

        assert snowflake_adapter.build_post_load_maintenance_phase().status == "FAILED"

    def test_catalog_probe_binds_the_physical_table(self, snowflake_adapter):
        probes = []
        connection = RecordingConnection()
        connection.recorder.execute = lambda sql, params=None: probes.append(params)
        snowflake_adapter.apply_table_tunings(clustering_tuning("lineitem", "l_orderkey"), connection)
        assert probes[0][1] == "LINEITEM"

    def test_recluster_uses_the_physical_table(self, snowflake_adapter):
        connection = RecordingConnection()
        snowflake_adapter.analyze_table(connection, "lineitem")
        assert connection.recorder.statements == ["ALTER TABLE LINEITEM RECLUSTER"]


class TestRedshiftPhysicalIdentifiers:
    def test_logical_table_resolves_to_what_create_schema_made(self, redshift_adapter, tpch):
        connection = RecordingConnection()
        redshift_adapter.create_schema(tpch, connection)
        table, _ = created_identifiers(connection.recorder.statements, "lineitem", str.lower)
        for logical in ("LINEITEM", "lineitem", "LineItem"):
            assert redshift_adapter.resolve_physical_table(logical, connection) == table

    def test_logical_column_resolves_to_what_create_schema_made(self, redshift_adapter, tpch):
        connection = RecordingConnection()
        redshift_adapter.create_schema(tpch, connection)
        _, columns = created_identifiers(connection.recorder.statements, "lineitem", str.lower)
        for logical in ("L_ORDERKEY", "l_orderkey"):
            assert redshift_adapter.resolve_physical_column("LINEITEM", logical, connection) == columns["l_orderkey"]

    def test_maintenance_statements_use_the_physical_table(self, redshift_adapter):
        connection = RecordingConnection()
        tuning = clustering_tuning("LINEITEM", "L_ORDERKEY")
        redshift_adapter.apply_table_tunings(tuning, connection)
        statements = connection.recorder.statements
        assert any("tablename = 'lineitem'" in statement for statement in statements)
        assert "ANALYZE lineitem" not in statements
        config = SimpleNamespace(table_tunings={"LINEITEM": tuning})
        assert redshift_adapter.auto_analyze is True
        assert redshift_adapter.apply_post_load_tunings("LINEITEM", config, connection) is False
        assert "ANALYZE lineitem" not in connection.recorder.statements
        redshift_adapter.auto_analyze = False
        assert redshift_adapter.apply_post_load_tunings("LINEITEM", config, connection) is True
        assert "ANALYZE lineitem" in connection.recorder.statements
        assert not any(statement.startswith("VACUUM") for statement in connection.recorder.statements)

    def test_a_refused_analyze_fails_the_maintenance_phase(self, redshift_adapter):
        connection = RecordingConnection()

        def refuse(sql, params=None):
            raise RuntimeError("analyze refused")

        connection.recorder.execute = refuse
        redshift_adapter.auto_analyze = False
        config = SimpleNamespace(table_tunings={"LINEITEM": clustering_tuning("LINEITEM", "L_ORDERKEY")})
        redshift_adapter.run_post_load_tunings("LINEITEM", config, connection)

        assert redshift_adapter.build_post_load_maintenance_phase().status == "FAILED"

    def test_existing_keys_compare_against_physical_column_names(self, redshift_adapter, caplog):
        row = ("public", "lineitem", "KEY", "l_orderkey", "l_orderkey", "l_linenumber", None, None)
        connection = RecordingConnection(row)
        tuning = TableTuning(
            table_name="LINEITEM",
            distribution=[TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1)],
            sorting=[
                TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1),
                TuningColumn(name="L_LINENUMBER", type="INTEGER", order=2),
            ],
        )
        with caplog.at_level(logging.INFO):
            redshift_adapter.apply_table_tunings(tuning, connection)
        assert "Current configuration for lineitem" in caplog.text
        assert "mismatch" not in caplog.text


@pytest.mark.parametrize("adapter_file", ["snowflake.py", "redshift.py"])
def test_tuning_builders_do_not_case_fold_the_logical_table_name(adapter_file):
    source = (PLATFORMS_DIR / adapter_file).read_text()
    assert "table_tuning.table_name.upper()" not in source
    assert "table_tuning.table_name.lower()" not in source


