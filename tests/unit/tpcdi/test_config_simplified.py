# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import multiprocessing
import tempfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


pytest.importorskip("pandas")

from benchbox.core.tpcdi.config import (
    TPCDIConfig,
    get_fast_config,
    get_safe_config,
    get_simple_config,
)
from benchbox.utils.path_utils import resolve_benchmark_runs_dir


@pytest.mark.unit
@pytest.mark.tpcdi
class TestTPCDIConfig:
    def test_default_config_creation(self):
        config = TPCDIConfig()

        assert config.scale_factor == 1.0
        assert config.output_dir is not None
        assert config.enable_parallel is False
        assert config.max_workers is not None
        assert config.chunk_size == 10000
        assert config.enable_validation is True
        assert config.strict_validation is False
        assert config.optimize_memory is True
        assert config.log_level == "INFO"

    def test_config_with_custom_values(self):
        output_dir = Path("/tmp/custom_output")

        config = TPCDIConfig(
            scale_factor=2.0,
            output_dir=output_dir,
            enable_parallel=True,
            max_workers=8,
            chunk_size=5000,
            enable_validation=False,
            strict_validation=True,
            optimize_memory=False,
            log_level="DEBUG",
        )

        assert config.scale_factor == 2.0
        assert config.output_dir == output_dir
        assert config.enable_parallel is True
        assert config.max_workers == 8
        assert config.chunk_size == 5000
        assert config.enable_validation is False
        assert config.strict_validation is True
        assert config.optimize_memory is False
        assert config.log_level == "DEBUG"

    def test_config_post_init_validation(self):
        config = TPCDIConfig()
        assert config.output_dir == resolve_benchmark_runs_dir() / "datagen" / "tpcdi_sf1"

        config = TPCDIConfig(max_workers=0)
        assert config.max_workers == 1

        config = TPCDIConfig(max_workers=20)
        assert config.max_workers == 16

        config = TPCDIConfig(chunk_size=500)
        assert config.chunk_size == 1000

        config = TPCDIConfig(chunk_size=100000)
        assert config.chunk_size == 50000

        config = TPCDIConfig(scale_factor=0)
        assert config.scale_factor == 1.0

        config = TPCDIConfig(scale_factor=-1)
        assert config.scale_factor == 1.0

    def test_config_parallel_settings(self):
        config = TPCDIConfig(enable_parallel=False)
        assert config.max_workers == 1

        config = TPCDIConfig(enable_parallel=True)
        cpu_count = multiprocessing.cpu_count()
        expected_max = min(8, cpu_count + 2)
        assert config.max_workers == expected_max

        config = TPCDIConfig(enable_parallel=True, max_workers=4)
        assert config.max_workers == 4

    def test_get_streaming_config(self):
        config = TPCDIConfig(chunk_size=5000, max_workers=4, enable_parallel=True)

        streaming_config = config.get_streaming_config()

        assert streaming_config["chunk_size"] == 5000
        assert streaming_config["max_workers"] == 4
        assert streaming_config["enable_parallel"] is True
        assert streaming_config["buffer_size"] == 8192

    def test_get_validation_config(self):
        config = TPCDIConfig(enable_validation=True, strict_validation=True)

        validation_config = config.get_validation_config()

        assert validation_config["strict_mode"] is True
        assert validation_config["max_violations_per_rule"] == 1000
        assert validation_config["enable_cross_table_validation"] is True
        assert validation_config["enable_performance_monitoring"] is True

    def test_get_etl_config(self):
        config = TPCDIConfig(enable_parallel=True, max_workers=6, chunk_size=8000, optimize_memory=True)

        etl_config = config.get_etl_config()

        assert etl_config["enable_parallel_extract"] is True
        assert etl_config["enable_parallel_transform"] is True
        assert etl_config["enable_parallel_load"] is True
        assert etl_config["chunk_size"] == 8000
        assert etl_config["max_workers"] == 6
        assert etl_config["worker_timeout"] == 300.0
        assert etl_config["memory_optimization"] is True

    def test_get_batch_config(self):
        config = TPCDIConfig(
            enable_parallel=True,
            max_workers=4,
            chunk_size=6000,
            enable_validation=True,
            strict_validation=False,
        )

        batch_config = config.get_batch_config()

        assert batch_config["parallel_processing"] is True
        assert batch_config["max_workers"] == 4
        assert batch_config["chunk_size"] == 6000
        assert batch_config["enable_validation"] is True
        assert batch_config["strict_mode"] is False

    def test_create_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "test_output"
            config = TPCDIConfig(output_dir=output_dir)

            config.create_directories()

            assert output_dir.exists()
            assert (output_dir / "source").exists()
            assert (output_dir / "staging").exists()
            assert (output_dir / "warehouse").exists()
            assert (output_dir / "logs").exists()

    def test_get_performance_profile(self):
        config = TPCDIConfig(enable_parallel=False)
        assert config.get_performance_profile() == "single_threaded"

        config = TPCDIConfig(enable_parallel=True, max_workers=1)
        assert config.get_performance_profile() == "light"

        config = TPCDIConfig(enable_parallel=True, max_workers=3)
        assert config.get_performance_profile() == "standard"

        config = TPCDIConfig(enable_parallel=True, max_workers=6)
        assert config.get_performance_profile() == "heavy"

        config = TPCDIConfig(enable_parallel=True, max_workers=10)
        assert config.get_performance_profile() == "maximum"

    def test_adjust_for_scale_factor(self):
        config = TPCDIConfig(scale_factor=0.1, chunk_size=2000, max_workers=8)
        config.adjust_for_scale_factor()

        assert config.chunk_size == 1000
        assert config.max_workers == 4

        config = TPCDIConfig(scale_factor=5.0, chunk_size=10000, max_workers=8)
        config.adjust_for_scale_factor()

        assert config.chunk_size == 12000
        assert config.max_workers == 9

        config = TPCDIConfig(scale_factor=10.0, chunk_size=10000, max_workers=8)
        config.adjust_for_scale_factor()

        assert config.chunk_size == 15000
        assert config.max_workers == 10
        assert config.optimize_memory is True

    def test_get_memory_settings(self):
        config = TPCDIConfig(optimize_memory=True)
        memory_settings = config.get_memory_settings()

        assert memory_settings["enable_chunked_processing"] is True
        assert memory_settings["clear_cache_between_chunks"] is True
        assert memory_settings["use_memory_efficient_dtypes"] is True
        assert memory_settings["limit_concurrent_operations"] is True

        config = TPCDIConfig(optimize_memory=False)
        memory_settings = config.get_memory_settings()

        assert memory_settings["enable_chunked_processing"] is False
        assert memory_settings["clear_cache_between_chunks"] is False
        assert memory_settings["use_memory_efficient_dtypes"] is False
        assert memory_settings["limit_concurrent_operations"] is False

    def test_to_dict(self):
        config = TPCDIConfig(
            scale_factor=2.0,
            enable_parallel=True,
            max_workers=4,
            chunk_size=5000,
            enable_validation=False,
            strict_validation=True,
            optimize_memory=False,
            log_level="DEBUG",
        )

        config_dict = config.to_dict()

        assert config_dict["scale_factor"] == 2.0
        assert config_dict["enable_parallel"] is True
        assert config_dict["max_workers"] == 4
        assert config_dict["chunk_size"] == 5000
        assert config_dict["enable_validation"] is False
        assert config_dict["strict_validation"] is True
        assert config_dict["optimize_memory"] is False
        assert config_dict["log_level"] == "DEBUG"
        assert "output_dir" in config_dict
        assert "performance_profile" in config_dict

    def test_from_dict(self):
        config_dict = {
            "scale_factor": 3.0,
            "output_dir": "/tmp/test_output",
            "enable_parallel": True,
            "max_workers": 6,
            "chunk_size": 8000,
            "enable_validation": False,
            "strict_validation": True,
            "optimize_memory": False,
            "log_level": "WARNING",
        }

        config = TPCDIConfig.from_dict(config_dict)

        assert config.scale_factor == 3.0
        assert config.output_dir == Path("/tmp/test_output")
        assert config.enable_parallel is True
        assert config.max_workers == 6
        assert config.chunk_size == 8000
        assert config.enable_validation is False
        assert config.strict_validation is True
        assert config.optimize_memory is False
        assert config.log_level == "WARNING"

    def test_from_dict_with_unknown_keys(self):
        config_dict = {
            "scale_factor": 2.0,
            "unknown_key": "value",
            "another_unknown": 123,
        }

        config = TPCDIConfig.from_dict(config_dict)

        assert config.scale_factor == 2.0
        assert config.enable_parallel is False
        assert not hasattr(config, "unknown_key")

    def test_for_development(self):
        config = TPCDIConfig.for_development()

        assert config.scale_factor == 0.1
        assert config.enable_parallel is False
        assert config.max_workers == 1
        assert config.chunk_size == 1000
        assert config.enable_validation is True
        assert config.strict_validation is False
        assert config.optimize_memory is False
        assert config.log_level == "DEBUG"

    def test_for_production(self):
        config = TPCDIConfig.for_production(scale_factor=5.0)

        assert config.scale_factor == 5.0
        assert config.enable_parallel is False
        assert config.chunk_size >= 15000
        assert config.enable_validation is True
        assert config.strict_validation is True
        assert config.optimize_memory is True
        assert config.log_level == "INFO"

    def test_for_performance_testing(self):
        config = TPCDIConfig.for_performance_testing(scale_factor=10.0)

        assert config.scale_factor == 10.0
        assert config.enable_parallel is False
        assert config.max_workers == multiprocessing.cpu_count()
        assert config.chunk_size == 25000
        assert config.enable_validation is False
        assert config.strict_validation is False
        assert config.optimize_memory is True
        assert config.log_level == "WARNING"


