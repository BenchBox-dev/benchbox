from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

SCALE_FACTOR = 0.01
BASE_PARTS_AS_VARIANTS = {14, 23, 24}
OBT_COVERS_FEWER_PARTS = {14: "the converted statement covers part 1 of the two-part query only"}
BASE_DOES_NOT_RUN = {90: "DuckDB rejects the alias `at` in the base statement"}


def _select_row_counts(connection, sql: str) -> list[int]:
    counts = []
    for statement in (part.strip() for part in sql.split(";")):
        if not statement:
            continue
        assert statement.upper().lstrip("( \n").startswith(("SELECT", "WITH")), (
            f"unexpected statement kind: {statement[:60]!r}"
        )
        counts.append(len(connection.execute(statement).fetchall()))
    return counts


def _base_row_counts(base, benchmark, query_id: int) -> list[int]:
    variants = ("a", "b") if query_id in BASE_PARTS_AS_VARIANTS else (None,)
    counts: list[int] = []
    for variant in variants:
        sql = benchmark.get_query(query_id, scale_factor=SCALE_FACTOR, variant=variant)
        counts.extend(_select_row_counts(base, sql))
    return counts


@pytest.fixture(scope="module")
def engines(tmp_path_factory: pytest.TempPathFactory):
    duckdb = pytest.importorskip("duckdb")
    from benchbox.core.tpcds.generator.manager import TPCDSDataGenerator
    from benchbox.core.tpcds_obt.etl.transformer import TPCDSOBTTransformer
    from benchbox.core.tpcds_obt.queries import TPCDSOBTQueryManager
    from benchbox.platforms.duckdb import DuckDBAdapter
    from benchbox.tpcds import TPCDS

    root = tmp_path_factory.mktemp("tpcds_obt_empty_results")
    generator = TPCDSDataGenerator(
        scale_factor=SCALE_FACTOR, output_dir=root / "tpcds", compress_data=False, quiet=True
    )
    if not generator.dsdgen_available:
        pytest.skip("dsdgen is not available")
    generator.generate()

    outputs = TPCDSOBTTransformer().transform(root / "tpcds", root / "obt", scale_factor=SCALE_FACTOR)
    obt = duckdb.connect()
    obt.execute(
        f"CREATE VIEW tpcds_sales_returns_obt AS SELECT * FROM read_parquet('{Path(outputs['table']).as_posix()}')"
    )

    benchmark = TPCDS(scale_factor=SCALE_FACTOR, output_dir=root / "tpcds")
    adapter = DuckDBAdapter(database_path=":memory:")
    base = adapter.create_connection()
    adapter.create_schema(benchmark, base)
    adapter.load_data(benchmark, base, root / "tpcds")

    yield obt, base, benchmark, TPCDSOBTQueryManager()
    obt.close()
    adapter.close_connection(base)


@pytest.mark.parametrize("query_id", CONVERTIBLE_QUERY_IDS)
def test_an_obt_statement_is_empty_only_where_the_base_statement_is(engines, query_id: int) -> None:
    duckdb = pytest.importorskip("duckdb")
    obt, base, benchmark, manager = engines
    obt_counts = _select_row_counts(obt, manager.get_query(query_id, scale_factor=SCALE_FACTOR))
    assert obt_counts, f"Q{query_id} produced no SELECT statement"

    if query_id in BASE_DOES_NOT_RUN:
        with pytest.raises(duckdb.Error):
            _base_row_counts(base, benchmark, query_id)
        assert all(count > 0 for count in obt_counts), (
            f"Q{query_id}: {BASE_DOES_NOT_RUN[query_id]}, so the OBT statement must return rows on its own"
        )
        return

    base_counts = _base_row_counts(base, benchmark, query_id)
    if query_id in OBT_COVERS_FEWER_PARTS:
        assert len(obt_counts) < len(base_counts), f"stale exemption for Q{query_id}: the part counts now agree"
    else:
        assert len(obt_counts) == len(base_counts), (
            f"Q{query_id}: the OBT statement has {len(obt_counts)} part(s), the base statement {len(base_counts)}"
        )

    for part, (obt_rows, base_rows) in enumerate(zip(obt_counts, base_counts, strict=False), start=1):
        assert not (obt_rows == 0 and base_rows > 0), (
            f"Q{query_id} part {part}: the OBT returns no rows where the base TPC-DS statement returns "
            f"{base_rows} on the same data and parameters"
        )
