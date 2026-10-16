from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox import TPCH
from benchbox.core.tuning.ddl_generator import ColumnDefinition, get_ddl_generator
from benchbox.core.tuning.generators.clickhouse import (
    ClickHouseDDLGenerator,
    ClickHousePrimaryKeyPrefixError,
    clickhouse_sort_key_columns,
)
from benchbox.core.tuning.interface import (
    TableTuning,
    TuningColumn,
    UnifiedTuningConfiguration,
)
from benchbox.platforms.clickhouse.workload import ClickHouseWorkloadMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "clickhouse_tpch_untuned_ddl.json"

ORDERS_STATEMENT_WITH_PRIMARY_KEY = (
    "CREATE TABLE orders (\n"
    "    o_orderkey INTEGER NOT NULL,\n"
    "    o_custkey INTEGER NOT NULL,\n"
    "    o_orderdate DATE NOT NULL,\n"
    "    o_comment VARCHAR(79) NOT NULL,\n"
    "    PRIMARY KEY (o_orderkey)\n"
    ")"
)
ORDERS_STATEMENT_WITHOUT_PRIMARY_KEY = (
    "CREATE TABLE orders (\n"
    "    o_orderkey INTEGER NOT NULL,\n"
    "    o_custkey INTEGER NOT NULL,\n"
    "    o_orderdate DATE NOT NULL,\n"
    "    o_comment VARCHAR(79) NOT NULL\n"
    ")"
)

NON_PREFIX_MESSAGE = (
    "ClickHouse table ORDERS: primary key (o_orderkey) must be a prefix of the tuned sort key "
    "(O_ORDERDATE, O_ORDERKEY). Put the PK columns first in the sort key, or disable primary_keys."
)


class _Host(ClickHouseWorkloadMixin):
    def __init__(self):
        self.logger = Mock()


class _SchemaHost(ClickHouseWorkloadMixin):
    def __init__(self, config: UnifiedTuningConfiguration):
        self.logger = Mock()
        self.tuning_enabled = True
        self._config = config

    def get_effective_tuning_configuration(self):
        return self._config

    def _log_constraint_configuration(self, enable_primary_keys, enable_foreign_keys):
        return None


def _sorting(*names: str) -> list[TuningColumn]:
    return [TuningColumn(name=name, type="INTEGER", order=position) for position, name in enumerate(names, start=1)]


def _orders_tuning(sorting: tuple[str, ...] = (), clustering: tuple[str, ...] = ()) -> TableTuning:
    return TableTuning(
        table_name="ORDERS",
        sorting=_sorting(*sorting) if sorting else None,
        clustering=_sorting(*clustering) if clustering else None,
    )


def _config(table_tuning: TableTuning, primary_keys_enabled: bool) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.primary_keys.enabled = primary_keys_enabled
    config.foreign_keys.enabled = False
    config.unique_constraints.enabled = False
    config.check_constraints.enabled = False
    config.table_tunings[table_tuning.table_name] = table_tuning
    return config


def _primary_key_clauses(ddl: str) -> list[str]:
    return re.findall(r"PRIMARY\s+KEY[^\n]*", ddl, flags=re.IGNORECASE)


class TestSortKeyColumns:
    def test_clustering_columns_come_before_sorting_columns(self):
        tuning = _orders_tuning(sorting=("O_ORDERDATE",), clustering=("O_ORDERKEY",))

        assert clickhouse_sort_key_columns(tuning) == ["O_ORDERKEY", "O_ORDERDATE"]

    def test_tuning_clauses_use_the_same_effective_sort_key(self):
        tuning = _orders_tuning(sorting=("O_ORDERDATE", "O_CUSTKEY"), clustering=("O_ORDERKEY",))

        clauses = ClickHouseDDLGenerator().generate_tuning_clauses(tuning)

        assert clauses.sort_by == ", ".join(clickhouse_sort_key_columns(tuning))