@pytest.mark.unit
@pytest.mark.tpcdi
class TestConfigConvenienceFunctions:
    def test_get_simple_config(self):
        config = get_simple_config(scale_factor=2.0, parallel=True, validation=False)

        assert config.scale_factor == 2.0
        assert config.enable_parallel is True
        assert config.enable_validation is False

        config = get_simple_config()
        assert config.scale_factor == 1.0
        assert config.enable_parallel is True
        assert config.enable_validation is True

    def test_get_fast_config(self):
        config = get_fast_config(scale_factor=3.0)

        assert config.scale_factor == 3.0
        assert config.enable_parallel is False
        assert config.enable_validation is False
        assert config.strict_validation is False
        assert config.optimize_memory is False

        config = get_fast_config()
        assert config.scale_factor == 1.0

    def test_get_safe_config(self):
        config = get_safe_config(scale_factor=4.0)

        assert config.scale_factor == 4.0
        assert config.enable_parallel is False
        assert config.enable_validation is True
        assert config.strict_validation is True
        assert config.optimize_memory is True

        config = get_safe_config()
        assert config.scale_factor == 1.0


@pytest.mark.unit
@pytest.mark.tpcdi
class TestConfigLogging:
    def test_log_level_configuration(self):
        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        for level in log_levels:
            for handler in logging.root.handlers[:]:
                logging.root.removeHandler(handler)
            logging.root.setLevel(logging.NOTSET)

            config = TPCDIConfig(log_level=level)
            assert config.log_level == level

            logger = logging.getLogger()
            assert logger.level <= getattr(logging, level)

    def test_invalid_log_level(self):
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.root.setLevel(logging.NOTSET)

        TPCDIConfig(log_level="INVALID")

        logger = logging.getLogger()
        assert logger.level == logging.INFO

    def test_log_level_case_insensitive(self):
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.root.setLevel(logging.NOTSET)

        config = TPCDIConfig(log_level="debug")
        assert config.log_level == "debug"

        logger = logging.getLogger()
        assert logger.level <= logging.DEBUG


