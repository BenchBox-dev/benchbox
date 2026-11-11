# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.utils.path_utils import (
    ensure_directory,
    find_work_tree_root,
    get_benchmark_runs_databases_path,
    get_benchmark_runs_dataframe_path,
    get_benchmark_runs_datagen_path,
    get_default_data_directory,
    get_results_path,
    resolve_benchmark_runs_dir,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGetDefaultDataDirectory:
    @patch.dict(os.environ, {"BENCHBOX_DATA_DIR": "/custom/data/path"})
    def test_get_default_data_directory_from_env(self):
        result = get_default_data_directory()

        assert result == Path("/custom/data/path")
        assert isinstance(result, Path)

    @patch.dict(os.environ, {}, clear=True)
    @patch("benchbox.utils.path_utils.Path.cwd")
    def test_get_default_data_directory_fallback(self, mock_cwd):
        mock_cwd.return_value = Path("/current/working/dir")

        result = get_default_data_directory()

        assert result == Path("/current/working/dir/data")
        mock_cwd.assert_called_once()

    @patch.dict(os.environ, {"BENCHBOX_DATA_DIR": ""})
    @patch("benchbox.utils.path_utils.Path.cwd")
    def test_get_default_data_directory_empty_env(self, mock_cwd):
        mock_cwd.return_value = Path("/fallback/path")

        result = get_default_data_directory()

        assert result == Path("/fallback/path/data")
        mock_cwd.assert_called_once()

    def test_get_default_data_directory_returns_path_object(self, tmp_path):
        test_path = str(tmp_path / "env_path")
        with patch.dict(os.environ, {"BENCHBOX_DATA_DIR": test_path}):
            result = get_default_data_directory()

            assert isinstance(result, Path)
            assert result == Path(test_path)


class TestGetResultsPath:
    def test_get_results_path_default_base_dir(self):
        with patch("benchbox.utils.path_utils.get_default_data_directory") as mock_default:
            mock_default.return_value = Path("/default/data")

            result = get_results_path("tpch", "20250115_123045")

            assert result == Path("/default/data/results/tpch_20250115_123045")
            mock_default.assert_called_once()

    def test_get_results_path_custom_base_dir_string(self):
        result = get_results_path("tpcds", "20250115_143000", "/custom/results/base")

        assert result == Path("/custom/results/base/results/tpcds_20250115_143000")

    def test_get_results_path_custom_base_dir_path(self):
        base_path = Path("/custom/path/object")
        result = get_results_path("ssb", "20250115_160000", base_path)

        assert result == Path("/custom/path/object/results/ssb_20250115_160000")

    def test_get_results_path_various_benchmarks(self):
        base_dir = "/test/results"
        timestamp = "20250115_120000"

        tpch_path = get_results_path("tpch", timestamp, base_dir)
        tpcds_path = get_results_path("tpcds", timestamp, base_dir)
        ssb_path = get_results_path("ssb", timestamp, base_dir)

        assert tpch_path == Path("/test/results/results/tpch_20250115_120000")
        assert tpcds_path == Path("/test/results/results/tpcds_20250115_120000")
        assert ssb_path == Path("/test/results/results/ssb_20250115_120000")

    def test_get_results_path_various_timestamps(self):
        base_dir = "/test/timestamps"
        benchmark = "tpch"

        iso_path = get_results_path(benchmark, "2025-01-15T12:30:45", base_dir)
        compact_path = get_results_path(benchmark, "20250115_123045", base_dir)
        custom_path = get_results_path(benchmark, "run_001", base_dir)

        assert iso_path == Path("/test/timestamps/results/tpch_2025-01-15T12:30:45")
        assert compact_path == Path("/test/timestamps/results/tpch_20250115_123045")
        assert custom_path == Path("/test/timestamps/results/tpch_run_001")

    def test_get_results_path_returns_path_object(self):
        result = get_results_path("test", "timestamp", "/base")

        assert isinstance(result, Path)

    def test_get_results_path_special_characters(self):
        base_dir = "/test/special"

        special_benchmark = get_results_path("test-benchmark_v2", "2025-01-15_run-001", base_dir)

        assert special_benchmark == Path("/test/special/results/test-benchmark_v2_2025-01-15_run-001")


