from __future__ import annotations

import itertools
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox.core.throughput.result import ThroughputStreamResult
from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
from benchbox.core.tpcds.schema import TABLES
from benchbox.core.validation import ValidationResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def tpcds_benchmark(tmp_path, monkeypatch):
    class FakeQueryManager:
        def get_query(self, query_id, **kwargs):
            return f"SELECT {query_id}"

        def get_all_queries(self, **kwargs):
            return {1: "SELECT 1"}

    class FakeDataGenerator:
        def __init__(self, scale_factor=1.0, parallel=1, output_dir=None, **kwargs):
            self.scale_factor = scale_factor
            self.parallel = parallel
            self.output_dir = Path(output_dir) if output_dir else Path.cwd()

        def generate(self):
            return {"store_sales": self.output_dir / "store_sales.dat"}

    class FakeCTools:
        def get_tools_info(self):
            return {"available_tools": []}

    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.TPCDSQueryManager", FakeQueryManager)
    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.TPCDSDataGenerator", FakeDataGenerator)
    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.TPCDSCTools", FakeCTools)

    return TPCDSBenchmark(scale_factor=1.0, output_dir=tmp_path, verbose=False)


def test_get_query_validates_inputs(tpcds_benchmark):
    with pytest.raises(TypeError):
        tpcds_benchmark.get_query("1")
    with pytest.raises(ValueError):
        tpcds_benchmark.get_query(0)
    with pytest.raises(TypeError):
        tpcds_benchmark.get_query(1, seed="x")
    with pytest.raises(ValueError):
        tpcds_benchmark.get_query(1, scale_factor=0)


def test_get_query_variant_fallback_without_dsqgen(tpcds_benchmark):
    class NoDSQGen:
        def get_query(self, query_id, **kwargs):
            return f"SELECT {query_id}"

    tpcds_benchmark.query_manager = NoDSQGen()

    query = tpcds_benchmark.get_query(14, variant="a", dialect="duckdb", base_dialect="netezza")

    assert "SELECT" in query


def test_translate_query_text_converts_day_suffixes_to_intervals(tpcds_benchmark):
    raw = "select cast('2000-01-01' as date) + 60 days - 30 days"
    translated = tpcds_benchmark.translate_query_text(raw, "netezza", "duckdb")
    assert "+ INTERVAL '60' DAY" in translated
    assert "- INTERVAL '30' DAY" in translated


@pytest.mark.parametrize(
    "dialect,identifier,type_name",
    [
        ("spark", "`pmc`", "DECIMAL"),
        ("postgres", "pmc", "DECIMAL"),
        ("datafusion", "pmc", "DECIMAL"),
        ("duckdb", "pmc", "DECIMAL"),
        ("tsql", "[pmc]", "NUMERIC"),
    ],
)
def test_q90_guards_zero_denominator_in_every_dialect(tpcds_benchmark, dialect, identifier, type_name):
    query = f"SELECT amc / CAST({identifier} AS {type_name}(15, 4)) AS am_pm_ratio FROM counts"

    rewritten = tpcds_benchmark._apply_target_dialect_overrides(90, query, dialect)

    assert f"NULLIF(CAST({identifier} AS {type_name}(15, 4)), 0)" in rewritten
    assert tpcds_benchmark._apply_target_dialect_overrides(90, rewritten, dialect) == rewritten


@pytest.mark.parametrize("pmc,expected", [(0, None), (2, 0.5)])
def test_translated_tsql_q90_guards_zero_without_changing_nonzero_ratio(tpcds_benchmark, pmc, expected):
    import duckdb

    from benchbox._vendor import sqlglot

    query = 'SELECT CAST("amc" AS DECIMAL(15, 4))/CAST("pmc" AS DECIMAL(15, 4)) AS am_pm_ratio FROM counts'
    translated = tpcds_benchmark.translate_query_text(query, "duckdb", "tsql")
    assert "CAST([pmc] AS NUMERIC(15, 4))" in translated

    rewritten = tpcds_benchmark._apply_target_dialect_overrides(90, translated, "tsql")
    assert "NULLIF(CAST([pmc] AS NUMERIC(15, 4)), 0)" in rewritten
    executable = sqlglot.transpile(rewritten, read="tsql", write="duckdb")[0]
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE counts (amc INTEGER, pmc INTEGER)")
        connection.execute("INSERT INTO counts VALUES (1, ?)", [pmc])
        assert connection.execute(executable).fetchone()[0] == expected


