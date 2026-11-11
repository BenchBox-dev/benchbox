from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.visualization.ascii_api import ChartOptions
from benchbox.core.visualization.orchestration import render_chart_set
from benchbox.core.visualization.suggestions import build_visualization_profile, recommend_charts
from tests.fixtures.result_dict_fixtures import make_normalized_result

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_recommend_charts_includes_power_bar_for_power_runs() -> None:
    result = make_normalized_result(
        benchmark="joinorder",
        raw={"benchmark": {"test_type": "power"}},
        queries=[SimpleNamespace(query_id="1a", execution_time_ms=12.0)],
    )

    recommendations = recommend_charts([result])

    assert "power_bar" in [recommendation.chart_type for recommendation in recommendations]
    assert build_visualization_profile([result]).has_power_data is True


def test_pairwise_only_recommendations_require_exactly_two_results() -> None:
    one = make_normalized_result(queries=[SimpleNamespace(query_id="Q1", execution_time_ms=10.0)])
    two = make_normalized_result(platform="Postgres", queries=[SimpleNamespace(query_id="Q1", execution_time_ms=20.0)])

    single_types = {recommendation.chart_type for recommendation in recommend_charts([one])}
    pair_types = {recommendation.chart_type for recommendation in recommend_charts([one, two])}

    assert "comparison_bar" not in single_types
    assert "diverging_bar" not in single_types
    assert "comparison_bar" in pair_types
    assert "diverging_bar" in pair_types


def test_render_chart_set_distinguishes_unknown_from_known_inapplicable() -> None:
    result = make_normalized_result(queries=[SimpleNamespace(query_id="Q1", execution_time_ms=10.0)])

    unknown = render_chart_set([result], ["not_a_chart"], options=ChartOptions(use_color=False))
    inapplicable = render_chart_set([result], ["stacked_phase"], options=ChartOptions(use_color=False))

    assert unknown.validation_error is not None
    assert "Unknown chart type" in unknown.validation_error.message
    assert inapplicable.validation_error is None
    assert inapplicable.rendered == []
    assert inapplicable.skipped[0].chart_type == "stacked_phase"
    assert "phase" in inapplicable.skipped[0].reason


def test_render_chart_set_template_tracks_rendered_and_skipped_charts() -> None:
    result = make_normalized_result(queries=[SimpleNamespace(query_id="Q1", execution_time_ms=10.0)])

    outcome = render_chart_set([result], template_name="default", options=ChartOptions(use_color=False))

    assert outcome.template is not None
    assert outcome.template.name == "default"
    assert outcome.rendered
    assert {chart.chart_type for chart in outcome.rendered}.issubset(set(outcome.template.chart_types))
    assert {skipped.chart_type for skipped in outcome.skipped}.issubset(set(outcome.template.chart_types))


def test_render_chart_set_continues_after_renderer_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.core.visualization import ascii_api

    result = make_normalized_result(queries=[SimpleNamespace(query_id="Q1", execution_time_ms=10.0)])

    def fake_renderer(results, chart_type, *, options=None, subtitle=None):
        if chart_type == "performance_bar":
            raise RuntimeError("boom")
        return f"rendered {chart_type}"

    monkeypatch.setattr(ascii_api, "render_ascii_chart_from_results", fake_renderer)

    outcome = render_chart_set(
        [result],
        ["performance_bar", "power_bar"],
        options=ChartOptions(use_color=False),
    )

    assert outcome.validation_error is None
    assert outcome.chart_types_rendered == ["power_bar"]
    assert len(outcome.skipped) == 1
    skipped = outcome.skipped[0]
    assert skipped.chart_type == "performance_bar"
    assert skipped.reason == "renderer failed"
    assert skipped.details == {"exception_type": "RuntimeError", "message": "boom"}


def test_non_comparison_templates_skip_pairwise_charts_without_failing() -> None:
    result = make_normalized_result(
        raw={"benchmark": {"test_type": "power"}},
        queries=[SimpleNamespace(query_id="Q1", execution_time_ms=10.0)],
    )

    outcome = render_chart_set([result], template_name="regression_triage", options=ChartOptions(use_color=False))

    assert outcome.validation_error is None
    assert outcome.rendered
    assert {"comparison_bar", "diverging_bar"}.issubset({skipped.chart_type for skipped in outcome.skipped})


def test_comparison_template_keeps_strict_pairwise_validation() -> None:
    result = make_normalized_result(queries=[SimpleNamespace(query_id="Q1", execution_time_ms=10.0)])

    outcome = render_chart_set([result], template_name="comparison", options=ChartOptions(use_color=False))

    assert outcome.validation_error is not None
    assert "require exactly 2 results" in outcome.validation_error.message