class TestEnsureDirectory:
    def test_ensure_directory_string_path(self, tmp_path):
        test_dir = tmp_path / "test_string_dir"
        test_dir_str = str(test_dir)

        result = ensure_directory(test_dir_str)

        assert result == Path(test_dir_str)
        assert test_dir.exists()
        assert test_dir.is_dir()

    def test_ensure_directory_path_object(self, tmp_path):
        test_dir = tmp_path / "test_path_dir"

        result = ensure_directory(test_dir)

        assert result == test_dir
        assert test_dir.exists()
        assert test_dir.is_dir()

    def test_ensure_directory_nested_path(self, tmp_path):
        nested_dir = tmp_path / "level1" / "level2" / "level3"

        result = ensure_directory(nested_dir)

        assert result == nested_dir
        assert nested_dir.exists()
        assert nested_dir.is_dir()
        assert (tmp_path / "level1").exists()
        assert (tmp_path / "level1" / "level2").exists()

    def test_ensure_directory_already_exists(self, tmp_path):
        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        result = ensure_directory(existing_dir)

        assert result == existing_dir
        assert existing_dir.exists()
        assert existing_dir.is_dir()

    def test_ensure_directory_parents_true(self, tmp_path):
        deep_dir = tmp_path / "a" / "b" / "c" / "d" / "e"

        result = ensure_directory(deep_dir)

        assert result == deep_dir
        assert deep_dir.exists()
        current = tmp_path
        for part in ["a", "b", "c", "d", "e"]:
            current = current / part
            assert current.exists()
            assert current.is_dir()

    def test_ensure_directory_exist_ok_true(self, tmp_path):
        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        result = ensure_directory(existing_dir)

        assert result == existing_dir
        assert existing_dir.exists()

    def test_ensure_directory_returns_path_object(self, tmp_path):
        test_dir = tmp_path / "test_return_type"

        result_from_string = ensure_directory(str(test_dir))
        assert isinstance(result_from_string, Path)

        test_dir2 = tmp_path / "test_return_type2"
        result_from_path = ensure_directory(test_dir2)
        assert isinstance(result_from_path, Path)

    def test_ensure_directory_absolute_path(self, tmp_path):
        abs_dir = tmp_path / "absolute_test"

        result = ensure_directory(abs_dir)

        assert result.is_absolute()
        assert result == abs_dir
        assert abs_dir.exists()

    def test_ensure_directory_relative_path(self):
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            relative_dir = Path("relative/test/dir")

            result = ensure_directory(relative_dir)

            assert result == relative_dir
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_ensure_directory_permission_error(self):
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            mock_mkdir.side_effect = PermissionError("Permission denied")

            with pytest.raises(PermissionError, match="Permission denied"):
                ensure_directory("/restricted/path")

    def test_ensure_directory_file_exists_error(self, tmp_path):
        file_path = tmp_path / "file_not_dir"
        file_path.write_text("content")

        with pytest.raises(FileExistsError):
            ensure_directory(file_path)


