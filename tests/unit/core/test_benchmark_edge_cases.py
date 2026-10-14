from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _StatefulConn:
    def __init__(self, counts: dict[str, int]):
        self.counts = dict(counts)
        self.lock_held = False
        self.executed: list[str] = []

    def execute(self, sql: str, params=None):
        sql_clean = " ".join(str(sql).split())
        self.executed.append(sql_clean)

        if "setup_lock" in sql_clean and sql_clean.startswith("CREATE TABLE IF NOT EXISTS"):
            return SimpleNamespace(fetchone=lambda: None, fetchall=list)

        if (
            "INSERT INTO transaction_primitives_setup_lock" in sql_clean
            or "INSERT INTO write_primitives_setup_lock" in sql_clean
        ):
            if self.lock_held:
                raise RuntimeError("unique constraint failed")
            self.lock_held = True
            return SimpleNamespace(fetchone=lambda: None, fetchall=list)

        if (
            "DELETE FROM transaction_primitives_setup_lock" in sql_clean
            or "DELETE FROM write_primitives_setup_lock" in sql_clean
        ):
            self.lock_held = False
            return SimpleNamespace(fetchone=lambda: None, fetchall=list)

        m = re.search(r"SELECT 1 FROM \"?([a-zA-Z0-9_]+)\"? LIMIT 0", sql_clean)
        if m:
            table = m.group(1)
            if table not in self.counts:
                raise RuntimeError("table does not exist")
            return SimpleNamespace(fetchone=lambda: (1,), fetchall=lambda: [(1,)])

        m = re.search(r"SELECT 1 FROM ([a-zA-Z0-9_]+) LIMIT 1", sql_clean)
        if m:
            table = m.group(1)
            if self.counts.get(table, 0) <= 0:
                raise RuntimeError("table does not exist")
            return SimpleNamespace(fetchone=lambda: (1,), fetchall=lambda: [(1,)])

        m = re.search(r"SELECT COUNT\(\*\) FROM \"?([a-zA-Z0-9_]+)\"?", sql_clean)
        if m:
            table = m.group(1)
            return SimpleNamespace(
                fetchone=lambda: (self.counts.get(table, 0),), fetchall=lambda: [(self.counts.get(table, 0),)]
            )

        m = re.search(r'DROP TABLE IF EXISTS "?([a-zA-Z0-9_]+)"?', sql_clean)
        if m:
            self.counts[m.group(1)] = 0
            return SimpleNamespace(fetchone=lambda: None, fetchall=list)

        m = re.search(r'INSERT INTO "?([a-zA-Z0-9_]+)"? SELECT .+? FROM "?([a-zA-Z0-9_]+)"?', sql_clean)
        if m:
            target, source = m.group(1), m.group(2)
            self.counts[target] = self.counts.get(source, 0)
            return SimpleNamespace(fetchone=lambda: None, fetchall=list)

        return SimpleNamespace(fetchone=lambda: None, fetchall=list)


def test_transaction_primitives_setup_walk(monkeypatch, tmp_path):
    import benchbox.core.transaction_primitives.benchmark as mod
    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

    monkeypatch.setattr(
        mod,
        "STAGING_TABLES",
        {
            "txn_orders": {"columns": []},
            "txn_lineitem": {"columns": []},
            "txn_customer": {"columns": []},
        },
    )

    b = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    conn = _StatefulConn(
        {
            "orders": 10,
            "lineitem": 20,
            "customer": 5,
            "txn_orders": 0,
            "txn_lineitem": 0,
            "txn_customer": 0,
        }
    )

    result = b.setup(conn, force=True)
    assert result["success"] is True
    assert result["table_row_counts"]["txn_orders"] == 10
    assert result["table_row_counts"]["txn_lineitem"] == 20
    assert result["table_row_counts"]["txn_customer"] == 5


def test_write_primitives_setup_walk(monkeypatch, tmp_path):
    import benchbox.core.write_primitives.benchmark as mod
    from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

    monkeypatch.setattr(
        mod,
        "STAGING_TABLES",
        {
            "update_ops_orders": {"columns": []},
            "delete_ops_lineitem": {"columns": []},
            "merge_ops_target": {"columns": []},
        },
    )

    b = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    conn = _StatefulConn(
        {
            "orders": 12,
            "lineitem": 22,
            "update_ops_orders": 0,
            "delete_ops_lineitem": 0,
            "merge_ops_target": 0,
        }
    )

    result = b.setup(conn, force=True)
    assert result["success"] is True
    assert result["table_row_counts"]["update_ops_orders"] == 12
    assert result["table_row_counts"]["delete_ops_lineitem"] == 22


