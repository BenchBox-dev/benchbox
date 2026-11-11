# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning.interface import (
    TableTuning,
    TuningColumn,
    UnifiedTuningConfiguration,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_hash_is_full_64_char_lowercase_hex_sha256():
    config = UnifiedTuningConfiguration()

    digest = config.get_configuration_hash()

    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_is_deterministic_for_the_same_configuration():
    config = UnifiedTuningConfiguration()

    assert config.get_configuration_hash() == config.get_configuration_hash()


def test_hash_is_stable_across_construction_and_insertion_ordering():
    config_a = UnifiedTuningConfiguration()
    config_a.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem", sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
    )
    config_a.table_tunings["orders"] = TableTuning(
        table_name="orders", sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)]
    )
    config_a.foreign_keys.enabled = False
    config_a.platform_optimizations.z_ordering_enabled = True
    config_a.platform_optimizations.z_ordering_columns = ["a", "b"]

    config_b = UnifiedTuningConfiguration()
    config_b.platform_optimizations.z_ordering_columns = ["a", "b"]
    config_b.platform_optimizations.z_ordering_enabled = True
    config_b.foreign_keys.enabled = False
    config_b.table_tunings["orders"] = TableTuning(
        table_name="orders", sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)]
    )
    config_b.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem", sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
    )

    assert config_a.get_configuration_hash() == config_b.get_configuration_hash()


def test_hash_is_distinct_for_differing_configurations():
    baseline = UnifiedTuningConfiguration()

    tuned = UnifiedTuningConfiguration()
    tuned.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem", sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)]
    )

    assert baseline.get_configuration_hash() != tuned.get_configuration_hash()


def test_hash_changes_when_a_single_flag_flips():
    config = UnifiedTuningConfiguration()
    original = config.get_configuration_hash()

    config.foreign_keys.enabled = False

    assert config.get_configuration_hash() != original


