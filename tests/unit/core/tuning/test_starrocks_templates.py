from __future__ import annotations

import re
from pathlib import Path

import pytest

import benchbox
from benchbox.cli.config import ConfigManager
from benchbox.platforms.starrocks import StarRocksAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLES = REPO_ROOT / "examples" / "tunings" / "starrocks"
PACKAGED = REPO_ROOT / "benchbox" / "core" / "tuning" / "templates" / "starrocks"
BENCHMARKS = {
    "ssb": benchbox.SSB,
    "tpch": benchbox.TPCH,
}
EXPECTED_LAYOUTS = {
    "ssb": {"lineorder": ("lo_orderkey", "lo_orderdate, lo_orderkey")},
    "tpch": {
        "lineitem": ("l_orderkey", "l_shipdate, l_orderkey"),
    },
}


def _rendered_tables(benchmark_id: str) -> dict[str, str]:
    config = ConfigManager().load_unified_tuning_config(PACKAGED / f"{benchmark_id}_tuned.yaml", platform="starrocks")
    schema = BENCHMARKS[benchmark_id](scale_factor=1).get_create_tables_sql(dialect="duckdb", tuning_config=config)
    adapter = StarRocksAdapter(host="localhost", database="benchbox")
    rendered = {}
    for statement in re.sub(r"--[^\n]*\n?", "", schema).split(";"):
        if statement.strip():
            sql = adapter._translate_schema_statement(statement.strip(), config.table_tunings).replace("`", "").lower()
            match = re.match(r"create table (?:if not exists )?(\w+)", sql)
            assert match is not None, sql
            rendered[match.group(1)] = re.sub(r"\s+", " ", sql)
    return rendered


@pytest.mark.parametrize("benchmark_id", sorted(BENCHMARKS))
def test_packaged_template_matches_the_example(benchmark_id: str) -> None:
    name = f"{benchmark_id}_tuned.yaml"
    assert (PACKAGED / name).read_text(encoding="utf-8") == (EXAMPLES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("benchmark_id", sorted(BENCHMARKS))
def test_template_renders_its_layout_on_every_tuned_table(benchmark_id: str) -> None:
    rendered = _rendered_tables(benchmark_id)
    for table, (distribution, sort) in EXPECTED_LAYOUTS[benchmark_id].items():
        assert re.search(rf"distributed by hash ?\({distribution}\) buckets 8", rendered[table]), rendered[table]
        assert re.search(rf"order by \({sort}\)", rendered[table]), rendered[table]
        assert "primary key" not in rendered[table]


@pytest.mark.parametrize("benchmark_id", sorted(BENCHMARKS))
def test_template_leaves_other_tables_unsorted(benchmark_id: str) -> None:
    for table, sql in _rendered_tables(benchmark_id).items():
        assert ("order by" in sql) is (table in EXPECTED_LAYOUTS[benchmark_id]), table
