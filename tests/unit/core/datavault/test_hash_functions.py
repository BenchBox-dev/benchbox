# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib

import duckdb
import pytest

from benchbox.core.datavault.etl.hash_functions import (
    generate_hash_key,
    generate_hash_key_sql,
    generate_hashdiff,
    generate_hashdiff_sql,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGenerateHashKey:
    def test_single_value(self):
        result = generate_hash_key(1)
        assert len(result) == 32
        assert result == generate_hash_key(1)

    def test_multiple_values(self):
        result = generate_hash_key(1, 2, 3)
        assert len(result) == 32
        assert result == generate_hash_key(1, 2, 3)

    def test_different_values_produce_different_hashes(self):
        hash1 = generate_hash_key(1)
        hash2 = generate_hash_key(2)
        assert hash1 != hash2

    def test_order_matters(self):
        hash1 = generate_hash_key(1, 2)
        hash2 = generate_hash_key(2, 1)
        assert hash1 != hash2

    def test_none_handling(self):
        result = generate_hash_key(None)
        assert len(result) == 32
        assert result == generate_hash_key(None)

    def test_string_values(self):
        result = generate_hash_key("test")
        assert len(result) == 32

    def test_sha1_algorithm_raises(self):
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            generate_hash_key(1, algorithm="sha1")

    def test_sha256_algorithm_accepted(self):
        result = generate_hash_key(1, algorithm="sha256")
        assert len(result) == 64

    def test_unsupported_algorithm_raises(self):
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            generate_hash_key(1, algorithm="invalid")


class TestGenerateHashdiff:
    def test_hashdiff_same_as_hash_key(self):
        attrs = ("John", "Doe", "123 Main St")
        hashdiff = generate_hashdiff(*attrs)
        hash_key = generate_hash_key(*attrs)
        assert hashdiff == hash_key

    def test_hashdiff_detects_changes(self):
        hashdiff1 = generate_hashdiff("John", "Doe")
        hashdiff2 = generate_hashdiff("Jane", "Doe")
        assert hashdiff1 != hashdiff2


class TestGenerateHashKeySql:
    def test_single_column(self):
        result = generate_hash_key_sql("c_custkey")
        assert "md5" in result
        assert "c_custkey" in result
        assert "CAST" in result
        assert "VARCHAR" in result

    def test_multiple_columns(self):
        result = generate_hash_key_sql("ps_partkey", "ps_suppkey")
        assert "md5" in result
        assert "ps_partkey" in result
        assert "ps_suppkey" in result
        assert "||" in result
        assert "'|'" in result

    def test_with_table_alias(self):
        result = generate_hash_key_sql("c_custkey", table_alias="c")
        assert "c.c_custkey" in result

    def test_sha256_algorithm_accepted(self):
        result = generate_hash_key_sql("c_custkey", algorithm="sha256")
        assert "sha256" in result
        assert "c_custkey" in result

    def test_unsupported_algorithm_raises(self):
        with pytest.raises(ValueError, match="only supports"):
            generate_hash_key_sql("col", algorithm="sha1")


class TestGenerateHashdiffSql:
    def test_hashdiff_uses_coalesce(self):
        result = generate_hashdiff_sql("c_name", "c_address")
        assert "COALESCE" in result
        assert "md5" in result
        assert "c_name" in result
        assert "c_address" in result

    def test_hashdiff_with_alias(self):
        result = generate_hashdiff_sql("c_name", table_alias="sc")
        assert "sc.c_name" in result

    def test_sha256_algorithm_accepted(self):
        result = generate_hashdiff_sql("c_name", "c_address", algorithm="sha256")
        assert "sha256" in result
        assert "COALESCE" in result

    def test_unsupported_algorithm_raises(self):
        with pytest.raises(ValueError, match="only supports"):
            generate_hashdiff_sql("col", algorithm="sha1")


class TestSqlRoundTrip:
    @pytest.fixture(scope="class")
    def conn(self):
        connection = duckdb.connect(":memory:")
        yield connection
        connection.close()

    @pytest.mark.parametrize(
        ("algorithm", "expected_len"),
        [("md5", 32), ("sha256", 64)],
    )
    def test_hash_key_sql_matches_hashlib(self, conn, algorithm, expected_len):
        expr = generate_hash_key_sql("c_custkey", algorithm=algorithm)
        sql_result = conn.execute(f"SELECT {expr} FROM (SELECT 42 AS c_custkey)").fetchone()[0]
        py_result = generate_hash_key(42, algorithm=algorithm)
        assert sql_result == py_result
        assert len(sql_result) == expected_len

    @pytest.mark.parametrize(
        ("algorithm", "expected_len"),
        [("md5", 32), ("sha256", 64)],
    )
    def test_hashdiff_sql_matches_hashlib(self, conn, algorithm, expected_len):
        expr = generate_hashdiff_sql("c_name", "c_address", algorithm=algorithm)
        sql_result = conn.execute(
            f"SELECT {expr} FROM (SELECT 'John' AS c_name, '123 Main St' AS c_address)"
        ).fetchone()[0]
        py_result = hashlib.new(algorithm, b"John|123 Main St").hexdigest()
        assert sql_result == py_result
        assert len(sql_result) == expected_len

    def test_hashdiff_sql_null_handling(self, conn):
        expr = generate_hashdiff_sql("c_name", "c_address", algorithm="sha256")
        sql_result = conn.execute(
            f"SELECT {expr} FROM (SELECT 'John' AS c_name, CAST(NULL AS VARCHAR) AS c_address)"
        ).fetchone()[0]
        py_result = hashlib.sha256(b"John|").hexdigest()
        assert sql_result == py_result


