# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.tpch.streams import TPCHStreamRunner, TPCHStreams
from benchbox.utils.tpc_compilation import get_tpc_compiler, get_tpc_templates_dir

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPermutationMatrix:
    def test_matrix_has_41_rows(self):
        assert len(TPCHStreams.PERMUTATION_MATRIX) == 41

    def test_each_row_has_22_queries(self):
        for i, row in enumerate(TPCHStreams.PERMUTATION_MATRIX):
            assert len(row) == 22, f"Row {i} has {len(row)} entries, expected 22"

    def test_each_row_is_permutation_of_1_to_22(self):
        expected = set(range(1, 23))
        for i, row in enumerate(TPCHStreams.PERMUTATION_MATRIX):
            assert set(row) == expected, f"Row {i} is not a permutation of 1-22"

    def test_first_row_matches_spec(self):
        expected = [14, 2, 9, 20, 6, 17, 18, 8, 21, 13, 3, 22, 16, 4, 11, 15, 1, 10, 19, 5, 7, 12]
        assert TPCHStreams.PERMUTATION_MATRIX[0] == expected

    def test_last_row_matches_spec(self):
        expected = [13, 15, 17, 1, 22, 11, 3, 4, 7, 20, 14, 21, 9, 8, 2, 18, 16, 6, 10, 12, 5, 19]
        assert TPCHStreams.PERMUTATION_MATRIX[40] == expected


class TestGetStreamPermutation:
    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_0_returns_first_permutation(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=1, verbose=0)
        perm = streams._get_stream_permutation(0)
        assert perm == TPCHStreams.PERMUTATION_MATRIX[0]

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_permutation_wraps_around(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=100, verbose=0)

        perm41 = streams._get_stream_permutation(41)
        perm0 = streams._get_stream_permutation(0)
        assert perm41 == perm0

        perm82 = streams._get_stream_permutation(82)
        assert perm82 == perm0

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_permutation_returns_copy(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=1, verbose=0)

        perm1 = streams._get_stream_permutation(0)
        perm2 = streams._get_stream_permutation(0)
        assert perm1 == perm2
        assert perm1 is not perm2

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_different_streams_different_permutations(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=3, verbose=0)

        perm0 = streams._get_stream_permutation(0)
        perm1 = streams._get_stream_permutation(1)
        perm2 = streams._get_stream_permutation(2)
        assert perm0 != perm1
        assert perm1 != perm2

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_permutation_always_22_elements(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=50, verbose=0)

        for stream_id in range(50):
            perm = streams._get_stream_permutation(stream_id)
            assert len(perm) == 22


