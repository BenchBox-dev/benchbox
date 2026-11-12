# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

START_DATE = "2024-12-01"
END_DATE = "2025-01-01"

FLOAT_SLACK = 1e-9


def _flight_row(flight_id: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "flight_id": flight_id,
        "flight_date": date(2024, 12, 1 + (flight_id % 28)),
        "year": 2024,
        "month": 12,
        "day_of_month": 1 + (flight_id % 28),
        "day_of_week": 1 + (flight_id % 7),
        "reporting_airline": "AA",
        "flight_number": 100 + flight_id,
        "origin": "JFK",
        "dest": "LAX",
        "crs_dep_time": 800,
        "dep_time": 810.0,
        "dep_delay": 10.0,
        "crs_arr_time": 1100,
        "arr_time": 1110.0,
        "arr_delay": 10.0,
        "cancelled": 0,
        "cancellation_code": None,
        "diverted": 0,
        "crs_elapsed_time": 360.0,
        "actual_elapsed_time": 370.0,
        "air_time": 310.0,
        "distance": 2500.0,
        "carrier_delay": 5.0,
        "weather_delay": 0.0,
        "nas_delay": 0.0,
        "security_delay": 0.0,
        "late_aircraft_delay": 5.0,
    }
    row.update(overrides)
    return row


def _build_flights() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    flight_id = 1

    def add(**overrides: Any) -> None:
        nonlocal flight_id
        rows.append(_flight_row(flight_id, **overrides))
        flight_id += 1

    for i in range(1050):
        add(reporting_airline="AA", origin="JFK", dest="LAX", distance=2500.0)
    for i in range(1050):
        add(
            reporting_airline="DL",
            origin="LAX",
            dest="JFK",
            dep_delay=12.0,
            arr_delay=12.0,
            dep_time=812.0,
            arr_time=1112.0,
            distance=300.0,
            carrier_delay=6.0,
            late_aircraft_delay=6.0,
        )

    for code in ("A", "B", "C", "D"):
        add(
            reporting_airline="UA",
            origin="ORD",
            dest="DFW",
            cancelled=1,
            cancellation_code=code,
            dep_time=None,
            dep_delay=None,
            arr_time=None,
            arr_delay=None,
            actual_elapsed_time=None,
            air_time=None,
            carrier_delay=None,
            weather_delay=None,
            nas_delay=None,
            security_delay=None,
            late_aircraft_delay=None,
        )

    for crs in (559, 560, 5, 2359):
        add(reporting_airline="B6", origin="BOS", dest="MIA", crs_dep_time=crs, distance=100.0)

    add(reporting_airline="WN", origin="DAL", dest="HOU", late_aircraft_delay=10.2, arr_delay=20.5)
    add(reporting_airline="WN", origin="DAL", dest="HOU", late_aircraft_delay=10.3, arr_delay=20.5)

    add(
        reporting_airline="B6",
        origin="BOS",
        dest="MIA",
        dep_delay=45.0,
        arr_delay=40.0,
        carrier_delay=10.0,
        weather_delay=8.0,
        nas_delay=6.0,
        security_delay=2.0,
        late_aircraft_delay=14.0,
        distance=1500.0,
    )
    add(
        reporting_airline="B6",
        origin="BOS",
        dest="MIA",
        dep_delay=80.0,
        arr_delay=70.0,
        carrier_delay=20.0,
        weather_delay=15.0,
        nas_delay=10.0,
        security_delay=5.0,
        late_aircraft_delay=20.0,
        distance=1500.0,
        flight_date=date(2024, 12, 25),
        day_of_month=25,
    )
    return rows


@pytest.fixture(scope="module")
def flights_pdf():
    pd = pytest.importorskip("pandas")
    from benchbox.core.flightdata.schema import FLIGHT_SCHEMA

    columns = list(FLIGHT_SCHEMA["flights"]["columns"])
    frame = pd.DataFrame(_build_flights(), columns=columns)
    return frame


@pytest.fixture(scope="module")
def airlines_pdf():
    pd = pytest.importorskip("pandas")
    return pd.DataFrame(
        {
            "code": ["AA", "DL", "UA", "WN", "B6"],
            "name": ["American", "Delta", "United", "Southwest", "JetBlue"],
        }
    )