@pytest.mark.integration
@pytest.mark.tpcdi
class TestConfigIntegration:
    def test_config_roundtrip_serialization(self):
        original_config = TPCDIConfig(
            scale_factor=5.0,
            enable_parallel=True,
            max_workers=8,
            chunk_size=15000,
            enable_validation=False,
            strict_validation=True,
            optimize_memory=True,
            log_level="WARNING",
        )

        config_dict = original_config.to_dict()

        restored_config = TPCDIConfig.from_dict(config_dict)

        assert restored_config.scale_factor == original_config.scale_factor
        assert restored_config.enable_parallel == original_config.enable_parallel
        assert restored_config.max_workers == original_config.max_workers
        assert restored_config.chunk_size == original_config.chunk_size
        assert restored_config.enable_validation == original_config.enable_validation
        assert restored_config.strict_validation == original_config.strict_validation
        assert restored_config.optimize_memory == original_config.optimize_memory
        assert restored_config.log_level == original_config.log_level

    def test_config_with_directory_operations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "tpcdi_test"

            config = TPCDIConfig(output_dir=output_dir)

            config.create_directories()

            assert output_dir.exists()
            assert (output_dir / "source").exists()
            assert (output_dir / "staging").exists()
            assert (output_dir / "warehouse").exists()
            assert (output_dir / "logs").exists()

            assert output_dir.is_dir()
            assert (output_dir / "source").is_dir()

    def test_config_performance_profiles(self):
        profiles = [
            (TPCDIConfig(enable_parallel=False), "single_threaded"),
            (TPCDIConfig(enable_parallel=True, max_workers=2), "light"),
            (TPCDIConfig(enable_parallel=True, max_workers=4), "standard"),
            (TPCDIConfig(enable_parallel=True, max_workers=8), "heavy"),
            (TPCDIConfig(enable_parallel=True, max_workers=16), "maximum"),
        ]

        for config, expected_profile in profiles:
            assert config.get_performance_profile() == expected_profile

    def test_config_scale_adjustments(self):
        scale_factors = [0.1, 1.0, 5.0, 10.0, 20.0]

        for scale in scale_factors:
            config = TPCDIConfig(scale_factor=scale)

            config.adjust_for_scale_factor()

            assert config.chunk_size >= 1000
            assert config.chunk_size <= 50000
            assert config.max_workers >= 1
            assert config.max_workers <= 16

            if scale >= 10.0:
                assert config.optimize_memory is True

    def test_config_with_all_convenience_functions(self):
        configs = [
            get_simple_config(),
            get_fast_config(),
            get_safe_config(),
            TPCDIConfig.for_development(),
            TPCDIConfig.for_production(),
            TPCDIConfig.for_performance_testing(),
        ]

        for config in configs:
            assert isinstance(config, TPCDIConfig)
            assert config.scale_factor > 0
            assert config.chunk_size >= 1000
            assert config.max_workers >= 1
            assert config.output_dir is not None

            assert isinstance(config.get_streaming_config(), dict)
            assert isinstance(config.get_validation_config(), dict)
            assert isinstance(config.get_etl_config(), dict)
            assert isinstance(config.get_batch_config(), dict)
            assert isinstance(config.get_memory_settings(), dict)
            assert isinstance(config.get_performance_profile(), str)
            assert isinstance(config.to_dict(), dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


