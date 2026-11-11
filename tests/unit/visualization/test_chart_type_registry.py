from __future__ import annotations

import pytest

from benchbox.cli.commands.visualize import SUPPORTED_CHART_TYPES
from benchbox.core.visualization import ascii_runtime
from benchbox.core.visualization.chart_types import ALL_CHART_TYPES
from benchbox.core.visualization.exporters import PRIMITIVE_ASCII_CHART_TYPES, RESULT_AWARE_ONLY_CHART_TYPES
from benchbox.core.visualization.templates import list_templates
from benchbox.mcp.tools.discovery import _list_chart_templates_impl
from benchbox.mcp.tools.visualization import CHART_TYPE_DESCRIPTIONS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_cli_and_mcp_use_canonical_chart_registry():
    assert set(SUPPORTED_CHART_TYPES) == set(ALL_CHART_TYPES)
    assert set(CHART_TYPE_DESCRIPTIONS.keys()) == set(ALL_CHART_TYPES)


def test_runtime_dispatch_covers_canonical_chart_registry():
    canonical = set(ALL_CHART_TYPES)
    dispatch = set(ascii_runtime._CHART_TYPE_DISPATCH)

    assert sorted(canonical - dispatch) == []
    assert sorted(dispatch - canonical) == []


def test_data_first_exporter_declares_intentional_partial_coverage():
    canonical = set(ALL_CHART_TYPES)
    primitive = set(PRIMITIVE_ASCII_CHART_TYPES)
    result_aware_only = set(RESULT_AWARE_ONLY_CHART_TYPES)

    assert sorted(canonical - primitive) == sorted(result_aware_only)
    assert sorted(primitive - canonical) == []
    assert result_aware_only == {"power_bar"}


def test_discovery_chart_metadata_covers_semantic_registry():
    metadata = _list_chart_templates_impl()

    assert metadata["chart_type_coverage"] == "complete_semantic_registry"
    assert metadata["chart_namespace"] == "benchbox_result_aware_semantic_ids"
    assert sorted(set(ALL_CHART_TYPES) - set(metadata["chart_types"])) == []
    assert sorted(set(metadata["chart_types"]) - set(ALL_CHART_TYPES)) == []


def test_all_template_chart_types_exist_in_registry():
    known = set(ALL_CHART_TYPES)
    for template in list_templates():
        assert set(template.chart_types).issubset(known), template.name


