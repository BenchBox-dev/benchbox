from __future__ import annotations

import pytest
import yaml

from scripts.tuning_evidence import duckdb_arms

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("name", duckdb_arms.BENCHMARKS)
def test_constraints_only_keeps_constraints_and_drops_sort_order(name: str) -> None:
    template = duckdb_arms.load_template(name)
    arm = duckdb_arms.derive(template, "constraints_only")
    assert arm["table_tunings"] == {}
    assert arm["primary_keys"] == template["primary_keys"]
    assert arm["foreign_keys"] == template["foreign_keys"]
    assert arm["_metadata"]["configuration_type"] == "constraints_only"


@pytest.mark.parametrize("name", duckdb_arms.BENCHMARKS)
def test_sort_only_keeps_sort_order_and_drops_constraints(name: str) -> None:
    template = duckdb_arms.load_template(name)
    arm = duckdb_arms.derive(template, "sort_only")
    assert arm["table_tunings"] == template["table_tunings"]
    assert arm["primary_keys"]["enabled"] is False
    assert arm["foreign_keys"]["enabled"] is False
    assert template["primary_keys"]["enabled"] is True


def test_unknown_variant_is_refused() -> None:
    with pytest.raises(ValueError):
        duckdb_arms.derive({}, "nope")


def test_write_arms_writes_both_variants(tmp_path) -> None:
    paths = duckdb_arms.write_arms("ssb", tmp_path)
    assert set(paths) == set(duckdb_arms.VARIANTS)
    assert (
        yaml.safe_load(paths["sort_only"].read_text(encoding="utf-8"))["_metadata"]["configuration_type"] == "sort_only"
    )