def test_q90_guard_leaves_other_denominators_unchanged(tpcds_benchmark):
    query = "SELECT amc / CAST([other_pmc] AS NUMERIC(15, 4)) FROM counts"
    assert tpcds_benchmark._apply_target_dialect_overrides(90, query, "tsql") == query


@pytest.mark.parametrize("dialect", ["postgres", "cedardb"])
def test_postgres_rollup_order_alias_rewrite_repeats_grouping_expression(tpcds_benchmark, dialect):
    query = (
        "SELECT GROUPING(i_category) + GROUPING(i_class) AS lochierarchy "
        "FROM store_sales GROUP BY ROLLUP (i_category, i_class) "
        "ORDER BY lochierarchy DESC, CASE WHEN lochierarchy = 0 THEN i_category END, rank_within_parent"
    )

    rewritten = tpcds_benchmark._apply_target_dialect_overrides(36, query, dialect)

    assert "CASE WHEN lochierarchy = 0" not in rewritten
    assert "ORDER BY lochierarchy DESC" in rewritten
    assert "CASE WHEN (GROUPING(i_category) + GROUPING(i_class)) = 0 THEN i_category END" in rewritten


def test_rollup_order_alias_rewrite_only_applies_to_postgres_family(tpcds_benchmark):
    query = (
        "SELECT GROUPING(i_category) AS lochierarchy FROM store_sales GROUP BY ROLLUP (i_category) "
        "ORDER BY lochierarchy DESC, CASE WHEN lochierarchy = 0 THEN i_category END"
    )

    assert tpcds_benchmark._apply_target_dialect_overrides(36, query, "duckdb") == query


def test_translate_query_text_qualifies_query58_order_by(tpcds_benchmark):
    query = (
        "WITH ss_items AS (SELECT 1 AS item_id), cs_items AS (SELECT 1 AS item_id), "
        'ws_items AS (SELECT 1 AS item_id) SELECT * FROM ss_items ORDER BY "item_id"'
    )
    fixed = tpcds_benchmark.translate_query_text(query, "netezza", "duckdb")
    assert 'ORDER BY "ss_items"."item_id"' in fixed


def test_get_create_tables_sql_uses_tuning_flags(tpcds_benchmark, monkeypatch):
    captured = {}

    def fake_get_create_all_tables_sql(enable_primary_keys=False, enable_foreign_keys=False):
        captured["pk"] = enable_primary_keys
        captured["fk"] = enable_foreign_keys
        return "-- schema"

    monkeypatch.setattr("benchbox.core.tpcds.schema.get_create_all_tables_sql", fake_get_create_all_tables_sql)

    tuning = SimpleNamespace(
        primary_keys=SimpleNamespace(enabled=True),
        foreign_keys=SimpleNamespace(enabled=False),
    )

    sql = tpcds_benchmark.get_create_tables_sql(tuning_config=tuning)

    assert sql == "-- schema"
    assert captured == {"pk": True, "fk": False}


def test_get_all_streams_info_skips_invalid(tpcds_benchmark, monkeypatch):
    def fake_get_stream_info(stream_id):
        if stream_id == 1:
            raise ValueError("bad stream")
        return {"stream_id": stream_id}

    monkeypatch.setattr(tpcds_benchmark, "get_stream_info", fake_get_stream_info)

    all_info = tpcds_benchmark.get_all_streams_info(num_streams=3)

    assert [item["stream_id"] for item in all_info] == [0, 2]


def test_validate_data_integrity_combines_results(tpcds_benchmark, monkeypatch):
    file_result = ValidationResult(is_valid=True, warnings=["w1"], details={"files": "ok"})
    db_result = ValidationResult(is_valid=False, errors=["db-fail"], details={"db": "bad"})

    monkeypatch.setattr(tpcds_benchmark, "validate_generated_data", lambda: file_result)
    monkeypatch.setattr(tpcds_benchmark, "validate_loaded_data", lambda _conn: db_result)

    combined = tpcds_benchmark.validate_data_integrity(connection=object())

    assert combined.is_valid is False
    assert combined.errors == ["db-fail"]
    assert combined.warnings == ["w1"]
    assert combined.details["file_validation"] == {"files": "ok"}
    assert combined.details["database_validation"] == {"db": "bad"}


