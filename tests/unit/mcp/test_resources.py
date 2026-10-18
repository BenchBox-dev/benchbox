# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _future_benchmark_meta(support_status: str, surface: str) -> dict[str, object]:
    return {
        "display_name": f"Future {support_status}/{surface}",
        "description": "synthetic future-status fixture",
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
        "support_status": support_status,
        "surface": surface,
    }


class TestBenchmarkResources:
    def test_list_benchmarks_resource_format(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        all_benchmarks = get_all_benchmarks()
        benchmarks = []
        for name, meta in all_benchmarks.items():
            benchmarks.append(
                {
                    "name": name,
                    "display_name": meta.get("display_name", name),
                    "description": meta.get("description", ""),
                    "category": meta.get("category", "unknown"),
                    "support_status": meta.get("support_status", "unknown"),
                    "query_count": meta.get("num_queries", 0),
                }
            )

        result = {"benchmarks": benchmarks, "count": len(benchmarks)}
        json_str = json.dumps(result)

        parsed = json.loads(json_str)
        assert "benchmarks" in parsed
        assert "count" in parsed
        assert parsed["count"] > 0

    def test_list_benchmarks_resource_projects_support_status(self):
        from benchbox.mcp.resources.registry import _build_benchmarks_list

        parsed = json.loads(_build_benchmarks_list())
        benchmarks = {row["name"]: row for row in parsed["benchmarks"]}

        assert benchmarks["tpch"]["support_status"] == "stable"
        assert benchmarks["ai_primitives"]["support_status"] == "experimental"
        assert "joinorder_synthetic" not in benchmarks

    def test_get_benchmark_resource_tpch(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks

        all_benchmarks = get_all_benchmarks()
        meta = all_benchmarks["tpch"]

        result = {
            "name": "tpch",
            "display_name": meta.get("display_name"),
            "description": meta.get("description"),
            "support_status": meta.get("support_status"),
            "query_count": meta.get("num_queries"),
        }

        assert result["name"] == "tpch"
        assert result["display_name"] == "TPC-H"
        assert result["support_status"] == "stable"
        assert result["query_count"] == 22

    def test_get_benchmark_resource_projects_support_status(self):
        from benchbox.mcp.resources.registry import _build_benchmark_detail

        parsed = json.loads(_build_benchmark_detail("tpch"))

        assert parsed["name"] == "tpch"
        assert parsed["support_status"] == "stable"

    def test_internal_future_status_hidden_from_resources(self):
        from unittest.mock import patch

        from benchbox.core import benchmark_registry
        from benchbox.mcp.resources.registry import _build_benchmark_detail, _build_benchmarks_list

        fixtures = {"x_future_internal": _future_benchmark_meta("document_only", "internal")}
        with patch.dict(benchmark_registry.BENCHMARK_METADATA, fixtures, clear=False):
            listed = {row["name"] for row in json.loads(_build_benchmarks_list())["benchmarks"]}
            assert "x_future_internal" not in listed

            detail = json.loads(_build_benchmark_detail("x_future_internal"))
            assert "error" in detail
            assert "support_status" not in detail

    def test_public_future_status_exposed_in_resources(self):
        from unittest.mock import patch

        from benchbox.core import benchmark_registry
        from benchbox.mcp.resources.registry import _build_benchmark_detail, _build_benchmarks_list

        fixtures = {"x_future_public": _future_benchmark_meta("deprecated", "public")}
        with patch.dict(benchmark_registry.BENCHMARK_METADATA, fixtures, clear=False):
            listed = {row["name"]: row for row in json.loads(_build_benchmarks_list())["benchmarks"]}
            assert listed["x_future_public"]["support_status"] == "deprecated"

            detail = json.loads(_build_benchmark_detail("x_future_public"))
            assert detail["support_status"] == "deprecated"


class TestPlatformResources:
    def test_list_platforms_resource_format(self):
        from benchbox.core.platform_registry import PlatformRegistry

        platforms = []
        all_metadata = PlatformRegistry.get_all_platform_metadata()

        for name, metadata in all_metadata.items():
            capabilities = metadata.get("capabilities", {})
            info = PlatformRegistry.get_platform_info(name)

            platforms.append(
                {
                    "name": name,
                    "display_name": metadata.get("display_name", name),
                    "category": metadata.get("category", "unknown"),
                    "available": info.available if info else False,
                    "supports_sql": capabilities.get("supports_sql", False),
                    "supports_dataframe": capabilities.get("supports_dataframe", False),
                }
            )

        result = {"platforms": platforms, "count": len(platforms)}
        json_str = json.dumps(result)

        parsed = json.loads(json_str)
        assert "platforms" in parsed
        assert "count" in parsed
        assert parsed["count"] > 0

    def test_get_platform_resource_duckdb(self):
        from benchbox.core.platform_registry import PlatformRegistry

        all_metadata = PlatformRegistry.get_all_platform_metadata()
        metadata = all_metadata["duckdb"]
        info = PlatformRegistry.get_platform_info("duckdb")

        assert metadata is not None
        assert info is not None
        assert info.available


class TestSystemResources:
    def test_system_profile_resource_format(self):
        import platform

        import psutil

        import benchbox

        memory = psutil.virtual_memory()

        result = {
            "cpu": {
                "cores": psutil.cpu_count(logical=False) or 1,
                "threads": psutil.cpu_count(logical=True) or 1,
                "architecture": platform.machine(),
            },
            "memory": {
                "total_gb": round(memory.total / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
            },
            "python": {
                "version": platform.python_version(),
            },
            "benchbox_version": getattr(benchbox, "__version__", "unknown"),
        }

        json_str = json.dumps(result)
        parsed = json.loads(json_str)

        assert "cpu" in parsed
        assert "memory" in parsed
        assert parsed["cpu"]["threads"] > 0
        assert parsed["memory"]["total_gb"] > 0
