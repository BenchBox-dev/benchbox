from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pl = pytest.importorskip("polars")

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.tpch import dataframe_throughput, throughput_test as throughput_module
from benchbox.core.tpch.dataframe_refresh import DataFrameRefresher, execute_refresh_function
from benchbox.core.tpch.dataframe_throughput import DataFrameTPCHThroughputTest
from benchbox.core.tpch.refresh import TPCHRefreshUpdateSet
from benchbox.core.tpch.schema import LINEITEM, ORDERS
from benchbox.core.tpch.streams import TPCHStreams
from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter
from benchbox.platforms.dataframe.tpch_full_spec import PowerRefresh, TpchRefreshPlan

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]

LINEITEMS_PER_ORDER = 3
BASE_ORDERS = 20


def _dtype(column: Any) -> Any:
    name = column.data_type.name
    if name == "INTEGER":
        return pl.Int64
    if name == "DECIMAL":
        return pl.Float64
    if name == "DATE":
        return pl.Date
    return pl.String


def _rows(table: Any, keys: list[int], per_key: int) -> pl.DataFrame:
    data: dict[str, list[Any]] = {}
    key_name = "o_orderkey" if table.name.lower() == "orders" else "l_orderkey"
    for column in table.columns:
        name = column.name.lower()
        dtype = _dtype(column)
        if name == key_name:
            values: list[Any] = [key for key in keys for _ in range(per_key)]
        elif dtype == pl.Date:
            from datetime import date

            values = [date(1995, 1, 1)] * (len(keys) * per_key)
        elif dtype == pl.String:
            values = ["x"] * (len(keys) * per_key)
        else:
            values = [1] * (len(keys) * per_key)
        data[name] = values
    return pl.DataFrame(data, schema={column.name.lower(): _dtype(column) for column in table.columns})


def _source_file(path: Path, table: Any, keys: list[int], per_key: int) -> None:
    lines = []
    for key in keys:
        for _ in range(per_key):
            values = []
            for column in table.columns:
                if column.name.upper() in {"O_ORDERKEY", "L_ORDERKEY"}:
                    values.append(str(key))
                elif column.data_type.name in {"INTEGER", "DECIMAL"}:
                    values.append("1")
                elif column.data_type.name == "DATE":
                    values.append("1995-01-01")
                else:
                    values.append("x")
            lines.append("|".join(values) + "|\n")
    path.write_text("".join(lines), encoding="utf-8")


def _update_set(tmp_path: Path, set_id: int, insert_keys: list[int], delete_keys: list[int]) -> TPCHRefreshUpdateSet:
    update_set = TPCHRefreshUpdateSet(
        set_id=set_id,
        orders_path=tmp_path / f"orders.tbl.u{set_id}",
        lineitem_path=tmp_path / f"lineitem.tbl.u{set_id}",
        delete_path=tmp_path / f"delete.{set_id}",
    )
    _source_file(update_set.orders_path, ORDERS, insert_keys, 1)
    _source_file(update_set.lineitem_path, LINEITEM, insert_keys, 2)
    update_set.delete_path.write_text("".join(f"{key}|\n" for key in delete_keys), encoding="utf-8")
    return update_set


@pytest.fixture(params=["polars", "datafusion"])
def adapter(request: pytest.FixtureRequest) -> Any:
    if request.param == "polars":
        return PolarsDataFrameAdapter()
    pytest.importorskip("datafusion")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    return DataFusionDataFrameAdapter()


def _frame(adapter: Any, table: Any, keys: list[int], per_key: int) -> Any:
    rows = _rows(table, keys, per_key)
    if isinstance(adapter, PolarsDataFrameAdapter):
        return rows.lazy()
    return adapter.session_ctx.from_arrow(rows.to_arrow())


@pytest.fixture
def ctx(adapter: Any) -> Any:
    context = adapter.create_context()
    keys = list(range(1, BASE_ORDERS + 1))
    context.register_table("orders", _frame(adapter, ORDERS, keys, 1))
    context.register_table("lineitem", _frame(adapter, LINEITEM, keys, LINEITEMS_PER_ORDER))
    return context


