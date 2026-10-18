from pathlib import Path
from typing import Callable

import pytest

from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.platforms.sqlite import SQLiteAdapter

from .common import create_smoke_benchmark, run_smoke_benchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
@pytest.mark.parametrize(
    "adapter_factory",
    [
        pytest.param(lambda tmp: DuckDBAdapter(database_path=":memory:"), id="duckdb"),
        pytest.param(
            lambda tmp: SQLiteAdapter(database_path=str(Path(tmp) / "smoke.sqlite")),
            id="sqlite",
        ),
    ],
)
def test_local_adapters_smoke(adapter_factory: Callable[[Path], object], tmp_path: Path):
    benchmark = create_smoke_benchmark(tmp_path)
    adapter = adapter_factory(tmp_path)

    stats, metadata, _ = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert any(count > 0 for count in stats.values())
    assert metadata["platform_name"] in {"DuckDB", "SQLite"}
