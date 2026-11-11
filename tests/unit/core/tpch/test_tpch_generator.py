from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.tpch.generator import (
    _TPCH_BASE_ROW_COUNTS,
    _TPCH_TABLE_CODES,
    TPCHDataGenerator,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCHGeneratorInit:
    def test_default_params(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=1.0, output_dir=tmp_path, quiet=True)
        assert gen.scale_factor == 1.0
        assert gen.output_dir == tmp_path
        assert gen.parallel == 1
        assert gen.force_regenerate is False

    def test_custom_output_dir_as_string(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=str(tmp_path), quiet=True)
        assert gen.output_dir == tmp_path

    def test_verbose_levels(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, verbose=2)
        assert gen.very_verbose is True

    def test_quiet_mode(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        assert gen.verbose_enabled is False

    def test_validator_initialized(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        assert gen.validator is not None

    def test_dbgen_exe_deferred(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        assert gen._dbgen_exe is None


class TestParameterValidation:
    def test_zero_scale_factor_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="positive"):
            TPCHDataGenerator(scale_factor=0, output_dir=tmp_path, quiet=True)

    def test_negative_scale_factor_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="positive"):
            TPCHDataGenerator(scale_factor=-1.0, output_dir=tmp_path, quiet=True)

    def test_too_small_scale_factor_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="too small"):
            TPCHDataGenerator(scale_factor=0.001, output_dir=tmp_path, quiet=True)

    def test_too_large_scale_factor_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="too large"):
            TPCHDataGenerator(scale_factor=200000, output_dir=tmp_path, quiet=True)

    def test_parallel_below_one_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match=">="):
            TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, parallel=0, quiet=True)

    def test_parallel_above_max_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Too many"):
            TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, parallel=65, quiet=True)

    def test_valid_edge_params(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, parallel=64, quiet=True)
        assert gen.scale_factor == 0.01
        assert gen.parallel == 64


class TestExpectedRowCount:
    def test_region_is_constant(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=10.0, output_dir=tmp_path, quiet=True)
        assert gen._expected_row_count("region") == 5

    def test_nation_is_constant(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=10.0, output_dir=tmp_path, quiet=True)
        assert gen._expected_row_count("nation") == 25

    @pytest.mark.parametrize("table", ["customer", "lineitem", "orders", "part", "partsupp", "supplier"])
    def test_scales_with_factor(self, tmp_path: Path, table: str):
        gen = TPCHDataGenerator(scale_factor=2.0, output_dir=tmp_path, quiet=True)
        expected = int(_TPCH_BASE_ROW_COUNTS[table] * 2.0)
        assert gen._expected_row_count(table) == expected

    def test_unknown_table_returns_zero(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=1.0, output_dir=tmp_path, quiet=True)
        assert gen._expected_row_count("nonexistent") == 0


class TestConstants:
    def test_table_codes_has_eight_tables(self):
        assert len(_TPCH_TABLE_CODES) == 8

    def test_base_row_counts_has_eight_tables(self):
        assert len(_TPCH_BASE_ROW_COUNTS) == 8

    def test_table_codes_and_row_counts_same_keys(self):
        assert set(_TPCH_TABLE_CODES.keys()) == set(_TPCH_BASE_ROW_COUNTS.keys())


class TestPathResolution:
    def test_package_root_dir_is_parent(self):
        root = TPCHDataGenerator._package_root_dir()
        assert root.exists()

    def test_candidate_paths_yields_at_least_two(self):
        candidates = list(TPCHDataGenerator._candidate_dbgen_paths())
        assert len(candidates) >= 2

    def test_resolve_dbgen_path_returns_path_or_none(self):
        result = TPCHDataGenerator.resolve_dbgen_path()
        assert result is None or isinstance(result, Path)

    def test_has_dbgen_sources_returns_bool(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        assert isinstance(gen.has_dbgen_sources(), bool)


class TestCollectExistingTableFiles:
    def test_finds_uncompressed_tbl(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        (tmp_path / "customer.tbl").write_text("data")
        result = gen._collect_existing_table_files(tmp_path)
        assert "customer" in result

    def test_finds_compressed_tbl_zst(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        (tmp_path / "orders.tbl.zst").write_text("data")
        result = gen._collect_existing_table_files(tmp_path)
        assert "orders" in result

    def test_finds_compressed_tbl_gz(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        (tmp_path / "lineitem.tbl.gz").write_text("data")
        result = gen._collect_existing_table_files(tmp_path)
        assert "lineitem" in result

    def test_finds_sharded_files(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        for i in range(1, 4):
            (tmp_path / f"lineitem.tbl.{i}").write_text(f"data{i}")
        result = gen._collect_existing_table_files(tmp_path)
        assert "lineitem" in result
        assert isinstance(result["lineitem"], list)
        assert len(result["lineitem"]) == 3

    def test_empty_dir_returns_empty_dict(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        result = gen._collect_existing_table_files(tmp_path)
        assert result == {}


class TestStdoutSupport:
    def test_caches_result(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        gen._stdout_support_cached = True
        assert gen._check_stdout_support() is True

    def test_returns_false_when_binary_unavailable(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        gen._dbgen_exe = tmp_path / "nonexistent"

        result = gen._check_stdout_support()
        assert result is False


class TestDataOrganizationConfig:
    def test_default_none(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        assert gen._data_organization_config is None

    def test_from_kwargs(self, tmp_path: Path):
        mock_config = SimpleNamespace(format="parquet")
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True, data_organization=mock_config)
        assert gen._data_organization_config is mock_config

    def test_from_env_var_invalid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON", "not-json")
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        assert gen._data_organization_config is None


class TestDbgenExeProperty:
    def test_raises_when_binary_unavailable(self, tmp_path: Path):
        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, quiet=True)
        gen.dbgen_available = False

        with patch.object(gen, "_find_or_build_dbgen", side_effect=FileNotFoundError("no binary")):
            with pytest.raises(RuntimeError):
                _ = gen.dbgen_exe


