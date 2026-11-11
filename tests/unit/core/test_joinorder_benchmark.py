from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.benchmark_loader import get_benchmark_instance
from benchbox.core.errors import ScaleFactorNotSupportedError
from benchbox.core.joinorder.benchmark import JoinOrderBenchmark
from benchbox.core.joinorder.schema import JoinOrderSchema
from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark
from benchbox.core.schemas import BenchmarkConfig, SystemProfile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_system_profile() -> SystemProfile:

    return SystemProfile(
        os_name="TestOS",
        os_version="1.0",
        architecture="x86_64",
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=12.0,
        python_version="3.11.0",
        disk_space_gb=256.0,
        timestamp=datetime.now(),
    )


def test_joinorder_benchmark_propagates_compression(tmp_path) -> None:

    benchmark = JoinOrderSyntheticBenchmark(
        scale_factor=0.1,
        output_dir=tmp_path,
        compress_data=True,
        compression_type="zstd",
        compression_level=7,
        parallel=2,
    )

    generator = benchmark._generator

    assert benchmark.parallel == 2
    assert generator.compress_data is True
    assert generator.compression_type == "zstd"
    assert generator.compression_level == 7


def test_joinorder_benchmark_propagates_compression_gzip(tmp_path) -> None:

    benchmark = JoinOrderSyntheticBenchmark(
        scale_factor=0.1,
        output_dir=tmp_path,
        compress_data=True,
        compression_type="gzip",
        compression_level=6,
        parallel=2,
    )

    generator = benchmark._generator

    assert benchmark.parallel == 2
    assert generator.compress_data is True
    assert generator.compression_type == "gzip"
    assert generator.compression_level == 6


def test_get_benchmark_instance_handles_joinorder_compression() -> None:

    config = BenchmarkConfig(
        name="joinorder_synthetic",
        display_name="JoinOrder Synthetic",
        scale_factor=1.0,
        compress_data=True,
        compression_type="zstd",
        compression_level=5,
        options={"force_regenerate": True},
    )

    profile = _make_system_profile()

    instance = get_benchmark_instance(config, profile)

    generator = instance._generator

    assert isinstance(instance, JoinOrderSyntheticBenchmark)
    assert instance.parallel == profile.cpu_cores_logical

    assert instance.force_regenerate is True
    assert generator.compress_data is True
    assert generator.compression_type == "zstd"
    assert generator.compression_level == 5
    assert generator.force_regenerate is True


def test_get_benchmark_instance_rejects_unsupported_joinorder_scale() -> None:
    config = BenchmarkConfig(
        name="joinorder",
        display_name="JoinOrder",
        scale_factor=0.5,
    )

    with pytest.raises(ScaleFactorNotSupportedError, match=r"joinorder accepts scale_factor in \[1\.0\]"):
        get_benchmark_instance(config, _make_system_profile())


def test_joinorder_invalid_parallel_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="parallel must be a positive integer"):
        JoinOrderSyntheticBenchmark(scale_factor=0.1, output_dir=tmp_path, parallel=0)


def test_joinorder_generate_data_logs_and_returns_files(tmp_path: Path) -> None:
    benchmark = JoinOrderSyntheticBenchmark(scale_factor=0.1, output_dir=tmp_path)
    benchmark._generator = MagicMock()
    benchmark._generator.generate_data.return_value = [tmp_path / "title.csv"]

    with patch.object(benchmark, "log_verbose") as log_verbose:
        result = benchmark.generate_data()

    assert result == [tmp_path / "title.csv"]
    assert log_verbose.call_count == 2