def test_get_benchmark_info_includes_tools(tpcds_benchmark):
    info = tpcds_benchmark.get_benchmark_info()
    assert info["name"] == "TPC-DS"
    assert info["maintenance_test_supported"] is True
    assert "available_tools" in info["c_tools_info"]


class _Conn:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.last_query = ""
        self.closed = False

    def execute(self, query, *args, **kwargs):
        self.last_query = str(query)
        if self.fail_on and self.fail_on in self.last_query:
            raise RuntimeError("forced failure")
        return self

    def fetchall(self):
        return [(1,)]

    def fetchone(self):
        return (0,)

    def close(self):
        self.closed = True


def _routed_driver_result(**overrides):
    streams = [
        ThroughputStreamResult(
            stream_id=stream_id,
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            queries_executed=99,
            queries_successful=99,
            queries_failed=0,
        )
        for stream_id in range(2)
    ]
    values = {
        "total_time": 2.5,
        "streams_executed": 2,
        "streams_successful": 2,
        "stream_results": streams,
        "throughput_at_size": 50.0,
        "success": True,
        "errors": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _adapter(result=None):
    adapter = Mock()
    adapter._run_routed_throughput.return_value = result or _routed_driver_result()
    return adapter


def test_run_throughput_test_routes_through_the_adapter_with_deprecation(tpcds_benchmark):
    adapter = _adapter()
    connection = object()
    factory = Mock()

    with pytest.warns(DeprecationWarning, match="TPCDSBenchmark.run_throughput_test is deprecated"):
        result = tpcds_benchmark.run_throughput_test(
            factory, num_streams=3, base_seed=9, stream_timeout=120, adapter=adapter, connection=connection
        )

    adapter._run_routed_throughput.assert_called_once()
    args = adapter._run_routed_throughput.call_args.args
    assert args[0] is tpcds_benchmark
    assert args[1] is connection
    assert args[2]["num_streams"] == 3
    assert args[2]["seed"] == 9
    assert args[2]["stream_timeout_seconds"] == 120
    assert "validation_mode" not in args[2]
    factory.assert_not_called()
    assert result.success is True
    assert result.streams_executed == 2
    assert result.streams_successful == 2
    assert result.throughput_at_size == 50.0
    assert result.total_duration == 2.5
    assert result.stream_results[0]["queries_executed"] == 99
    assert result.error is None


def test_run_throughput_test_forwards_disabled_validation(tpcds_benchmark):
    adapter = _adapter()

    with pytest.warns(DeprecationWarning):
        tpcds_benchmark.run_throughput_test(
            Mock(), num_streams=2, enable_validation=False, adapter=adapter, connection=object()
        )

    assert adapter._run_routed_throughput.call_args.args[2]["validation_mode"] == "disabled"


def test_run_throughput_test_warns_about_options_that_no_longer_apply(tpcds_benchmark):
    with pytest.warns(DeprecationWarning), pytest.warns(UserWarning, match="query_timeout, max_retries, output_dir"):
        tpcds_benchmark.run_throughput_test(
            Mock(),
            num_streams=2,
            query_timeout=10,
            max_retries=0,
            output_dir="out",
            adapter=_adapter(),
            connection=object(),
        )


def test_run_throughput_test_withholds_metric_when_a_stream_fails(tpcds_benchmark):
    failed = _routed_driver_result(
        success=False, throughput_at_size=None, streams_successful=1, errors=["Stream 1 failed: boom"]
    )

    with pytest.warns(DeprecationWarning):
        result = tpcds_benchmark.run_throughput_test(
            Mock(), num_streams=2, adapter=_adapter(failed), connection=object()
        )

    assert result.success is False
    assert result.throughput_at_size is None
    assert result.error == "Stream 1 failed: boom"


def test_run_throughput_test_refuses_fewer_than_two_streams(tpcds_benchmark):
    adapter = _adapter()

    with pytest.raises(ValueError, match="at least 2"):
        tpcds_benchmark.run_throughput_test(Mock(), num_streams=1, adapter=adapter, connection=object())

    adapter._run_routed_throughput.assert_not_called()


def test_run_throughput_test_without_an_adapter_fails_closed(tpcds_benchmark):
    factory = Mock()

    with (
        pytest.warns(DeprecationWarning),
        pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"),
    ):
        tpcds_benchmark.run_throughput_test(factory, num_streams=2)

    factory.assert_not_called()


def test_run_throughput_test_with_adapter_requires_a_connection(tpcds_benchmark):
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="connection is required"):
        tpcds_benchmark.run_throughput_test(Mock(), num_streams=2, adapter=_adapter())