def _row_count(context: Any, name: str) -> int:
    return context._adapter.tpch_refresh_backend().row_count(context.get_native_table(name))


def _counts(context: Any) -> tuple[int, int]:
    return _row_count(context, "orders"), _row_count(context, "lineitem")


class TestRefreshRowCounts:
    def test_rf1_appends_exact_rows(self, adapter: Any, ctx: Any, tmp_path: Path) -> None:
        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        update_set = _update_set(tmp_path, 2, [101, 102, 103], [1, 2, 3])

        result = execute_refresh_function(refresher, "RF1", update_set, check=True)

        assert result["status"] == "SUCCESS"
        assert result["check_status"] == "PASSED"
        assert result["rows_inserted"] == 3 + 3 * 2
        assert _counts(ctx) == (BASE_ORDERS + 3, BASE_ORDERS * LINEITEMS_PER_ORDER + 6)

    def test_rf2_deletes_exact_rows(self, adapter: Any, ctx: Any, tmp_path: Path) -> None:
        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        update_set = _update_set(tmp_path, 2, [101, 102, 103], [1, 2, 3])

        result = execute_refresh_function(refresher, "RF2", update_set, check=True)

        assert result["status"] == "SUCCESS"
        assert result["check_status"] == "PASSED"
        assert result["orders_deleted"] == 3
        assert result["lineitems_deleted"] == 3 * LINEITEMS_PER_ORDER
        assert _counts(ctx) == (BASE_ORDERS - 3, (BASE_ORDERS - 3) * LINEITEMS_PER_ORDER)

    def test_rf2_fails_the_count_check_when_a_delete_key_matches_no_order(
        self, adapter: Any, ctx: Any, tmp_path: Path
    ) -> None:
        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        update_set = _update_set(tmp_path, 2, [101], [1, 2, 999])

        result = execute_refresh_function(refresher, "RF2", update_set, check=True)

        assert result["status"] == "FAILED"
        assert result["check_status"] == "FAILED"
        assert "deleted 2 orders, expected 3" in result["error"]

    def test_rf2_row_delta_check_detects_a_table_changed_behind_the_refresher(
        self, adapter: Any, ctx: Any, tmp_path: Path
    ) -> None:
        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        update_set = _update_set(tmp_path, 2, [101], [1, 2, 3])
        counts = refresher.apply_rf2(update_set)
        state = refresher._applied[("RF2", 2)]
        state.changed["lineitem"] += 1

        with pytest.raises(ValueError, match="lineitem shrank by 9 rows, expected 10"):
            refresher.check_rf2_applied(update_set, counts.orders)

    def test_rf1_times_the_parse_and_append_not_a_lazy_pointer_swap(
        self, adapter: Any, ctx: Any, tmp_path: Path
    ) -> None:
        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        update_set = _update_set(tmp_path, 2, [101, 102, 103], [1, 2, 3])
        before = ctx.get_native_table("orders")

        refresher.apply_rf1(update_set)

        assert ctx.get_native_table("orders") is not before
        assert _counts(ctx)[0] == BASE_ORDERS + 3

    def test_back_to_back_pairs_keep_the_tables_consistent(self, adapter: Any, ctx: Any, tmp_path: Path) -> None:
        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        first = _update_set(tmp_path, 2, [101, 102], [1, 2])
        second = _update_set(tmp_path, 3, [201, 202], [3, 4])

        for update_set in (first, second):
            for function in ("RF1", "RF2"):
                assert execute_refresh_function(refresher, function, update_set, check=True)["status"] == "SUCCESS"

        assert _counts(ctx) == (BASE_ORDERS, BASE_ORDERS * LINEITEMS_PER_ORDER + 4 * 2 - 4 * LINEITEMS_PER_ORDER)