class TestTPCHStreamsInit:
    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_default_values(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(verbose=0)

        assert streams.num_streams == 1
        assert streams.scale_factor == 1.0
        assert streams.rng_seed == 1
        assert streams.query_count == 22

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_custom_values(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(
            num_streams=4,
            scale_factor=10.0,
            output_dir="/tmp/test_streams",
            rng_seed=42,
            verbose=0,
        )

        assert streams.num_streams == 4
        assert streams.scale_factor == 10.0
        assert streams.output_dir == Path("/tmp/test_streams")
        assert streams.rng_seed == 42

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_default_output_dir(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(verbose=0)
        assert streams.output_dir == Path.cwd() / "tpch_streams"

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_none_seed_defaults_to_1(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(rng_seed=None, verbose=0)
        assert streams.rng_seed == 1


class TestGetStreamInfo:
    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_info_structure(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=3, scale_factor=2.0, rng_seed=10, verbose=0)

        info = streams.get_stream_info(0)
        assert info["stream_id"] == 0
        assert info["scale_factor"] == 2.0
        assert info["rng_seed"] == 10
        assert info["query_count"] == 22
        assert info["permutation_index"] == 0
        assert len(info["query_order"]) == 22
        assert info["output_file"] == streams.output_dir / "stream_0.sql"

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_info_seed_per_stream(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=5, rng_seed=100, verbose=0)

        info0 = streams.get_stream_info(0)
        info3 = streams.get_stream_info(3)

        assert info0["rng_seed"] == 100
        assert info3["rng_seed"] == 103

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_info_invalid_stream_id(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=2, verbose=0)

        with pytest.raises(ValueError, match="Invalid stream ID"):
            streams.get_stream_info(2)

        with pytest.raises(ValueError, match="Invalid stream ID"):
            streams.get_stream_info(5)

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_get_all_streams_info(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=3, verbose=0)

        all_info = streams.get_all_streams_info()
        assert len(all_info) == 3
        assert all_info[0]["stream_id"] == 0
        assert all_info[1]["stream_id"] == 1
        assert all_info[2]["stream_id"] == 2

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_info_permutation_index_wraps(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=50, verbose=0)

        info0 = streams.get_stream_info(0)
        info41 = streams.get_stream_info(41)
        assert info0["permutation_index"] == 0
        assert info41["permutation_index"] == 0

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_stream_info_query_order_matches_permutation(self, mock_templates):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(num_streams=10, verbose=0)

        for sid in range(10):
            info = streams.get_stream_info(sid)
            expected_row = TPCHStreams.PERMUTATION_MATRIX[sid % 41]
            assert info["query_order"] == expected_row


class TestTPCHStreamRunner:
    def test_runner_initialization(self):
        runner = TPCHStreamRunner(
            connection_string="duckdb:///:memory:",
            dialect="standard",
            verbose=0,
        )
        assert runner.connection_string == "duckdb:///:memory:"
        assert runner.dialect == "standard"

    def test_runner_default_dialect(self):
        runner = TPCHStreamRunner(
            connection_string="test://conn",
            verbose=0,
        )
        assert runner.dialect == "standard"


class TestRunStream:
    def test_run_stream_raises_not_implemented_for_missing_file(self):
        runner = TPCHStreamRunner(connection_string="test://conn", verbose=0)
        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            runner.run_stream(Path("/nonexistent/stream.sql"), stream_id=0)

    def test_run_stream_raises_not_implemented_for_valid_file(self, tmp_path):
        stream_file = tmp_path / "stream_0.sql"
        stream_file.write_text(
            "-- TPC-H Stream 0\n"
            "-- Query 14 (Stream 0, Position 1)\n"
            "SELECT 1;\n\n"
            "-- Query 2 (Stream 0, Position 2)\n"
            "SELECT 2;\n\n"
            "-- Query 9 (Stream 0, Position 3)\n"
            "SELECT 3;\n\n"
        )

        runner = TPCHStreamRunner(connection_string="test://conn", verbose=0)
        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            runner.run_stream(stream_file, stream_id=0)

    def test_run_stream_raises_not_implemented_for_empty_file(self, tmp_path):
        stream_file = tmp_path / "stream_empty.sql"
        stream_file.write_text("-- Empty stream\n")

        runner = TPCHStreamRunner(connection_string="test://conn", verbose=0)
        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            runner.run_stream(stream_file, stream_id=0)


class TestCompileQgen:
    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    @patch("subprocess.run")
    def test_compile_qgen_failure_returns_none(self, mock_run, mock_templates, tmp_path):
        import subprocess

        mock_templates.return_value = tmp_path / "templates"
        (tmp_path / "templates").mkdir()

        mock_run.side_effect = subprocess.CalledProcessError(1, "make")

        streams = TPCHStreams(num_streams=1, verbose=0)
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        result = streams._compile_qgen(work_dir)
        assert result is None

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    @patch("subprocess.run")
    def test_compile_qgen_success_returns_path(self, mock_run, mock_templates, tmp_path):
        import os

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        mock_templates.return_value = templates_dir

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        streams = TPCHStreams(num_streams=1, verbose=0)
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        qgen_name = "qgen.exe" if os.name == "nt" else "qgen"
        qgen_exe = templates_dir / qgen_name
        qgen_exe.write_bytes(b"fake exe")

        result = streams._compile_qgen(work_dir)
        assert result == work_dir / "tpch_tools" / qgen_name

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    @patch("subprocess.run")
    def test_compile_qgen_no_exe_after_success(self, mock_run, mock_templates, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        mock_templates.return_value = templates_dir

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        streams = TPCHStreams(num_streams=1, verbose=0)
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        result = streams._compile_qgen(work_dir)
        assert result is None


class TestCompileQgenPlatform:
    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    @patch("subprocess.run")
    @patch("platform.system", return_value="Linux")
    def test_linux_machine_flag(self, _mock_sys, mock_run, mock_templates, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        mock_templates.return_value = templates_dir
        mock_run.side_effect = __import__("subprocess").CalledProcessError(1, "make")

        streams = TPCHStreams(num_streams=1, verbose=0)
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        streams._compile_qgen(work_dir)

        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get("args", [])
        assert "MACHINE=LINUX" in cmd


class TestGenerateStreamQueriesQgen:
    @pytest.mark.parametrize(("stream_id", "expected_qgen_stream"), [(0, 0), (1, 1), (2, 2), (41, 0)])
    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_generate_stream_queries_qgen_writes_permuted_stream_file(
        self, mock_templates, tmp_path, stream_id, expected_qgen_stream
    ):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(output_dir=tmp_path, scale_factor=0.01, rng_seed=7, verbose=2)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "1.sql").write_text("SELECT 1;")
        (work_dir / "2.sql").write_text("SELECT 2;")

        with (
            patch.object(streams, "_get_stream_permutation", return_value=[2, 1, 3]),
            patch("benchbox.core.tpch.streams.subprocess.run", return_value=Mock(stdout="generated", stderr="")) as run,
            patch.object(streams.logger, "warning") as warning_log,
        ):
            stream_file = streams._generate_stream_queries_qgen(stream_id, Path("/fake/qgen"), work_dir)

        assert stream_file == tmp_path / f"stream_{stream_id}.sql"
        content = stream_file.read_text()
        assert f"-- TPC-H Stream {stream_id}" in content
        assert "-- Query Order (Permuted): [2, 1, 3]" in content
        assert content.index("-- Query 2") < content.index("-- Query 1")
        warning_log.assert_called_once_with("Query %s not generated by qgen for stream %s", 3, stream_id)

        run.assert_called_once()
        cmd = run.call_args.args[0]
        assert cmd == [
            str(Path("/fake/qgen")),
            "-p",
            str(expected_qgen_stream),
            "-s",
            "0.01",
            "-r",
            str(7 + stream_id),
            "-o",
            str(work_dir),
        ]

    def test_bundled_qgen_uses_the_first_query_in_each_stream_permutation(self, tmp_path, monkeypatch):
        qgen = get_tpc_compiler(auto_compile=False).get_binary_path("qgen")
        if qgen is None:
            pytest.skip("bundled qgen binary is unavailable")
        if shutil.which("qgen", path=str(qgen.parent)) is None:
            pytest.skip("bundled qgen binary is not runnable on this platform")

        templates = get_tpc_templates_dir("tpc-h")
        query_dir = templates / "queries" if (templates / "queries").is_dir() else templates
        distributions = templates / "dists.dss"
        if not distributions.is_file() or not any(query_dir.glob("*.sql")):
            pytest.skip("bundled qgen templates are unavailable")

        env = {
            **os.environ,
            "DSS_QUERY": str(query_dir),
            "PATH": os.pathsep.join([str(qgen.parent), os.environ.get("PATH", "")]),
        }
        monkeypatch.setenv("PATH", env["PATH"])
        first_query_signatures = {
            14: "as promo_revenue",
            21: "as numwait",
            6: "sum(l_extendedprice * l_discount) as revenue",
        }
        for stream_id, query_order in enumerate(TPCHStreams.PERMUTATION_MATRIX[:3]):
            work_dir = tmp_path / str(stream_id)
            work_dir.mkdir()
            shutil.copy2(distributions, work_dir / distributions.name)

            first_query = (
                subprocess.run(
                    ["qgen", "-a", "-d", "-p", str(stream_id), "-s", "0.01", "-r", "7"],
                    cwd=work_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.partition(";")[0]
                .lower()
            )
            assert first_query_signatures[query_order[0]] in first_query

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_generate_stream_queries_qgen_wraps_subprocess_failures(self, mock_templates, tmp_path):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(output_dir=tmp_path, verbose=0)
        error = subprocess.CalledProcessError(returncode=1, cmd=["qgen"], stderr="compile failed")

        with (
            patch.object(streams, "_get_stream_permutation", return_value=[1]),
            patch("benchbox.core.tpch.streams.subprocess.run", side_effect=error),
            pytest.raises(RuntimeError, match="Failed to generate stream 2 with qgen"),
        ):
            streams._generate_stream_queries_qgen(2, Path("/fake/qgen"), tmp_path)


class TestGenerateStreams:
    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_generate_streams_skips_failed_streams_and_logs_summary(self, mock_templates, tmp_path):
        mock_templates.return_value = Path("/fake/templates")
        output_dir = tmp_path / "streams"
        streams = TPCHStreams(num_streams=3, output_dir=output_dir, verbose=1)
        generated_files = [output_dir / "stream_0.sql", output_dir / "stream_2.sql"]

        with (
            patch.object(streams, "_compile_qgen", return_value=Path("/fake/qgen")) as compile_qgen,
            patch.object(
                streams,
                "_generate_stream_queries_qgen",
                side_effect=[generated_files[0], RuntimeError("bad stream"), generated_files[1]],
            ) as generate_stream,
            patch.object(streams.logger, "error") as error_log,
            patch.object(streams.logger, "info") as info_log,
        ):
            stream_files = streams.generate_streams()

        assert output_dir.exists()
        assert stream_files == generated_files
        compile_qgen.assert_called_once()
        assert generate_stream.call_count == 3
        error_log.assert_called_once()
        info_log.assert_any_call("Generated %s stream files", 2)

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    def test_generate_streams_wraps_compile_failures(self, mock_templates, tmp_path):
        mock_templates.return_value = Path("/fake/templates")
        streams = TPCHStreams(output_dir=tmp_path, verbose=0)

        with (
            patch.object(streams, "_compile_qgen", side_effect=RuntimeError("missing make")),
            pytest.raises(RuntimeError, match="TPC-H streams generation requires qgen compilation"),
        ):
            streams.generate_streams()


class TestTPCHStreamRunnerAdditional:
    def test_run_stream_raises_not_implemented_when_verbose(self, tmp_path):
        runner = TPCHStreamRunner(connection_string="duckdb:///:memory:", verbose=1)
        stream_file = tmp_path / "stream.sql"
        stream_file.write_text("-- Query 1 (Stream 0, Position 1)\nSELECT 1;\n")

        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            runner.run_stream(stream_file, stream_id=0)

    def test_run_concurrent_streams_raises_not_implemented_no_longer_uses_executor(self, tmp_path):
        runner = TPCHStreamRunner(connection_string="duckdb:///:memory:", verbose=1)
        stream_file = tmp_path / "stream_0.sql"
        stream_file.write_text("-- Query 1 (Stream 0, Position 1)\nSELECT 1;\n")

        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            runner.run_concurrent_streams([stream_file])

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    @patch("subprocess.run")
    @patch("platform.system", return_value="Windows")
    def test_windows_machine_flag(self, _mock_sys, mock_run, mock_templates, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        mock_templates.return_value = templates_dir
        mock_run.side_effect = __import__("subprocess").CalledProcessError(1, "make")

        streams = TPCHStreams(num_streams=1, verbose=0)
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        streams._compile_qgen(work_dir)

        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get("args", [])
        assert "MACHINE=WIN32" in cmd

    @patch("benchbox.utils.tpc_compilation.get_tpc_templates_dir")
    @patch("subprocess.run")
    @patch("platform.system", return_value="SomeOS")
    def test_unknown_os_defaults_to_linux(self, _mock_sys, mock_run, mock_templates, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        mock_templates.return_value = templates_dir
        mock_run.side_effect = __import__("subprocess").CalledProcessError(1, "make")

        streams = TPCHStreams(num_streams=1, verbose=0)
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        streams._compile_qgen(work_dir)

        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get("args", [])
        assert "MACHINE=LINUX" in cmd