class TestGeneratorPrimaryKey:
    def test_prefix_primary_key_is_emitted_after_order_by(self):
        generator = get_ddl_generator("clickhouse")
        tuning = _orders_tuning(sorting=("O_ORDERKEY", "O_ORDERDATE"))

        clauses = generator.generate_tuning_clauses(tuning, primary_key_columns=["o_orderkey"])
        ddl = generator.generate_create_table_ddl(
            "orders",
            [ColumnDefinition(name="o_orderkey", data_type="BIGINT")],
            clauses,
        )

        assert clauses.sort_by == "O_ORDERKEY, O_ORDERDATE"
        assert clauses.primary_key == "o_orderkey"
        assert "ORDER BY (O_ORDERKEY, O_ORDERDATE)\nPRIMARY KEY (o_orderkey)\nSETTINGS" in ddl

    def test_primary_key_is_omitted_when_not_requested(self):
        generator = get_ddl_generator("clickhouse")
        tuning = _orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY"))

        clauses = generator.generate_tuning_clauses(tuning)
        ddl = generator.generate_create_table_ddl(
            "orders",
            [ColumnDefinition(name="o_orderkey", data_type="BIGINT")],
            clauses,
        )

        assert clauses.primary_key is None
        assert _primary_key_clauses(ddl) == []
        assert "ORDER BY (O_ORDERDATE, O_ORDERKEY)" in ddl

    def test_non_prefix_primary_key_raises_named_error(self):
        generator = get_ddl_generator("clickhouse")
        tuning = _orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY"))

        with pytest.raises(ClickHousePrimaryKeyPrefixError) as excinfo:
            generator.generate_tuning_clauses(tuning, primary_key_columns=["o_orderkey"])

        assert str(excinfo.value) == NON_PREFIX_MESSAGE

    @pytest.mark.parametrize(
        ("sort_columns", "primary_key_columns"),
        [
            (("O_ORDERKEY", "O_ORDERDATE"), ["o_orderkey"]),
            (("o_orderkey", "o_linenumber", "o_orderdate"), ["O_ORDERKEY", "O_LINENUMBER"]),
            (("O_ORDERKEY",), ["o_orderkey"]),
        ],
    )
    def test_prefix_comparison_ignores_case(self, sort_columns, primary_key_columns):
        generator = get_ddl_generator("clickhouse")

        clauses = generator.generate_tuning_clauses(
            _orders_tuning(sorting=sort_columns), primary_key_columns=primary_key_columns
        )

        assert clauses.primary_key == ", ".join(primary_key_columns)

    def test_primary_key_longer_than_sort_key_is_not_a_prefix(self):
        generator = get_ddl_generator("clickhouse")
        tuning = _orders_tuning(sorting=("O_ORDERKEY",))

        with pytest.raises(ClickHousePrimaryKeyPrefixError):
            generator.generate_tuning_clauses(tuning, primary_key_columns=["o_orderkey", "o_custkey"])

    def test_clustering_column_ahead_of_sorting_satisfies_prefix(self):
        generator = get_ddl_generator("clickhouse")
        tuning = _orders_tuning(sorting=("O_ORDERDATE",), clustering=("O_ORDERKEY",))

        clauses = generator.generate_tuning_clauses(tuning, primary_key_columns=["o_orderkey"])

        assert clauses.sort_by == "O_ORDERKEY, O_ORDERDATE"
        assert clauses.primary_key == "o_orderkey"

    def test_clustering_column_ahead_of_sorting_can_break_prefix(self):
        generator = get_ddl_generator("clickhouse")
        tuning = _orders_tuning(sorting=("O_ORDERKEY",), clustering=("O_ORDERDATE",))

        with pytest.raises(ClickHousePrimaryKeyPrefixError):
            generator.generate_tuning_clauses(tuning, primary_key_columns=["o_orderkey"])