def test_metadata_generator_full_feature_walk(monkeypatch):
    import benchbox.core.metadata_primitives.generator as gen_mod
    from benchbox.core.metadata_primitives.complexity import (
        ConstraintDensity,
        MetadataComplexityConfig,
        PermissionDensity,
        RoleHierarchyDepth,
        TypeComplexity,
    )
    from benchbox.core.metadata_primitives.generator import MetadataGenerator

    monkeypatch.setattr(gen_mod, "supports_views", lambda _dialect: True)
    monkeypatch.setattr(gen_mod, "supports_complex_types", lambda _dialect: True)
    monkeypatch.setattr(gen_mod, "supports_foreign_keys", lambda _dialect: True)
    monkeypatch.setattr(gen_mod, "supports_acl", lambda _dialect: True)
    monkeypatch.setattr(gen_mod, "supports_role_hierarchy", lambda _dialect: True)
    monkeypatch.setattr(gen_mod, "supports_column_grants", lambda _dialect: True)

    executed: list[str] = []
    conn = SimpleNamespace(
        execute=lambda sql: executed.append(str(sql)) or SimpleNamespace(fetchall=list, fetchone=lambda: None)
    )

    cfg = MetadataComplexityConfig(
        width_factor=12,
        catalog_size=4,
        view_depth=3,
        type_complexity=TypeComplexity.NESTED,
        constraint_density=ConstraintDensity.DENSE,
        acl_role_count=4,
        acl_permission_density=PermissionDensity.DENSE,
        acl_hierarchy_depth=RoleHierarchyDepth.MODERATE,
        acl_column_grants=True,
    )

    gen = MetadataGenerator()
    generated = gen.setup(conn, "duckdb", cfg)
    assert generated.total_objects > 0

    gen.teardown(conn, "duckdb", generated)
    monkeypatch.setattr(gen, "_find_objects_with_prefix", lambda *_args: ["benchbox_x1", "benchbox_x2"])
    dropped = gen.cleanup_all(conn, "duckdb", "benchbox_")
    assert dropped >= 2
    assert len(executed) > 0


def test_transaction_primitives_helper_paths(tmp_path):
    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)

    assert bench._quote_identifier("safe_name") == '"safe_name"'
    with pytest.raises(ValueError):
        bench._quote_identifier("bad-name")

    conn = _StatefulConn({"some_table": 1})
    assert bench._table_exists(conn, "some_table") is True
    assert bench._table_exists(conn, "missing_table") is False


def test_amplab_load_data_private_path(tmp_path):
    from benchbox.core.amplab.benchmark import AMPLabBenchmark

    bench = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)

    rankings = tmp_path / "rankings.csv"
    documents = tmp_path / "documents.csv"
    uservisits = tmp_path / "uservisits.csv"

    rankings.write_text("1|u|10|10\n", encoding="utf-8")
    documents.write_text("u|hello world\n", encoding="utf-8")
    uservisits.write_text("1.1.1.1|u|2000-01-01|1.2|ua|US|en|q|10\n", encoding="utf-8")

    bench.tables = {
        "rankings": rankings,
        "documents": documents,
        "uservisits": uservisits,
    }

    executed: list[str] = []
    conn = SimpleNamespace(
        execute=lambda sql, *args, **kwargs: executed.append(str(sql)) or SimpleNamespace(fetchall=list, rowcount=1),
        commit=lambda: None,
    )

    bench._load_data(conn)
    assert any("CREATE TABLE" in sql.upper() for sql in executed)


def test_transaction_primitives_auxiliary_and_metadata_paths(monkeypatch, tmp_path):
    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)

    bench.data_generator.generate = lambda: {"orders": tmp_path / "orders.tbl"}
    generated = bench.generate_data()
    assert len(generated) == 1

    calls: list[str] = []
    bench.data_generator.check_bulk_load_files_exist = lambda: False
    bench.data_generator._acquire_bulk_load_lock = lambda timeout=300: True
    bench.data_generator.generate_bulk_load_files = lambda: calls.append("generated") or [tmp_path / "bulk.tbl"]
    bench.data_generator._release_bulk_load_lock = lambda: calls.append("released")
    bench.ensure_auxiliary_data_files()
    assert calls == ["generated", "released"]

    bench.data_generator._acquire_bulk_load_lock = lambda timeout=300: False
    bench.ensure_auxiliary_data_files()

    bench.data_generator.check_bulk_load_files_exist = lambda: True
    bench.ensure_auxiliary_data_files()

    bench.operations_manager = SimpleNamespace(
        get_operation_count=lambda: 1,
        get_operation_categories=lambda: ["transaction"],
        get_all_operations=lambda: {"op1": SimpleNamespace(write_sql="SELECT 1")},
        get_operation=lambda _op: SimpleNamespace(write_sql="SELECT 1"),
        get_operations_by_category=lambda _cat: {"op1": SimpleNamespace(write_sql="SELECT 1")},
    )
    info = bench.get_benchmark_info()
    assert info["data_source"] == "tpch"
    assert bench.get_queries()["op1"] == "SELECT 1"
    assert bench.get_query("op1") == "SELECT 1"
    assert bench.get_queries_by_category("transaction")["op1"] == "SELECT 1"
    assert "CREATE TABLE" in bench.get_create_tables_sql()


