# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAdapterDryRunFlag:
    def _make_adapter(self, **config):

        from benchbox.platforms.base.adapter import PlatformAdapter

        class StubAdapter(PlatformAdapter):
            platform_name = "stub"
            supports_external_tables = False

            @staticmethod
            def add_cli_arguments(parser):
                pass

            @staticmethod
            def get_target_dialect():
                return "standard"

            def connect(self, **kwargs):
                pass

            def disconnect(self):
                pass

            def execute_query(self, sql, **kwargs):
                pass

            def create_database(self, **kwargs):
                pass

            def create_tables(self, benchmark, connection=None, data_dir=None):
                pass

            def load_data(self, benchmark, connection=None, data_dir=None):
                pass

            def check_database_exists(self, **kwargs):
                return True

            def get_database_path(self, **kwargs):
                return ":memory:"

            def create_connection(self, **kwargs):
                pass

            def create_schema(self, benchmark, connection=None):
                pass

            @classmethod
            def from_config(cls, **kwargs):
                return cls(**kwargs)

            def configure_for_benchmark(self, benchmark_name, scale_factor=None, **kwargs):
                pass

            def apply_platform_optimizations(self, config, connection=None):
                pass

            def apply_constraint_configuration(self, config, benchmark=None, connection=None):
                pass

        return StubAdapter(**config)

    def test_dry_run_defaults_to_false(self):
        adapter = self._make_adapter()
        assert adapter.dry_run is False

    def test_dry_run_stored_from_config(self):
        adapter = self._make_adapter(dry_run=True)
        assert adapter.dry_run is True

    def test_handle_existing_database_skips_when_dry_run(self):
        adapter = self._make_adapter(dry_run=True)

        with patch.object(adapter, "check_database_exists") as mock_check:
            adapter.handle_existing_database(database="test")
            mock_check.assert_not_called()

    def test_capture_mode_skips_existing_database_validation(self):
        adapter = self._make_adapter(dry_run=False)
        adapter.enable_dry_run()

        with patch.object(adapter, "check_database_exists") as mock_check:
            adapter.handle_existing_database(database="test")
            mock_check.assert_not_called()

        adapter.disable_dry_run()
        with patch.object(adapter, "check_database_exists", return_value=False) as mock_check:
            adapter.handle_existing_database(database="test")
            mock_check.assert_called_once()

    def test_handle_existing_database_runs_when_not_dry_run(self):
        adapter = self._make_adapter(dry_run=False)

        with patch.object(adapter, "check_database_exists", return_value=False) as mock_check:
            adapter.handle_existing_database(database="test")
            mock_check.assert_called_once()

    def test_is_dry_run_property_and_synchronization(self):
        adapter = self._make_adapter(dry_run=False)
        assert adapter.dry_run is False
        assert adapter.dry_run_mode is False
        assert adapter.is_dry_run is False

        adapter.enable_dry_run()
        assert adapter.dry_run is True
        assert adapter.dry_run_mode is True
        assert adapter.is_dry_run is True

        adapter.disable_dry_run()
        assert adapter.dry_run is False
        assert adapter.dry_run_mode is False
        assert adapter.is_dry_run is False

    def test_enable_dry_run_preserves_initial_true_config_on_disable(self):
        adapter = self._make_adapter(dry_run=True)
        assert adapter.dry_run is True
        assert adapter.is_dry_run is True

        adapter.enable_dry_run()
        assert adapter.dry_run is True
        assert adapter.is_dry_run is True

        adapter.disable_dry_run()
        assert adapter.dry_run is True
        assert adapter.is_dry_run is True

    def test_disable_dry_run_resets_connection_default_job_config(self):
        adapter = self._make_adapter(dry_run=False)
        adapter.enable_dry_run()

        mock_conn = Mock()
        mock_conn._default_job_config = Mock()
        mock_conn._default_job_config.dry_run = True

        adapter.disable_dry_run(mock_conn)
        assert adapter.dry_run is False
        assert mock_conn._default_job_config.dry_run is False