@pytest.fixture(scope="module")
def airports_pdf():
    pd = pytest.importorskip("pandas")
    return pd.DataFrame(
        {
            "code": ["JFK", "LAX", "ORD", "DFW", "BOS", "MIA", "DAL", "HOU"],
            "name": ["JFK", "LAX", "ORD", "DFW", "BOS", "MIA", "DAL", "HOU"],
            "city": ["New York", "Los Angeles", "Chicago", "Dallas", "Boston", "Miami", "Dallas", "Houston"],
            "state": ["NY", "CA", "IL", "TX", "MA", "FL", "TX", "TX"],
            "latitude": [40.6, 33.9, 41.9, 32.9, 42.4, 25.8, 32.8, 29.6],
            "longitude": [-73.8, -118.4, -87.9, -97.0, -71.0, -80.3, -96.8, -95.3],
        }
    )


@pytest.fixture(scope="module")
def duckdb_conn(flights_pdf, airlines_pdf, airports_pdf):
    duckdb = pytest.importorskip("duckdb")
    from benchbox.core.flightdata.benchmark import FlightDataBenchmark

    conn = duckdb.connect(":memory:")
    benchmark = FlightDataBenchmark(scale_factor=0.01)
    for statement in benchmark.get_create_tables_sql(dialect="duckdb").strip().split(";"):
        if statement.strip():
            conn.execute(statement.strip())
    conn.register("_flights_df", flights_pdf)
    conn.register("_airlines_df", airlines_pdf)
    conn.register("_airports_df", airports_pdf)
    conn.execute("INSERT INTO flights SELECT * FROM _flights_df")
    conn.execute("INSERT INTO airlines SELECT * FROM _airlines_df")
    conn.execute("INSERT INTO airports SELECT * FROM _airports_df")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def polars_ctx(flights_pdf, airlines_pdf, airports_pdf):
    pl = pytest.importorskip("polars")
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    ctx = PolarsDataFrameAdapter().create_context()
    ctx.register_table("flights", pl.from_pandas(flights_pdf))
    ctx.register_table("airlines", pl.from_pandas(airlines_pdf))
    ctx.register_table("airports", pl.from_pandas(airports_pdf))
    return ctx


@pytest.fixture(scope="module")
def pandas_ctx(flights_pdf, airlines_pdf, airports_pdf):
    tables = {"flights": flights_pdf, "airlines": airlines_pdf, "airports": airports_pdf}

    class SimplePandasContext:
        def get_table(self, name: str):
            return tables[name]

    return SimplePandasContext()


@pytest.fixture(scope="module")
def datafusion_ctx(flights_pdf, airlines_pdf, airports_pdf):
    pytest.importorskip("datafusion")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter()
    ctx = adapter.create_context()
    for name, frame in (("flights", flights_pdf), ("airlines", airlines_pdf), ("airports", airports_pdf)):
        ctx.register_table(name, adapter.session_ctx.from_pandas(frame))
    return ctx


def _sql_columns(conn: Any, query_key: str) -> list[str]:
    from benchbox.core.flightdata.queries import FlightDataQueryManager

    sql = FlightDataQueryManager(start_date=START_DATE, end_date=END_DATE).get_query(query_key)
    return [desc[0] for desc in conn.execute(sql).description]


def _frame_columns(frame: Any) -> list[str]:
    native = getattr(frame, "native", frame)
    if hasattr(native, "collect"):
        native = native.collect()
    if hasattr(native, "columns"):
        return list(native.columns)
    return list(native.to_pandas().columns)


def _cells_equal(left: list[tuple], right: list[tuple]) -> bool:
    if len(left) != len(right):
        return False
    for left_row, right_row in zip(left, right):
        if len(left_row) != len(right_row):
            return False
        for left_cell, right_cell in zip(left_row, right_row):
            if left_cell is None or right_cell is None:
                if left_cell is not right_cell and not (left_cell is None and right_cell is None):
                    return False
            elif isinstance(left_cell, float) or isinstance(right_cell, float):
                if abs(float(left_cell) - float(right_cell)) > FLOAT_SLACK:
                    return False
            elif left_cell != right_cell:
                return False
    return True


def _materialize(frame: Any) -> list[tuple]:
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    native = getattr(frame, "native", frame)
    if hasattr(native, "collect"):
        native = native.collect()
    if isinstance(native, list):
        pa = pytest.importorskip("pyarrow")
        frame = pa.Table.from_batches(native).to_pandas()
    return materialize_rows(frame)


def _reference_rows(conn: Any, query_key: str) -> list[tuple]:
    from benchbox.core.equivalence.dataframe_surface import fetch_reference_rows
    from benchbox.core.flightdata.queries import FlightDataQueryManager

    sql = FlightDataQueryManager(start_date=START_DATE, end_date=END_DATE).get_query(query_key)
    return fetch_reference_rows(conn, sql)