def test_transaction_primitives_reset_and_setup_checks(tmp_path):
    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    conn = _StatefulConn(
        {
            "orders": 10,
            "lineitem": 20,
            "customer": 5,
            "txn_orders": 1,
            "txn_lineitem": 2,
            "txn_customer": 3,
        }
    )

    populated: list[tuple[str, str]] = []
    bench._populate_staging_table = lambda _c, t, s: populated.append((t, s))
    bench.reset(conn)
    assert ("txn_orders", "orders") in populated

    manifest = bench._STAGING_MANIFEST_TABLE
    assert not any(sql.startswith("INSERT INTO") and manifest in sql for sql in conn.executed)

    assert bench.is_setup(conn) is False
    assert any(manifest in sql for sql in conn.executed)

    conn_zero = _StatefulConn({"txn_orders": 0, "txn_lineitem": 1, "txn_customer": 1})
    assert bench.is_setup(conn_zero) is False
    assert not any(manifest in sql for sql in conn_zero.executed)

    bench.load_data(conn)
    bench.teardown(conn)


def test_transaction_primitives_execute_operation_and_dataframe_paths(monkeypatch, tmp_path):
    import benchbox.core.transaction_primitives.benchmark as mod
    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)

    class _ExecConn:
        def __init__(self):
            self.calls: list[str] = []

        def execute(self, sql: str):
            self.calls.append(sql)
            if "FAIL_WRITE" in sql:
                raise RuntimeError("write failed")
            if "VAL_RANGE" in sql:
                return SimpleNamespace(fetchall=lambda: [("x",), ("y",)], rowcount=2)
            if "VAL_EMPTY" in sql:
                return SimpleNamespace(fetchall=list, rowcount=0)
            if "BAD_CLEANUP" in sql:
                raise RuntimeError("cleanup failed")
            return SimpleNamespace(fetchall=lambda: [("ok",)], rowcount=1)

    val_exact = SimpleNamespace(
        id="v1",
        sql="VAL_EXACT",
        expected_rows=1,
        expected_rows_min=None,
        expected_rows_max=None,
    )
    val_range = SimpleNamespace(
        id="v2",
        sql="VAL_RANGE",
        expected_rows=None,
        expected_rows_min=1,
        expected_rows_max=3,
    )
    val_none = SimpleNamespace(
        id="v3",
        sql="VAL_EMPTY",
        expected_rows=None,
        expected_rows_min=None,
        expected_rows_max=None,
    )

    bench.is_setup = lambda _c: True
    bench.operations_manager = SimpleNamespace(
        get_operation=lambda _op: SimpleNamespace(
            requires_setup=False,
            write_sql="WRITE_SQL",
            validation_queries=[val_exact, val_range, val_none],
            cleanup_sql="BAD_CLEANUP",
        ),
        get_all_operations=lambda: {"op1": object()},
        get_operations_by_category=lambda _cat: {"op1": object()},
    )

    ok = bench.execute_operation("op1", _ExecConn())
    assert ok.success is True
    assert ok.cleanup_success is False
    assert ok.validation_passed is True

    bench.operations_manager = SimpleNamespace(
        get_operation=lambda _op: SimpleNamespace(
            requires_setup=False,
            write_sql="FAIL_WRITE",
            validation_queries=[],
            cleanup_sql=None,
        ),
    )
    failed = bench.execute_operation("op1", _ExecConn())
    assert failed.success is False
    assert "failed" in (failed.error or "").lower()

    bench.operations_manager = SimpleNamespace(
        get_operation=lambda op_id: SimpleNamespace(id=op_id),
        get_all_operations=lambda: {"a": object(), "b": object()},
        get_operations_by_category=lambda cat: {"c": object()} if cat == "transaction" else {},
    )
    bench.execute_operation = lambda op_id, _conn: SimpleNamespace(
        success=True, validation_passed=True, write_duration_ms=1.0, validation_duration_ms=1.0, operation_id=op_id
    )
    assert len(bench.run_benchmark(_ExecConn(), operation_ids=["a"])) == 1
    assert len(bench.run_benchmark(_ExecConn(), categories=["transaction"])) == 1
    assert len(bench.run_benchmark(_ExecConn())) == 2

    import benchbox.core.transaction_primitives.dataframe_operations as _df_ops_mod

    monkeypatch.setattr(
        _df_ops_mod,
        "validate_transaction_primitives_platform",
        lambda p: (False, "unsupported") if p == "duckdb" else (True, ""),
    )
    monkeypatch.setattr(
        _df_ops_mod,
        "get_dataframe_transaction_manager",
        lambda p, spark_session=None, execution_engine="default": None if p == "none" else {"platform": p},
    )
    assert bench.supports_dataframe_mode("delta-lake") is True
    assert bench.supports_dataframe_mode("duckdb") is False
    assert bench.validate_dataframe_configuration("duckdb")[0] is False
    assert bench.validate_dataframe_configuration("pyspark-df", spark_session=None)[0] is False
    assert bench.validate_dataframe_configuration("delta-lake")[0] is True
    assert bench.get_dataframe_operations("delta-lake") == {"platform": "delta-lake"}
    with pytest.raises(ValueError):
        bench.get_dataframe_operations("none")
