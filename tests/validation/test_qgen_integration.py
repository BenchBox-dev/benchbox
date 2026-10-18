# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
import subprocess
import tempfile

import pytest

from benchbox.core.tpch.queries import QGenBinary, TPCHQueries

pytestmark = pytest.mark.medium



class TestQGenBinaryIntegration:

    def test_qgen_binary_discovery(self):
        qgen = QGenBinary()

        assert qgen.qgen_path is not None
        assert os.path.exists(qgen.qgen_path)
        assert os.access(qgen.qgen_path, os.X_OK)

    def test_qgen_templates_directory(self):
        qgen = QGenBinary()

        assert qgen.templates_dir is not None
        assert os.path.exists(qgen.templates_dir)
        assert os.path.isdir(qgen.templates_dir)

    def test_qgen_binary_execution(self):
        qgen = QGenBinary()

        result = qgen.generate(1, seed=12345, scale_factor=1.0)

        assert isinstance(result, str)
        assert len(result) > 50
        assert "select" in result.lower() or "with" in result.lower()

    def test_qgen_parameter_passing(self):
        qgen = QGenBinary()

        result1 = qgen.generate(1, seed=11111)
        result2 = qgen.generate(1, seed=22222)

        assert result1 != result2

        result3 = qgen.generate(1, seed=11111)
        assert result1 == result3

    def test_qgen_scale_factor_handling(self):
        qgen = QGenBinary()

        result_small = qgen.generate(1, seed=12345, scale_factor=0.1)
        result_large = qgen.generate(1, seed=12345, scale_factor=10.0)

        assert isinstance(result_small, str)
        assert isinstance(result_large, str)
        assert len(result_small) > 50
        assert len(result_large) > 50

    def test_qgen_ansi_mode(self):
        qgen = QGenBinary()

        result = qgen.generate(1, seed=54321)

        assert isinstance(result, str)
        assert "select" in result.lower() or "with" in result.lower()

        result_lower = result.lower()
        assert "limit" not in result_lower or "order by" in result_lower

    def test_qgen_output_cleaning(self):
        qgen = QGenBinary()

        result = qgen.generate(1, seed=99999)

        assert result == result.strip()

        lines = result.split("\n")
        non_empty_lines = [line for line in lines if line.strip()]
        assert len(non_empty_lines) >= len(lines) // 2

    def test_qgen_timeout_handling(self):
        qgen = QGenBinary()

        import time

        start_time = time.time()
        result = qgen.generate(1, seed=12345)
        execution_time = time.time() - start_time

        assert execution_time < 5.0
        assert isinstance(result, str)
        assert len(result) > 50

    def test_qgen_error_handling(self):
        qgen = QGenBinary()

        with pytest.raises(subprocess.CalledProcessError):
            qgen.generate(25, seed=12345)

    def test_qgen_concurrent_execution(self):
        import threading

        qgen = QGenBinary()
        results = []
        errors = []

        def generate_query(query_id, seed):
            try:
                result = qgen.generate(query_id, seed=seed)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            thread = threading.Thread(target=generate_query, args=(1, 10000 + i))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, f"Concurrent execution errors: {errors}"
        assert len(results) == 5

        for result in results:
            assert isinstance(result, str)
            assert len(result) > 50

    def test_qgen_all_queries_generation(self):
        qgen = QGenBinary()

        for query_id in range(1, 23):
            result = qgen.generate(query_id, seed=77777)
            assert isinstance(result, str), f"Query {query_id} failed"
            assert len(result) > 50, f"Query {query_id} too short"

    def test_qgen_output_consistency(self):
        qgen = QGenBinary()

        results = []
        for _ in range(3):
            result = qgen.generate(1, seed=12345, scale_factor=1.0)
            results.append(result)

        assert len(set(results)) == 1, "QGen should produce consistent output"

    def test_qgen_working_directory(self):
        qgen = QGenBinary()

        result = qgen.generate(1, seed=88888)
        assert isinstance(result, str)
        assert len(result) > 50

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                os.chdir(tmpdir)
                result2 = qgen.generate(1, seed=88888)
                assert result == result2
            finally:
                os.chdir(original_cwd)

    def test_qgen_environment_isolation(self):
        qgen = QGenBinary()

        original_env = os.environ.copy()
        try:
            os.environ["RANDOM_VAR"] = "test_value"
            os.environ["PATH"] = "/nonexistent:" + os.environ.get("PATH", "")

            result = qgen.generate(1, seed=12345)
            assert isinstance(result, str)
            assert len(result) > 50
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_qgen_integration_with_tpch_queries(self):
        queries = TPCHQueries()

        assert queries.qgen is not None
        assert isinstance(queries.qgen, QGenBinary)

        result = queries.get_query(1, seed=12345)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_qgen_binary_version_compatibility(self):
        qgen = QGenBinary()

        try:
            result = subprocess.run([qgen.qgen_path, "-h"], capture_output=True, text=True, timeout=5)
            assert result.returncode in [0, 1]
        except subprocess.TimeoutExpired:
            pytest.skip("QGen binary timeout on help command")

    def test_qgen_output_format_validation(self):
        qgen = QGenBinary()

        for query_id in [1, 5, 10, 15, 20]:
            result = qgen.generate(query_id, seed=12345)

            assert isinstance(result, str)
            assert len(result) > 50

            assert result.isprintable() or all(c in "\t\n\r" for c in result if not c.isprintable())

            result_lower = result.lower()
            assert "select" in result_lower or "with" in result_lower

    def test_qgen_resource_usage(self):
        import os
        import time

        import psutil

        qgen = QGenBinary()

        process = psutil.Process(os.getpid())
        mem_before = process.memory_info().rss

        start_time = time.time()

        for query_id in range(1, 6):
            result = qgen.generate(query_id, seed=12345)
            assert isinstance(result, str)

        execution_time = time.time() - start_time
        mem_after = process.memory_info().rss

        assert execution_time < 10.0
        assert (mem_after - mem_before) < 100 * 1024 * 1024

    def test_qgen_reproducibility(self):
        qgen = QGenBinary()

        session1_results = []
        session2_results = []

        seed = 12345
        for query_id in [1, 2, 3]:
            session1_results.append(qgen.generate(query_id, seed=seed))

        qgen2 = QGenBinary()
        for query_id in [1, 2, 3]:
            session2_results.append(qgen2.generate(query_id, seed=seed))

        assert session1_results == session2_results