class TestPathUtilsIntegration:
    def test_datagen_and_results_paths_independence(self, tmp_path):
        base_dir = tmp_path / "test_integration"
        base_dir.mkdir()
        benchmark = "tpch"
        scale_factor = 1.0
        timestamp = "20250115_120000"

        datagen_path = get_benchmark_runs_datagen_path(benchmark, scale_factor, base_dir)
        results_path = get_results_path(benchmark, timestamp, base_dir)

        assert datagen_path != results_path

        assert datagen_path.is_relative_to(base_dir)
        assert results_path.is_relative_to(base_dir)

        assert datagen_path == base_dir / "tpch_sf1"
        assert results_path == base_dir / "results" / "tpch_20250115_120000"

    def test_full_workflow_path_creation(self, tmp_path):
        base_dir = tmp_path / "workflow_test"
        benchmark = "tpcds"
        scale_factor = 0.1
        timestamp = "20250115_140000"

        datagen_path = get_benchmark_runs_datagen_path(benchmark, scale_factor, base_dir)
        results_path = get_results_path(benchmark, timestamp, base_dir)

        datagen_dir = ensure_directory(datagen_path)
        results_dir = ensure_directory(results_path)

        assert datagen_dir.exists() and datagen_dir.is_dir()
        assert results_dir.exists() and results_dir.is_dir()

        assert datagen_dir == base_dir / "tpcds_sf01"
        assert results_dir == base_dir / "results" / "tpcds_20250115_140000"

        assert base_dir.exists()

    def test_environment_variable_integration(self, tmp_path):
        custom_data_dir = tmp_path / "env_test_data"

        with patch.dict(os.environ, {"BENCHBOX_DATA_DIR": str(custom_data_dir)}):
            default_dir = get_default_data_directory()
            assert default_dir == custom_data_dir

            results_path = get_results_path("ssb", "20250115_150000")

            assert str(results_path).startswith(str(custom_data_dir))

            ensure_directory(results_path)

            assert results_path.exists()

    def test_mixed_path_types_handling(self, tmp_path):
        base_dir_str = str(tmp_path / "mixed_test")
        base_dir_path = Path(tmp_path / "mixed_test2")

        path1 = get_benchmark_runs_datagen_path("test1", 1.0, base_dir_str)
        ensure_directory(path1)

        path2 = get_results_path("test2", "timestamp", base_dir_path)
        ensure_directory(path2)

        assert isinstance(path1, Path)
        assert isinstance(path2, Path)
        assert path1.exists()
        assert path2.exists()


class TestGetBenchmarkRunsDatabasesPath:
    def test_default_databases_path(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("benchbox.utils.path_utils.Path.cwd", return_value=Path("/repo")),
        ):
            result = get_benchmark_runs_databases_path("tpch", 1.0)
        assert result == Path("/repo/benchmark_runs/databases/tpch_sf1")

    def test_custom_base_dir(self):
        result = get_benchmark_runs_databases_path("tpcds", 0.01, "/tmp/base")
        assert result == Path("/tmp/base/tpcds_sf001")


class TestBenchboxOutputDirRedirection:
    def test_datagen_path_honors_output_dir(self, tmp_path):
        custom_root = tmp_path / "runs"
        with patch.dict(os.environ, {"BENCHBOX_OUTPUT_DIR": str(custom_root)}):
            result = get_benchmark_runs_datagen_path("tpch", 1.0)
        assert result == custom_root / "datagen" / "tpch_sf1"

    def test_databases_path_honors_output_dir(self, tmp_path):
        custom_root = tmp_path / "runs"
        with patch.dict(os.environ, {"BENCHBOX_OUTPUT_DIR": str(custom_root)}):
            result = get_benchmark_runs_databases_path("tpcds", 0.01)
        assert result == custom_root / "databases" / "tpcds_sf001"

    def test_dataframe_path_honors_output_dir(self, tmp_path):
        custom_root = tmp_path / "runs"
        with patch.dict(os.environ, {"BENCHBOX_OUTPUT_DIR": str(custom_root)}):
            result = get_benchmark_runs_dataframe_path()
        assert result == custom_root / "datagen"

    def test_explicit_base_dir_overrides_env(self, tmp_path):
        env_root = tmp_path / "from_env"
        explicit = tmp_path / "from_arg"
        with patch.dict(os.environ, {"BENCHBOX_OUTPUT_DIR": str(env_root)}):
            result = get_benchmark_runs_databases_path("tpch", 1.0, explicit)
        assert result == explicit / "tpch_sf1"

    def test_resolve_benchmark_runs_dir_expands_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", "~/runs")
        assert resolve_benchmark_runs_dir() == tmp_path / "runs"

    def test_resolve_benchmark_runs_dir_falls_back_to_cwd(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("benchbox.utils.path_utils.Path.cwd", return_value=Path("/repo")),
        ):
            assert resolve_benchmark_runs_dir() == Path("/repo/benchmark_runs")

    def test_empty_env_var_falls_back_to_cwd(self):
        with (
            patch.dict(os.environ, {"BENCHBOX_OUTPUT_DIR": ""}),
            patch("benchbox.utils.path_utils.Path.cwd", return_value=Path("/repo")),
        ):
            assert resolve_benchmark_runs_dir() == Path("/repo/benchmark_runs")