class TestDateFirstSortKeyDdl:
    def test_primary_keys_disabled_emits_no_primary_key_clause(self):
        tuning = _orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY"))

        rendered = _Host()._optimize_table_definition(
            ORDERS_STATEMENT_WITH_PRIMARY_KEY,
            {"ORDERS": tuning},
            primary_keys_enabled=False,
        )

        assert _primary_key_clauses(rendered) == []
        assert rendered.endswith("ENGINE = MergeTree() ORDER BY (o_orderdate, o_orderkey)")

    def test_primary_keys_disabled_with_schema_that_has_no_primary_key(self):
        tuning = _orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY"))

        rendered = _Host()._optimize_table_definition(
            ORDERS_STATEMENT_WITHOUT_PRIMARY_KEY,
            {"ORDERS": tuning},
            primary_keys_enabled=False,
        )

        assert _primary_key_clauses(rendered) == []
        assert rendered.endswith("ENGINE = MergeTree() ORDER BY (o_orderdate, o_orderkey)")

    def test_enabled_non_prefix_primary_key_is_a_named_error(self):
        tuning = _orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY"))

        with pytest.raises(ClickHousePrimaryKeyPrefixError) as excinfo:
            _Host()._optimize_table_definition(
                ORDERS_STATEMENT_WITH_PRIMARY_KEY,
                {"ORDERS": tuning},
                primary_keys_enabled=True,
            )

        assert str(excinfo.value) == NON_PREFIX_MESSAGE

    def test_enabled_prefix_primary_key_is_emitted_once_after_order_by(self):
        tuning = _orders_tuning(sorting=("O_ORDERKEY", "O_ORDERDATE"))

        rendered = _Host()._optimize_table_definition(
            ORDERS_STATEMENT_WITH_PRIMARY_KEY,
            {"ORDERS": tuning},
            primary_keys_enabled=True,
        )

        assert _primary_key_clauses(rendered) == ["PRIMARY KEY (o_orderkey)"]
        assert rendered.endswith("ENGINE = MergeTree() ORDER BY (o_orderkey, o_orderdate) PRIMARY KEY (o_orderkey)")
        assert "o_comment VARCHAR(79) NOT NULL\n)" in rendered

    def test_partition_only_tuning_keeps_the_schema_primary_key(self):
        tuning = TableTuning(
            table_name="ORDERS",
            partitioning=[TuningColumn(name="O_ORDERDATE", type="DATE", order=1)],
        )

        rendered = _Host()._optimize_table_definition(
            ORDERS_STATEMENT_WITH_PRIMARY_KEY,
            {"ORDERS": tuning},
            primary_keys_enabled=True,
        )

        assert "PRIMARY KEY (o_orderkey)\n)" in rendered
        assert rendered.endswith("PARTITION BY (toYYYYMM(o_orderdate)) ORDER BY (o_orderkey)")


