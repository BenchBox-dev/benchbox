from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from benchbox.core.tpcds.refresh_data import (
    SOURCE_TABLES,
    SOURCE_TABLES_BY_NAME,
    RefreshDataError,
    RefreshSet,
    create_staging_tables,
    drop_staging_tables,
    generate_refresh_sets,
    load_refresh_file,
    refresh_file_name,
)

duckdb = pytest.importorskip("duckdb")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

EXPECTED_ROWS_SF001_SET1 = {
    "s_purchase": 12,
    "s_purchase_lineitem": 144,
    "s_catalog_order": 8,
    "s_catalog_order_lineitem": 72,
    "s_web_order": 2,
    "s_web_order_lineitem": 24,
    "s_store_returns": 13,
    "s_catalog_returns": 8,
    "s_web_returns": 2,
    "s_inventory": 540,
    "s_delete": 3,
    "s_inventory_delete": 3,
}
STATEMENT_TARGET = re.compile(
    r"\b(?:INSERT\s+INTO|DROP\s+TABLE\s+IF\s+EXISTS|CREATE\s+TABLE|DELETE\s+FROM|UPDATE|TRUNCATE(?:\s+TABLE)?)\s+(\w+)",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def refresh_set(tmp_path_factory: pytest.TempPathFactory) -> RefreshSet:
    return generate_refresh_sets(0.01, 1, 1, tmp_path_factory.mktemp("refresh") / "dsdgen")[1]


def _split_lines(path: Path) -> list[list[str]]:
    return [line.split("|") for line in path.read_text(encoding="utf-8").splitlines()]


def _column_checksums(path: Path, width: int) -> list[str]:
    rows = _split_lines(path)
    return [
        hashlib.sha256("\x1f".join(row[index] for row in rows).encode("utf-8")).hexdigest() for index in range(width)
    ]


def _fingerprint(refresh_set: RefreshSet) -> dict[str, tuple[int, list[str]]]:
    return {
        table.name: (
            refresh_set.row_counts[table.name],
            _column_checksums(refresh_set.path_for(table.name), len(table.columns)),
        )
        for table in SOURCE_TABLES
    }


class _RecordingConnection:
    def __init__(self, inner):
        self._inner = inner
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(sql)
        return self._inner.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _raw_connection():
    return duckdb.connect(":memory:")


def _native_connection():
    from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
    from benchbox.platforms.duckdb import DuckDBAdapter

    return PlatformAdapterConnection(duckdb.connect(":memory:"), DuckDBAdapter(), maintenance_mode=True)


def _stage_all(connection, refresh_set: RefreshSet) -> None:
    create_staging_tables(connection, SOURCE_TABLES)
    for table in SOURCE_TABLES:
        load_refresh_file(connection, table, refresh_set.path_for(table.name), refresh_set.row_counts[table.name])


def _order_by(table) -> str:
    return ", ".join(str(index) for index in range(1, len(table.columns) + 1))


def _create_warehouse_stub(connection) -> None:
    connection.execute("CREATE TABLE store_sales (ss_item_sk INTEGER)")
    connection.execute("INSERT INTO store_sales VALUES (1), (2), (3)")


def _warehouse_rows(connection) -> list[tuple]:
    return connection.execute("SELECT ss_item_sk FROM store_sales ORDER BY 1").fetchall()


def test_source_tables_map_to_twelve_distinct_files(refresh_set: RefreshSet) -> None:
    assert len(SOURCE_TABLES) == 12
    assert {table.name for table in SOURCE_TABLES} == set(EXPECTED_ROWS_SF001_SET1)
    assert len({table.file_stem for table in SOURCE_TABLES}) == 12
    assert set(refresh_set.files) == set(SOURCE_TABLES_BY_NAME)
    for table in SOURCE_TABLES:
        path = refresh_set.path_for(table.name)
        assert path.is_file()
        assert path.name == refresh_file_name(table, 1)


def test_every_refresh_row_has_exactly_the_declared_field_count(refresh_set: RefreshSet) -> None:
    for table in SOURCE_TABLES:
        rows = _split_lines(refresh_set.path_for(table.name))
        assert rows, table.name
        assert {len(row) for row in rows} == {len(table.columns)}, table.name


def test_refresh_set_row_counts_match_files_and_known_scale_factor_values(refresh_set: RefreshSet) -> None:
    for table in SOURCE_TABLES:
        assert refresh_set.row_counts[table.name] == len(_split_lines(refresh_set.path_for(table.name)))
    assert dict(refresh_set.row_counts) == EXPECTED_ROWS_SF001_SET1


def test_refresh_generation_is_deterministic_per_table_and_column(tmp_path: Path, refresh_set: RefreshSet) -> None:
    first = generate_refresh_sets(0.01, 1, 1, tmp_path / "first")[1]
    second = generate_refresh_sets(0.01, 1, 1, tmp_path / "second")[1]

    assert _fingerprint(first) == _fingerprint(second)
    assert _fingerprint(first) == _fingerprint(refresh_set)


def test_distinct_refresh_set_indexes_generate_different_data(tmp_path: Path) -> None:
    sets = generate_refresh_sets(0.01, 1, 2, tmp_path / "pair")

    assert sorted(sets) == [1, 2]
    assert _fingerprint(sets[1]) != _fingerprint(sets[2])


@pytest.mark.parametrize("first_set", ["1", 1.0, True, None])
def test_generate_rejects_non_integer_first_set(tmp_path: Path, first_set) -> None:
    with pytest.raises(TypeError, match="first_set"):
        generate_refresh_sets(0.01, first_set, 1, tmp_path / "out")


@pytest.mark.parametrize("count", ["1", 2.5, True, None])
def test_generate_rejects_non_integer_count(tmp_path: Path, count) -> None:
    with pytest.raises(TypeError, match="count"):
        generate_refresh_sets(0.01, 1, count, tmp_path / "out")


@pytest.mark.parametrize("value", [0, -1])
def test_generate_rejects_non_positive_first_set_and_count(tmp_path: Path, value: int) -> None:
    with pytest.raises(ValueError, match="first_set"):
        generate_refresh_sets(0.01, value, 1, tmp_path / "out")
    with pytest.raises(ValueError, match="count"):
        generate_refresh_sets(0.01, 1, value, tmp_path / "out")


def test_generate_rejects_non_empty_output_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "occupied"
    output_dir.mkdir()
    (output_dir / "stale.dat").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        generate_refresh_sets(0.01, 1, 1, output_dir)

    assert [path.name for path in output_dir.iterdir()] == ["stale.dat"]


def test_generate_works_with_output_directory_longer_than_one_hundred_characters(
    tmp_path: Path, refresh_set: RefreshSet
) -> None:
    output_dir = tmp_path / ("a" * 60) / ("b" * 60) / "dsdgen"
    assert len(str(output_dir)) > 100

    generated = generate_refresh_sets(0.01, 1, 1, output_dir)[1]

    assert generated.directory == output_dir
    assert dict(generated.row_counts) == dict(refresh_set.row_counts)
    assert _fingerprint(generated) == _fingerprint(refresh_set)


def test_fallback_and_native_loaders_produce_identical_rows_for_every_staging_table(
    refresh_set: RefreshSet,
) -> None:
    fallback = _raw_connection()
    native = _native_connection()
    _stage_all(fallback, refresh_set)
    _stage_all(native, refresh_set)

    for table in SOURCE_TABLES:
        query = f"SELECT * FROM {table.name} ORDER BY {_order_by(table)}"
        fallback_rows = fallback.execute(query).fetchall()
        native_rows = native.execute(query).fetchall()
        assert len(fallback_rows) == refresh_set.row_counts[table.name], table.name
        assert fallback_rows == native_rows, table.name


@pytest.mark.parametrize("make_connection", [_raw_connection, _native_connection], ids=["fallback", "native"])
def test_empty_fields_become_null_for_every_column(refresh_set: RefreshSet, make_connection) -> None:
    connection = make_connection()
    _stage_all(connection, refresh_set)

    total_nulls = 0
    for table in SOURCE_TABLES:
        rows = _split_lines(refresh_set.path_for(table.name))
        for index, (name, _, _) in enumerate(table.columns):
            empty = sum(1 for row in rows if row[index] == "")
            nulls = connection.execute(f"SELECT COUNT(*) FROM {table.name} WHERE {name} IS NULL").fetchone()[0]
            assert nulls == empty, f"{table.name}.{name}"
            total_nulls += empty
    assert total_nulls > 0


@pytest.mark.parametrize("make_connection", [_raw_connection, _native_connection], ids=["fallback", "native"])
def test_row_count_mismatch_raises_and_leaves_warehouse_untouched(refresh_set: RefreshSet, make_connection) -> None:
    connection = make_connection()
    _create_warehouse_stub(connection)
    table = SOURCE_TABLES_BY_NAME["s_purchase"]
    create_staging_tables(connection, [table])

    with pytest.raises(RefreshDataError, match="s_purchase"):
        load_refresh_file(
            connection, table, refresh_set.path_for("s_purchase"), refresh_set.row_counts["s_purchase"] + 1
        )

    assert _warehouse_rows(connection) == [(1,), (2,), (3,)]


def _corrupt_copy(source: Path, target: Path, mutate) -> Path:
    lines = source.read_text(encoding="utf-8").splitlines()
    lines[len(lines) // 2] = mutate(lines[len(lines) // 2])
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


@pytest.mark.parametrize("make_connection", [_raw_connection, _native_connection], ids=["fallback", "native"])
@pytest.mark.parametrize(
    "mutate",
    [lambda line: line + "|extra", lambda line: "|".join(["not-a-number", *line.split("|")[1:]])],
    ids=["wrong-field-count", "non-numeric-integer"],
)
def test_corrupt_row_aborts_load_and_leaves_warehouse_and_staging_empty(
    tmp_path: Path, refresh_set: RefreshSet, make_connection, mutate
) -> None:
    connection = make_connection()
    _create_warehouse_stub(connection)
    table = SOURCE_TABLES_BY_NAME["s_purchase"]
    create_staging_tables(connection, [table])
    corrupt = _corrupt_copy(refresh_set.path_for("s_purchase"), tmp_path / "s_purchase_1.dat", mutate)

    with pytest.raises((RefreshDataError, duckdb.Error)):
        load_refresh_file(connection, table, corrupt, refresh_set.row_counts["s_purchase"])

    assert _warehouse_rows(connection) == [(1,), (2,), (3,)]
    assert connection.execute("SELECT COUNT(*) FROM s_purchase").fetchone()[0] == 0


@pytest.mark.parametrize("make_connection", [_raw_connection, _native_connection], ids=["fallback", "native"])
def test_create_drop_and_load_only_touch_staging_tables(refresh_set: RefreshSet, make_connection) -> None:
    inner = make_connection()
    _create_warehouse_stub(inner)
    connection = _RecordingConnection(inner)

    _stage_all(connection, refresh_set)
    create_staging_tables(connection, SOURCE_TABLES)
    drop_staging_tables(connection, SOURCE_TABLES)

    assert _warehouse_rows(inner) == [(1,), (2,), (3,)]
    assert connection.statements
    for statement in connection.statements:
        assert "TRUNCATE" not in statement.upper()
        assert "store_sales" not in statement
        targets = STATEMENT_TARGET.findall(statement)
        assert targets, statement[:80]
        assert all(target.startswith("s_") for target in targets), statement[:80]


def test_create_staging_tables_replaces_existing_staging_tables_only() -> None:
    connection = _raw_connection()
    _create_warehouse_stub(connection)
    table = SOURCE_TABLES_BY_NAME["s_delete"]
    create_staging_tables(connection, [table])
    connection.execute("INSERT INTO s_delete VALUES ('2000-01-01', '2000-01-02')")

    create_staging_tables(connection, [table])

    assert connection.execute("SELECT COUNT(*) FROM s_delete").fetchone()[0] == 0
    assert _warehouse_rows(connection) == [(1,), (2,), (3,)]


