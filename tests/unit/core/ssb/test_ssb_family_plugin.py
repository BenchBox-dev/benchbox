from __future__ import annotations

import pytest

from benchbox.base import BaseBenchmark
from benchbox.core.benchmark_loader import get_benchmark_instance, get_core_benchmark_class
from benchbox.core.benchmark_registry import (
    BenchmarkFamilyPlugin,
    get_benchmark_registry_summary,
    get_family_plugin,
    list_family_plugin_ids,
    list_loader_benchmark_ids,
    list_public_benchmark_ids,
)
from benchbox.core.errors import ScaleFactorOutOfRangeError
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.ssb.benchmark import SSBBenchmark
from benchbox.core.ssb.family import SSBFamily
from benchbox.ssb import SSB

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_ssb_family_is_the_only_registered_plugin() -> None:
    assert list_family_plugin_ids() == ["ssb"]
    assert get_family_plugin("tpch") is None
    assert get_family_plugin("ssb") is not None


def test_ssb_family_is_not_a_third_base_benchmark() -> None:
    family = SSBFamily()
    assert not issubclass(SSBFamily, BaseBenchmark)
    assert isinstance(family, BenchmarkFamilyPlugin)
    assert not isinstance(family, BaseBenchmark)


def test_ssb_family_fields_are_registry_backed() -> None:
    family = SSBFamily()
    assert family.benchmark_id == "ssb"
    assert family.public_class_name == "SSB"
    assert family.surface == "public"
    assert family.core_class is SSBBenchmark
    assert family.core_class is get_core_benchmark_class("ssb")


def test_ssb_family_default_scale_uses_registry_options() -> None:
    family = SSBFamily()
    assert family.default_scale() == 0.01
    assert family.default_scale(1.0) == 1.0
    assert family.default_scale(2.0) == 2.0
    with pytest.raises(ScaleFactorOutOfRangeError):
        family.default_scale(1.5)


def test_ssb_family_create_constructs_core_benchmark() -> None:
    family = SSBFamily()
    config = BenchmarkConfig(name="ssb", display_name="SSB", scale_factor=0.01)
    instance = family.create(config, None)
    assert isinstance(instance, SSBBenchmark)
    assert instance.scale_factor == 0.01


def test_loader_uses_ssb_family_plugin() -> None:
    config = BenchmarkConfig(name="ssb", display_name="SSB", scale_factor=0.01)
    instance = get_benchmark_instance(config, None)
    assert isinstance(instance, SSBBenchmark)
    assert instance.scale_factor == 0.01


def test_ssb_family_phases_and_result_metadata() -> None:
    family = SSBFamily()
    assert family.phases() == ("generate", "load", "execute")
    metadata = family.result_metadata()
    assert metadata["query_count"] == 13
    assert metadata["query_flights"] == 4
    assert metadata["supports_streams"] is False
    assert metadata["supports_dataframe"] is True


def test_public_ssb_wrapper_facade_remains() -> None:
    assert SSB.__name__ == "SSB"
    assert issubclass(SSB, BaseBenchmark)


def test_registry_wrapper_and_loader_counts_are_unchanged() -> None:
    summary = get_benchmark_registry_summary()
    assert summary["total"] == 23
    assert summary["loader"] == len(list_loader_benchmark_ids()) == 23
    assert summary["public"] == len(list_public_benchmark_ids()) == 22
    assert summary["support_status"]["stable"] == 6
