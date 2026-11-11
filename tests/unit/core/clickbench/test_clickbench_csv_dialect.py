# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestClickBenchCsvDialect:
    def test_csv_delimiter_is_pipe(self) -> None:
        assert ClickBenchBenchmark().csv_delimiter == "|"

    def test_csv_null_marker_is_sentinel(self) -> None:

        assert ClickBenchBenchmark().csv_null_marker == "__NULL__"

    def test_facade_delegates_csv_dialect(self, tmp_path) -> None:

        from benchbox.clickbench import ClickBench

        facade = ClickBench(scale_factor=0.1, output_dir=tmp_path)
        assert facade.csv_delimiter == "|"
        assert facade.csv_null_marker == "__NULL__"


class TestClickBenchSnowflakeQ29Rewrite:
    def _q29(self, dialect: str) -> str:
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        return ClickBenchBenchmark().get_queries(dialect=dialect)["Q29"]

    def test_snowflake_q29_uses_capturing_group(self) -> None:
        bs = chr(92)
        q29 = self._q29("snowflake")
        assert "(?:" not in q29
        assert "(www" + bs * 2 + ".)?" in q29
        assert "'" + bs * 2 + "2'" in q29

    def test_other_dialects_keep_source_spelling(self) -> None:
        bs = chr(92)
        q29 = self._q29("bigquery")
        assert "(?:www" + bs * 2 + ".)?" in q29
        assert "'" + bs * 2 + "1'" in q29


