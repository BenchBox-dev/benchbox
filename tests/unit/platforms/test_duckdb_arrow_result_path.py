from __future__ import annotations

import warnings

import pytest

from benchbox.platforms.duckdb import _dbapi_arrow_fetcher

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_dbapi_arrow_fetch_does_not_raise_the_extra_copy_warning() -> None:
    import duckdb

    result = duckdb.connect(":memory:").execute("SELECT range AS i FROM range(3)")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _dbapi_arrow_fetcher(result)().num_rows == 3


def test_fetcher_is_absent_when_the_result_has_no_arrow_method() -> None:
    assert _dbapi_arrow_fetcher(object()) is None


def test_other_warnings_still_surface() -> None:
    class Result:
        @staticmethod
        def to_arrow_table():
            warnings.warn("something else", UserWarning, stacklevel=1)

    with pytest.warns(UserWarning, match="something else"):
        _dbapi_arrow_fetcher(Result())()
