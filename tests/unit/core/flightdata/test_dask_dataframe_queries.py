from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

pd = pytest.importorskip("pandas")
dd = pytest.importorskip("dask.dataframe")
pa = pytest.importorskip("pyarrow")

_DAYS = [
    (12, 1),
    (12, 3),
    (12, 5),
    (12, 7),
    (12, 9),
    (12, 11),
    (12, 15),
    (12, 23),
    (12, 25),
    (12, 27),
    (12, 31),
    (1, 1),
    (1, 2),
    (9, 5),
    (5, 28),
    (7, 4),
    (11, 22),
    (6, 10),
]


def _tables():
    rows = []
    for repeat in range(200):
        for index, (month, day) in enumerate(_DAYS):
            n = repeat * len(_DAYS) + index
            cancelled = 1 if n % 7 == 3 else 0
            dep_delay = None if cancelled else float((n * 13) % 75 - 10)
            arr_delay = None if cancelled or n % 11 == 5 else float((n * 7) % 70 - 8)
            distance = float((n * 211) % 2600 + 50)
            late = None if cancelled else float(n % 4 * 5)
            rows.append(
                {
                    "flight_id": n + 1,
                    "flight_date": date(2024 if month == 12 else 2025, month, day),
                    "year": 2024 if month == 12 else 2025,
                    "month": month,
                    "day_of_month": day,
                    "day_of_week": n % 7 + 1,
                    "reporting_airline": ["AA", "DL", "UA"][n % 3],
                    "origin": ["JFK", "LAX", "ORD", "ATL"][n % 4],
                    "dest": ["LAX", "ORD", "ATL", "JFK"][n % 4],
                    "crs_dep_time": 600 + (n % 15) * 100,
                    "dep_delay": dep_delay,
                    "arr_delay": arr_delay,
                    "cancelled": cancelled,
                    "cancellation_code": ["A", "B", "C", "D"][n % 4] if cancelled else None,
                    "actual_elapsed_time": None if cancelled else 120.0 + n,
                    "distance": distance,
                    "carrier_delay": None if cancelled else float(n % 3 * 5),
                    "weather_delay": None if cancelled else float(n % 5 * 2),
                    "nas_delay": None if cancelled else float(n % 2 * 3),
                    "security_delay": None if cancelled else 0.0,
                    "late_aircraft_delay": late,
                }
            )
    airlines = pd.DataFrame({"code": ["AA", "DL", "UA"], "name": ["American", "Delta", "United"]})
    airports = pd.DataFrame(
        {
            "code": ["JFK", "LAX", "ORD", "ATL"],
            "name": ["JFK Intl", "LAX Intl", "O'Hare", "Hartsfield"],
            "city": ["New York", "Los Angeles", "Chicago", "Atlanta"],
            "state": ["NY", "CA", "IL", "GA"],
        }
    )
    flights = pd.DataFrame(rows)
    flights["flight_date"] = flights["flight_date"].astype(pd.ArrowDtype(pa.date32()))
    return {"flights": flights, "airlines": airlines, "airports": airports}


def _context(family):
    tables = _tables()
    if family == "pandas":
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, frame in tables.items():
            ctx.register_table(name, frame)
        return ctx
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    ctx = DaskDataFrameAdapter(execution_engine="threaded").create_context()
    for name, frame in tables.items():
        ctx.register_table(name, dd.from_pandas(frame, npartitions=3))
    return ctx


def _rows(result):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    return materialize_rows(result)


def _query_ids():
    from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

    return [query.query_id for query in get_dataframe_queries().get_all_queries()]


def _close(left, right):
    assert len(left) == len(right)
    for left_row, right_row in zip(left, right, strict=True):
        assert len(left_row) == len(right_row)
        for a, b in zip(left_row, right_row, strict=True):
            if isinstance(a, float) or isinstance(b, float):
                if pd.isna(a) or pd.isna(b):
                    assert pd.isna(a) and pd.isna(b)
                else:
                    assert a == pytest.approx(b)
            else:
                assert (None if pd.isna(a) else a) == (None if pd.isna(b) else b)


@pytest.fixture(scope="module")
def pandas_context():
    return _context("pandas")


@pytest.fixture(scope="module")
def dask_context():
    return _context("dask")


@pytest.fixture(autouse=True)
def window():
    from benchbox.core.flightdata.dataframe_queries import set_parameter_overrides

    set_parameter_overrides({"start_date": date(2024, 1, 1), "end_date": date(2026, 1, 1)})
    yield
    set_parameter_overrides(None)


@pytest.mark.parametrize("query_id", _query_ids())
def test_pandas_impl_matches_on_dask(query_id, pandas_context, dask_context):
    from benchbox.core.flightdata.dataframe_queries import get_dataframe_queries

    query = get_dataframe_queries().get_or_raise(query_id)
    expected = _rows(query.pandas_impl(pandas_context))
    actual = _rows(query.pandas_impl(dask_context))

    assert expected
    _close(actual, expected)


def test_holiday_and_bucket_labels_cover_every_branch(pandas_context, dask_context):
    from benchbox.core.flightdata.dataframe_queries import (
        distance_delay_pandas_impl,
        holiday_impact_pandas_impl,
        recovery_time_pandas_impl,
    )

    holiday = {row[0] for row in _rows(holiday_impact_pandas_impl(dask_context))}
    assert {"Regular Day", "Christmas", "New Year", "July 4th", "Thanksgiving Week", "Memorial Day"} <= holiday
    assert len(_rows(distance_delay_pandas_impl(dask_context))) == 5
    assert len(_rows(recovery_time_pandas_impl(dask_context))) >= 4


def test_delay_causes_returns_plain_python_row_on_dask(dask_context):
    from benchbox.core.flightdata.dataframe_queries import delay_causes_pandas_impl

    result = delay_causes_pandas_impl(dask_context)

    assert list(result.columns)[0] == "carrier_delay_count"
    assert len(result) == 1
