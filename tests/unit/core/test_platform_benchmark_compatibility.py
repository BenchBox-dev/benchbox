# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.platform_registry import PlatformRegistry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBenchmarkCompatibility:
    def setup_method(self):

        PlatformRegistry.clear_cache()

    def test_blocked_benchmark_reports_reason(self):
        reason = PlatformRegistry.get_benchmark_block_reason("questdb", "vector_search")
        assert reason, "expected a block reason for questdb/vector_search"
        assert "VECTOR" in reason

    def test_blocked_benchmark_is_not_supported(self):
        assert not PlatformRegistry.is_benchmark_supported("questdb", "vector_search")

    def test_supported_benchmark_has_no_block_reason(self):
        assert PlatformRegistry.get_benchmark_block_reason("questdb", "tpch") is None
        assert PlatformRegistry.is_benchmark_supported("questdb", "tpch")

    def test_lakesail_ai_primitives_blocked(self):
        assert not PlatformRegistry.is_benchmark_supported("lakesail", "ai_primitives")

    def test_unsupported_map_matches_capabilities(self):
        unsupported = PlatformRegistry.get_unsupported_benchmarks("questdb")
        assert unsupported["vector_search"] == PlatformRegistry.get_benchmark_block_reason("questdb", "vector_search")

    def test_unsupported_map_is_a_copy(self):
        unsupported = PlatformRegistry.get_unsupported_benchmarks("questdb")
        unsupported["vector_search"] = "mutated"
        assert PlatformRegistry.get_benchmark_block_reason("questdb", "vector_search") != "mutated"

    def test_alias_resolves_to_canonical(self):
        canonical = PlatformRegistry.get_unsupported_benchmarks("sqlite")
        aliased = PlatformRegistry.get_unsupported_benchmarks("sqlite3")
        assert aliased == canonical

    def test_unknown_platform_is_not_gated(self):
        assert PlatformRegistry.get_unsupported_benchmarks("no_such_platform") == {}
        assert PlatformRegistry.get_benchmark_block_reason("no_such_platform", "tpch") is None


