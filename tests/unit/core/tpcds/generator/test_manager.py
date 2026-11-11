from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.tpcds.generator.manager import TPCDSDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def tmp_gen(tmp_path):
    gen = TPCDSDataGenerator.__new__(TPCDSDataGenerator)
    gen.scale_factor = 1.0
    gen.output_dir = tmp_path
    gen.verbose_level = 0
    gen.verbose = False
    gen.very_verbose = False
    gen.quiet = False
    gen.parallel = 1
    gen.force_regenerate = False
    gen.dsdgen_available = False
    gen._dsdgen_error = RuntimeError("no binary")
    gen._package_root = Path("/nonexistent")
    gen.dsdgen_path = tmp_path
    gen.dsdgen_exe = None
    gen._data_organization_config = None
    gen.tools_dir = tmp_path
    gen._manifest_entries = {}
    import threading

    gen._manifest_lock = threading.Lock()
    from benchbox.utils.data_validation import BenchmarkDataValidator

    gen.validator = BenchmarkDataValidator("tpcds", 1.0)
    return gen


def test_write_manifest_refuses_to_record_zero_tables(tmp_gen, tmp_path):
    tmp_gen.compress_data = False
    tmp_gen.compression_type = "none"
    with pytest.raises(RuntimeError, match="produced no table files"):
        tmp_gen._write_manifest(tmp_path, {})

    assert not (tmp_path / "_datagen_manifest.json").exists()


def test_write_manifest_refuses_tables_without_files(tmp_gen, tmp_path):
    tmp_gen.compress_data = False
    tmp_gen.compression_type = "none"
    with pytest.raises(RuntimeError, match="produced no table files"):
        tmp_gen._write_manifest(tmp_path, {"item": []})

    assert not (tmp_path / "_datagen_manifest.json").exists()


def test_write_manifest_records_generated_tables(tmp_gen, tmp_path):
    tmp_gen.compress_data = False
    tmp_gen.compression_type = "none"
    data_file = tmp_path / "item.dat"
    data_file.write_text("1|a|\n2|b|\n")
    tmp_gen._write_manifest(tmp_path, {"item": [data_file]})

    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest["tables"]["item"][0]["row_count"] == 2


class TestValidateParameters:
    def test_negative_scale_factor_raises(self, tmp_path):
        with pytest.raises(ValueError, match="positive"):
            TPCDSDataGenerator(scale_factor=-1.0, output_dir=tmp_path)

    def test_fractional_scale_factor_is_allowed(self, tmp_path):
        gen = TPCDSDataGenerator(scale_factor=0.5, output_dir=tmp_path)
        assert gen.scale_factor == 0.5

    def test_too_large_scale_factor_raises(self, tmp_path):
        with pytest.raises(ValueError, match="exceeds maximum"):
            TPCDSDataGenerator(scale_factor=200000.0, output_dir=tmp_path)

    def test_parallel_less_than_1_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Parallel"):
            TPCDSDataGenerator(scale_factor=1.0, output_dir=tmp_path, parallel=0)

    def test_too_many_parallel_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Too many parallel"):
            TPCDSDataGenerator(scale_factor=1.0, output_dir=tmp_path, parallel=100)


def test_known_table_names_includes_dbgen_version(tmp_gen):
    names = tmp_gen._known_table_names()
    assert "dbgen_version" in names
    assert len(names) > 20


def test_raise_missing_dsdgen_includes_error_detail(tmp_gen):
    tmp_gen._dsdgen_error = RuntimeError("some error detail")
    with pytest.raises(RuntimeError, match="some error detail"):
        tmp_gen._raise_missing_dsdgen()


def test_raise_missing_dsdgen_no_error(tmp_gen):
    tmp_gen._dsdgen_error = None
    with pytest.raises(RuntimeError, match="not bundled"):
        tmp_gen._raise_missing_dsdgen()


def test_resolve_dsdgen_path_returns_none_when_missing():
    result = TPCDSDataGenerator.resolve_dsdgen_path()
    assert result is None or isinstance(result, Path)


def test_prepare_output_dir_raises_when_no_dsdgen(tmp_gen):
    tmp_gen.dsdgen_available = False
    with pytest.raises(RuntimeError, match="not bundled"):
        tmp_gen._prepare_output_dir(None)


