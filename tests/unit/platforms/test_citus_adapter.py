# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import re
from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.citus as citus_module
import benchbox.platforms.postgresql as postgresql_module
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.citus import CITUS_EXTENSION, CitusAdapter
from benchbox.platforms.postgresql import POSTGRES_DIALECT, PostgreSQLAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def citus_stubs(monkeypatch):

    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(citus_module, "psycopg", mock_psycopg)
    monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

    return mock_psycopg


class _ScriptedCursor:
    def __init__(
        self,
        *,
        columns: dict[str, set[str]] | None = None,
        placements: dict[str, tuple[str, str | None]] | None = None,
        partitioned: tuple[str, ...] = (),
        fail_on: str | None = None,
        readback_method: str | None = None,
        shards: tuple[int, int] = (1, 1),
        indexed_columns: tuple[str, ...] = ("l_orderkey",),
        columnar_extension_fails: bool = False,
    ):
        self.executed: list[str] = []
        self.closed = False
        self._columns = columns
        self._placements = dict(placements or {})
        self._partitioned = set(partitioned)
        self._fail_on = fail_on
        self._readback_method = readback_method
        self._shards = shards
        self._indexed_columns = indexed_columns
        self._columnar_extension_fails = columnar_extension_fails
        self._access_methods: dict[str, str] = {}
        self._rows: list[tuple] = []
        self._one: tuple | None = None

    def execute(self, sql, params=()):
        self.executed.append(sql)
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError("worker unavailable")
        if self._columnar_extension_fails and "citus_columnar" in sql:
            raise RuntimeError("extension citus_columnar is not available")
        self._rows = []
        if "information_schema.columns" in sql:
            name, column = params
            self._one = (1,) if self._columns is None or column in self._columns.get(name, set()) else None
            return
        table = params[0].strip('"') if params and isinstance(params[0], str) else None
        created = re.search(r"create_distributed_table\('\"(\w+)\"', '(\w+)'\)", sql)
        if created:
            self._placements[created.group(1)] = ("h", created.group(2))
        reference = re.search(r"create_reference_table\('\"(\w+)\"'\)", sql)
        if reference:
            self._placements[reference.group(1)] = ("n", None)
        columnar = re.search(r"alter_table_set_access_method\('\"(\w+)\"', 'columnar'\)", sql)
        if columnar:
            self._access_methods[columnar.group(1)] = "columnar"
        if "FROM pg_dist_partition" in sql and table in self._placements:
            method, column = self._placements[table]
            method = self._readback_method or method
            self._rows = [(method, column, 7)] if "colocationid" in sql else [(method, column)]
        elif "relkind" in sql and "WITH RECURSIVE" not in sql:
            self._rows = [("p" if table in self._partitioned else "r",)]
        elif "pg_am" in sql:
            self._rows = [(self._access_methods.get(table, "heap"),)]
        elif "run_command_on_shards" in sql:
            self._rows = [self._shards]
        elif "WITH RECURSIVE" in sql:
            self._rows = (
                [(101,)]
                if "relkind = 'r'" in sql
                else [(201, 101, position, name) for position, name in enumerate(self._indexed_columns, start=1)]
            )

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


def _connection(cursor: _ScriptedCursor) -> Mock:
    connection = Mock()
    connection.cursor.return_value = cursor
    return connection


def _benchmark(*tables: str) -> Mock:
    benchmark = Mock()
    benchmark._get_active_tables.return_value = list(tables)
    return benchmark


def _column(name: str, order: int = 1) -> TuningColumn:
    return TuningColumn(name=name, type="INTEGER", order=order)


def _tune(adapter: CitusAdapter, **tables: TableTuning) -> None:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings.update(tables)
    adapter.tuning_enabled = True
    adapter.unified_tuning_configuration = config
    adapter._applied_tuning_ledger = AppliedTuningLedger()


def _create_schema(adapter: CitusAdapter, cursor: _ScriptedCursor, *tables: str) -> Mock:
    connection = _connection(cursor)
    with patch.object(PostgreSQLAdapter, "create_schema", return_value=1.0):
        adapter.create_schema(_benchmark(*tables), connection)
    return connection


def _statements(cursor: _ScriptedCursor, needle: str) -> list[str]:
    return [sql for sql in cursor.executed if needle in sql]


