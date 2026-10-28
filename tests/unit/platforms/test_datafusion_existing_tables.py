from __future__ import annotations

import pytest

from benchbox.platforms.datafusion import DataFusionAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_existing_tables_exclude_information_schema(tmp_path):
    datafusion = pytest.importorskip("datafusion")
    ctx = datafusion.SessionContext(datafusion.SessionConfig().with_information_schema(True))
    adapter = DataFusionAdapter(working_dir=str(tmp_path / "wd"), data_format="parquet")

    assert adapter._get_existing_tables(ctx) == []

    ctx.sql("CREATE TABLE Hits (a INT)").collect()

    assert adapter._get_existing_tables(ctx) == ["hits"]
