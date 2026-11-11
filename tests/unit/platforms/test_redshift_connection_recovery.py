from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_adapter(**kwargs):

    try:
        from benchbox.platforms.redshift import RedshiftAdapter
    except ImportError:
        pytest.skip("Redshift drivers not installed")

    defaults = {
        "host": "test-cluster.redshift.amazonaws.com",
        "database": "test_db",
        "username": "test_user",
        "password": "test_pass",
        "strict_validation": False,
    }
    defaults.update(kwargs)
    return RedshiftAdapter(**defaults)


def _make_smart_cursor(*, tables=None, fail_on=None):

    cursor = Mock()
    cursor.fetchall.return_value = tables or []
    cursor.fetchone.return_value = ("off",)

    fail_on = fail_on or set()

    def execute_side_effect(sql, *args, **kwargs):
        sql_upper = sql.strip().upper()
        for prefix in fail_on:
            if sql_upper.startswith(prefix):
                raise TimeoutError("The read operation timed out")

    cursor.execute.side_effect = execute_side_effect
    return cursor


class TestVacuumAnalyzeConnectionIsolation:
    @pytest.fixture(autouse=True)
    def _skip_cluster_state_check(self):

        from benchbox.platforms.redshift import RedshiftAdapter

        with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=10):
            yield

    def test_vacuum_timeout_does_not_affect_main_connection(self):

        adapter = _make_adapter(auto_vacuum=True, auto_analyze=False)

        main_conn = Mock()
        main_cursor = _make_smart_cursor(tables=[("public", "orders")])
        main_conn.cursor.return_value = main_cursor

        maint_conn = Mock()
        maint_conn.autocommit = True
        maint_cursor = _make_smart_cursor(fail_on={"VACUUM"})
        maint_conn.cursor.return_value = maint_cursor

        with patch.object(adapter, "_connect_with_driver", return_value=maint_conn):
            adapter.configure_for_benchmark(main_conn, "tpch")

        main_execute_calls = [str(c) for c in main_cursor.execute.call_args_list]
        assert not any("VACUUM" in c for c in main_execute_calls)

        maint_conn.close.assert_called()

    def test_successful_vacuum_analyze_uses_separate_connection(self):

        adapter = _make_adapter(auto_vacuum=True, auto_analyze=True)

        main_conn = Mock()
        main_cursor = _make_smart_cursor(tables=[("public", "nation")])
        main_conn.cursor.return_value = main_cursor

        maint_conn = Mock()
        maint_conn.autocommit = True
        maint_cursor = _make_smart_cursor()
        maint_conn.cursor.return_value = maint_cursor

        with patch.object(adapter, "_connect_with_driver", return_value=maint_conn):
            adapter.configure_for_benchmark(main_conn, "tpch")

        maint_execute_calls = [str(c) for c in maint_cursor.execute.call_args_list]
        assert any("VACUUM" in c for c in maint_execute_calls)
        assert any("ANALYZE" in c for c in maint_execute_calls)

        main_execute_calls = [str(c) for c in main_cursor.execute.call_args_list]
        assert not any("VACUUM" in c for c in main_execute_calls)
        assert not any("ANALYZE" in c for c in main_execute_calls)

    def test_vacuum_timeout_aborts_remaining_tables(self):

        adapter = _make_adapter(auto_vacuum=True, auto_analyze=True)

        main_conn = Mock()
        main_cursor = _make_smart_cursor(tables=[("public", "customer"), ("public", "orders"), ("public", "lineitem")])
        main_conn.cursor.return_value = main_cursor

        maint_conn = Mock()
        maint_conn.autocommit = True
        maint_cursor = _make_smart_cursor(fail_on={"VACUUM"})
        maint_conn.cursor.return_value = maint_cursor

        with patch.object(adapter, "_connect_with_driver", return_value=maint_conn):
            adapter.configure_for_benchmark(main_conn, "tpch")

        vacuum_calls = [c for c in maint_cursor.execute.call_args_list if "VACUUM" in str(c)]
        assert len(vacuum_calls) == 1

    def test_maintenance_connection_failure_is_nonfatal(self):

        adapter = _make_adapter(auto_vacuum=True, auto_analyze=True)

        main_conn = Mock()
        main_cursor = _make_smart_cursor(tables=[("public", "nation")])
        main_conn.cursor.return_value = main_cursor

        with patch.object(adapter, "_connect_with_driver", side_effect=Exception("connection refused")):
            adapter.configure_for_benchmark(main_conn, "tpch")

    def test_no_tables_skips_maintenance_connection(self):

        adapter = _make_adapter(auto_vacuum=True, auto_analyze=True)

        main_conn = Mock()
        main_cursor = _make_smart_cursor(tables=[])
        main_conn.cursor.return_value = main_cursor

        with patch.object(adapter, "_connect_with_driver") as mock_connect:
            adapter.configure_for_benchmark(main_conn, "tpch")
            mock_connect.assert_not_called()

    def test_maintenance_connection_uses_long_running_timeout(self):

        adapter = _make_adapter(auto_vacuum=True, auto_analyze=True)

        main_conn = Mock()
        main_cursor = _make_smart_cursor(tables=[("public", "nation")])
        main_conn.cursor.return_value = main_cursor

        maint_conn = Mock()
        maint_conn.autocommit = True
        maint_conn.cursor.return_value = _make_smart_cursor()

        with (
            patch.object(adapter, "_long_running_timeout", return_value=321) as mock_timeout,
            patch.object(adapter, "_connect_with_driver", return_value=maint_conn) as mock_connect,
        ):
            adapter.configure_for_benchmark(main_conn, "tpch")

        mock_timeout.assert_called_once_with()
        mock_connect.assert_called_once_with(
            application_name="BenchBox-Maintenance",
            connect_timeout=321,
        )

    def test_vacuum_disabled_only_runs_analyze(self):

        adapter = _make_adapter(auto_vacuum=False, auto_analyze=True)

        main_conn = Mock()
        main_cursor = _make_smart_cursor(tables=[("public", "region")])
        main_conn.cursor.return_value = main_cursor

        maint_conn = Mock()
        maint_conn.autocommit = True
        maint_cursor = _make_smart_cursor()
        maint_conn.cursor.return_value = maint_cursor

        with patch.object(adapter, "_connect_with_driver", return_value=maint_conn):
            adapter.configure_for_benchmark(main_conn, "tpch")

        maint_execute_calls = [str(c) for c in maint_cursor.execute.call_args_list]
        assert not any("VACUUM" in c for c in maint_execute_calls)
        assert any("ANALYZE" in c for c in maint_execute_calls)