def test_adapter_gate_refusal_propagates_without_a_metric(tpcds_benchmark):
    adapter = Mock()
    adapter._run_routed_throughput.side_effect = RuntimeError("stream_connection_capability=UNSUPPORTED")

    with pytest.warns(DeprecationWarning), pytest.raises(RuntimeError, match="UNSUPPORTED"):
        tpcds_benchmark.run_throughput_test(Mock(), num_streams=2, adapter=adapter, connection=object())


def test_run_power_test_withholds_power_at_size_when_any_query_fails(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(tpcds_benchmark, "get_query", lambda qid, **kwargs: f"SELECT {qid}")
    ticks = itertools.count()
    monkeypatch.setattr(
        "benchbox.core.tpcds.benchmark.runner.time",
        SimpleNamespace(time=lambda: next(ticks) * 0.001, sleep=time.sleep),
    )

    failed = tpcds_benchmark.run_power_test(connection=_Conn(fail_on="SELECT 42"), warm_up=False)
    passed = tpcds_benchmark.run_power_test(connection=_Conn(), warm_up=False)

    assert failed["success"] is False
    assert failed["power_at_size"] == 0.0
    assert passed["success"] is True
    assert passed["power_at_size"] > 0.0


def test_official_run_hands_its_dialect_to_the_throughput_phase(tpcds_benchmark, monkeypatch):
    routed = Mock(return_value=SimpleNamespace(throughput_at_size=9.0, success=True))
    monkeypatch.setattr(tpcds_benchmark, "run_power_test", lambda **kwargs: {"total_time": 1.0, "power_at_size": 1.0})
    monkeypatch.setattr(tpcds_benchmark, "run_throughput_test", routed)

    adapter = object()
    connection = object()
    tpcds_benchmark.run_official_benchmark(connection, maintenance_test=False, dialect="duckdb", adapter=adapter)

    assert routed.call_args.kwargs["dialect"] == "duckdb"
    assert routed.call_args.kwargs["adapter"] is adapter
    assert routed.call_args.kwargs["connection"] is connection


def test_official_run_without_an_adapter_fails_closed_before_any_phase(tpcds_benchmark, monkeypatch):
    power = Mock()
    monkeypatch.setattr(tpcds_benchmark, "run_power_test", power)

    with pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"):
        tpcds_benchmark.run_official_benchmark(object(), num_streams=2)

    power.assert_not_called()


def test_official_run_withholds_phase_metrics_for_failed_phases(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(
        tpcds_benchmark, "run_power_test", lambda **kwargs: {"total_time": 1.0, "power_at_size": 16.0, "success": False}
    )
    monkeypatch.setattr(
        tpcds_benchmark,
        "run_throughput_test",
        lambda **kwargs: SimpleNamespace(throughput_at_size=None, success=False),
    )

    result = tpcds_benchmark.run_official_benchmark(object(), maintenance_test=False, adapter=object())

    assert result["success"] is False
    assert result["power_at_size"] == 0.0
    assert result["throughput_at_size"] == 0.0
    assert "qphds_at_size" not in result
    assert any("Power@Size withheld" in error for error in result["errors"])
    assert any("Throughput@Size withheld" in error for error in result["errors"])


def test_run_throughput_test_carries_outstanding_stream_ids(tpcds_benchmark):
    leaked = _routed_driver_result(success=False, throughput_at_size=None, outstanding_stream_ids=[1])

    with pytest.warns(DeprecationWarning):
        result = tpcds_benchmark.run_throughput_test(
            Mock(), num_streams=2, adapter=_adapter(leaked), connection=object()
        )

    assert result.outstanding_stream_ids == [1]


def test_official_run_refuses_maintenance_while_streams_are_outstanding(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(tpcds_benchmark, "run_power_test", lambda **kwargs: {"total_time": 1.0, "power_at_size": 16.0})
    monkeypatch.setattr(
        tpcds_benchmark,
        "run_throughput_test",
        lambda **kwargs: SimpleNamespace(throughput_at_size=None, success=False, outstanding_stream_ids=[1]),
    )
    maintenance = Mock()
    monkeypatch.setattr(tpcds_benchmark, "run_maintenance_test", maintenance)

    result = tpcds_benchmark.run_official_benchmark(object(), adapter=object())

    maintenance.assert_not_called()
    assert result["success"] is False
    assert result["maintenance_test_result"]["status"] == "contained"
    assert result["maintenance_test_result"]["outstanding_stream_ids"] == [1]
    assert any("Maintenance Test refused" in error for error in result["errors"])


def test_official_run_still_runs_maintenance_when_boundary_is_clear(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(tpcds_benchmark, "run_power_test", lambda **kwargs: {"total_time": 1.0, "power_at_size": 16.0})
    monkeypatch.setattr(
        tpcds_benchmark,
        "run_throughput_test",
        lambda **kwargs: SimpleNamespace(throughput_at_size=9.0, success=True),
    )
    maintenance = Mock(return_value=SimpleNamespace(successful_operations=1, total_operations=1))
    monkeypatch.setattr(tpcds_benchmark, "run_maintenance_test", maintenance)

    result = tpcds_benchmark.run_official_benchmark(object(), adapter=object())

    maintenance.assert_called_once()
    assert result["success"] is True


def test_run_power_test_collects_query_error(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(tpcds_benchmark, "get_query", lambda qid, **kwargs: f"SELECT {qid}")

    conn = _Conn(fail_on="SELECT 42")
    result = tpcds_benchmark.run_power_test(connection=conn, warm_up=False, verbose=False)

    assert result["total_time"] >= 0
    assert len(result["query_results"]) == 99
    assert result["errors"]
    assert result["query_results"][42]["status"] == "failed"


def test_run_maintenance_test_maps_operations(tpcds_benchmark, monkeypatch):
    class FakeMaintenanceTest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self, config):
            op = SimpleNamespace(
                operation_type="insert",
                table_name="store_sales",
                start_time=1.0,
                end_time=2.0,
                duration=1.0,
                rows_affected=10,
                success=True,
                error=None,
            )
            return {
                "total_time": 1.0,
                "total_operations": 1,
                "successful_operations": 1,
                "failed_operations": 0,
                "overall_throughput": 1.0,
                "operations": [op],
                "errors": [],
            }

    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", FakeMaintenanceTest)

    result = tpcds_benchmark.run_maintenance_test(connection=_Conn())
    assert result.total_operations == 1
    assert result.successful_operations == 1
    assert result.maintenance_operations[0]["operation"] == "insert_store_sales"


def test_validate_maintenance_data_integrity(tpcds_benchmark):
    conn = _Conn()
    result = tpcds_benchmark.validate_maintenance_data_integrity(connection=conn)
    assert len(result["validation_checks"]) == 3
    assert result["integrity_score"] == 1.0
    assert conn.closed is True


def test_run_official_benchmark_aggregates(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(
        tpcds_benchmark,
        "run_power_test",
        lambda **kwargs: {"total_time": 1.0, "power_at_size": 16.0},
    )
    monkeypatch.setattr(
        tpcds_benchmark,
        "run_throughput_test",
        lambda **kwargs: SimpleNamespace(throughput_at_size=9.0),
    )
    monkeypatch.setattr(
        tpcds_benchmark,
        "run_maintenance_test",
        lambda **kwargs: SimpleNamespace(successful_operations=1, total_operations=1),
    )

    result = tpcds_benchmark.run_official_benchmark(connection=_Conn(), num_streams=1, adapter=object())
    assert result["success"] is True
    assert result["power_at_size"] == 16.0
    assert result["throughput_at_size"] == 9.0
    assert "qphds_at_size" not in result


def test_load_table_data_trims_padding_and_null_conversion(tpcds_benchmark, tmp_path):
    table_schema = next(table for table in TABLES if len(table.columns) >= 3)
    table_name = table_schema.name.lower()
    num_columns = len(table_schema.columns)
    data_file = tmp_path / f"{table_name}.dat"

    first_row = [f"v{i}" for i in range(num_columns)] + ["ignored"]
    second_row = ["", "x"]
    data_file.write_text("|".join(first_row) + "\n" + "|".join(second_row) + "\n")

    connection = Mock()

    loaded = tpcds_benchmark._load_table_data(connection, table_name, data_file)

    assert loaded == 2
    assert connection.execute.call_count == 2
    first_call = connection.execute.call_args_list[0]
    second_call = connection.execute.call_args_list[1]
    assert first_call.args[0].startswith(f"INSERT INTO {table_schema.name.upper()} VALUES")
    assert len(first_call.args[1]) == num_columns
    assert len(second_call.args[1]) == num_columns
    assert second_call.args[1][0] is None
    assert second_call.args[1][2:] == [None] * (num_columns - 2)


def test_load_table_data_raises_for_unknown_table(tpcds_benchmark, tmp_path):
    data_file = tmp_path / "unknown.dat"
    data_file.write_text("1|2|3\n")

    with pytest.raises(ValueError, match="Unknown table"):
        tpcds_benchmark._load_table_data(Mock(), "not_a_table", data_file)


def test_load_data_requires_generated_tables(tpcds_benchmark):
    tpcds_benchmark.tables = {}

    with pytest.raises(ValueError, match="No data has been generated"):
        tpcds_benchmark._load_data(Mock())


def test_load_data_executes_split_schema_and_skips_missing_or_empty_files(tpcds_benchmark, tmp_path, monkeypatch):
    valid_table = "call_center"
    empty_table = "catalog_page"
    missing_table = "customer"
    valid_file = tmp_path / f"{valid_table}.dat"
    empty_file = tmp_path / f"{empty_table}.dat"
    missing_file = tmp_path / f"{missing_table}.dat"
    valid_file.write_text("1|2|3\n")
    empty_file.write_text("")

    tpcds_benchmark.tables = {
        valid_table: valid_file,
        empty_table: empty_file,
        missing_table: missing_file,
    }

    connection = Mock()
    monkeypatch.setattr(tpcds_benchmark, "get_create_tables_sql", lambda: "CREATE TABLE a; CREATE TABLE b;")
    monkeypatch.setattr(tpcds_benchmark, "_load_table_data", Mock(return_value=7))

    tpcds_benchmark._load_data(connection)

    assert connection.execute.call_args_list[0].args[0] == "CREATE TABLE a"
    assert connection.execute.call_args_list[1].args[0] == "CREATE TABLE b"
    tpcds_benchmark._load_table_data.assert_called_once_with(connection, valid_table, valid_file)
    assert connection.commit.call_count == 2


def test_run_power_phase_records_errors(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(tpcds_benchmark, "run_power_test", Mock(side_effect=RuntimeError("power boom")))
    result = {"errors": [], "success": True, "power_at_size": 0.0}

    tpcds_benchmark._run_power_phase(connection=object(), dialect="duckdb", logger=Mock(), result=result)

    assert result["success"] is False
    assert result["errors"] == ["Power Test failed: power boom"]


def test_run_throughput_phase_records_errors(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(tpcds_benchmark, "run_throughput_test", Mock(side_effect=RuntimeError("throughput boom")))
    result = {"errors": [], "success": True, "throughput_at_size": 0.0}

    tpcds_benchmark._run_throughput_phase(num_streams=2, logger=Mock(), result=result)

    assert result["success"] is False
    assert result["errors"] == ["Throughput Test failed: throughput boom"]


def test_run_maintenance_phase_records_errors(tpcds_benchmark, monkeypatch):
    monkeypatch.setattr(tpcds_benchmark, "run_maintenance_test", Mock(side_effect=RuntimeError("maintenance boom")))
    result = {"errors": [], "success": True}

    tpcds_benchmark._run_maintenance_phase(connection=object(), dialect="duckdb", logger=Mock(), result=result)

    assert result["success"] is False
    assert result["errors"] == ["Maintenance Test failed: maintenance boom"]


def test_finalize_benchmark_result_populates_timing_fields(tpcds_benchmark):
    result = {
        "total_time": 0.0,
        "end_time": None,
        "power_at_size": 1.0,
        "throughput_at_size": 2.0,
        "success": True,
        "errors": [],
    }

    tpcds_benchmark._finalize_benchmark_result(result, benchmark_start_time=0.0, logger=Mock())

    assert result["total_time"] >= 0
    assert isinstance(result["end_time"], str)


def test_get_available_tables_returns_list(tpcds_benchmark):
    tables = tpcds_benchmark.get_available_tables()
    assert isinstance(tables, list)
    assert len(tables) > 0
    assert "store_sales" in tables


def test_get_available_queries_returns_1_to_99(tpcds_benchmark):
    queries = tpcds_benchmark.get_available_queries()
    assert queries == list(range(1, 100))


def test_get_table_loading_order_respects_fk_dependencies(tpcds_benchmark):
    available = ["store_sales", "date_dim", "store", "item"]
    order = tpcds_benchmark.get_table_loading_order(available)

    assert sorted(order) == sorted(available)

    assert order.index("date_dim") < order.index("store_sales")


def test_get_table_loading_order_appends_unknown_tables(tpcds_benchmark):
    available = ["store_sales", "my_custom_table"]
    order = tpcds_benchmark.get_table_loading_order(available)
    assert "my_custom_table" in order
    assert "store_sales" in order


def test_get_schema_returns_all_tpcds_tables(tpcds_benchmark):
    schema = tpcds_benchmark.get_schema()
    assert isinstance(schema, dict)
    assert len(schema) > 0

    first_table = next(iter(schema.values()))
    assert "name" in first_table
    assert "columns" in first_table
    col0 = first_table["columns"][0]
    assert "name" in col0
    assert "type" in col0
    assert "nullable" in col0


def test_generate_data_populates_tables(tpcds_benchmark, tmp_path):
    files = tpcds_benchmark.generate_data()
    assert isinstance(files, list)
    assert len(files) > 0

    assert "store_sales" in tpcds_benchmark.tables


def test_aggregate_stream_results_sums_queries():
    import time

    from benchbox.core.tpcds.benchmark.runner import _aggregate_stream_results

    start = time.time() - 1.0
    streams = [
        {"queries_executed": 10, "queries_successful": 8, "queries_failed": 2, "success": True, "error": None},
        {"queries_executed": 5, "queries_successful": 5, "queries_failed": 0, "success": True, "error": None},
    ]
    result = _aggregate_stream_results(streams, start)

    assert result["num_streams"] == 2
    assert result["streams_executed"] == 2
    assert result["streams_successful"] == 2
    assert result["total_queries_executed"] == 15
    assert result["total_queries_successful"] == 13
    assert result["total_queries_failed"] == 2
    assert result["success"] is True
    assert result["errors"] == []
    assert result["total_duration"] > 0


def test_aggregate_stream_results_with_failures():
    import time

    from benchbox.core.tpcds.benchmark.runner import _aggregate_stream_results

    start = time.time()
    streams = [
        {"queries_executed": 3, "queries_successful": 2, "queries_failed": 1, "success": False, "error": "timeout"},
    ]
    result = _aggregate_stream_results(streams, start)

    assert result["success"] is False
    assert result["errors"] == ["timeout"]


def test_translate_query_text_returns_string(tpcds_benchmark):
    query = "SELECT * FROM store_sales WHERE ss_sold_date_sk = 1"
    translated = tpcds_benchmark.translate_query_text(query, "netezza", "duckdb")
    assert isinstance(translated, str)
    assert len(translated) > 0


def test_get_queries_returns_dict_with_string_keys(tpcds_benchmark):
    queries = tpcds_benchmark.get_queries()
    assert isinstance(queries, dict)
    assert len(queries) > 0

    assert all(isinstance(k, str) for k in queries)

    assert all(isinstance(v, str) and len(v) > 0 for v in queries.values())
