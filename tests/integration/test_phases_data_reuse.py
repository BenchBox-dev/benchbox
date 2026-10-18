from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.benchmark_loader import get_benchmark_instance
from benchbox.core.schemas import BenchmarkConfig, SystemProfile
from benchbox.utils.datagen_version import current_datagen_stamp

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "test_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def mock_system_profile() -> SystemProfile:
    from datetime import datetime

    return SystemProfile(
        os_name="test_os",
        os_version="1.0",
        architecture="x86_64",
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=8.0,
        python_version="3.10.0",
        disk_space_gb=100.0,
        timestamp=datetime.now(),
    )


class TestPhasesDataReuse:
    def test_power_phase_reuses_existing_data(self, data_dir: Path, mock_system_profile: SystemProfile):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="power",
            compression_type="none",
        )

        import json

        fake_data = b"fake data\n"
        customer_path = data_dir / "customer.tbl"
        customer_path.write_bytes(fake_data)

        manifest = {
            "version": 2,
            "benchmark": "tpch",
            **current_datagen_stamp("tpch"),
            "scale_factor": 0.01,
            "created_at": "2025-01-01T00:00:00",
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": customer_path.stat().st_size, "row_count": 1}],
                    }
                },
            },
        }
        manifest_path = data_dir / "_datagen_manifest.json"
        with manifest_path.open("w") as f:
            json.dump(manifest, f)

        from benchbox.core.runner.runner import _ensure_data_generated

        benchmark = get_benchmark_instance(config, mock_system_profile)
        benchmark.output_dir = data_dir
        benchmark.generate_data = Mock()

        was_generated = _ensure_data_generated(benchmark, config)

        assert was_generated == (False, True), "Data should be reused, not regenerated"
        benchmark.generate_data.assert_not_called()

    def test_power_phase_generates_if_no_manifest(self, data_dir: Path, mock_system_profile: SystemProfile):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="power",
            compression_type="none",
        )

        benchmark = get_benchmark_instance(config, mock_system_profile)
        benchmark.output_dir = data_dir
        benchmark.generate_data = Mock()

        from benchbox.core.runner.runner import _ensure_data_generated

        was_generated = _ensure_data_generated(benchmark, config)

        assert was_generated == (True, False), "Data should be generated when no manifest exists"
        benchmark.generate_data.assert_called_once()

    def test_force_regenerate_ignores_manifest(self, data_dir: Path, mock_system_profile: SystemProfile):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="power",
            options={"force_regenerate": True},
            compression_type="none",
        )

        import json

        fake_data = b"fake data\n"
        customer_path = data_dir / "customer.tbl"
        customer_path.write_bytes(fake_data)

        manifest = {
            "version": 2,
            "benchmark": "tpch",
            **current_datagen_stamp("tpch"),
            "scale_factor": 0.01,
            "created_at": "2025-01-01T00:00:00",
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": customer_path.stat().st_size, "row_count": 1}],
                    }
                },
            },
        }
        manifest_path = data_dir / "_datagen_manifest.json"
        with manifest_path.open("w") as f:
            json.dump(manifest, f)

        from benchbox.core.runner.runner import _ensure_data_generated

        benchmark = get_benchmark_instance(config, mock_system_profile)
        benchmark.output_dir = data_dir
        benchmark.generate_data = Mock()

        was_generated = _ensure_data_generated(benchmark, config)

        assert was_generated == (True, False), "Data should be regenerated with force_regenerate"
        benchmark.generate_data.assert_called_once()

    def test_no_regenerate_fails_without_manifest(self, data_dir: Path, mock_system_profile: SystemProfile):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="power",
            options={"no_regenerate": True},
            compression_type="none",
        )

        benchmark = get_benchmark_instance(config, mock_system_profile)
        benchmark.output_dir = data_dir
        benchmark.generate_data = Mock()

        from benchbox.core.runner.runner import _ensure_data_generated

        with pytest.raises(RuntimeError, match="no_regenerate is set but manifest is missing"):
            _ensure_data_generated(benchmark, config)

        benchmark.generate_data.assert_not_called()

    def test_lifecycle_ensures_data_for_power_test(self, data_dir: Path, mock_system_profile: SystemProfile):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="power",
            compression_type="none",
        )

        import json

        from benchbox.core.runner.runner import LifecyclePhases, run_benchmark_lifecycle

        fake_data = b"fake data\n"
        customer_path = data_dir / "customer.tbl"
        customer_path.write_bytes(fake_data)

        manifest = {
            "version": 2,
            "benchmark": "tpch",
            **current_datagen_stamp("tpch"),
            "scale_factor": 0.01,
            "created_at": "2025-01-01T00:00:00",
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": customer_path.stat().st_size, "row_count": 1}],
                    }
                },
            },
        }
        manifest_path = data_dir / "_datagen_manifest.json"
        with manifest_path.open("w") as f:
            json.dump(manifest, f)

        phases = LifecyclePhases(generate=False, load=False, execute=True)

        benchmark = get_benchmark_instance(config, mock_system_profile)
        benchmark.output_dir = data_dir

        benchmark.generate_data = Mock(side_effect=RuntimeError("Should not generate!"))

        with patch("benchbox.core.runner.runner.get_platform_adapter") as mock_adapter_factory:
            mock_adapter = Mock()
            mock_adapter.platform_name = "test"
            mock_adapter.get_normalized_result_metadata.return_value = {}
            mock_adapter.run_benchmark = Mock(
                return_value=benchmark.create_enhanced_benchmark_result(
                    platform="test",
                    query_results=[],
                    duration_seconds=0.0,
                    phases={"power_test": {"status": "COMPLETED"}},
                    execution_metadata={"test_type": "power"},
                )
            )
            mock_adapter_factory.return_value = mock_adapter

            try:
                from benchbox.core.schemas import DatabaseConfig

                db_config = DatabaseConfig(type="duckdb", name="test")
                result = run_benchmark_lifecycle(
                    benchmark_config=config,
                    database_config=db_config,
                    system_profile=mock_system_profile,
                    phases=phases,
                    output_root=str(data_dir),
                    benchmark_instance=benchmark,
                    platform_adapter=mock_adapter,
                )

                benchmark.generate_data.assert_not_called()

                assert result is not None

            except RuntimeError as e:
                if "Should not generate!" in str(e):
                    pytest.fail("Data was regenerated when it should have been reused!")
                raise
