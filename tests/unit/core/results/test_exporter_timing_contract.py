from __future__ import annotations

import pytest

from benchbox.core.results.exporter import ResultExporter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_render_query_row_uses_execution_time_seconds_when_ms_missing(tmp_path) -> None:
    exporter = ResultExporter(output_dir=tmp_path, anonymize=False)
    row = exporter._render_query_row(
        {
            "query_id": "Q1",
            "status": "SUCCESS",
            "execution_time_seconds": 1.25,
            "rows_returned": 3,
        }
    )

    assert "1250.0" in row