class TestDataFusionParquetBackedTables:
    def test_refresh_applies_to_view_typed_parquet_tables(self, tmp_path: Path) -> None:
        pytest.importorskip("datafusion")
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter()
        context = adapter.create_context()
        keys = list(range(1, BASE_ORDERS + 1))
        for name, table, per_key in (("orders", ORDERS, 1), ("lineitem", LINEITEM, LINEITEMS_PER_ORDER)):
            path = tmp_path / f"{name}.parquet"
            _rows(table, keys, per_key).write_parquet(path)
            context.register_table(name, adapter.read_parquet(path))
        assert "string_view" in str(context.get_native_table("orders").schema())
        refresher = DataFrameRefresher(context, adapter.tpch_refresh_backend())
        update_set = _update_set(tmp_path, 2, [101, 102, 103], [1, 2, 3])

        for function in ("RF1", "RF2"):
            assert execute_refresh_function(refresher, function, update_set, check=True)["status"] == "SUCCESS"

        assert _counts(context) == (BASE_ORDERS, BASE_ORDERS * LINEITEMS_PER_ORDER + 6 - 3 * LINEITEMS_PER_ORDER)

    def test_a_source_file_without_a_trailing_delimiter_is_accepted(self, tmp_path: Path) -> None:
        pytest.importorskip("datafusion")
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter()
        like = adapter.session_ctx.from_arrow(_rows(ORDERS, [1], 1).to_arrow())
        path = tmp_path / "orders.tbl.u2"
        _source_file(path, ORDERS, [7, 8], 1)
        path.write_text(path.read_text(encoding="utf-8").replace("|\n", "\n"), encoding="utf-8")

        parsed = adapter.tpch_refresh_backend().parse_delimited(path, like)

        assert parsed.count() == 2


class TestSnapshotIsolation:
    def test_a_snapshot_keeps_the_tables_it_was_taken_with(self, adapter: Any, ctx: Any, tmp_path: Path) -> None:
        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        snapshot = ctx.snapshot()
        before = _counts(snapshot)

        refresher.apply_rf1(_update_set(tmp_path, 2, [101, 102, 103], [1, 2, 3]))

        assert _counts(snapshot) == before
        assert _counts(ctx) != before
        assert _counts(ctx.snapshot()) == _counts(ctx)

    def test_concurrent_readers_see_the_before_or_after_pair_never_a_mix(self, ctx: Any) -> None:
        before = (ctx.get_native_table("orders"), ctx.get_native_table("lineitem"))
        grown = list(range(1, BASE_ORDERS + 4))
        after = (
            _frame(ctx._adapter, ORDERS, grown, 1),
            _frame(ctx._adapter, LINEITEM, grown, LINEITEMS_PER_ORDER),
        )
        legal = {
            (BASE_ORDERS, BASE_ORDERS * LINEITEMS_PER_ORDER),
            (BASE_ORDERS + 3, (BASE_ORDERS + 3) * LINEITEMS_PER_ORDER),
        }
        stop = threading.Event()
        observed: set[tuple[int, int]] = set()
        errors: list[BaseException] = []

        def reader() -> None:
            try:
                while not stop.is_set():
                    view = ctx.snapshot()
                    orders_frame = view.get_native_table("orders")
                    time.sleep(0)
                    lineitem_frame = view.get_native_table("lineitem")
                    backend = view._adapter.tpch_refresh_backend()
                    observed.add((backend.row_count(orders_frame), backend.row_count(lineitem_frame)))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for thread in threads:
            thread.start()
        try:
            for index in range(300):
                orders, lineitem = after if index % 2 == 0 else before
                ctx.swap_tables({"orders": orders, "lineitem": lineitem})
        finally:
            stop.set()
            for thread in threads:
                thread.join()

        assert not errors
        assert observed
        assert observed <= legal

    def test_swap_rejects_an_unregistered_table(self, ctx: Any) -> None:
        with pytest.raises(KeyError, match="nation"):
            ctx.swap_tables({"nation": ctx.get_native_table("orders")})


