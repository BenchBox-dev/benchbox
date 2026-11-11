from __future__ import annotations

import pytest

from benchbox.core.visualization.exceptions import (
    VisualizationDependencyError,
    VisualizationError,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_dependency_error_is_visualization_error() -> None:
    error = VisualizationDependencyError("matplotlib")
    assert isinstance(error, VisualizationError)
    assert error.package == "matplotlib"
    assert error.advice is None
    assert "matplotlib" in str(error)


def test_dependency_error_appends_advice_to_message() -> None:
    error = VisualizationDependencyError("plotly", advice="Install with: uv add plotly")
    message = str(error)
    assert "plotly" in message
    assert "Install with: uv add plotly" in message


