# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.tpch.queries import QGenBinary

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQGenBinary:
    def test_qgen_discovery_success(self):
        qgen = QGenBinary()
        assert qgen.qgen_path is not None
        assert Path(qgen.qgen_path).exists()
        assert Path(qgen.qgen_path).is_file()
        assert qgen.templates_dir is not None
        assert Path(qgen.templates_dir).exists()

    @patch("benchbox.core.tpch.queries.Path.exists")
    def test_qgen_discovery_failure(self, mock_exists):
        mock_exists.return_value = False

        with pytest.raises(RuntimeError) as exc_info:
            QGenBinary()

        assert "qgen binary required but not found" in str(exc_info.value)
        assert "TPC-H requires the compiled qgen tool" in str(exc_info.value)

    def test_generate_basic_query(self):
        qgen = QGenBinary()

        sql = qgen.generate(1)

        assert isinstance(sql, str)
        assert len(sql) > 100
        assert "select" in sql.lower()
        assert "lineitem" in sql.lower()
        assert ":1" not in sql

    def test_generate_with_seed(self):
        qgen = QGenBinary()

        sql1 = qgen.generate(1, seed=12345)
        sql2 = qgen.generate(1, seed=12345)

        assert sql1 == sql2

        sql3 = qgen.generate(1, seed=54321)

        assert sql1 != sql3

    def test_generate_with_scale_factor(self):
        qgen = QGenBinary()

        sql_sf1 = qgen.generate(1, scale_factor=1.0)
        sql_sf01 = qgen.generate(1, scale_factor=0.1)

        assert isinstance(sql_sf1, str)
        assert isinstance(sql_sf01, str)
        assert len(sql_sf1) > 0
        assert len(sql_sf01) > 0

    def test_sql_cleaning(self):
        qgen = QGenBinary()

        test_sql = """-- TPC Query comment
        select * from test;
        go
        """

        cleaned = qgen._clean_sql(test_sql)

        assert "-- TPC Query comment" not in cleaned
        assert "go" not in cleaned.lower()
        assert "select * from test" in cleaned

    def test_subprocess_timeout(self):
        qgen = QGenBinary()

        sql = qgen.generate(1)
        assert len(sql) > 0

    def test_invalid_query_id_handling(self):
        qgen = QGenBinary()

        with pytest.raises(subprocess.CalledProcessError):
            qgen.generate(23)

        with pytest.raises(subprocess.CalledProcessError):
            qgen.generate(0)

    def test_generate_multiple_queries(self):
        qgen = QGenBinary()

        queries = {}
        for query_id in [1, 2, 3, 5, 10]:
            sql = qgen.generate(query_id, seed=42)
            queries[query_id] = sql

            assert isinstance(sql, str)
            assert len(sql) > 50
            assert ":" not in sql

        sqls = list(queries.values())
        for i, sql1 in enumerate(sqls):
            for j, sql2 in enumerate(sqls):
                if i != j:
                    assert sql1 != sql2

    def test_seed_parameter_determinism(self):
        qgen = QGenBinary()

        results1 = {}
        results2 = {}

        for query_id in [1, 2, 3]:
            results1[query_id] = qgen.generate(query_id, seed=999)
            results2[query_id] = qgen.generate(query_id, seed=999)

        assert results1 == results2

    def test_working_directory_isolation(self):
        qgen = QGenBinary()
        original_cwd = os.getcwd()

        qgen.generate(1)

        assert os.getcwd() == original_cwd

    @patch("subprocess.run")
    def test_subprocess_error_propagation(self, mock_run):
        qgen = QGenBinary()

        mock_run.side_effect = subprocess.CalledProcessError(1, "qgen", stderr="Mock error")

        with pytest.raises(subprocess.CalledProcessError):
            qgen.generate(1)

    def test_ansi_mode_flag(self):
        qgen = QGenBinary()

        sql = qgen.generate(1)

        assert isinstance(sql, str)
        assert len(sql) > 0