class TestPowerRefresh:
    def _plan(self, adapter: Any, ctx: Any, update_set: TPCHRefreshUpdateSet) -> TpchRefreshPlan:
        import tempfile

        return TpchRefreshPlan(
            DataFrameRefresher(ctx, adapter.tpch_refresh_backend()),
            update_set.set_id,
            update_set,
            tempfile.TemporaryDirectory(),
        )

    def test_rf1_then_rf2_are_recorded_with_checks(self, adapter: Any, ctx: Any, tmp_path: Path) -> None:
        plan = self._plan(adapter, ctx, _update_set(tmp_path, 1, [101, 102], [1, 2]))
        refresh = PowerRefresh(plan)

        refresh.start()
        assert _counts(ctx)[0] == BASE_ORDERS + 2
        refresh.finish()

        assert [function["function"] for function in plan.power_functions] == ["RF1", "RF2"]
        assert all(function["status"] == "SUCCESS" for function in plan.power_functions)
        assert all(function["check_status"] == "PASSED" for function in plan.power_functions)
        valid, first_set = TPCMetricsCalculator._power_refresh_evidence(
            {"refresh_first_set": 1, "refresh_functions": plan.power_functions}
        )
        assert valid and first_set == 1
        plan.close()

    def test_a_failed_rf1_records_a_skipped_rf2_and_reports_the_error(
        self, adapter: Any, ctx: Any, tmp_path: Path
    ) -> None:
        update_set = _update_set(tmp_path, 1, [101], [1])
        update_set.orders_path.write_text("not|a|valid|row\n", encoding="utf-8")
        plan = self._plan(adapter, ctx, update_set)
        refresh = PowerRefresh(plan)

        assert refresh.start() is False
        refresh.finish()

        assert [function["status"] for function in plan.power_functions] == ["FAILED", "SKIPPED"]
        assert refresh.error is not None and "RF1 failed" in refresh.error
        plan.close()

    def test_a_failed_rf2_is_recorded_without_raising(self, adapter: Any, ctx: Any, tmp_path: Path) -> None:
        update_set = _update_set(tmp_path, 1, [101], [1, 999])
        plan = self._plan(adapter, ctx, update_set)
        refresh = PowerRefresh(plan)

        assert refresh.start() is True
        refresh.finish()

        assert [function["status"] for function in plan.power_functions] == ["SUCCESS", "FAILED"]
        assert refresh.error is not None and "RF2 failed" in refresh.error
        plan.close()

    def test_finish_without_start_does_nothing(self, adapter: Any, ctx: Any, tmp_path: Path) -> None:
        plan = self._plan(adapter, ctx, _update_set(tmp_path, 1, [101], [1]))

        PowerRefresh(plan).finish()

        assert plan.power_functions == []
        plan.close()


class TestStreamCount:
    def test_omitting_streams_uses_the_tpc_minimum(self) -> None:
        from benchbox.platforms.dataframe.tpch_full_spec import resolve_stream_count

        assert resolve_stream_count(SimpleNamespace(concurrency=None)) == 2
        assert resolve_stream_count(SimpleNamespace(concurrency=4)) == 4

    @pytest.mark.parametrize("value", [0, 1, True, "2"])
    def test_an_invalid_stream_count_is_refused(self, value: Any) -> None:
        from benchbox.platforms.dataframe.tpch_full_spec import resolve_stream_count

        with pytest.raises(ValueError, match="--streams"):
            resolve_stream_count(SimpleNamespace(concurrency=value))


def _legal_states() -> set[tuple[int, int]]:
    orders, lineitem = BASE_ORDERS, BASE_ORDERS * LINEITEMS_PER_ORDER
    states = {(orders, lineitem)}
    for _ in range(2):
        orders, lineitem = orders + 3, lineitem + 6
        states.add((orders, lineitem))
        orders, lineitem = orders - 3, lineitem - 3 * LINEITEMS_PER_ORDER
        states.add((orders, lineitem))
    return states