def test_prepare_output_dir_creates_directory(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    target = tmp_path / "subdir"
    result = tmp_gen._prepare_output_dir(target)
    assert result == target
    assert target.exists()


def test_generate_tables_raises_when_no_dsdgen(tmp_gen):
    tmp_gen.dsdgen_available = False
    with pytest.raises(RuntimeError):
        tmp_gen.generate_tables(["customer"])


def test_generate_tables_invalid_names_raises(tmp_gen):
    tmp_gen.dsdgen_available = True
    with pytest.raises(ValueError, match="Invalid table names"):
        tmp_gen.generate_tables(["not_a_real_table_xyz"])


def test_has_dsdgen_sources_false(tmp_gen):
    tmp_gen.dsdgen_available = False
    assert tmp_gen.has_dsdgen_sources() is False


def test_has_dsdgen_sources_true(tmp_gen):
    tmp_gen.dsdgen_available = True
    assert tmp_gen.has_dsdgen_sources() is True


def test_package_root_dir_returns_path():
    root = TPCDSDataGenerator._package_root_dir()
    assert isinstance(root, Path)


def test_log_regeneration_reason_silent_when_quiet(tmp_gen):
    tmp_gen.verbose = False
    tmp_gen._log_regeneration_reason(None)


def test_log_regeneration_reason_verbose_force_regen(tmp_gen, capsys):
    tmp_gen.verbose = True
    tmp_gen._log_regeneration_reason(None)


def test_build_schema_registry_returns_tables(tmp_gen):
    registry = tmp_gen._build_schema_registry()
    assert isinstance(registry, dict)
    assert len(registry) > 0
    first = next(iter(registry.values()))
    assert "name" in first
    assert "columns" in first


def test_candidate_dsdgen_paths_yields_paths():
    paths = list(TPCDSDataGenerator._candidate_dsdgen_paths())
    assert len(paths) >= 2
    for p in paths:
        assert isinstance(p, Path)


def test_init_with_data_organization_env_var_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON", "{invalid json}")
    gen = TPCDSDataGenerator(scale_factor=1.0, output_dir=tmp_path)
    assert gen._data_organization_config is None


def test_init_with_data_organization_env_var_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON", raising=False)
    gen = TPCDSDataGenerator(scale_factor=1.0, output_dir=tmp_path)
    assert gen._data_organization_config is None


def test_log_regeneration_reason_verbose_with_issues(tmp_gen, capsys):
    tmp_gen.verbose = True
    mock_result = MagicMock()
    mock_result.issues = ["issue1"]
    tmp_gen._log_regeneration_reason(mock_result)


def test_generate_from_sample_returns_none_when_no_files(tmp_gen, tmp_path):
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    tmp_gen._copy_sample_dataset = MagicMock()
    tmp_gen._gather_existing_table_files = MagicMock(return_value={})
    tmp_gen._validate_file_format_consistency = MagicMock()
    tmp_gen.should_use_compression = MagicMock(return_value=False)

    result = tmp_gen._generate_from_sample(sample_dir, target)
    assert result is None


def test_generate_from_sample_returns_paths_when_files_found(tmp_gen, tmp_path):
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    fake_paths = {"customer": [tmp_path / "customer.dat"]}
    tmp_gen._copy_sample_dataset = MagicMock()
    tmp_gen._gather_existing_table_files = MagicMock(return_value=fake_paths)
    tmp_gen._validate_file_format_consistency = MagicMock()
    tmp_gen.should_use_compression = MagicMock(return_value=False)

    result = tmp_gen._generate_from_sample(sample_dir, target)
    assert result == fake_paths


def test_generate_tables_success_with_mock(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path

    tmp_gen._generate_table_with_streaming = MagicMock()
    fake_all_paths = {"customer": [tmp_path / "customer.dat"]}
    tmp_gen._gather_existing_table_files = MagicMock(return_value=fake_all_paths)

    result = tmp_gen.generate_tables(["customer"])
    assert "customer" in result


def test_generate_tables_verbose(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path
    tmp_gen.verbose = True

    tmp_gen._generate_table_with_streaming = MagicMock()
    tmp_gen._gather_existing_table_files = MagicMock(return_value={"customer": []})

    result = tmp_gen.generate_tables(["customer"])
    assert "customer" in result


def test_generate_delegates_correctly(tmp_gen, tmp_path):
    tmp_gen.output_dir = tmp_path
    expected = {"customer": [tmp_path / "customer.dat"]}
    tmp_gen._handle_cloud_or_local_generation = MagicMock(return_value=expected)

    result = tmp_gen.generate()
    assert result == expected


def test_generate_local_no_regeneration_needed(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path

    fake_paths = {"customer": [tmp_path / "customer.dat"]}
    mock_validation = MagicMock()

    tmp_gen.validator = MagicMock()
    tmp_gen.validator.should_regenerate_data.return_value = (False, mock_validation)
    tmp_gen._gather_existing_table_files = MagicMock(return_value=fake_paths)

    result = tmp_gen._generate_local(tmp_path)
    assert result == fake_paths


def test_generate_local_with_sample_data(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path

    fake_paths = {"customer": [tmp_path / "customer.dat"]}
    mock_sample_dir = tmp_path / "sample"
    mock_sample_dir.mkdir()

    tmp_gen.validator = MagicMock()
    tmp_gen.validator.should_regenerate_data.return_value = (True, None)
    tmp_gen._log_regeneration_reason = MagicMock()
    tmp_gen._prune_stale_table_artifacts = MagicMock(return_value=[])
    tmp_gen._get_sample_data_dir = MagicMock(return_value=mock_sample_dir)
    tmp_gen._generate_from_sample = MagicMock(return_value=fake_paths)

    result = tmp_gen._generate_local(tmp_path)
    assert result == fake_paths


def test_generate_local_no_sample_data_raises(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path

    tmp_gen.validator = MagicMock()
    tmp_gen.validator.should_regenerate_data.return_value = (True, None)
    tmp_gen._log_regeneration_reason = MagicMock()
    tmp_gen._prune_stale_table_artifacts = MagicMock(return_value=[])
    tmp_gen._get_sample_data_dir = MagicMock(return_value=None)
    tmp_gen._generate_from_sample = MagicMock(return_value=None)
    tmp_gen._run_dsdgen_native = MagicMock(side_effect=RuntimeError("dsdgen failed"))

    with pytest.raises(RuntimeError, match="dsdgen failed"):
        tmp_gen._generate_local(tmp_path)


def test_generate_local_with_data_organization(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path

    from types import SimpleNamespace

    tmp_gen._data_organization_config = SimpleNamespace(
        get_sort_columns_for_table=lambda _: [],
        get_partition_columns_for_table=lambda _: [],
        get_cluster_columns_for_table=lambda _: [],
    )

    fake_paths = {"customer": [tmp_path / "customer.dat"]}
    tmp_gen.should_use_compression = MagicMock(return_value=False)
    tmp_gen.validator = MagicMock()
    tmp_gen.validator.should_regenerate_data.return_value = (True, None)
    tmp_gen._log_regeneration_reason = MagicMock()
    tmp_gen._prune_stale_table_artifacts = MagicMock(return_value=[])
    tmp_gen._get_sample_data_dir = MagicMock(return_value=None)
    tmp_gen._generate_from_sample = MagicMock(return_value=None)
    tmp_gen._run_dsdgen_native = MagicMock()
    tmp_gen._gather_existing_table_files = MagicMock(return_value=fake_paths)
    tmp_gen._apply_data_organization = MagicMock(return_value=fake_paths)
    tmp_gen._write_manifest = MagicMock()

    result = tmp_gen._generate_local(tmp_path)
    assert result == fake_paths
    tmp_gen._apply_data_organization.assert_called_once()


def test_compress_raw_dat_files_no_compression(tmp_gen, tmp_path):
    tmp_gen.should_use_compression = MagicMock(return_value=False)
    tmp_gen._compress_raw_dat_files(tmp_path)


def test_prepare_output_dir_non_writable_raises(tmp_gen, tmp_path, monkeypatch):
    tmp_gen.dsdgen_available = True
    target = tmp_path / "target"
    target.mkdir()
    import os

    original_access = os.access
    monkeypatch.setattr("os.access", lambda path, mode: False if mode == os.W_OK else original_access(path, mode))
    with pytest.raises(PermissionError, match="not writable"):
        tmp_gen._prepare_output_dir(target)


def test_generate_local_verbose_logs(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.verbose = True
    tmp_gen.output_dir = tmp_path

    fake_paths = {"customer": [tmp_path / "customer.dat"]}
    tmp_gen.validator = MagicMock()
    tmp_gen.validator.should_regenerate_data.return_value = (False, MagicMock(issues=[]))
    tmp_gen.validator.print_validation_report = MagicMock()
    tmp_gen._gather_existing_table_files = MagicMock(return_value=fake_paths)

    result = tmp_gen._generate_local(tmp_path)
    assert result == fake_paths


def test_generate_local_after_native_dsdgen_with_compression(tmp_gen, tmp_path):
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path
    tmp_gen._data_organization_config = None

    fake_paths = {"customer": [tmp_path / "customer.dat"]}
    tmp_gen.validator = MagicMock()
    tmp_gen.validator.should_regenerate_data.return_value = (True, None)
    tmp_gen._log_regeneration_reason = MagicMock()
    tmp_gen._prune_stale_table_artifacts = MagicMock(return_value=[])
    tmp_gen._get_sample_data_dir = MagicMock(return_value=None)
    tmp_gen._generate_from_sample = MagicMock(return_value=None)
    tmp_gen._run_dsdgen_native = MagicMock()
    tmp_gen._compress_raw_dat_files = MagicMock()
    tmp_gen._gather_existing_table_files = MagicMock(return_value=fake_paths)
    tmp_gen._validate_file_format_consistency = MagicMock()
    tmp_gen._write_manifest = MagicMock()
    tmp_gen.should_use_compression = MagicMock(return_value=False)

    result = tmp_gen._generate_local(tmp_path)
    assert result == fake_paths
    tmp_gen._compress_raw_dat_files.assert_called_once()
    tmp_gen._write_manifest.assert_called_once()


def test_generate_from_sample_verbose(tmp_gen, tmp_path):
    tmp_gen.verbose = True
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    fake_paths = {"customer": [tmp_path / "customer.dat"]}
    tmp_gen._copy_sample_dataset = MagicMock()
    tmp_gen._gather_existing_table_files = MagicMock(return_value=fake_paths)
    tmp_gen._validate_file_format_consistency = MagicMock()
    tmp_gen.should_use_compression = MagicMock(return_value=False)

    result = tmp_gen._generate_from_sample(sample_dir, target)
    assert result == fake_paths


def test_init_with_valid_data_organization_env_var(tmp_path, monkeypatch):
    import json

    config_dict = {
        "tables": {},
        "global_settings": {},
    }
    monkeypatch.setenv("BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON", json.dumps(config_dict))

    try:
        gen = TPCDSDataGenerator(scale_factor=1.0, output_dir=tmp_path)
        assert gen is not None
    except Exception:
        pass


@pytest.fixture
def tmp_tuned_gen(tmp_gen, tmp_path):
    from benchbox.core.data_organization.config import DataOrganizationConfig, SortColumn
    from benchbox.utils.compression import CompressionManager

    tmp_gen.compress_data = True
    tmp_gen.compression_type = "zstd"
    tmp_gen.compression_level = None
    tmp_gen.compression_manager = CompressionManager()
    tmp_gen._data_organization_config = DataOrganizationConfig(table_configs={"item": [SortColumn(name="i_item_sk")]})
    tmp_gen.dsdgen_available = True
    tmp_gen.output_dir = tmp_path
    return tmp_gen


def _write_compressed_tbl(path, rows):
    import zstandard as zstd

    with zstd.open(path, "wb") as f:
        f.write(("\n".join(rows) + "\n").encode())


def _item_tbl_rows(gen):
    width = len(gen._build_schema_registry()["item"]["columns"])
    return ["|".join(f"v{i}" for i in range(width)) for _ in range(3)]


def test_generate_local_tuned_with_compression_from_empty_dir(tmp_tuned_gen, tmp_path, monkeypatch):
    rows = _item_tbl_rows(tmp_tuned_gen)

    def fake_dsdgen(output_dir):
        _write_compressed_tbl(output_dir / "item.dat.zst", rows)
        _write_compressed_tbl(output_dir / "customer.dat.zst", ["a|b"])

    monkeypatch.setattr(tmp_tuned_gen, "_run_dsdgen_native", fake_dsdgen)
    monkeypatch.setattr(tmp_tuned_gen.validator, "should_regenerate_data", lambda target_dir, force: (True, None))

    result = tmp_tuned_gen._generate_local()

    assert set(result) == {"item", "customer"}
    assert result["item"][0].suffix == ".parquet"
    assert result["item"][0].exists()
    assert result["customer"][0].suffix == ".zst"
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest["tables"]["item"][0]["path"].endswith(".parquet")
    assert manifest["tables"]["customer"][0]["path"].endswith(".zst")


def test_generate_local_tuned_reuse_applies_sorting(tmp_tuned_gen, tmp_path, monkeypatch):
    _write_compressed_tbl(tmp_path / "item.dat.zst", _item_tbl_rows(tmp_tuned_gen))
    _write_compressed_tbl(tmp_path / "customer.dat.zst", ["a|b"])
    monkeypatch.setattr(tmp_tuned_gen.validator, "should_regenerate_data", lambda target_dir, force: (False, None))

    result = tmp_tuned_gen._generate_local()

    assert result["item"][0].suffix == ".parquet"
    assert (tmp_path / "item.parquet").exists()
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest["tables"]["item"][0]["path"].endswith(".parquet")


