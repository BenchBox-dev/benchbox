# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import inspect
from typing import Any
from unittest.mock import Mock

import pytest

import benchbox

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestConnectionInterfaceConsistency:
    def test_tpc_benchmarks_use_connection_objects(self):

        tpch = benchbox.TPCH()

        if hasattr(tpch, "run_power_test"):
            power_test_sig = inspect.signature(tpch.run_power_test)
            params = list(power_test_sig.parameters.values())
            assert params[0].name in ["connection_factory", "connection"]

        if hasattr(tpch, "run_maintenance_test"):
            maintenance_test_sig = inspect.signature(tpch.run_maintenance_test)
            params = list(maintenance_test_sig.parameters.values())
            assert params[0].name in ["connection_factory", "connection"]

        tpcds = benchbox.TPCDS()

        if hasattr(tpcds, "run_power_test"):
            power_test_sig = inspect.signature(tpcds.run_power_test)
            params = list(power_test_sig.parameters.values())
            assert params[0].name == "connection"
            assert params[0].annotation == Any

        if hasattr(tpcds, "run_throughput_test"):
            throughput_test_sig = inspect.signature(tpcds.run_throughput_test)
            params = list(throughput_test_sig.parameters.values())
            assert params[0].name == "connection"
            assert params[0].annotation == Any

        if hasattr(tpcds, "run_maintenance_test"):
            maintenance_test_sig = inspect.signature(tpcds.run_maintenance_test)
            params = list(maintenance_test_sig.parameters.values())
            assert params[0].name == "connection"
            assert params[0].annotation == Any

    def test_no_connection_string_parameters_in_tpc_benchmarks(self):

        tpch = benchbox.TPCH()

        methods_to_check = [
            "run_power_test",
            "run_maintenance_test",
        ]

        for method_name in methods_to_check:
            if hasattr(tpch, method_name):
                method = getattr(tpch, method_name)
                sig = inspect.signature(method)

                param_names = [param.name for param in sig.parameters.values()]
                assert "connection_string" not in param_names, f"Found connection_string parameter in {method_name}"

                for param in sig.parameters.values():
                    if "connection" in param.name.lower():
                        assert param.annotation != str, (
                            f"Connection parameter {param.name} should not have str annotation in {method_name}"
                        )

        tpcds = benchbox.TPCDS()

        methods_to_check_tpcds = [
            "run_power_test",
            "run_throughput_test",
            "run_maintenance_test",
        ]
        for method_name in methods_to_check_tpcds:
            if hasattr(tpcds, method_name):
                method = getattr(tpcds, method_name)
                sig = inspect.signature(method)

                param_names = [param.name for param in sig.parameters.values()]
                assert "connection_string" not in param_names, f"Found connection_string parameter in {method_name}"

                for param in sig.parameters.values():
                    if "connection" in param.name.lower():
                        assert param.annotation != str, (
                            f"Connection parameter {param.name} should not have str annotation in {method_name}"
                        )

    def test_tpc_benchmarks_accept_connection_objects_functionally(self):

        mock_connection = Mock()
        mock_connection.execute = Mock()
        mock_connection.fetchall = Mock(return_value=[])
        mock_connection.commit = Mock()
        mock_connection.close = Mock()

        tpch = benchbox.TPCH()

        if hasattr(tpch, "run_power_test"):
            try:
                tpch.run_power_test(mock_connection)
            except Exception as e:
                assert "connection_string" not in str(e).lower()

        tpcds = benchbox.TPCDS()

        if hasattr(tpcds, "run_power_test"):
            try:
                tpcds.run_power_test(mock_connection, seed=42)
            except Exception as e:
                assert "connection_string" not in str(e).lower()

    def test_non_tpc_benchmarks_consistent_interface(self):

        primitives = benchbox.ReadPrimitives()
        if hasattr(primitives, "run"):
            sig = inspect.signature(primitives.run)
            params = list(sig.parameters.values())
            if len(params) > 0 and "connection" in params[0].name.lower():
                assert params[0].annotation != str, "Connection parameter should not be str type"

    def test_platform_adapter_delegation_compatibility(self):

        from benchbox.platforms.base import PlatformAdapter

        adapter_methods = [
            "run_power_test",
            "run_throughput_test",
            "run_maintenance_test",
        ]

        for method_name in adapter_methods:
            if hasattr(PlatformAdapter, method_name):
                method = getattr(PlatformAdapter, method_name)
                sig = inspect.signature(method)
                params = list(sig.parameters.values())

                assert len(params) >= 3, f"{method_name} should have at least self, benchmark, and **kwargs"
                assert params[0].name == "self"
                assert params[1].name == "benchmark"

                has_kwargs = any(param.kind == param.VAR_KEYWORD for param in params)
                assert has_kwargs, f"{method_name} should accept **kwargs for connection delegation"

    def test_database_connection_wrapper_consistency(self):

        from benchbox.core.connection import DatabaseConnection

        sig = inspect.signature(DatabaseConnection.__init__)
        params = list(sig.parameters.values())

        connection_param = params[1]
        assert connection_param.name == "connection"
        assert connection_param.annotation != str