class TestThroughputWithRefreshStream:
    @pytest.fixture
    def throughput(self, monkeypatch: pytest.MonkeyPatch, ctx: Any, adapter: Any, tmp_path: Path):
        update_sets = {
            2: _update_set(tmp_path, 2, [101, 102, 103], [1, 2, 3]),
            3: _update_set(tmp_path, 3, [201, 202, 203], [4, 5, 6]),
        }
        monkeypatch.setattr(throughput_module, "generate_refresh_update_sets", lambda **_: update_sets)
        monkeypatch.setattr(
            dataframe_throughput,
            "get_tpch_dataframe_queries",
            lambda stream_id: [
                SimpleNamespace(query_id=f"Q{number}")
                for number in TPCHStreams.PERMUTATION_MATRIX[stream_id % len(TPCHStreams.PERMUTATION_MATRIX)]
            ],
        )
        monkeypatch.setattr(
            dataframe_throughput,
            "get_tpch_query_extracted_parameters",
            lambda query_id, seed, scale_factor: {"seed": seed, "query": query_id},
        )
        observations: list[tuple[int, int, int, int]] = []
        lock = threading.Lock()

        def execute_query(view: Any, query: Any) -> dict[str, Any]:
            orders = view.get_native_table("orders")
            time.sleep(0.001)
            lineitem = view.get_native_table("lineitem")
            backend = view._adapter.tpch_refresh_backend()
            seen = (backend.row_count(orders), backend.row_count(lineitem))
            from benchbox.core.tpch.dataframe_queries import get_tpch_parameters

            number = int(str(query.query_id)[1:])
            with lock:
                observations.append((*seen, number, get_tpch_parameters(number)["seed"]))
            return {
                "query_id": query.query_id,
                "status": "SUCCESS",
                "execution_time_seconds": 0.001,
                "rows_returned": 1,
            }

        refresher = DataFrameRefresher(ctx, adapter.tpch_refresh_backend())
        test = DataFrameTPCHThroughputTest(
            ctx, refresher, execute_query, scale_factor=1.0, num_streams=2, refresh_first_set=1
        )
        return test, observations

    def test_two_streams_and_the_refresh_stream_complete_with_exact_counts(self, throughput: Any) -> None:
        test, observations = throughput

        result = test.run()

        assert result.success, result.errors
        assert result.streams_executed == 2
        assert result.streams_successful == 2
        assert [stream.queries_executed for stream in result.stream_results] == [22, 22]
        assert result.throughput_at_size and result.throughput_at_size > 0
        refresh = result.refresh_stream
        assert refresh is not None and refresh.status == "COMPLETED"
        assert [pair["update_set"] for pair in refresh.pairs] == [2, 3]
        for pair in refresh.pairs:
            rf1, rf2 = pair["functions"]
            assert (rf1["function"], rf1["rows_inserted"], rf1["check_status"]) == ("RF1", 9, "PASSED")
            assert (rf2["function"], rf2["orders_deleted"], rf2["lineitems_deleted"]) == ("RF2", 3, 9)
            assert rf2["check_status"] == "PASSED"
            assert rf1["duration_ms"] >= 1 and rf2["duration_ms"] >= 1
        assert len(observations) == 44

    def test_every_query_sees_a_whole_refresh_state(self, throughput: Any) -> None:
        test, observations = throughput

        test.run()

        assert {(orders, lineitem) for orders, lineitem, *_ in observations} <= _legal_states()

    def test_streams_bind_their_own_per_position_seeds(self, throughput: Any) -> None:
        test, observations = throughput

        test.run()

        for stream_id in (1, 2):
            permutation = TPCHStreams.PERMUTATION_MATRIX[stream_id % len(TPCHStreams.PERMUTATION_MATRIX)]
            expected = {42 + 1001 * stream_id + position for position in range(len(permutation))}
            assert expected <= {seed for *_rest, seed in observations}

    def test_refresh_evidence_satisfies_the_metrics_calculator(self, throughput: Any) -> None:
        test, _observations = throughput

        result = test.run()

        valid, first_set, streams = TPCMetricsCalculator._throughput_refresh_evidence(
            {
                "num_streams": 2,
                "stream_results": [{}, {}],
                "refresh_stream": result.refresh_stream.to_dict(),
            }
        )
        assert (valid, first_set, streams) == (True, 2, 2)

    def test_a_failing_refresh_marks_the_stream_failed_and_the_run_unsuccessful(
        self, throughput: Any, tmp_path: Path
    ) -> None:
        test, _observations = throughput
        original = test._execute_refresh_function

        def failing(function: str, update_set: Any, connection: Any) -> dict[str, Any]:
            if function == "RF2":
                return {
                    "function": function,
                    "update_set": update_set.set_id,
                    "duration_ms": 0,
                    "status": "FAILED",
                    "check_status": "NOT_RUN",
                    "error": "boom",
                    "rows": 0,
                }
            return original(function, update_set, connection)

        test._execute_refresh_function = failing

        result = test.run()

        assert not result.success
        assert result.refresh_stream is not None and result.refresh_stream.status == "FAILED"
        assert result.throughput_at_size in (0.0, None)


