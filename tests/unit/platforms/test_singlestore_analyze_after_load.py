from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.mysql_wire import MySqlWireLifecycleMixin
from benchbox.platforms.singlestore import SingleStoreAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _adapter() -> SingleStoreAdapter:
    with patch("benchbox.platforms.singlestore._s2", MagicMock()):
        return SingleStoreAdapter()


def test_load_data_analyzes_each_loaded_table():
    adapter = _adapter()
    connection = MagicMock()
    with (
        patch.object(
            MySqlWireLifecycleMixin,
            "load_data",
            return_value=({"store_sales": 10, "item": 3}, 1.5, {"k": "v"}),
        ),
        patch.object(adapter, "optimize_table") as optimize,
        patch.object(adapter, "analyze_table") as analyze,
    ):
        stats, seconds, details = adapter.load_data(MagicMock(), connection, "/data")

    assert stats == {"store_sales": 10, "item": 3}
    assert details == {"k": "v"}
    assert seconds >= 1.5
    assert [call.args for call in optimize.call_args_list] == [(connection, "store_sales"), (connection, "item")]
    assert [call.kwargs for call in optimize.call_args_list] == [{"flush": True}, {"flush": True}]
    assert [call.args for call in analyze.call_args_list] == [(connection, "store_sales"), (connection, "item")]


def test_load_data_with_nothing_loaded_analyzes_nothing():
    adapter = _adapter()
    with (
        patch.object(MySqlWireLifecycleMixin, "load_data", return_value=({}, 0.1, None)),
        patch.object(adapter, "optimize_table") as optimize,
        patch.object(adapter, "analyze_table") as analyze,
    ):
        adapter.load_data(MagicMock(), MagicMock(), "/data")

    optimize.assert_not_called()
    analyze.assert_not_called()
