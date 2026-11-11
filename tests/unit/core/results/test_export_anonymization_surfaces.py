# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from types import SimpleNamespace

import pytest

from benchbox.core.results.exporter import ResultExporter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SENTINEL_ERROR = "connect databend://joe:PW-SENTINEL@secret-host.internal:8000 refused"


def _result_with_error():
    return SimpleNamespace(
        query_results=[
            {
                "query_id": "Q1",
                "status": "FAILED",
                "execution_time_ms": 5.0,
                "rows_returned": 0,
                "error_message": SENTINEL_ERROR,
            }
        ]
    )


class TestCsvHtmlAnonymization:
    def test_csv_scrubs_error_text_when_anonymizing(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
        path = exporter._export_csv_detailed(_result_with_error(), "run1")
        content = path.read_text(encoding="utf-8")
        assert "PW-SENTINEL" not in content
        assert "Q1" in content, "column layout keeps non-sensitive values"

    def test_html_scrubs_error_text_when_anonymizing(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
        path = exporter._export_html_detailed(_result_with_error(), "run1")
        assert "PW-SENTINEL" not in path.read_text(encoding="utf-8")

    def test_csv_error_text_unchanged_without_anonymization(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path, anonymize=False)
        path = exporter._export_csv_detailed(_result_with_error(), "run1")
        assert SENTINEL_ERROR in path.read_text(encoding="utf-8")

    def test_html_escapes_untrusted_metadata_and_query_values(self, tmp_path):
        untrusted = '"><script>alert(1)</script>'
        result = SimpleNamespace(
            benchmark_name=untrusted,
            execution_id=untrusted,
            platform=untrusted,
            query_results=[
                {
                    "query_id": untrusted,
                    "status": untrusted,
                    "execution_time_ms": 1.0,
                    "rows_returned": untrusted,
                    "error_message": untrusted,
                }
            ],
        )

        path = ResultExporter(output_dir=tmp_path)._export_html_detailed(result, "run1")
        content = path.read_text(encoding="utf-8")

        assert untrusted not in content
        assert "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in content

    def test_html_row_shape_preserved(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
        row = exporter._render_query_row(
            {"query_id": "Q1", "status": "SUCCESS", "execution_time_ms": 1.0, "rows_returned": 2}
        )
        assert row.count("<td>") + row.count("<td class=") == 5


class TestScrubFreeTextHelper:
    def test_noop_when_not_anonymizing(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path, anonymize=False)
        assert exporter._anonymize_free_text(SENTINEL_ERROR) == SENTINEL_ERROR

    def test_scrubs_connection_string_material(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
        assert "PW-SENTINEL" not in str(exporter._anonymize_free_text(SENTINEL_ERROR))

    def test_empty_values_pass_through(self, tmp_path):
        exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
        assert exporter._anonymize_free_text("") == ""
        assert exporter._anonymize_free_text(None) is None