DERIVED_PARAMETERS = frozenset({"end_date", "cutoff_date"})


def _leaf_values(value: Any):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaf_values(item)
    else:
        yield value


@pytest.mark.qgen
class TestStreamParameterParity:
    def test_dataframe_stream_parameters_appear_in_the_sql_stream_queries(self) -> None:
        from datetime import date

        from benchbox.core.tpch.benchmark import TPCHBenchmark
        from benchbox.core.tpch.throughput_test import _derive_query_seed, _stream_permutation

        try:
            benchmark = TPCHBenchmark(scale_factor=0.01)
        except RuntimeError as exc:
            pytest.skip(f"qgen binary not available: {exc}")
        test = DataFrameTPCHThroughputTest(
            None, None, lambda *_: {}, scale_factor=0.01, num_streams=2, refresh_first_set=1
        )
        checked = 0

        for stream_id in (1, 2):
            for position, query_id in enumerate(_stream_permutation(stream_id, None)):
                seed = _derive_query_seed(42 + stream_id, stream_id, position)
                token = test._get_stream_query(query_id, seed, stream_id, 0.01)
                sql = benchmark.get_query(
                    query_id,
                    seed=seed,
                    params={"stream_id": stream_id % len(TPCHStreams.PERMUTATION_MATRIX)},
                    scale_factor=0.01,
                    dialect=None,
                )
                for name, value in token.parameters.items():
                    if name in DERIVED_PARAMETERS:
                        continue
                    for leaf in _leaf_values(value):
                        if isinstance(leaf, float):
                            continue
                        text = leaf.isoformat() if isinstance(leaf, date) else str(leaf)
                        assert text in sql, (stream_id, query_id, name, text)
                        checked += 1

        assert checked > 100

    def test_different_streams_and_positions_bind_different_parameters(self) -> None:
        from benchbox.core.tpch.throughput_test import _derive_query_seed

        test = DataFrameTPCHThroughputTest(
            None, None, lambda *_: {}, scale_factor=0.01, num_streams=2, refresh_first_set=1
        )

        first = test._get_stream_query(3, _derive_query_seed(43, 1, 0), 1, 0.01)
        second = test._get_stream_query(3, _derive_query_seed(44, 2, 0), 2, 0.01)

        assert first.parameters != second.parameters


class TestRefreshPlanResolution:
    def _config(self, name: str = "tpch", refresh: bool = True) -> Any:
        options = {"benchmark_options": {"tpch_refresh_functions": True}} if refresh else {}
        return SimpleNamespace(name=name, scale_factor=0.01, options=options)

    def test_no_plan_when_refresh_is_not_requested(self, adapter: Any, ctx: Any) -> None:
        from benchbox.platforms.dataframe.tpch_full_spec import resolve_refresh_plan

        assert resolve_refresh_plan(adapter, None, self._config(refresh=False), ctx) is None

    def test_no_plan_for_another_benchmark(self, adapter: Any, ctx: Any) -> None:
        from benchbox.platforms.dataframe.tpch_full_spec import resolve_refresh_plan

        assert resolve_refresh_plan(adapter, None, self._config(name="tpcds"), ctx) is None

    def test_a_platform_without_a_refresh_backend_is_refused(self, ctx: Any) -> None:
        from benchbox.platforms.dataframe.tpch_full_spec import resolve_refresh_plan

        adapter = SimpleNamespace(platform_name="pandas-df", tpch_refresh_backend=lambda: None)

        with pytest.raises(NotImplementedError, match="pandas-df"):
            resolve_refresh_plan(adapter, None, self._config(), ctx)

    def test_the_first_set_option_must_be_a_positive_integer(self, adapter: Any, ctx: Any) -> None:
        from benchbox.platforms.dataframe.tpch_full_spec import resolve_refresh_plan

        config = self._config()
        config.options["benchmark_options"]["tpch_refresh_first_set"] = 0

        with pytest.raises(ValueError, match="tpch_refresh_first_set"):
            resolve_refresh_plan(adapter, None, config, ctx)
