# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("symbol", "expected_type"),
    [
        ("TPCH_DATAFRAME_QUERIES", "QueryRegistry"),
        ("TPCDS_DATAFRAME_QUERIES", "QueryRegistry"),
        ("DATAFRAME_PLATFORMS", "dict"),
    ],
)
def test_public_reexports_accessible_from_benchbox(symbol: str, expected_type: str) -> None:
    importlib.import_module("benchbox.core.dataframe.benchmark_suite")

    benchbox = importlib.import_module("benchbox")
    obj = getattr(benchbox, symbol)
    assert type(obj).__name__ == expected_type, f"{symbol} has unexpected type {type(obj)}"
    assert symbol in benchbox.__all__


_EXPERIMENTAL_SUBSYSTEMS = {"nl2sql", "aiml_functions", "multiregion", "gpu", "load_testing"}


def test_experimental_modules_not_in_benchmark_registry() -> None:
    import benchbox

    registry_modules = {spec.module for spec in benchbox._BENCHMARK_REGISTRY.values()}
    leaked = _EXPERIMENTAL_SUBSYSTEMS & registry_modules
    assert not leaked, f"Experimental modules leaked into _BENCHMARK_REGISTRY: {leaked}"

    all_lower = {name.lower() for name in benchbox.__all__}
    leaked_all = _EXPERIMENTAL_SUBSYSTEMS & all_lower
    assert not leaked_all, f"Experimental modules leaked into __all__: {leaked_all}"


def test_benchmarks_submodule_remains_available_as_package_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "benchbox.cli.benchmarks", raising=False)
    monkeypatch.delitem(sys.modules, "benchbox.cli", raising=False)

    cli_module = importlib.import_module("benchbox.cli")
    benchmarks_module = cli_module.benchmarks

    assert benchmarks_module is importlib.import_module("benchbox.cli.benchmarks")
    assert cli_module.benchmarks is benchmarks_module


