from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core import benchmark_registry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize("benchmark_id", sorted(benchmark_registry.BENCHMARK_METADATA.keys()))
def test_existing_benchmarks_default_to_public(benchmark_id: str) -> None:
    surface = benchmark_registry.get_benchmark_surface(benchmark_id)
    declared = benchmark_registry.BENCHMARK_METADATA[benchmark_id].get("surface")
    if declared == "internal":
        assert surface == "internal"
    else:
        assert surface == "public"


def test_unregistered_benchmark_defaults_public() -> None:
    assert benchmark_registry.get_benchmark_surface("does-not-exist") == "public"


def test_internal_surface_recognized() -> None:
    fake_meta = {
        "x_internal": {
            "display_name": "Internal Test",
            "description": "test",
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
            "surface": "internal",
        }
    }
    with patch.dict(benchmark_registry.BENCHMARK_METADATA, fake_meta, clear=False):
        assert benchmark_registry.get_benchmark_surface("x_internal") == "internal"


def test_public_surface_recognized() -> None:
    fake_meta = {
        "x_public": {
            "display_name": "Public Test",
            "description": "test",
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
            "surface": "public",
        }
    }
    with patch.dict(benchmark_registry.BENCHMARK_METADATA, fake_meta, clear=False):
        assert benchmark_registry.get_benchmark_surface("x_public") == "public"


def test_joinorder_synthetic_hidden() -> None:
    assert benchmark_registry.get_benchmark_surface("joinorder_synthetic") == "internal"


def test_public_benchmark_ids_exclude_joinorder_synthetic() -> None:
    public_ids = benchmark_registry.list_public_benchmark_ids()

    assert "joinorder" in public_ids
    assert "joinorder_synthetic" not in public_ids


def _synthetic_meta(support_status: str, surface: str, *, supports_dataframe: bool = False) -> dict[str, object]:
    return {
        "display_name": f"Synthetic {support_status}/{surface}",
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
        "supports_dataframe": supports_dataframe,
        "support_status": support_status,
        "surface": surface,
    }


@pytest.mark.parametrize("support_status", sorted(benchmark_registry.BENCHMARK_SUPPORT_STATUS_VALUES))
def test_surface_gates_discovery_independent_of_support_status(support_status: str) -> None:
    fixtures = {
        "x_future_public": _synthetic_meta(support_status, "public"),
        "x_future_internal": _synthetic_meta(support_status, "internal"),
    }
    with patch.dict(benchmark_registry.BENCHMARK_METADATA, fixtures, clear=False):
        public_ids = benchmark_registry.list_public_benchmark_ids()
        assert "x_future_public" in public_ids, f"public {support_status} benchmark must be discoverable"
        assert "x_future_internal" not in public_ids, f"internal {support_status} benchmark must be hidden"
        assert benchmark_registry.get_benchmark_support_status("x_future_public") == support_status
        assert benchmark_registry.get_benchmark_support_status("x_future_internal") == support_status


@pytest.mark.parametrize("support_status", sorted(benchmark_registry.BENCHMARK_SUPPORT_STATUS_VALUES))
def test_registry_summary_counts_dataframe_capability_independent_of_support_status(support_status: str) -> None:
    base_supported = benchmark_registry.get_benchmark_registry_summary()["dataframe_supported"]
    fixtures = {
        f"x_{support_status}_df": _synthetic_meta(support_status, "public", supports_dataframe=True),
        f"x_{support_status}_nodf": _synthetic_meta(support_status, "public", supports_dataframe=False),
    }
    with patch.dict(benchmark_registry.BENCHMARK_METADATA, fixtures, clear=False):
        summary = benchmark_registry.get_benchmark_registry_summary()

    assert summary["dataframe_supported"] == base_supported + 1


