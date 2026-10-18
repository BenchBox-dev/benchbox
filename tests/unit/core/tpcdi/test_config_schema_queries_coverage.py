from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.tpcdi.config import (
    TPCDIConfig,
    get_fast_config,
    get_safe_config,
    get_simple_config,
)
from benchbox.core.tpcdi.queries import TPCDIQueryManager
from benchbox.core.tpcdi.schema import TPCDISchemaManager, get_all_create_table_sql, get_create_table_sql

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Conn:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, sql: str):
        self.calls.append(sql)


class _QueryConn:
    def __init__(self):
        self.calls: list[str] = []

    def query(self, sql: str):
        self.calls.append(sql)


def test_config_post_init_and_profiles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("benchbox.core.tpcdi.config.multiprocessing.cpu_count", lambda: 4)
    monkeypatch.setattr(
        "benchbox.core.tpcdi.config.get_benchmark_runs_datagen_path",
        lambda _name, _sf: tmp_path / "auto",
        raising=False,
    )

    cfg = TPCDIConfig(scale_factor=-1, enable_parallel=True, max_workers=99, chunk_size=50, log_level="INVALID")
    assert cfg.scale_factor == 1.0
    assert cfg.max_workers == 16
    assert cfg.chunk_size == 1000
    assert cfg.output_dir is not None
    assert cfg.get_performance_profile() == "maximum"

    cfg.adjust_for_scale_factor()
    mem = cfg.get_memory_settings()
    as_dict = cfg.to_dict()
    recreated = TPCDIConfig.from_dict(as_dict | {"unknown": 1})
    dev = TPCDIConfig.for_development()
    prod = TPCDIConfig.for_production(scale_factor=2.0)

    assert mem["enable_chunked_processing"] is True
    assert recreated.scale_factor == cfg.scale_factor
    assert dev.get_performance_profile() == "single_threaded"
    assert prod.enable_parallel is False
    prod.create_directories()
    assert (prod.output_dir / "logs").exists()

    perf = TPCDIConfig.for_performance_testing(1.0)
    assert perf.enable_validation is False
    assert get_simple_config(1.0, parallel=True, validation=False).enable_parallel is True
    assert get_fast_config(1.0).enable_validation is False
    assert get_safe_config(1.0).strict_validation is True


def test_schema_helpers_and_manager_paths(monkeypatch: pytest.MonkeyPatch):
    ddl = get_create_table_sql("DimCustomer")
    all_ddl = get_all_create_table_sql()
    assert "CREATE TABLE IF NOT EXISTS DimCustomer" in ddl
    assert "FactTrade" in all_ddl
    assert "TimeValue STRING" in get_create_table_sql("DimTime", dialect="lakesail")
    assert "TimeValue TIME" in get_create_table_sql("DimTime", dialect="duckdb")

    with pytest.raises(ValueError, match="Unknown table"):
        get_create_table_sql("Nope")

    manager = TPCDISchemaManager(include_extensions=False)
    conn = _Conn()
    qconn = _QueryConn()

    monkeypatch.setattr("benchbox._vendor.sqlglot.transpile", lambda sql, **_kwargs: [sql])
    manager.create_schema(conn, dialect="duckdb")
    translated = manager.translate_schema("standard", "duckdb")
    table_schema = manager.get_table_schema("DimCustomer")
    col_names = manager.get_column_names("DimCustomer")
    pk = manager.get_primary_key("DimCustomer")
    manager.create_foreign_key_constraints(conn, dialect="duckdb")
    manager.drop_schema(qconn, if_exists=True)

    assert len(translated) == manager.get_table_count()
    assert table_schema["name"] == "DimCustomer"
    assert "CustomerID" in col_names
    assert pk == "SK_CustomerID"
    assert manager.get_core_tables()[0] == "DimDate"
    assert manager.get_extended_tables() == []
    assert any(call.startswith("DROP TABLE IF EXISTS") for call in qconn.calls)

    with pytest.raises(ValueError, match="Unknown table"):
        manager.get_table_schema("Nope")


def test_query_manager_core_accessors_and_order(monkeypatch: pytest.MonkeyPatch):
    manager = TPCDIQueryManager()

    q1 = manager.get_query("V1")
    a2 = manager.get_query("A2", {"min_trades": 1, "limit_rows": 3})
    deps = manager.get_query_dependencies("A1")
    meta = manager.get_query_metadata("A1")
    by_type = manager.get_queries_by_type("validation")
    all_queries = manager.get_all_queries()
    stats = manager.get_query_statistics()
    plan = manager.get_execution_plan()
    cat = manager.get_queries_by_category("referential_integrity")

    assert "DimCustomer" in q1
    assert "LIMIT 3" in a2
    assert "V1" in deps
    assert meta["query_type"] == "analytical"
    assert len(by_type) > 0
    assert "V1" in all_queries
    assert stats["total_queries"] >= 30
    assert len(plan) == len(all_queries)
    assert isinstance(cat, list)

    translated = manager.translate_query_text("SELECT 1", "duckdb")
    assert isinstance(translated, str)

    with pytest.raises(ValueError, match="Invalid query ID"):
        manager.get_query("BAD")
    with pytest.raises(ValueError, match="Invalid query type"):
        manager.get_queries_by_type("bad")

    manager._all_metadata = {"Q1": {"relies_on": ["Q2"]}, "Q2": {"relies_on": ["Q1"]}}
    manager._all_queries = {"Q1": "select 1", "Q2": "select 2"}
    with pytest.raises(ValueError, match="Circular dependency"):
        manager.resolve_query_order(["Q1", "Q2"])


def test_lakesail_tpcdi_skip_list_matches_remediation_evidence(tmp_path: Path):
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
    from benchbox.sql_compat.rules.execution_filter.lakesail_tpcdi import LAKESAIL_TPCDI_SKIPS

    benchmark = TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path)

    assert set(benchmark.get_platform_skip_queries("lakesail")) == set(LAKESAIL_TPCDI_SKIPS)
    assert benchmark.get_platform_skip_queries("duckdb") == []
