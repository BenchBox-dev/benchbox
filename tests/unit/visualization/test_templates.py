from __future__ import annotations

import pytest

from benchbox.core.visualization import (
    VisualizationError,
)
from benchbox.core.visualization.templates import (
    ChartTemplate,
    get_template,
    list_templates,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGetTemplate:
    def test_default_template(self):
        template = get_template("default")
        assert template.name == "default"
        assert "performance_bar" in template.chart_types

    def test_flagship_template(self):
        template = get_template("flagship")
        assert template.name == "flagship"
        assert "cost_scatter" in template.chart_types
        assert "ascii" in template.formats

    def test_head_to_head_template(self):
        template = get_template("head_to_head")
        assert template.name == "head_to_head"

    def test_trends_template(self):
        template = get_template("trends")
        assert "time_series" in template.chart_types

    def test_cost_optimization_template(self):
        template = get_template("cost_optimization")
        assert "cost_scatter" in template.chart_types

    def test_case_insensitive(self):
        assert get_template("DEFAULT").name == "default"
        assert get_template("Flagship").name == "flagship"

    def test_hyphen_to_underscore(self):

        template = get_template("head-to-head")
        assert template.name == "head_to_head"

    def test_unknown_template_raises_error(self):
        with pytest.raises(VisualizationError, match="Unknown chart template"):
            get_template("nonexistent")


class TestListTemplates:
    def test_returns_all_templates(self):
        templates = list_templates()
        assert len(templates) >= 5
        names = {t.name for t in templates}
        assert "default" in names
        assert "flagship" in names
        assert "head_to_head" in names
        assert "trends" in names
        assert "cost_optimization" in names
        assert "latency_deep_dive" in names
        assert "regression_triage" in names
        assert "executive_summary" in names

    def test_returns_chart_template_instances(self):
        templates = list_templates()
        for t in templates:
            assert isinstance(t, ChartTemplate)


class TestChartTemplate:
    def test_frozen_dataclass(self):
        from dataclasses import FrozenInstanceError

        template = ChartTemplate(
            name="test",
            description="Test template",
            chart_types=("performance_bar",),
        )
        with pytest.raises(FrozenInstanceError):
            template.name = "modified"

    def test_default_formats(self):
        template = ChartTemplate(
            name="test",
            description="Test",
            chart_types=("performance_bar",),
        )
        assert template.formats == ("ascii",)