class TestCitusAdapter:
    def test_initialization_defaults(self, citus_stubs):

        adapter = CitusAdapter()

        assert adapter.platform_name == "citus"
        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 5432
        assert adapter.database == "benchbox"
        assert adapter.username == "postgres"
        assert adapter.schema == "public"
        assert adapter.distribution_column is None

    def test_initialization_with_distribution_column(self, citus_stubs):

        adapter = CitusAdapter(distribution_column="l_orderkey")

        assert adapter.distribution_column == "l_orderkey"

    def test_dialect_is_postgres(self, citus_stubs):

        adapter = CitusAdapter()

        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.get_target_dialect() == "postgres"

    def test_from_config_basic(self, citus_stubs):

        config = {
            "host": "citus.local",
            "port": 5433,
            "database": "test_analytics",
            "distribution_column": "l_orderkey",
        }

        adapter = CitusAdapter.from_config(config)

        assert adapter.host == "citus.local"
        assert adapter.port == 5433
        assert adapter.database == "test_analytics"
        assert adapter.distribution_column == "l_orderkey"

    def test_create_connection_verifies_extension(self, citus_stubs):

        adapter = CitusAdapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ("12.1",)
        mock_cursor.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(PostgreSQLAdapter, "create_connection", return_value=mock_conn):
            result = adapter.create_connection()

        assert result is mock_conn
        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any(CITUS_EXTENSION in sql for sql in executed)

    def test_create_connection_raises_when_extension_unavailable(self, citus_stubs):

        adapter = CitusAdapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_cursor.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(PostgreSQLAdapter, "create_connection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="citus extension is not available"):
                adapter.create_connection()

    def test_flag_distributes_every_table_that_has_the_column(self, citus_stubs):
        adapter = CitusAdapter(distribution_column="l_orderkey")
        cursor = _ScriptedCursor(columns={"lineitem": {"l_orderkey"}, "nation": set()})

        _create_schema(adapter, cursor, "lineitem", "nation")

        assert len(_statements(cursor, "create_distributed_table")) == 1
        assert "lineitem" in _statements(cursor, "create_distributed_table")[0]

    def test_nothing_is_placed_without_a_flag_or_tuning(self, citus_stubs):
        adapter = CitusAdapter()
        cursor = _ScriptedCursor()

        connection = _create_schema(adapter, cursor, "lineitem")

        connection.cursor.assert_not_called()

    def test_flag_must_be_a_plain_identifier(self, citus_stubs):
        adapter = CitusAdapter(distribution_column="x'); DROP TABLE t; --")

        with pytest.raises(ValueError, match="plain SQL identifier"):
            _create_schema(adapter, _ScriptedCursor(), "lineitem")

    def test_flag_distribution_failure_aborts_the_run(self, citus_stubs):
        adapter = CitusAdapter(distribution_column="l_orderkey")
        cursor = _ScriptedCursor(fail_on="create_distributed_table")

        with pytest.raises(RuntimeError, match="worker unavailable"):
            _create_schema(adapter, cursor, "lineitem")

    def test_reused_database_applies_missing_distribution(self, citus_stubs):
        from benchbox.platforms.base.result_capture import ResultCaptureMixin

        adapter = CitusAdapter(distribution_column="l_orderkey")
        cursor = _ScriptedCursor()

        with patch.object(ResultCaptureMixin, "_setup_reused_database_phases", return_value=("phases",)):
            phases = adapter._setup_reused_database_phases(_benchmark("lineitem"), _connection(cursor))

        assert phases == ("phases",)
        assert len(_statements(cursor, "create_distributed_table")) == 1

    def test_reused_database_keeps_matching_distribution(self, citus_stubs):
        from benchbox.platforms.base.result_capture import ResultCaptureMixin

        adapter = CitusAdapter(distribution_column="l_orderkey")
        cursor = _ScriptedCursor(placements={"lineitem": ("h", "l_orderkey")})

        with patch.object(ResultCaptureMixin, "_setup_reused_database_phases", return_value=()):
            adapter._setup_reused_database_phases(_benchmark("lineitem"), _connection(cursor))

        assert _statements(cursor, "create_distributed_table") == []

    def test_reused_database_rejects_conflicting_distribution(self, citus_stubs):
        from benchbox.platforms.base.result_capture import ResultCaptureMixin

        adapter = CitusAdapter(distribution_column="l_orderkey")
        cursor = _ScriptedCursor(placements={"lineitem": ("h", "o_orderkey")})

        with patch.object(ResultCaptureMixin, "_setup_reused_database_phases", return_value=()):
            with pytest.raises(RuntimeError, match="already placed"):
                adapter._setup_reused_database_phases(_benchmark("lineitem"), _connection(cursor))

    def test_reused_database_rejects_a_table_placed_the_other_way(self, citus_stubs):
        from benchbox.platforms.base.result_capture import ResultCaptureMixin

        adapter = CitusAdapter()
        _tune(adapter, nation=TableTuning(table_name="nation", reference=True))
        cursor = _ScriptedCursor(placements={"nation": ("h", "n_nationkey")})

        with patch.object(ResultCaptureMixin, "_setup_reused_database_phases", return_value=()):
            with pytest.raises(RuntimeError, match="reference table was requested"):
                adapter._setup_reused_database_phases(_benchmark("nation"), _connection(cursor))

    def test_reused_database_does_nothing_when_unconfigured(self, citus_stubs):
        from benchbox.platforms.base.result_capture import ResultCaptureMixin

        adapter = CitusAdapter()
        connection = _connection(_ScriptedCursor())

        with patch.object(ResultCaptureMixin, "_setup_reused_database_phases", return_value=()):
            adapter._setup_reused_database_phases(_benchmark("lineitem"), connection)

        connection.cursor.assert_not_called()


class TestCitusTuningFile:
    def test_file_places_tables_and_wins_over_the_flag(self, citus_stubs):
        adapter = CitusAdapter(distribution_column="l_partkey")
        _tune(
            adapter,
            lineitem=TableTuning(table_name="lineitem", distribution=[_column("l_orderkey")]),
            nation=TableTuning(table_name="nation", reference=True),
        )
        cursor = _ScriptedCursor()

        _create_schema(adapter, cursor, "lineitem", "nation", "part")

        created = _statements(cursor, "create_distributed_table")
        assert any("lineitem" in sql and "l_orderkey" in sql for sql in created)
        assert any("part" in sql and "l_partkey" in sql for sql in created)
        assert not any("lineitem" in sql and "l_partkey" in sql for sql in created)
        assert len(_statements(cursor, "create_reference_table")) == 1
        ledger = adapter._applied_tuning_ledger
        satisfied = {entry.intent: entry.reason for entry in ledger.satisfied}
        assert set(satisfied) == {"distribution:lineitem (l_orderkey)", "reference_table:nation"}
        assert all("pg_dist_partition readback" in reason for reason in satisfied.values())
        assert ledger.dropped == []

    def test_catalog_mismatch_drops_the_intent_instead_of_claiming_it(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(adapter, lineitem=TableTuning(table_name="lineitem", distribution=[_column("l_orderkey")]))

        _create_schema(adapter, _ScriptedCursor(readback_method="n"), "lineitem")

        ledger = adapter._applied_tuning_ledger
        assert ledger.satisfied == []
        assert [item.intent for item in ledger.dropped] == ["distribution:lineitem (l_orderkey)"]
        assert "catalog verification failed" in ledger.dropped[0].reason

    def test_failed_placement_is_recorded_and_does_not_abort(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(adapter, lineitem=TableTuning(table_name="lineitem", distribution=[_column("l_orderkey")]))

        _create_schema(adapter, _ScriptedCursor(fail_on="create_distributed_table"), "lineitem")

        ledger = adapter._applied_tuning_ledger
        assert [item.status for item in ledger.statements] == ["failed"]
        assert [item.intent for item in ledger.dropped] == ["distribution:lineitem (l_orderkey)"]
        assert ledger.satisfied == []

    def test_several_distribution_columns_are_not_applied(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(
            adapter,
            lineitem=TableTuning(
                table_name="lineitem", distribution=[_column("l_orderkey", 1), _column("l_linenumber", 2)]
            ),
        )
        cursor = _ScriptedCursor()

        _create_schema(adapter, cursor, "lineitem")

        assert _statements(cursor, "create_distributed_table") == []
        assert [item.intent for item in adapter._applied_tuning_ledger.dropped] == [
            "distribution:lineitem (l_orderkey, l_linenumber)"
        ]

    def test_missing_distribution_column_is_dropped_and_the_table_stays_local(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(adapter, nation=TableTuning(table_name="nation", distribution=[_column("l_orderkey")]))

        cursor = _ScriptedCursor(columns={"nation": set()})
        _create_schema(adapter, cursor, "nation")

        assert _statements(cursor, "create_distributed_table") == []
        assert "lacks distribution column" in adapter._applied_tuning_ledger.dropped[0].reason

    def test_columnar_is_applied_and_verified(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(adapter, orders=TableTuning(table_name="orders", columnar=True))

        cursor = _ScriptedCursor()
        _create_schema(adapter, cursor, "orders")

        assert len(_statements(cursor, "alter_table_set_access_method")) == 1
        assert [item.intent for item in adapter._applied_tuning_ledger.satisfied] == ["columnar:orders"]

    def test_columnar_is_not_applied_to_a_partitioned_table(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(adapter, orders=TableTuning(table_name="orders", columnar=True))

        cursor = _ScriptedCursor(partitioned=("orders",))
        _create_schema(adapter, cursor, "orders")

        assert _statements(cursor, "alter_table_set_access_method") == []
        assert "partitioned table" in adapter._applied_tuning_ledger.dropped[0].reason

    def test_columnar_needs_the_extension(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(adapter, orders=TableTuning(table_name="orders", columnar=True))

        cursor = _ScriptedCursor(columnar_extension_fails=True)
        _create_schema(adapter, cursor, "orders")

        assert _statements(cursor, "alter_table_set_access_method") == []
        assert "citus_columnar extension is not available" in adapter._applied_tuning_ledger.dropped[0].reason


class TestCitusClustering:
    @staticmethod
    def _clustered_adapter(
        table: TableTuning, cursor: _ScriptedCursor
    ) -> tuple[CitusAdapter, UnifiedTuningConfiguration]:
        adapter = CitusAdapter()
        _tune(adapter, **{table.table_name: table})
        _create_schema(adapter, cursor, table.table_name)
        return adapter, adapter.unified_tuning_configuration

    def test_cluster_is_skipped_on_columnar_tables(self, citus_stubs):
        table = TableTuning(table_name="orders", sorting=[_column("o_orderkey")], columnar=True)
        adapter, config = self._clustered_adapter(table, _ScriptedCursor())

        with patch.object(PostgreSQLAdapter, "apply_post_load_tunings") as base:
            performed = adapter.apply_post_load_tunings("orders", config, Mock())

        base.assert_not_called()
        assert performed is False
        assert "columnar" in adapter._applied_tuning_ledger.dropped[-1].reason

    def test_cluster_is_skipped_on_partitioned_distributed_tables(self, citus_stubs):
        table = TableTuning(
            table_name="lineitem", distribution=[_column("l_orderkey")], sorting=[_column("l_shipdate")]
        )
        adapter, config = self._clustered_adapter(table, _ScriptedCursor(partitioned=("lineitem",)))

        with patch.object(PostgreSQLAdapter, "apply_post_load_tunings") as base:
            performed = adapter.apply_post_load_tunings("lineitem", config, Mock())

        base.assert_not_called()
        assert performed is False
        assert "does not propagate CLUSTER" in adapter._applied_tuning_ledger.dropped[-1].reason

    def test_cluster_is_claimed_when_every_shard_is_clustered(self, citus_stubs):
        table = TableTuning(
            table_name="lineitem", distribution=[_column("l_orderkey")], sorting=[_column("l_shipdate")]
        )
        cursor = _ScriptedCursor(shards=(32, 32), indexed_columns=("l_shipdate",))
        adapter, config = self._clustered_adapter(table, cursor)

        assert adapter.apply_post_load_tunings("lineitem", config, _connection(cursor)) is True

        ledger = adapter._applied_tuning_ledger
        assert any("sorting:lineitem" in entry.intent for entry in ledger.satisfied)
        assert any(sql.startswith("CLUSTER") for sql in cursor.executed)

    def test_cluster_is_refused_when_a_shard_is_not_clustered(self, citus_stubs):
        table = TableTuning(
            table_name="lineitem", distribution=[_column("l_orderkey")], sorting=[_column("l_shipdate")]
        )
        cursor = _ScriptedCursor(shards=(31, 32), indexed_columns=("l_shipdate",))
        adapter, config = self._clustered_adapter(table, cursor)

        with pytest.raises(ValueError, match="CLUSTER reached 31 of 32 shards"):
            adapter.apply_post_load_tunings("lineitem", config, _connection(cursor))

        ledger = adapter._applied_tuning_ledger
        assert not any(entry.intent.startswith("sorting:") for entry in ledger.satisfied)
        assert any("CLUSTER reached 31 of 32 shards" in entry.reason for entry in ledger.dropped)

    def test_local_tables_keep_shell_level_verification_only(self, citus_stubs):
        table = TableTuning(table_name="nation", sorting=[_column("n_name")])
        cursor = _ScriptedCursor(indexed_columns=("n_name",))
        adapter, config = self._clustered_adapter(table, cursor)

        assert adapter.apply_post_load_tunings("nation", config, _connection(cursor)) is True
        assert _statements(cursor, "run_command_on_shards") == []

    def test_failed_distribution_leaves_cluster_to_the_shell_check(self, citus_stubs):
        table = TableTuning(
            table_name="lineitem", distribution=[_column("l_orderkey")], sorting=[_column("l_shipdate")]
        )
        cursor = _ScriptedCursor(fail_on="create_distributed_table", indexed_columns=("l_shipdate",))
        adapter, config = self._clustered_adapter(table, cursor)

        assert adapter.apply_post_load_tunings("lineitem", config, _connection(cursor)) is True
        assert _statements(cursor, "run_command_on_shards") == []

    def test_failed_columnar_conversion_does_not_skip_cluster(self, citus_stubs):
        table = TableTuning(table_name="orders", sorting=[_column("o_orderkey")], columnar=True)
        cursor = _ScriptedCursor(columnar_extension_fails=True, indexed_columns=("o_orderkey",))
        adapter, config = self._clustered_adapter(table, cursor)

        assert adapter.apply_post_load_tunings("orders", config, _connection(cursor)) is True
        assert any(sql.startswith("CLUSTER") for sql in cursor.executed)


class TestCitusPlacementRobustness:
    def test_partitioned_state_does_not_leak_into_the_next_schema(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(adapter, orders=TableTuning(table_name="orders", columnar=True))

        _create_schema(adapter, _ScriptedCursor(partitioned=("orders",)), "orders")
        second = _ScriptedCursor()
        _create_schema(adapter, second, "orders")

        assert len(_statements(second, "alter_table_set_access_method")) == 1

    def test_names_are_escaped_inside_sql_literals(self, citus_stubs):
        adapter = CitusAdapter(distribution_column="o_key")
        cursor = _ScriptedCursor()

        _create_schema(adapter, cursor, "o'brien")

        assert "SELECT create_distributed_table('\"o''brien\"', 'o_key')" in cursor.executed

    def test_reference_wins_over_distribution_and_the_conflict_is_recorded(self, citus_stubs):
        adapter = CitusAdapter()
        _tune(
            adapter,
            nation=TableTuning(table_name="nation", reference=True, distribution=[_column("n_nationkey")]),
        )
        cursor = _ScriptedCursor()

        _create_schema(adapter, cursor, "nation")

        assert _statements(cursor, "create_distributed_table") == []
        ledger = adapter._applied_tuning_ledger
        assert [item.intent for item in ledger.satisfied] == ["reference_table:nation"]
        assert [item.intent for item in ledger.dropped] == ["distribution:nation (n_nationkey)"]

    def test_catalog_failure_rolls_the_connection_back(self, citus_stubs):
        adapter = CitusAdapter(distribution_column="l_orderkey")
        cursor = _ScriptedCursor(fail_on="information_schema")

        with pytest.raises(RuntimeError, match="worker unavailable"):
            connection = _connection(cursor)
            with patch.object(PostgreSQLAdapter, "create_schema", return_value=1.0):
                adapter.create_schema(_benchmark("lineitem"), connection)

        connection.rollback.assert_called()

    def test_reused_database_places_missing_tables_and_records_the_ledger(self, citus_stubs):
        from benchbox.platforms.base.result_capture import ResultCaptureMixin

        adapter = CitusAdapter()
        _tune(
            adapter,
            lineitem=TableTuning(table_name="lineitem", distribution=[_column("l_orderkey")]),
            orders=TableTuning(table_name="orders", columnar=True),
        )
        cursor = _ScriptedCursor()

        with patch.object(ResultCaptureMixin, "_setup_reused_database_phases", return_value=()):
            adapter._setup_reused_database_phases(_benchmark("lineitem", "orders"), _connection(cursor))

        assert {item.intent for item in adapter._applied_tuning_ledger.satisfied} == {
            "distribution:lineitem (l_orderkey)",
            "columnar:orders",
        }

    def test_reused_database_keeps_placements_that_already_match(self, citus_stubs):
        from benchbox.platforms.base.result_capture import ResultCaptureMixin

        adapter = CitusAdapter()
        _tune(adapter, lineitem=TableTuning(table_name="lineitem", distribution=[_column("L_ORDERKEY")]))
        cursor = _ScriptedCursor(placements={"lineitem": ("h", "l_orderkey")})

        with patch.object(ResultCaptureMixin, "_setup_reused_database_phases", return_value=()):
            adapter._setup_reused_database_phases(_benchmark("lineitem"), _connection(cursor))

        assert _statements(cursor, "create_distributed_table") == []


class TestCitusPlatformInfo:
    def test_platform_info_reports_version_and_column(self, citus_stubs):

        adapter = CitusAdapter(distribution_column="l_orderkey")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ("12.1",)
        mock_conn.cursor.return_value = mock_cursor

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_type"] == "citus"
        assert info["citus_version"] == "12.1"
        assert info["configuration"]["distribution_column"] == "l_orderkey"

    def test_no_extension_conflicts_declared(self):

        from benchbox.core.platform_registry import PlatformRegistry

        assert PlatformRegistry.get_platform_conflicts("citus") == []
