from __future__ import annotations

import logging
import tempfile
from typing import Any

import pytest

from benchbox.core.benchmark_registry import get_benchmark_class, get_benchmark_default_scale, list_benchmark_ids
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms.doris import DorisAdapter
from benchbox.platforms.starrocks import StarRocksAdapter
from benchbox.utils.dialect_utils import is_create_table, split_sql_statements

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _schema_statements() -> list[tuple[str, str]]:
    constrained = UnifiedTuningConfiguration()
    constrained.enable_all_constraints()
    found: list[tuple[str, str]] = []
    for benchmark_id in list_benchmark_ids():
        cls = get_benchmark_class(benchmark_id)
        if cls is None:
            continue
        try:
            benchmark = cls(scale_factor=get_benchmark_default_scale(benchmark_id), output_dir=tempfile.mkdtemp())
        except TypeError:
            benchmark = cls(scale_factor=get_benchmark_default_scale(benchmark_id))
        if not hasattr(benchmark, "get_create_tables_sql"):
            continue
        for label, config in (("plain", None), ("constrained", constrained)):
            try:
                script = benchmark.get_create_tables_sql(tuning_config=config)
            except TypeError:
                script = benchmark.get_create_tables_sql()
            for index, statement in enumerate(split_sql_statements(script, "standard")):
                found.append((f"{benchmark_id}-{label}-{index}", statement))
    return found


SCHEMA_STATEMENTS = _schema_statements()


def _starrocks() -> Any:
    adapter = object.__new__(StarRocksAdapter)
    adapter.logger = logging.getLogger("test")
    adapter._applied_tuning_ledger = None
    return adapter


def _doris(partitioning: bool) -> Any:
    adapter = object.__new__(DorisAdapter)
    adapter.logger = logging.getLogger("test")
    adapter.table_model = "duplicate"
    adapter.default_buckets = 10
    adapter.enable_partitioning = partitioning
    adapter.replication_num = 1
    return adapter


def test_every_benchmark_contributes_schema_statements():
    assert len(SCHEMA_STATEMENTS) > 100
    assert {name.split("-")[0] for name, _ in SCHEMA_STATEMENTS} >= {"tpch", "tpcds", "tpcdi", "ssb", "clickbench"}


@pytest.mark.parametrize(("name", "statement"), SCHEMA_STATEMENTS, ids=[name for name, _ in SCHEMA_STATEMENTS])
def test_starrocks_translates_every_shipped_schema(name, statement):
    adapter = _starrocks()
    translated = adapter._translate_schema_statement(statement.strip())
    assert translated
    if is_create_table(statement):
        assert "DISTRIBUTED BY" in translated.upper()
        assert "FOREIGN KEY" not in translated.upper()


@pytest.mark.parametrize("partitioning", [False, True])
@pytest.mark.parametrize(("name", "statement"), SCHEMA_STATEMENTS, ids=[name for name, _ in SCHEMA_STATEMENTS])
def test_doris_translates_every_shipped_schema(name, statement, partitioning):
    adapter = _doris(partitioning)
    translated = adapter._transform_schema_statement(statement.strip(), None)
    assert translated
    if is_create_table(statement):
        assert "DUPLICATE KEY" in translated.upper()
        assert "DISTRIBUTED BY" in translated.upper()
        assert "PRIMARY KEY" not in translated.upper()
        assert "FOREIGN KEY" not in translated.upper()


def test_doris_requests_schema_in_standard_dialect():
    from unittest.mock import Mock

    adapter = _doris(False)
    adapter.get_effective_tuning_configuration = Mock(return_value=None)
    benchmark = Mock()
    benchmark.get_create_tables_sql.return_value = "CREATE TABLE t (a INT)"
    adapter._create_schema_with_tuning(benchmark)
    assert benchmark.get_create_tables_sql.call_args.kwargs["dialect"] == "standard"