class TestOutputColumnOrder:
    @pytest.mark.parametrize(
        "query_key",
        [
            "ontime-by-carrier",
            "delay-by-airport",
            "delay-by-hour",
            "best-routes",
            "improvement-trend",
            "delay-causes",
            "cascade-delays",
            "weather-impact",
            "recovery-time",
            "busiest-routes",
            "route-reliability",
            "distance-delay",
            "hub-connectivity",
            "day-of-week",
            "seasonal-trends",
            "holiday-impact",
            "time-of-day",
            "carrier-ranking",
            "cancellation-rate",
            "market-share",
        ],
    )
    def test_sql_expression_pandas_share_column_order(self, query_key, duckdb_conn, polars_ctx, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        query = get_dataframe_queries().get_or_raise(query_key)
        expression_impl = query.get_impl_for_family("expression")
        pandas_impl = query.get_impl_for_family("pandas")
        assert expression_impl is not None and pandas_impl is not None

        expected = _sql_columns(duckdb_conn, query_key)
        assert _frame_columns(expression_impl(polars_ctx)) == expected
        assert _frame_columns(pandas_impl(pandas_ctx)) == expected


class TestHourBucketing:
    @pytest.mark.parametrize("query_key,column", [("delay-by-hour", "dep_hour"), ("time-of-day", "hour_of_day")])
    def test_hours_are_integral_and_match_sql(self, query_key, column, duckdb_conn, polars_ctx, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        query = get_dataframe_queries().get_or_raise(query_key)
        expected = _reference_rows(duckdb_conn, query_key)
        position = _sql_columns(duckdb_conn, query_key).index(column)
        hours = [row[position] for row in expected]
        assert hours == sorted(hours)
        assert all(float(hour).is_integer() for hour in hours)
        assert 5 in hours
        assert not any(isinstance(hour, float) and not float(hour).is_integer() for hour in hours)

        expression_rows = _materialize(query.get_impl_for_family("expression")(polars_ctx))
        pandas_rows = query.get_impl_for_family("pandas")(pandas_ctx).itertuples(index=False, name=None)
        assert _cells_equal(expression_rows, expected)
        assert _cells_equal([tuple(row) for row in pandas_rows], expected)


class TestDeterministicTopNOrder:
    @pytest.mark.parametrize(
        "query_key",
        [
            "ontime-by-carrier",
            "best-routes",
            "cancellation-rate",
            "carrier-ranking",
            "cascade-delays",
            "route-reliability",
        ],
    )
    def test_tied_metrics_resolve_identically(self, query_key, duckdb_conn, polars_ctx, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        query = get_dataframe_queries().get_or_raise(query_key)
        expected = _reference_rows(duckdb_conn, query_key)
        assert len(expected) > 1
        expression_rows = _materialize(query.get_impl_for_family("expression")(polars_ctx))
        pandas_rows = [tuple(row) for row in query.get_impl_for_family("pandas")(pandas_ctx).itertuples(index=False)]
        assert _cells_equal(expression_rows, expected)
        assert _cells_equal(pandas_rows, expected)


class TestHalfAwayRounding:
    def test_cascade_total_on_exact_half(self, duckdb_conn, polars_ctx, pandas_ctx):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        query = get_dataframe_queries().get_or_raise("cascade-delays")
        columns = _sql_columns(duckdb_conn, "cascade-delays")
        position = columns.index("total_cascade_minutes")
        expected = _reference_rows(duckdb_conn, "cascade-delays")
        wn_sql = next(row for row in expected if row[0] == "WN")
        assert wn_sql[position] == 21

        expression_rows = _materialize(query.get_impl_for_family("expression")(polars_ctx))
        wn_expression = next(row for row in expression_rows if row[0] == "WN")
        assert wn_expression[position] == 21

        pandas_frame = query.get_impl_for_family("pandas")(pandas_ctx)
        wn_pandas = pandas_frame[pandas_frame["reporting_airline"] == "WN"].iloc[0]
        assert wn_pandas["total_cascade_minutes"] == 21

    @pytest.mark.parametrize(
        "value, digits, expected",
        [(2.5, 0, 3.0), (-2.5, 0, -3.0), (0.125, 2, 0.13), (-0.125, 2, -0.13), (2.45, 1, 2.5)],
    )
    def test_helpers_match_duckdb_round_at_halves(self, value, digits, expected):
        pd = pytest.importorskip("pandas")

        from benchbox.core.flightdata.dataframe_queries import _pandas_round_half_away

        duckdb = pytest.importorskip("duckdb")
        (sql_value,) = duckdb.query(f"SELECT ROUND(CAST({value} AS DOUBLE), {digits})").fetchall()[0]
        assert float(sql_value) == pytest.approx(expected)
        assert float(_pandas_round_half_away(pd.Series([value]), digits).iloc[0]) == pytest.approx(expected)


class TestCsvNullDialect:
    def test_manifest_resolves_to_null_loading(self, tmp_path):
        from benchbox.core.flightdata.downloader import FlightDataDownloader
        from benchbox.platforms.base.data_loading import (
            DataSourceResolver,
            DuckDBNativeHandler,
            resolve_csv_dialect,
        )

        flights_csv = tmp_path / "flights.csv"
        flights_csv.write_text(
            "flight_id,flight_date,arr_delay,cancellation_code\n1,2024-12-01,10.5,A\n2,2024-12-02,,\n",
            encoding="utf-8",
        )
        airlines_csv = tmp_path / "airlines.csv"
        airlines_csv.write_text("code,name\nAA,American\n", encoding="utf-8")
        airports_csv = tmp_path / "airports.csv"
        airports_csv.write_text("code,name\nJFK,JFK\n", encoding="utf-8")

        downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
        downloader._table_file_row_counts = {
            flights_csv: 2,
            airlines_csv: 1,
            airports_csv: 1,
        }
        downloader._write_manifest({"flights": flights_csv, "airlines": airlines_csv, "airports": airports_csv})

        from benchbox.core.flightdata.benchmark import FlightDataBenchmark

        benchmark = FlightDataBenchmark(scale_factor=0.01, output_dir=tmp_path)
        source = DataSourceResolver(platform_name="duckdb").resolve(benchmark, tmp_path)
        assert source is not None
        for table in ("flights", "airlines", "airports"):
            dialect = resolve_csv_dialect(source, table, tmp_path / f"{table}.csv", benchmark)
            assert dialect.null_marker == "", f"{table} must load empty fields as NULL"

        handler = DuckDBNativeHandler(",", None, benchmark, null_marker="")
        assert "nullstr=''" in handler._pipe_nullstr_config()

        import json

        from benchbox.platforms.base.data_loading import (
            DUCKDB_NO_NULL_CONVERSION_SENTINEL,
            DataSource,
        )
        from benchbox.platforms.duckdb import _build_csv_scan_expression

        columns = ["flight_id", "flight_date", "arr_delay", "cancellation_code"]

        def scan_sql():
            meta = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))["tables"]["flights"][
                "formats"
            ]["csv"][0]["metadata"]
            source = DataSource(
                source_type="manifest", tables={"flights": flights_csv}, table_metadata={"flights": meta}
            )
            return _build_csv_scan_expression([flights_csv], columns, data_source=source, table_name="flights")

        manifest_path = tmp_path / "_datagen_manifest.json"
        stale = json.loads(manifest_path.read_text(encoding="utf-8"))
        stale["tables"]["flights"]["formats"]["csv"][0]["metadata"]["csv_null_marker"] = None
        manifest_path.write_text(json.dumps(stale), encoding="utf-8")
        assert DUCKDB_NO_NULL_CONVERSION_SENTINEL in scan_sql()

        downloader.backfill_csv_dialect_metadata()
        healed_sql = scan_sql()
        assert "nullstr=''" in healed_sql
        assert DUCKDB_NO_NULL_CONVERSION_SENTINEL not in healed_sql

        duckdb = pytest.importorskip("duckdb")
        conn = duckdb.connect(":memory:")
        try:
            (is_null,) = conn.execute(
                f"SELECT cancellation_code IS NULL FROM {healed_sql} WHERE flight_id = 2"
            ).fetchone()
            assert is_null is True
        finally:
            conn.close()

    def test_stale_manifest_backfilled_in_place(self, tmp_path):
        import json

        from benchbox.core.flightdata.downloader import FlightDataDownloader

        manifest = {
            "benchmark": "flightdata",
            "tables": {
                "flights": {"formats": {"csv": [{"path": "flights.csv", "metadata": {"csv_delimiter": ","}}]}},
                "airlines": {"formats": {"csv": [{"path": "airlines.csv", "metadata": {"csv_null_marker": None}}]}},
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
        assert downloader.backfill_csv_dialect_metadata() is True
        healed = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
        tables = healed["tables"]
        assert tables["flights"]["formats"]["csv"][0]["metadata"]["csv_null_marker"] == ""
        assert tables["airlines"]["formats"]["csv"][0]["metadata"]["csv_null_marker"] == ""
        assert downloader.backfill_csv_dialect_metadata() is False

    def test_backfill_ignores_foreign_manifest(self, tmp_path):
        import json

        from benchbox.core.flightdata.downloader import FlightDataDownloader

        manifest = {
            "benchmark": "tpch",
            "tables": {
                "lineitem": {"formats": {"tbl": [{"path": "lineitem.tbl", "metadata": {"csv_null_marker": None}}]}}
            },
        }
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
        assert downloader.backfill_csv_dialect_metadata() is False
        assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest

    def test_ensure_auxiliary_files_heals_stale_manifest(self, tmp_path, monkeypatch):
        import json

        from benchbox.core.flightdata.benchmark import FlightDataBenchmark

        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "benchmark": "flightdata",
                    "tables": {"flights": {"formats": {"csv": [{"path": "flights.csv", "metadata": {}}]}}},
                }
            ),
            encoding="utf-8",
        )
        benchmark = FlightDataBenchmark(scale_factor=0.01, output_dir=tmp_path)
        monkeypatch.setattr(benchmark.downloader, "repair_reusable_layout", lambda: None)
        calls = []
        real_backfill = benchmark.downloader.backfill_csv_dialect_metadata
        monkeypatch.setattr(
            benchmark.downloader,
            "backfill_csv_dialect_metadata",
            lambda: calls.append(True) or real_backfill(),
        )
        benchmark.ensure_auxiliary_data_files()
        assert calls == [True]
        healed = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert healed["tables"]["flights"]["formats"]["csv"][0]["metadata"]["csv_null_marker"] == ""