class TestWorkTreeAnchoredDefault:
    def _make_work_tree(self, root: Path, *, linked: bool) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        marker = root / ".git"
        if linked:
            marker.write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
        else:
            marker.mkdir()
        return root

    @pytest.mark.parametrize("linked", [False, True], ids=["clone", "linked_worktree"])
    def test_find_work_tree_root_accepts_both_git_marker_forms(self, tmp_path, linked):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=linked)
        nested = work_tree / "benchbox" / "utils"
        nested.mkdir(parents=True)

        assert find_work_tree_root(work_tree) == work_tree
        assert find_work_tree_root(nested) == work_tree

    def test_find_work_tree_root_returns_none_outside_a_work_tree(self, tmp_path):
        plain = tmp_path / "not_a_repo" / "deep"
        plain.mkdir(parents=True)
        assert find_work_tree_root(plain) is None

    @pytest.mark.parametrize("linked", [False, True], ids=["clone", "linked_worktree"])
    def test_default_root_is_the_work_tree_sibling(self, tmp_path, monkeypatch, linked):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=linked)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
        monkeypatch.chdir(work_tree)

        assert resolve_benchmark_runs_dir() == tmp_path / "benchmark_runs"

    def test_default_root_is_never_inside_the_work_tree(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
        monkeypatch.chdir(work_tree)

        resolved = resolve_benchmark_runs_dir()

        assert resolved != work_tree
        assert work_tree not in resolved.parents

    def test_default_root_is_cwd_independent_within_a_work_tree(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        nested = work_tree / "tests" / "unit"
        nested.mkdir(parents=True)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

        monkeypatch.chdir(work_tree)
        from_root = resolve_benchmark_runs_dir()
        monkeypatch.chdir(nested)
        from_nested = resolve_benchmark_runs_dir()

        assert from_root == from_nested == tmp_path / "benchmark_runs"

    def test_sibling_worktrees_share_one_root(self, tmp_path, monkeypatch):
        clone = self._make_work_tree(tmp_path / "BenchBox", linked=False)
        linked = self._make_work_tree(tmp_path / "BenchBox.wt-linked", linked=True)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

        monkeypatch.chdir(clone)
        from_clone = resolve_benchmark_runs_dir()
        monkeypatch.chdir(linked)
        from_linked = resolve_benchmark_runs_dir()

        assert from_clone == from_linked == tmp_path / "benchmark_runs"

    def test_directory_name_is_still_benchmark_runs(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
        monkeypatch.chdir(work_tree)

        assert resolve_benchmark_runs_dir().name == "benchmark_runs"

    def test_env_var_still_wins_inside_a_work_tree(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        override = tmp_path / "explicit_root"
        monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(override))
        monkeypatch.chdir(work_tree)

        assert resolve_benchmark_runs_dir() == override

    def test_relative_env_var_still_wins_inside_a_work_tree(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", "local_runs")
        monkeypatch.chdir(work_tree)

        assert resolve_benchmark_runs_dir() == Path("local_runs")

    def test_explicit_benchmark_runs_env_is_not_inferred_as_absent(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", "benchmark_runs")
        monkeypatch.chdir(work_tree)

        assert resolve_benchmark_runs_dir() == Path("benchmark_runs")

    def test_env_var_tilde_still_expands_inside_a_work_tree(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", "~/runs")
        monkeypatch.chdir(work_tree)

        assert resolve_benchmark_runs_dir() == home / "runs"

    def test_derived_subdirs_follow_the_resolved_root(self, tmp_path, monkeypatch):
        work_tree = self._make_work_tree(tmp_path / "checkout", linked=False)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
        monkeypatch.chdir(work_tree)

        expected_root = tmp_path / "benchmark_runs"
        assert get_benchmark_runs_datagen_path("tpch", 0.01) == expected_root / "datagen" / "tpch_sf001"
        assert get_benchmark_runs_databases_path("tpch", 0.01) == expected_root / "databases" / "tpch_sf001"