class TestCreateSchemaOrdering:
    def _benchmark(self):
        return TPCH(scale_factor=0.01)

    def test_non_prefix_primary_key_fails_before_any_table_is_created(self):
        config = _config(_orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY")), primary_keys_enabled=True)
        connection = Mock()

        with pytest.raises(ClickHousePrimaryKeyPrefixError, match="must be a prefix of the tuned sort key"):
            _SchemaHost(config).create_schema(self._benchmark(), connection)

        connection.execute.assert_not_called()

    def test_date_first_sort_key_with_primary_keys_disabled_creates_every_table(self):
        config = _config(_orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY")), primary_keys_enabled=False)
        connection = Mock()

        _SchemaHost(config).create_schema(self._benchmark(), connection)

        executed = [call.args[0] for call in connection.execute.call_args_list]
        orders = next(statement for statement in executed if statement.startswith("CREATE TABLE orders"))
        assert len(executed) == 8
        assert _primary_key_clauses(orders) == []
        assert orders.endswith("ORDER BY (o_orderdate, o_orderkey)")

    def test_prefix_sort_key_with_primary_keys_enabled_creates_every_table(self):
        config = _config(_orders_tuning(sorting=("O_ORDERKEY", "O_ORDERDATE")), primary_keys_enabled=True)
        connection = Mock()

        _SchemaHost(config).create_schema(self._benchmark(), connection)

        executed = [call.args[0] for call in connection.execute.call_args_list]
        orders = next(statement for statement in executed if statement.startswith("CREATE TABLE orders"))
        assert len(executed) == 8
        assert _primary_key_clauses(orders) == ["PRIMARY KEY (o_orderkey)"]
        assert "ORDER BY (o_orderkey, o_orderdate) PRIMARY KEY (o_orderkey)" in orders


class TestValidatorPrimaryKeyPrefix:
    SCHEMA_PRIMARY_KEYS = {"orders": ["o_orderkey"], "lineitem": ["l_orderkey", "l_linenumber"]}

    @pytest.mark.parametrize("platform", ["clickhouse", "clickhouse-local", "clickhouse_server", "chdb"])
    def test_non_prefix_primary_key_is_an_error(self, platform):
        config = _config(_orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY")), primary_keys_enabled=True)

        errors, _warnings = config.validate_for_platform_detailed(
            platform, schema_primary_keys=self.SCHEMA_PRIMARY_KEYS
        )

        assert errors == [NON_PREFIX_MESSAGE]

    def test_prefix_primary_key_is_not_an_error(self):
        config = _config(_orders_tuning(sorting=("O_ORDERKEY", "O_ORDERDATE")), primary_keys_enabled=True)

        errors, _warnings = config.validate_for_platform_detailed(
            "clickhouse", schema_primary_keys=self.SCHEMA_PRIMARY_KEYS
        )

        assert errors == []

    def test_disabled_primary_keys_is_not_an_error(self):
        config = _config(_orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY")), primary_keys_enabled=False)

        errors, _warnings = config.validate_for_platform_detailed(
            "clickhouse", schema_primary_keys=self.SCHEMA_PRIMARY_KEYS
        )

        assert errors == []

    def test_other_platforms_are_not_checked(self):
        config = _config(_orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY")), primary_keys_enabled=True)

        errors, _warnings = config.validate_for_platform_detailed(
            "duckdb", schema_primary_keys=self.SCHEMA_PRIMARY_KEYS
        )

        assert not any("prefix" in error for error in errors)

    def test_tables_without_schema_primary_keys_are_not_checked(self):
        config = _config(_orders_tuning(sorting=("O_ORDERDATE", "O_ORDERKEY")), primary_keys_enabled=True)

        errors, _warnings = config.validate_for_platform_detailed("clickhouse", schema_primary_keys={})

        assert errors == []


class TestUntunedDdlSnapshot:
    @staticmethod
    def _render_untuned(primary_keys_enabled: bool) -> dict[str, str]:
        config = UnifiedTuningConfiguration()
        config.primary_keys.enabled = primary_keys_enabled
        config.foreign_keys.enabled = False
        tpch = TPCH(scale_factor=0.01)
        schema_sql = tpch.get_create_tables_sql(dialect="duckdb", tuning_config=config)
        nullable_columns = _Host._get_nullable_columns_by_table(tpch)
        host = _Host()
        rendered = {}
        for statement in [part.strip() for part in schema_sql.split(";") if part.strip()]:
            table_name = _Host._extract_table_name(statement)
            rendered[table_name] = host._optimize_table_definition(
                statement, None, nullable_columns=nullable_columns.get(table_name.lower(), set())
            )
        return rendered

    @pytest.mark.parametrize(
        ("snapshot_key", "primary_keys_enabled"),
        [("primary_keys_enabled", True), ("primary_keys_disabled", False)],
    )
    def test_untuned_tpch_ddl_is_byte_identical_to_the_recorded_baseline(self, snapshot_key, primary_keys_enabled):
        expected = json.loads(SNAPSHOT_PATH.read_text())[snapshot_key]

        assert self._render_untuned(primary_keys_enabled) == expected
