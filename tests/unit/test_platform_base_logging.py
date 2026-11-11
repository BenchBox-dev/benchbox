# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import unittest
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class ConcretePlatformAdapter(PlatformAdapter):
    @property
    def platform_name(self) -> str:
        return "test_platform"

    def add_cli_arguments(self, parser):
        pass

    @classmethod
    def from_config(cls, config):
        return cls(**config)

    def create_connection(self, **kwargs):
        return MagicMock()

    def create_schema(self, benchmark, connection):
        pass

    def load_data(self, benchmark, connection, data_dir):
        pass

    def configure_for_benchmark(self, connection, benchmark_type):
        pass

    def execute_query(self, connection, query, query_id):
        pass

    def apply_platform_optimizations(self, platform_config, connection):
        pass

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        pass

    def get_target_dialect(self) -> str:
        return "duckdb"


class TestPlatformBaseLogging(unittest.TestCase):
    def test_verbosity_disabled(self):

        adapter = ConcretePlatformAdapter(verbose_enabled=False, very_verbose=False)
        adapter.logger = MagicMock(spec=logging.Logger)

        adapter.log_verbose("Test info message.")
        adapter.log_very_verbose("Test debug message.")

        adapter.logger.info.assert_not_called()
        adapter.logger.debug.assert_not_called()

    def test_level_one_verbosity_v(self):

        adapter = ConcretePlatformAdapter(verbose_enabled=True, very_verbose=False)
        adapter.logger = MagicMock(spec=logging.Logger)

        adapter.log_verbose("Test info message.")
        adapter.log_very_verbose("Test debug message.")

        adapter.logger.info.assert_called_once_with("Test info message.")
        adapter.logger.debug.assert_not_called()

    def test_level_two_verbosity_vv(self):

        adapter = ConcretePlatformAdapter(verbose_enabled=True, very_verbose=True)
        adapter.logger = MagicMock(spec=logging.Logger)

        adapter.log_verbose("Test info message.")
        adapter.log_very_verbose("Test debug message.")

        adapter.logger.info.assert_called_once_with("Test info message.")
        adapter.logger.debug.assert_called_once_with("Test debug message.")

    def test_operation_start_logging(self):

        adapter_v = ConcretePlatformAdapter(verbose_enabled=True, very_verbose=False)
        adapter_v.logger = MagicMock(spec=logging.Logger)
        adapter_v.log_operation_start("Op1", "Details1")
        adapter_v.logger.info.assert_called_once_with("Starting Op1")

        adapter_vv = ConcretePlatformAdapter(verbose_enabled=True, very_verbose=True)
        adapter_vv.logger = MagicMock(spec=logging.Logger)
        adapter_vv.log_operation_start("Op2", "Details2")
        adapter_vv.logger.debug.assert_called_once_with("Starting Op2: Details2")

    def test_operation_complete_logging(self):

        adapter_v = ConcretePlatformAdapter(verbose_enabled=True, very_verbose=False)
        adapter_v.logger = MagicMock(spec=logging.Logger)
        adapter_v.log_operation_complete("Op1", duration=3.14)
        adapter_v.logger.info.assert_called_once_with("✓ Op1 completed in 3.14s")

        adapter_vv = ConcretePlatformAdapter(verbose_enabled=True, very_verbose=True)
        adapter_vv.logger = MagicMock(spec=logging.Logger)
        adapter_vv.log_operation_complete("Op2", duration=1.23, details="Result")
        adapter_vv.logger.debug.assert_called_once_with("Completed Op2 in 1.23s: Result")

        adapter_vv_no_duration = ConcretePlatformAdapter(verbose_enabled=True, very_verbose=True)
        adapter_vv_no_duration.logger = MagicMock(spec=logging.Logger)
        adapter_vv_no_duration.log_operation_complete("Op3", details="Result3")
        adapter_vv_no_duration.logger.debug.assert_called_once_with("Completed Op3: Result3")


if __name__ == "__main__":
    unittest.main()


