from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")

CHEAP_BENCHMARKS = ["tpch", "coffeeshop"]


class TestOneSharedBuilder:
    def test_resource_delegates_to_the_canonical_builder(self):
        from benchbox.mcp.resources import registry as registry_module

        sentinel = {"found": False, "requested": "tpch", "available": ["tpch"]}
        with patch.object(registry_module, "build_benchmark_payload", return_value=sentinel) as builder:
            json.loads(registry_module._build_benchmark_detail("tpch"))

        builder.assert_called_once_with("tpch")

    def test_tool_delegates_to_the_canonical_builder(self):
        from benchbox.mcp.tools import discovery as discovery_module

        sentinel = {"found": False, "requested": "tpch", "available": ["tpch"]}
        with patch.object(discovery_module, "build_benchmark_payload", return_value=sentinel) as builder:
            discovery_module._get_benchmark_info_impl("tpch")

        builder.assert_called_once_with("tpch")

    def test_both_platform_surfaces_delegate_to_the_canonical_builder(self):
        from benchbox.mcp.resources import registry as registry_module
        from benchbox.mcp.tools import discovery as discovery_module

        with patch.object(registry_module, "build_platform_payloads", return_value=[]) as resource_builder:
            json.loads(registry_module._build_platforms_list())
        with patch.object(discovery_module, "build_platform_payloads", return_value=[]) as tool_builder:
            discovery_module._list_platforms_impl()

        resource_builder.assert_called_once_with()
        tool_builder.assert_called_once_with()


class TestBenchmarkProjections:
    @pytest.mark.parametrize("benchmark_id", CHEAP_BENCHMARKS)
    def test_resource_and_tool_agree_on_every_shared_field(self, benchmark_id: str):
        from benchbox.mcp.resources.registry import _build_benchmark_detail
        from benchbox.mcp.tools.discovery import _get_benchmark_info_impl

        resource = json.loads(_build_benchmark_detail(benchmark_id))
        tool = _get_benchmark_info_impl(benchmark_id)

        for field in ("name", "display_name", "description", "category", "support_status", "complexity"):
            assert resource[field] == tool[field], field
        assert resource["scale_factors"] == tool["scale_factors"]
        assert resource["dataframe_support"] == tool["dataframe_support"]
        assert resource["query_count"] == tool["queries"]["count"]

    @pytest.mark.parametrize("benchmark_id", CHEAP_BENCHMARKS)
    def test_resource_query_ids_are_populated(self, benchmark_id: str):
        from benchbox.mcp.resources.registry import _build_benchmark_detail

        resource = json.loads(_build_benchmark_detail(benchmark_id))

        assert resource["query_ids"], f"{benchmark_id} reports no query ids"

    def test_the_tool_truncates_and_the_resource_does_not(self):
        from benchbox.mcp.resources import registry as registry_module
        from benchbox.mcp.tools import discovery as discovery_module
        from benchbox.mcp.tools.discovery import BENCHMARK_QUERY_ID_TOOL_LIMIT, build_benchmark_payload

        payload = dict(build_benchmark_payload("tpch"))
        payload["query_ids"] = [f"q{i}" for i in range(BENCHMARK_QUERY_ID_TOOL_LIMIT + 5)]

        with patch.object(registry_module, "build_benchmark_payload", return_value=payload):
            resource = json.loads(registry_module._build_benchmark_detail("tpch"))
        with patch.object(discovery_module, "build_benchmark_payload", return_value=payload):
            tool = discovery_module._get_benchmark_info_impl("tpch")

        assert len(resource["query_ids"]) == BENCHMARK_QUERY_ID_TOOL_LIMIT + 5
        assert tool["queries"]["ids"] == resource["query_ids"][:BENCHMARK_QUERY_ID_TOOL_LIMIT]
        assert tool["queries"]["truncated"] is True

    def test_both_surfaces_reject_a_non_public_benchmark_the_same_way(self):
        from benchbox.mcp.resources.registry import _build_benchmark_detail
        from benchbox.mcp.tools.discovery import _get_benchmark_info_impl

        resource = json.loads(_build_benchmark_detail("joinorder_synthetic"))
        tool = _get_benchmark_info_impl("joinorder_synthetic")

        assert "error" in resource
        assert "error" in tool
        assert resource["available"] == tool["available_benchmarks"]

    def test_resource_keeps_its_own_field_names(self):
        from benchbox.mcp.resources.registry import _build_benchmark_detail
        from benchbox.mcp.tools.discovery import _get_benchmark_info_impl

        resource = json.loads(_build_benchmark_detail("tpch"))
        tool = _get_benchmark_info_impl("tpch")

        assert set(resource) - set(tool) == {"query_count", "query_ids", "estimated_time_minutes"}
        assert set(tool) - set(resource) == {"queries", "schema", "supports_streams"}


class TestPlatformProjections:
    def test_resource_platforms_are_a_projection_of_the_tool_payload(self):
        from benchbox.mcp.resources.registry import _build_platforms_list
        from benchbox.mcp.tools.discovery import _list_platforms_impl

        resource = json.loads(_build_platforms_list())
        tool = _list_platforms_impl()

        assert resource["count"] == tool["count"]
        assert [p["name"] for p in resource["platforms"]] == [p["name"] for p in tool["platforms"]]
        for resource_platform, tool_platform in zip(resource["platforms"], tool["platforms"], strict=True):
            for field in ("display_name", "category", "available", "supports_sql", "supports_dataframe"):
                assert resource_platform[field] == tool_platform[field], field

    def test_resource_omits_the_tool_only_fields(self):
        from benchbox.mcp.resources.registry import _build_platforms_list
        from benchbox.mcp.tools.discovery import _list_platforms_impl

        resource = json.loads(_build_platforms_list())["platforms"][0]
        tool = _list_platforms_impl()["platforms"][0]

        assert set(tool) - set(resource) == {"adoption", "default_mode"}


class TestCredentialTableDerivation:
    def test_every_row_comes_from_a_registry_declaration(self):
        from benchbox.cli.commands.setup import credential_platforms
        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry.get_all_platform_metadata()
        rows = credential_platforms()

        assert rows
        for row in rows:
            assert row["required"] == list(metadata[row["key"]]["required_credentials"])

    def test_the_table_covers_every_credentialed_platform(self):
        from benchbox.cli.commands.setup import credential_platforms
        from benchbox.core.platform_registry import PlatformRegistry

        declared = {
            name
            for name, metadata in PlatformRegistry.get_all_platform_metadata().items()
            if metadata.get("required_credentials")
        }

        assert {row["key"] for row in credential_platforms()} == declared

    def test_rows_are_deterministically_ordered(self):
        from benchbox.cli.commands.setup import credential_platforms

        names = [row["name"] for row in credential_platforms()]

        assert names == sorted(names)