class TestOutputContractInvariants:
    _TOP_N_TIEBREAKERS = {
        "delay-by-airport": ["origin"],
        "best-routes": ["origin", "dest"],
        "busiest-routes": ["origin", "dest"],
        "route-reliability": ["origin", "dest"],
        "hub-connectivity": ["origin"],
        "market-share": ["reporting_airline"],
    }

    def _catalog_sql(self, tmp_path):
        from benchbox.core.flightdata.benchmark import FlightDataBenchmark

        benchmark = FlightDataBenchmark(scale_factor=0.01, output_dir=tmp_path)
        return benchmark.get_queries()

    @staticmethod
    def _order_keys(order_clause):
        keys = []
        for part in order_clause.split(","):
            tokens = [t for t in part.strip().split() if t.upper() not in ("ASC", "DESC", "NULLS", "LAST", "FIRST")]
            keys.append(tokens[-1].split(".")[-1])
        return keys

    def test_top_n_orders_end_in_unique_key(self, tmp_path):
        sql_by_key = self._catalog_sql(tmp_path)
        assert set(self._TOP_N_TIEBREAKERS) <= set(sql_by_key), "tiebreaker map covers every top-N query"
        for key, tiebreakers in self._TOP_N_TIEBREAKERS.items():
            sql = sql_by_key[key]
            assert "LIMIT" in sql, f"{key}: expected a top-N query"
            order = sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
            keys = self._order_keys(order)
            assert keys[-len(tiebreakers) :] == tiebreakers, f"{key}: ORDER BY must end in {tiebreakers}: {order!r}"

    def test_no_float_sum_rounding_in_sql(self, tmp_path):
        for key, sql in self._catalog_sql(tmp_path).items():
            assert "ROUND(SUM(" not in sql, f"{key}: float-sum rounding must use integer tenths"


class TestExpressionEngineParity:
    @pytest.mark.parametrize(
        "query_key",
        [
            "ontime-by-carrier",
            "improvement-trend",
            "delay-causes",
            "cascade-delays",
            "time-of-day",
            "weather-impact",
            "best-routes",
            "route-reliability",
            "delay-by-hour",
        ],
    )
    def test_polars_datafusion_agree(self, query_key, polars_ctx, datafusion_ctx):
        from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

        query = get_dataframe_queries().get_or_raise(query_key)
        polars_rows = _materialize(query.get_impl_for_family("expression")(polars_ctx))
        fusion_rows = _materialize(query.get_impl_for_family("expression")(datafusion_ctx))
        assert _cells_equal(fusion_rows, polars_rows)
