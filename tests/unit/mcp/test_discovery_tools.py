# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestListPlatformsTool:
    def test_returns_platform_list(self):
        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()

        assert len(all_metadata) > 0
        assert "duckdb" in all_metadata

    def test_platform_has_required_fields(self):
        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()

        for name, metadata in all_metadata.items():
            assert "display_name" in metadata or name
            assert "capabilities" in metadata


class TestListBenchmarksTool:
    def test_returns_benchmark_list(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        benchmarks = get_all_benchmarks()

        assert len(benchmarks) > 0
        assert "tpch" in benchmarks
        assert "tpcds" in benchmarks

    def test_benchmark_has_required_fields(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        benchmarks = get_all_benchmarks()

        for name, meta in benchmarks.items():
            assert "display_name" in meta
            assert "description" in meta
            assert "num_queries" in meta or meta.get("num_queries") == 0
            assert "category" in meta
            assert "support_status" in meta

    def test_list_benchmarks_projects_registry_support_status(self):
        from benchbox.mcp.tools.discovery import _list_benchmarks_impl

        result = _list_benchmarks_impl()
        benchmarks = {row["name"]: row for row in result["benchmarks"]}

        assert benchmarks["tpch"]["support_status"] == "stable"
        assert benchmarks["ai_primitives"]["support_status"] == "experimental"
        assert "joinorder_synthetic" not in benchmarks

    def test_list_benchmarks_matches_registry_for_every_public_benchmark(self):
        from benchbox.core.benchmark_registry import get_benchmark_support_status, list_public_benchmark_ids
        from benchbox.mcp.tools.discovery import _list_benchmarks_impl

        projected = {row["name"]: row["support_status"] for row in _list_benchmarks_impl()["benchmarks"]}
        expected = {bid: get_benchmark_support_status(bid) for bid in list_public_benchmark_ids()}

        assert projected == expected
        assert "beta" in projected.values()
        assert "experimental" in projected.values()

    def test_discovery_reads_live_registry_not_import_bound_metadata(self, monkeypatch: pytest.MonkeyPatch):
        from unittest.mock import patch

        from benchbox.core import benchmark_registry
        from benchbox.mcp.tools import discovery

        monkeypatch.setattr(discovery, "BENCHMARK_METADATA", {}, raising=False)
        fixture = {
            "x_live_registry": {
                "display_name": "Live Registry",
                "description": "synthetic registry fixture",
                "category": "Test",
                "num_queries": 0,
                "query_description": "n/a",
                "supports_streams": False,
                "default_scale": 1.0,
                "scale_options": [1.0],
                "min_scale": 1.0,
                "complexity": "Low",
                "estimated_time_range": (0, 0),
                "supports_dataframe": False,
                "support_status": "deprecated",
                "surface": "public",
            }
        }

        with patch.dict(benchmark_registry.BENCHMARK_METADATA, fixture, clear=False):
            listed = {row["name"]: row for row in discovery._list_benchmarks_impl()["benchmarks"]}
            assert listed["x_live_registry"]["support_status"] == "deprecated"

            info = discovery._get_benchmark_info_impl("x_live_registry")
            assert info["support_status"] == "deprecated"


class TestGetBenchmarkInfoTool:
    def test_get_tpch_info(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        benchmarks = get_all_benchmarks()
        assert "tpch" in benchmarks

        meta = benchmarks["tpch"]
        assert meta["display_name"] == "TPC-H"
        assert meta["num_queries"] == 22
        assert meta["support_status"] == "stable"

    def test_get_benchmark_info_projects_support_status(self):
        from benchbox.mcp.tools.discovery import _get_benchmark_info_impl

        result = _get_benchmark_info_impl("tpch")

        assert result["name"] == "tpch"
        assert result["support_status"] == "stable"

    def test_get_tpcds_info(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        benchmarks = get_all_benchmarks()
        assert "tpcds" in benchmarks

        meta = benchmarks["tpcds"]
        assert meta["display_name"] == "TPC-DS"
        assert meta["num_queries"] == 99

    def test_unknown_benchmark_error(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        benchmarks = get_all_benchmarks()
        assert "nonexistent_benchmark" not in benchmarks

    def test_get_benchmark_info_docstring_does_not_repeat_registry_list(self):

        source = Path("benchbox/mcp/tools/discovery.py").read_text(encoding="utf-8")

        assert "tpch, tpcds, ssb, clickbench" not in source
        assert 'list_available("benchmarks")' in source


class TestSystemProfileTool:
    def test_returns_system_info(self):
        import psutil

        cpu_count = psutil.cpu_count(logical=True)
        memory = psutil.virtual_memory()

        assert cpu_count is not None
        assert cpu_count > 0
        assert memory.total > 0
        assert memory.available > 0

    def test_benchbox_version_available(self):
        import benchbox

        version = getattr(benchbox, "__version__", None)
        assert version is not None


class TestChartDiscoveryContract:
    def test_chart_discovery_returns_complete_semantic_registry(self):
        from benchbox.core.visualization.chart_types import ALL_CHART_TYPES, CHART_TYPE_DESCRIPTIONS
        from benchbox.mcp.tools.discovery import _list_chart_templates_impl

        result = _list_chart_templates_impl()

        assert result["chart_type_coverage"] == "complete_semantic_registry"
        assert result["chart_namespace"] == "benchbox_result_aware_semantic_ids"
        assert set(result["chart_types"]) == set(ALL_CHART_TYPES)
        assert set(result["chart_types"]) == set(CHART_TYPE_DESCRIPTIONS)
        assert result["semantic_chart_ids"] == list(ALL_CHART_TYPES)

    def test_chart_discovery_keeps_textcharts_namespace_external(self):
        from benchbox.mcp.tools.discovery import _list_chart_templates_impl

        result = _list_chart_templates_impl()

        assert "bar" not in result["chart_types"]
        assert "heatmap" not in result["chart_types"]
        assert "query_heatmap" in result["chart_types"]
        assert "textcharts" in result["external_chart_namespaces"]
        assert "not accepted as BenchBox chart_type" in result["external_chart_namespaces"]["textcharts"]

    def test_chart_discovery_templates_follow_registry(self):
        from benchbox.core.visualization.templates import list_templates
        from benchbox.mcp.tools.discovery import _list_chart_templates_impl

        result = _list_chart_templates_impl()

        assert {item["name"] for item in result["templates"]} == {template.name for template in list_templates()}


