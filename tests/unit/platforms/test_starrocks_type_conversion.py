# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.platforms.starrocks.workload import StarRocksWorkloadMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def mixin() -> StarRocksWorkloadMixin:
    return StarRocksWorkloadMixin.__new__(StarRocksWorkloadMixin)


def _columns(mixin, ddl: str) -> str:
    out = mixin._translate_schema_statement(ddl)
    return out[out.index("(") : out.index(")") + 1].replace("`", "")


class TestTypeConversion:
    def test_integer_becomes_int(self, mixin) -> None:
        assert _columns(mixin, "CREATE TABLE t (a INTEGER)") == "(a INT)"

    def test_smallint_promoted_for_clickbench_range(self, mixin) -> None:
        assert _columns(mixin, "CREATE TABLE t (a SMALLINT)") == "(a INT)"

    def test_uppercase_timestamp_becomes_datetime(self, mixin) -> None:
        out = mixin._translate_schema_statement("CREATE TABLE t (ts TIMESTAMP)")
        assert "DATETIME" in out
        assert "TIMESTAMP" not in out.upper().replace("DATETIME", "")

    def test_timestamp_column_name_preserved(self, mixin) -> None:
        out = _columns(mixin, "CREATE TABLE t (timestamp BIGINT)")
        assert out == "(timestamp BIGINT)"

    def test_lowercase_type_in_type_position_normalized(self, mixin) -> None:
        assert _columns(mixin, "CREATE TABLE t (ts timestamp)") == "(ts DATETIME)"

    def test_bare_varchar_gets_size_but_sized_varchar_kept(self, mixin) -> None:
        assert "VARCHAR(65533)" in _columns(mixin, "CREATE TABLE t (c VARCHAR)")
        assert "VARCHAR(50)" in _columns(mixin, "CREATE TABLE t (c VARCHAR(50))")

    def test_string_and_text_become_sized_varchar(self, mixin) -> None:
        assert "VARCHAR(65533)" in _columns(mixin, "CREATE TABLE t (c STRING)")
        assert "VARCHAR(65533)" in _columns(mixin, "CREATE TABLE t (c TEXT)")


class TestOptimizeBaseline:
    def test_baseline_adds_duplicate_key_and_distribution(self, mixin) -> None:
        out = mixin._translate_schema_statement("CREATE TABLE lineitem (l_orderkey BIGINT, l_partkey BIGINT);")
        flat = out.replace("`", "")
        assert "DUPLICATE KEY (l_orderkey)" in flat
        assert "DISTRIBUTED BY HASH (l_orderkey) BUCKETS 8" in flat

    def test_non_create_passes_through(self, mixin) -> None:
        stmt = "INSERT INTO t VALUES (1)"
        assert mixin._translate_schema_statement(stmt) == stmt

    def test_autoincrement_removed(self, mixin) -> None:
        out = mixin._translate_schema_statement("CREATE TABLE t (id BIGINT AUTOINCREMENT, x INT);")
        assert "AUTOINCREMENT" not in out.upper()
