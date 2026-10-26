from __future__ import annotations

import re
from pathlib import Path

from scripts.sql_execution_gate.engines import Dataset

OBT_BENCHMARK = "tpcds_obt"
OBT_AUGMENTED = "tpcds_obt-augmented"


def build_obt_dataset(name: str, source: Dataset, scale: float, scratch: Path) -> Dataset:
    import duckdb

    from benchbox.core.tpcds_obt.etl.transformer import TPCDSOBTTransformer
    from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME, get_obt_table

    source_dir = scratch / f"obt-source-{name}"
    source_dir.mkdir()
    for table, path in source.tables.items():
        (source_dir / f"{table}.dat").symlink_to(path.resolve())
    outputs = TPCDSOBTTransformer().transform(source_dir, scratch / f"obt-{name}", scale_factor=scale)
    flat = scratch / f"obt-{name}" / f"{OBT_TABLE_NAME}.dat"
    connection = duckdb.connect()
    try:
        connection.execute(
            f"COPY (SELECT * FROM read_parquet('{Path(outputs['table']).as_posix()}')) TO '{flat.as_posix()}' "
            "(FORMAT csv, DELIMITER '|', HEADER false, QUOTE '', ESCAPE '', NULLSTR '')"
        )
    finally:
        connection.close()
    return Dataset(
        name, re.sub(r",\n\s*PRIMARY KEY \([^)]*\)", "", get_obt_table().get_create_table_sql()), {OBT_TABLE_NAME: flat}
    )