@pytest.mark.unit
class TestConnectionInterfaceRegression:
    def test_no_connection_string_creation_in_tpc_benchmarks(self):

        import inspect

        import benchbox.core.tpcds.benchmark as tpcds_module
        import benchbox.core.tpch.benchmark as tpch_module

        tpcds_source = inspect.getsource(tpcds_module)

        assert "_DatabaseConnection(connection_string)" not in tpcds_source
        assert "DatabaseConnection(connection_string)" not in tpcds_source

        tpch_source = inspect.getsource(tpch_module)

        assert "_DatabaseConnection(connection_string)" not in tpch_source
        assert "DatabaseConnection(connection_string)" not in tpch_source

    def test_tpc_modules_import_without_connection_string_usage(self):

        try:
            from benchbox.core.tpch.power_test import TPCHPowerTest

            sig = inspect.signature(TPCHPowerTest.__init__)
            params = list(sig.parameters.values())

            param_names = [p.name for p in params]
            assert "connection_string" not in param_names
            assert "connection" in param_names

            connection_param = next((p for p in params if p.name == "connection"), None)
            if connection_param:
                assert connection_param.annotation != str

        except ImportError:
            pass

        try:
            from benchbox.core.tpch.throughput_test import TPCHThroughputTest

            sig = inspect.signature(TPCHThroughputTest.__init__)
            params = list(sig.parameters.values())

            param_names = [p.name for p in params]
            assert "connection_string" not in param_names
            assert "connection_factory" in param_names

            connection_param = next((p for p in params if p.name == "connection_factory"), None)
            if connection_param:
                assert connection_param.annotation != str

        except ImportError:
            pass

        try:
            from benchbox.core.tpch.maintenance_test import MaintenanceTest

            sig = inspect.signature(MaintenanceTest.__init__)
            params = list(sig.parameters.values())

            param_names = [p.name for p in params]
            assert "connection_string" not in param_names

            connection_param = next((p for p in params if p.name == "connection"), None)
            if connection_param:
                assert connection_param.annotation != str

        except ImportError:
            pass

    def test_cli_orchestrator_uses_connection_objects(self):

        try:
            from benchbox.cli.orchestrator import BenchmarkOrchestrator

            BenchmarkOrchestrator()

            import inspect

            orchestrator_source = inspect.getsource(BenchmarkOrchestrator)

            problematic_patterns = [
                "connection_string=",
                "connection=connection_string",
                "_DatabaseConnection(connection_string)",
            ]

            for pattern in problematic_patterns:
                assert pattern not in orchestrator_source, f"Found problematic pattern: {pattern}"

        except ImportError:
            pass


if __name__ == "__main__":
    pytest.main(["-v", __file__])


