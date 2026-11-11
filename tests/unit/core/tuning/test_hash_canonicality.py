# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning.interface import (
    BenchmarkTunings,
    TableTuning,
    TuningColumn,
    UnifiedTuningConfiguration,
)
from benchbox.core.tuning.profile_validation import hash_tuning_template

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _tunings_with_tables(table_order: list[str]) -> BenchmarkTunings:

    tunings = BenchmarkTunings("tpch")
    columns = {
        "orders": [TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
        "customer": [TuningColumn(name="c_custkey", type="INTEGER", order=1)],
    }
    for table_name in table_order:
        tunings.add_table_tuning(TableTuning(table_name=table_name, sorting=columns[table_name]))
    return tunings


class TestGetConfigurationHashCanonicality:
    def test_stable_across_table_insertion_order(self) -> None:
        forward = _tunings_with_tables(["orders", "customer"])
        reverse = _tunings_with_tables(["customer", "orders"])

        assert forward.get_configuration_hash() == reverse.get_configuration_hash()

    def test_distinct_for_a_differing_column(self) -> None:
        base = _tunings_with_tables(["orders", "customer"])

        changed = BenchmarkTunings("tpch")
        changed.add_table_tuning(
            TableTuning(table_name="orders", sorting=[TuningColumn(name="o_custkey", type="INTEGER", order=1)])
        )
        changed.add_table_tuning(
            TableTuning(table_name="customer", sorting=[TuningColumn(name="c_custkey", type="INTEGER", order=1)])
        )

        assert base.get_configuration_hash() != changed.get_configuration_hash()

    def test_distinct_for_differing_constraint_flags(self) -> None:
        enabled = BenchmarkTunings("tpch")
        enabled.add_table_tuning(
            TableTuning(table_name="orders", sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)])
        )
        disabled = BenchmarkTunings("tpch")
        disabled.add_table_tuning(
            TableTuning(table_name="orders", sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)])
        )
        disabled.disable_all_constraints()

        assert enabled.get_configuration_hash() != disabled.get_configuration_hash()

    def test_empty_config_is_deterministic(self) -> None:
        a = BenchmarkTunings("tpch")
        b = BenchmarkTunings("tpch")

        assert a.get_configuration_hash() == b.get_configuration_hash()

    def test_hash_is_a_full_sha256_hexdigest_not_truncated(self) -> None:
        tunings = _tunings_with_tables(["orders"])
        digest = tunings.get_configuration_hash()

        assert len(digest) == 64, (
            "get_configuration_hash returns the FULL sha256 hexdigest (64 hex chars / 256 "
            "bits) -- contrast hash_tuning_template's 16-char (64-bit) truncation below. "
            "This is the collision-resistant identity key; TuningMetadataManager compares "
            "it directly (interface.py / metadata.py) to detect real configuration drift, "
            "so it must never be truncated."
        )
        assert all(c in "0123456789abcdef" for c in digest)


class TestHashTuningTemplateCanonicality:
    def test_stable_across_dict_key_order(self) -> None:
        forward = {"a": 1, "b": 2, "nested": {"x": 1, "y": 2}}
        reverse = {"nested": {"y": 2, "x": 1}, "b": 2, "a": 1}

        assert hash_tuning_template(forward) == hash_tuning_template(reverse)

    def test_stable_across_table_insertion_order_for_config_objects(self) -> None:
        forward = UnifiedTuningConfiguration()
        forward.table_tunings["orders"] = TableTuning(
            table_name="orders", sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)]
        )
        forward.table_tunings["customer"] = TableTuning(
            table_name="customer", sorting=[TuningColumn(name="c_custkey", type="INTEGER", order=1)]
        )

        reverse = UnifiedTuningConfiguration()
        reverse.table_tunings["customer"] = TableTuning(
            table_name="customer", sorting=[TuningColumn(name="c_custkey", type="INTEGER", order=1)]
        )
        reverse.table_tunings["orders"] = TableTuning(
            table_name="orders", sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)]
        )

        assert hash_tuning_template(forward) == hash_tuning_template(reverse)

    def test_distinct_for_differing_configs(self) -> None:
        assert hash_tuning_template({"a": 1}) != hash_tuning_template({"a": 2})

    def test_distinct_for_differing_config_objects(self) -> None:
        base = UnifiedTuningConfiguration()
        base.table_tunings["orders"] = TableTuning(
            table_name="orders", sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)]
        )
        changed = UnifiedTuningConfiguration()
        changed.table_tunings["orders"] = TableTuning(
            table_name="orders", sorting=[TuningColumn(name="o_custkey", type="INTEGER", order=1)]
        )

        assert hash_tuning_template(base) != hash_tuning_template(changed)

    def test_truncated_to_16_hex_chars_collision_posture(self) -> None:
        digest = hash_tuning_template({"a": 1})

        assert len(digest) == 16, (
            "hash_tuning_template truncates the sha256 digest to 16 hex chars (64 bits) for "
            "compact display in UAT logs/matrices (see profile_validation.py's docstring: "
            "'stable compact hash'). This is NOT a cryptographic collision-resistance "
            "boundary -- by the birthday bound, collisions become non-negligible past "
            "roughly 2**32 (~4 billion) distinct configurations hashed. It must never be "
            "used as a proof of template identity on its own; callers needing a "
            "collision-resistant identity key should use "
            "BenchmarkTunings.get_configuration_hash()'s full 64-char digest instead. This "
            "test exists so a future change that swaps one hash helper's truncation for the "
            "other's is caught here rather than discovered as a rare production collision."
        )
        assert all(c in "0123456789abcdef" for c in digest)


