from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from benchbox.core.visualization.exceptions import VisualizationError
from benchbox.core.visualization.exporters import export_ascii, render_ascii_chart

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class _Point:
    label: str
    value: float


def test_export_ascii_writes_with_expected_extension(tmp_path: Path) -> None:
    output = export_ascii("hello", tmp_path, "chart", format="ascii")
    assert output.name == "chart.txt"
    assert output.read_text(encoding="utf-8") == "hello"


def test_export_ascii_wraps_io_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)

    with pytest.raises(VisualizationError, match="Failed to write ASCII chart"):
        export_ascii("x", tmp_path, "chart")


def test_render_ascii_chart_rejects_bad_heatmap_data() -> None:
    with pytest.raises(VisualizationError, match="Heatmap data must be a dict"):
        render_ascii_chart("query_heatmap", [1, 2, 3])


def test_render_ascii_chart_handles_performance_bar_objects() -> None:
    rendered = render_ascii_chart("performance_bar", [_Point("A", 10), _Point("B", 5)], title="Perf")
    assert "Perf" in rendered
    assert "A" in rendered


def test_render_ascii_chart_rejects_unknown_type() -> None:
    with pytest.raises(VisualizationError, match="Unsupported ASCII chart type"):
        render_ascii_chart("nope", [])


def test_render_ascii_chart_explains_result_aware_only_chart() -> None:
    with pytest.raises(VisualizationError, match="requires normalized BenchBox results"):
        render_ascii_chart("power_bar", [])


