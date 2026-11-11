# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import ast
import inspect
import logging
import pkgutil
from importlib import import_module

import pytest

from benchbox.core.tuning import ddl_generator as ddl_generator_module, generators as generators_pkg
from benchbox.core.tuning.ddl_generator import (
    BaseDDLGenerator,
    NoOpDDLGenerator,
    get_ddl_generator,
)
from benchbox.core.tuning.generators.clickhouse import ClickHouseDDLGenerator
from benchbox.core.tuning.generators.pg_duckdb import PgDuckDBDDLGenerator
from benchbox.core.tuning.generators.pg_mooncake import PgMooncakeDDLGenerator
from benchbox.core.tuning.generators.questdb import QuestDBDDLGenerator
from benchbox.core.tuning.generators.starrocks import StarRocksDDLGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_EXEMPT_GENERATOR_CLASSES: frozenset[str] = frozenset(
    {
        "IcebergDDLGenerator",
        "ParquetDDLGenerator",
        "HiveDDLGenerator",
    }
)


def _discover_concrete_generator_classes() -> dict[str, type[BaseDDLGenerator]]:
    discovered: dict[str, type[BaseDDLGenerator]] = {}
    for module_info in pkgutil.iter_modules(generators_pkg.__path__, prefix=f"{generators_pkg.__name__}."):
        module = import_module(module_info.name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                obj.__module__ == module.__name__
                and issubclass(obj, BaseDDLGenerator)
                and obj is not BaseDDLGenerator
                and not inspect.isabstract(obj)
            ):
                discovered[name] = obj
    return discovered


def _registered_generator_class_names() -> set[str]:
    source = inspect.getsource(get_ddl_generator)
    tree = ast.parse(source)
    function_node = tree.body[0]
    assert isinstance(function_node, ast.FunctionDef)

    class_names: set[str] = set()
    for node in ast.walk(function_node):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "generators"
            or isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "generators" for target in node.targets)
        ):
            dict_node = node.value
        else:
            continue

        assert isinstance(dict_node, ast.Dict), "Expected `generators` to be assigned a dict literal"
        for value in dict_node.values:
            assert isinstance(value, ast.Name), f"Expected a bare class name in generators dict, got {value!r}"
            class_names.add(value.id)
        break
    else:
        raise AssertionError("Could not find a `generators = {...}` assignment in get_ddl_generator()")

    return class_names


class TestDDLGeneratorRegistryCompleteness:
    def test_exempt_list_has_no_stale_entries(self) -> None:
        discovered = _discover_concrete_generator_classes()
        registered = _registered_generator_class_names()
        unregistered = set(discovered) - registered

        stale = _EXEMPT_GENERATOR_CLASSES - unregistered
        assert not stale, f"Stale exemptions (already registered or no longer exist): {sorted(stale)}"

    def test_every_generator_module_class_is_registered(self) -> None:
        discovered = _discover_concrete_generator_classes()
        assert discovered, "Expected to discover at least one concrete DDL generator class"

        registered = _registered_generator_class_names()
        missing = set(discovered) - registered - _EXEMPT_GENERATOR_CLASSES

        assert not missing, (
            f"Generator classes not reachable via get_ddl_generator(): {sorted(missing)}. "
            "Add a platform key -> class entry in get_ddl_generator()'s `generators` "
            "mapping, or add the class to _EXEMPT_GENERATOR_CLASSES with a reason."
        )


class TestNewPlatformGeneratorRegistration:
    def test_questdb_is_registered(self) -> None:
        generator = get_ddl_generator("questdb")
        assert isinstance(generator, QuestDBDDLGenerator)
        assert not isinstance(generator, NoOpDDLGenerator)

    @pytest.mark.parametrize("platform_key", ["pg_duckdb", "pg-duckdb"])
    def test_pg_duckdb_is_registered(self, platform_key: str) -> None:
        generator = get_ddl_generator(platform_key)
        assert isinstance(generator, PgDuckDBDDLGenerator)
        assert not isinstance(generator, NoOpDDLGenerator)

    @pytest.mark.parametrize("platform_key", ["pg_mooncake", "pg-mooncake"])
    def test_pg_mooncake_is_registered(self, platform_key: str) -> None:
        generator = get_ddl_generator(platform_key)
        assert isinstance(generator, PgMooncakeDDLGenerator)
        assert not isinstance(generator, NoOpDDLGenerator)

    def test_starrocks_is_registered(self) -> None:
        generator = get_ddl_generator("starrocks")
        assert isinstance(generator, StarRocksDDLGenerator)
        assert not isinstance(generator, NoOpDDLGenerator)

    def test_clickhouse_cloud_is_registered(self) -> None:
        generator = get_ddl_generator("clickhouse-cloud")
        assert isinstance(generator, ClickHouseDDLGenerator)
        assert not isinstance(generator, NoOpDDLGenerator)

    def test_lookup_is_case_insensitive(self) -> None:
        assert isinstance(get_ddl_generator("QuestDB"), QuestDBDDLGenerator)
        assert isinstance(get_ddl_generator("PG-DUCKDB"), PgDuckDBDDLGenerator)
        assert isinstance(get_ddl_generator("Pg_Mooncake"), PgMooncakeDDLGenerator)