def test_joinorder_schema_and_query_helpers_delegate_to_components(tmp_path: Path) -> None:
    benchmark = JoinOrderSyntheticBenchmark(scale_factor=0.1, output_dir=tmp_path)
    benchmark._schema = MagicMock()
    benchmark._schema._tables = {"title": {"columns": []}}
    benchmark._schema.get_create_tables_sql.return_value = "CREATE TABLE title (id INT)"
    benchmark._schema.get_table_names.return_value = ["title"]
    benchmark._schema.get_table_info.return_value = {"name": "title"}
    benchmark._schema.get_relationship_tables.return_value = ["movie_info_idx"]
    benchmark._schema.get_dimension_tables.return_value = ["title"]
    benchmark._generator = MagicMock()
    benchmark._generator.get_total_size_estimate.return_value = 1024
    benchmark._generator.get_table_row_count.return_value = 42
    benchmark._query_manager = MagicMock()
    benchmark._query_manager.get_query.return_value = "SELECT * FROM title"
    benchmark._query_manager.get_all_queries.return_value = {"1a": "SELECT * FROM title"}
    benchmark._query_manager.get_query_ids.return_value = ["1a"]
    benchmark._query_manager.get_query_count.return_value = 1
    benchmark._query_manager.get_queries_by_complexity.return_value = {"simple": ["1a"]}
    benchmark._query_manager.get_queries_by_pattern.return_value = {"star": ["1a"]}

    assert benchmark.get_schema() == {"title": {"columns": []}}
    assert benchmark.get_create_tables_sql() == "CREATE TABLE title (id INT)"
    assert benchmark.get_table_names() == ["title"]
    assert benchmark.get_query("1a") == "SELECT * FROM title"
    assert benchmark.get_queries() == {"1a": "SELECT * FROM title"}
    assert benchmark.get_query_ids() == ["1a"]
    assert benchmark.get_query_count() == 1
    assert benchmark.get_queries_by_complexity() == {"simple": ["1a"]}
    assert benchmark.get_queries_by_pattern() == {"star": ["1a"]}
    assert benchmark.get_table_info("title") == {"name": "title"}
    assert benchmark.get_relationship_tables() == ["movie_info_idx"]
    assert benchmark.get_dimension_tables() == ["title"]
    assert benchmark.get_estimated_data_size() == 1024
    assert benchmark.get_table_row_count("title") == 42


def test_joinorder_get_query_rejects_params(tmp_path: Path) -> None:
    benchmark = JoinOrderSyntheticBenchmark(scale_factor=0.1, output_dir=tmp_path)

    with pytest.raises(ValueError, match="don't accept parameters"):
        benchmark.get_query("1a", params={"seed": 1})


def test_canonical_joinorder_schema_omits_foreign_keys_by_default() -> None:
    schema = JoinOrderSchema()

    sql = schema.get_create_tables_sql("postgres")
    sql_with_fks = schema.get_create_tables_sql("postgres", include_foreign_keys=True)

    assert "FOREIGN KEY" not in sql
    assert "FOREIGN KEY" in sql_with_fks


def test_joinorder_load_queries_from_directory_replaces_query_manager(tmp_path: Path) -> None:
    benchmark = JoinOrderSyntheticBenchmark(scale_factor=0.1, output_dir=tmp_path)
    replacement = MagicMock()

    with patch(
        "benchbox.core.joinorder_synthetic.benchmark.JoinOrderQueryManager", return_value=replacement
    ) as manager_cls:
        benchmark.load_queries_from_directory("/queries")

    manager_cls.assert_called_once_with("/queries")
    assert benchmark._query_manager is replacement


def test_joinorder_validate_query_benchmark_info_and_repr(tmp_path: Path) -> None:
    benchmark = JoinOrderSyntheticBenchmark(scale_factor=0.1, output_dir=tmp_path)

    with (
        patch.object(benchmark, "get_query", return_value="SELECT 1 FROM title"),
        patch.object(benchmark, "get_query_count", return_value=12),
        patch.object(benchmark, "get_table_names", return_value=["title", "name"]),
        patch.object(benchmark, "get_relationship_tables", return_value=["movie_info_idx"]),
        patch.object(benchmark, "get_dimension_tables", return_value=["title"]),
        patch.object(benchmark, "get_estimated_data_size", return_value=2048),
        patch.object(benchmark, "get_queries_by_complexity", return_value={"simple": ["1a"]}),
        patch.object(benchmark, "get_queries_by_pattern", return_value={"star": ["1a"]}),
    ):
        info = benchmark.get_benchmark_info()
        representation = repr(benchmark)

    assert benchmark.validate_query("1a") is True
    assert info["benchmark_name"] == "Synthetic Join Order Benchmark"
    assert info["total_queries"] == 12
    assert info["total_tables"] == 2
    assert info["estimated_size_bytes"] == 2048
    assert representation == "JoinOrderSyntheticBenchmark(scale_factor=0.1, queries=12)"


def test_joinorder_validate_query_returns_false_for_invalid_sql(tmp_path: Path) -> None:
    benchmark = JoinOrderSyntheticBenchmark(scale_factor=0.1, output_dir=tmp_path)

    with patch.object(benchmark, "get_query", return_value="DELETE title"):
        assert benchmark.validate_query("1a") is False

    with patch.object(benchmark, "get_query", side_effect=RuntimeError("bad query")):
        assert benchmark.validate_query("1a") is False


