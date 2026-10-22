from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

SCALE_FACTOR = 0.01
EMPTY_UNDER_ZERO_DEFAULTS = (7, 12, 20, 26, 33, 34, 43, 56, 60, 66, 74, 75, 89, 98)


@pytest.fixture(scope="module")
def obt_connection(tmp_path_factory: pytest.TempPathFactory):
    duckdb = pytest.importorskip("duckdb")
    from benchbox.core.tpcds.generator.manager import TPCDSDataGenerator
    from benchbox.core.tpcds_obt.etl.transformer import TPCDSOBTTransformer

    root = tmp_path_factory.mktemp("tpcds_obt_draws")
    generator = TPCDSDataGenerator(
        scale_factor=SCALE_FACTOR, output_dir=root / "tpcds", compress_data=False, quiet=True
    )
    if not generator.dsdgen_available:
        pytest.skip("dsdgen is not available")
    generator.generate()
    outputs = TPCDSOBTTransformer().transform(root / "tpcds", root / "obt", scale_factor=SCALE_FACTOR)
    connection = duckdb.connect()
    connection.execute(
        f"CREATE VIEW tpcds_sales_returns_obt AS SELECT * FROM read_parquet('{Path(outputs['table']).as_posix()}')"
    )
    yield connection
    connection.close()


def test_queries_return_rows_with_dsqgen_parameters(obt_connection) -> None:
    from benchbox.core.tpcds_obt.queries import TPCDSOBTQueryManager

    manager = TPCDSOBTQueryManager()
    empty = []
    for query_id in EMPTY_UNDER_ZERO_DEFAULTS:
        sql = manager.get_query(query_id, scale_factor=SCALE_FACTOR).rstrip().rstrip(";")
        (rows,) = obt_connection.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()
        if rows == 0:
            empty.append(query_id)
    assert empty == [], f"queries returned no rows with dsqgen parameters: {empty}"