class TestNoOpFallbackWarning:
    def test_unknown_platform_still_returns_noop_without_raising(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="benchbox.core.tuning.ddl_generator"):
            generator = get_ddl_generator("totally-unregistered-platform-xyz")

        assert isinstance(generator, NoOpDDLGenerator)
        assert generator.platform_name == "totally-unregistered-platform-xyz"

    def test_unknown_platform_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="benchbox.core.tuning.ddl_generator"):
            get_ddl_generator("totally-unregistered-platform-xyz")

        assert any(
            "totally-unregistered-platform-xyz" in record.getMessage() and record.levelno == logging.WARNING
            for record in caplog.records
        )

    @pytest.mark.parametrize(
        "platform_key",
        [
            "sqlite",
            "sqlite3",
            "pandas",
            "cudf",
            "dask",
            "polars",
            "datafusion",
            "pyspark",
            "polars-df",
            "pandas-df",
            "cudf-df",
            "dask-df",
            "datafusion-df",
            "pyspark-df",
            "dataframe-pandas",
            "dataframe-polars",
            "dataframe-dask",
            "dataframe-cudf",
            "dataframe-pyspark",
            "dataframe-datafusion",
        ],
    )
    def test_known_tuning_free_platforms_stay_silent(self, platform_key: str, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="benchbox.core.tuning.ddl_generator"):
            generator = get_ddl_generator(platform_key)

        assert isinstance(generator, NoOpDDLGenerator)
        assert caplog.records == []

    @pytest.mark.parametrize(
        "platform_key",
        [
            "snowflake-df",
            "dataframe-snowflake",
            "duckdb-df",
            "dataframe-bigquery",
            "lakesail",
            "velox",
            "lakesail-df",
            "velox-df",
            "dataframe-lakesail",
            "dataframe-databricks",
        ],
    )
    def test_undeclared_or_tuning_capable_keys_warn_and_fall_back_to_noop(
        self, platform_key: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="benchbox.core.tuning.ddl_generator"):
            generator = get_ddl_generator(platform_key)

        assert isinstance(generator, NoOpDDLGenerator)
        assert any(
            platform_key in record.getMessage() and record.levelno == logging.WARNING for record in caplog.records
        )

    def test_databricks_df_resolves_to_delta_generator(self) -> None:
        from benchbox.core.tuning.generators.spark_family import DeltaDDLGenerator

        assert isinstance(get_ddl_generator("databricks-df"), DeltaDDLGenerator)

    def test_dataframe_variant_matches_base_engine(self) -> None:
        assert type(get_ddl_generator("polars-df")).__name__ == type(get_ddl_generator("polars")).__name__
        assert type(get_ddl_generator("dataframe-polars")).__name__ == type(get_ddl_generator("polars")).__name__
        assert type(get_ddl_generator("datafusion-df")).__name__ == type(get_ddl_generator("datafusion")).__name__

    def test_registered_platform_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="benchbox.core.tuning.ddl_generator"):
            get_ddl_generator("questdb")

        assert caplog.records == []


class TestDataframeBaseSetsMatchDeclarations:
    def test_suffix_bases_match_manifest_df_spellings(self) -> None:
        from benchbox.core.platform_manifest import PLATFORM_MANIFEST

        expected: set[str] = set()
        for entry in PLATFORM_MANIFEST:
            if entry.key.endswith("-df"):
                expected.add(entry.key[: -len("-df")])
            for alias in entry.aliases:
                if alias.name.endswith("-df"):
                    expected.add(entry.key)

        assert frozenset(expected) == ddl_generator_module._DATAFRAME_SUFFIX_BASES, (
            "Drift between _DATAFRAME_SUFFIX_BASES and the platform manifest's "
            f'"<engine>-df" spellings: {sorted(set(expected) ^ set(ddl_generator_module._DATAFRAME_SUFFIX_BASES))}. '
            "Update the set with a reason when the manifest gains or loses a df spelling."
        )

    def test_prefix_bases_match_dataframe_extras(self) -> None:
        import tomllib
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[4]
        with open(repo_root / "pyproject.toml", "rb") as handle:
            pyproject = tomllib.load(handle)
        extras = pyproject["project"]["optional-dependencies"]
        non_spelling_extras = {"dataframe-all"}
        expected = {
            name[len("dataframe-") :]
            for name in extras
            if name.startswith("dataframe-") and not name.endswith("-family") and name not in non_spelling_extras
        }

        assert frozenset(expected) == ddl_generator_module._DATAFRAME_PREFIX_BASES, (
            "Drift between _DATAFRAME_PREFIX_BASES and pyproject's dataframe-* "
            f"extras: {sorted(set(expected) ^ set(ddl_generator_module._DATAFRAME_PREFIX_BASES))}. "
            "Update the set with a reason when extras change."
        )


