from __future__ import annotations

import inspect
import re

import pytest
import yaml

from benchbox.core.tuning.capability_registry import (
    PLATFORM_ALIASES,
    PLATFORM_TUNING_CAPABILITIES,
    get_capability,
    is_not_rendered_at_execution,
    known_registry_platforms,
    not_rendered_reason,
    resolve_platform_key,
)
from benchbox.core.tuning.interface import TableTuning, TuningColumn, TuningType, UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _partitioned_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings["orders"] = TableTuning(
        table_name="orders",
        partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
    )
    return config


def _distributed_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem",
        distribution=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
    )
    return config


class TestRequestedButNotRenderedIsAnError:
    def test_partitioning_on_postgresql_validates(self) -> None:
        errors, _warnings = _partitioned_config().validate_for_platform_detailed("postgresql")
        assert not any("partitioning" in e for e in errors)

    def test_partitioning_on_postgres_family_errors(self) -> None:
        for platform in ("cedardb", "citus"):
            errors, _warnings = _partitioned_config().validate_for_platform_detailed(platform)
            assert any("is not applied at execution" in e for e in errors), platform

    def test_distribution_on_redshift_errors(self) -> None:
        errors, _warnings = _distributed_config().validate_for_platform_detailed("redshift")
        assert any("requested distribution is not applied at execution on redshift" in e for e in errors)

    def test_layout_on_trino_and_athena_errors(self) -> None:
        for platform in ("trino", "athena"):
            errors, _warnings = _partitioned_config().validate_for_platform_detailed(platform)
            assert any("is not applied at execution" in e for e in errors), platform

    def test_starburst_alias_resolves_to_trino_rules(self) -> None:
        assert resolve_platform_key("Starburst") == "trino"
        errors, _warnings = _partitioned_config().validate_for_platform_detailed("starburst")
        assert any("is not applied at execution" in e for e in errors)

    def test_rendering_registry_entry_does_not_error(self) -> None:
        errors, warnings = _distributed_config().validate_for_platform_detailed("starrocks")
        assert errors == []
        assert warnings == []

    def test_doris_layout_and_constraints_error(self) -> None:
        config = UnifiedTuningConfiguration()
        errors, _warnings = config.validate_for_platform_detailed("doris")
        assert any("requested primary_keys is not applied at execution" in e for e in errors)
        assert any("requested foreign_keys is not applied at execution" in e for e in errors)

    def test_datafusion_constraints_error(self) -> None:
        config = UnifiedTuningConfiguration()
        errors, _warnings = config.validate_for_platform_detailed("datafusion")
        assert len(errors) == 4

    def test_motherduck_sorting_renders_but_constraints_error(self) -> None:
        sort_config = UnifiedTuningConfiguration()
        sort_config.disable_all_constraints()
        sort_config.table_tunings["orders"] = TableTuning(
            table_name="orders",
            sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
        )
        errors, _warnings = sort_config.validate_for_platform_detailed("motherduck")
        assert errors == []

        config = UnifiedTuningConfiguration()
        errors, _warnings = config.validate_for_platform_detailed("motherduck")
        assert any("is not applied at execution" in e for e in errors)

    def test_fabric_warehouse_partitioning_errors(self) -> None:
        errors, _warnings = _partitioned_config().validate_for_platform_detailed("fabric-warehouse")
        assert any("is not applied at execution" in e for e in errors)

    def test_stripped_constraint_types_error(self) -> None:
        for platform in ("doris", "databend", "starrocks"):
            config = UnifiedTuningConfiguration()
            config.disable_all_constraints()
            config.unique_constraints.enabled = True
            errors, _warnings = config.validate_for_platform_detailed(platform)
            assert any("requested unique_constraints is not applied at execution" in e for e in errors), platform

    def test_omitted_unique_and_check_keys_are_not_requested(self) -> None:
        config = UnifiedTuningConfiguration.from_dict({"primary_keys": {"enabled": True}})
        assert config.unique_constraints.enabled is False
        assert config.check_constraints.enabled is False
        errors, _warnings = config.validate_for_platform_detailed("duckdb")
        assert errors == []

    def test_explicit_unique_request_in_file_still_errors(self) -> None:
        config = UnifiedTuningConfiguration.from_dict(
            {"primary_keys": {"enabled": True}, "unique_constraints": {"enabled": True}}
        )
        errors, _warnings = config.validate_for_platform_detailed("duckdb")
        assert any("requested unique_constraints is not applied at execution" in e for e in errors)

    def test_vacuous_unique_check_types_error(self) -> None:
        for platform in (
            "duckdb",
            "databricks",
            "snowflake",
            "sqlite",
            "postgresql",
            "mysql",
            "singlestore",
            "cedardb",
            "citus",
            "paradedb",
            "fabric-warehouse",
            "athena",
            "azure-synapse",
            "timescaledb",
            "redshift",
            "firebolt",
            "questdb",
            "pg-duckdb",
            "pg-mooncake",
            "trino",
            "presto",
        ):
            for tuning_attr in ("unique_constraints", "check_constraints"):
                config = UnifiedTuningConfiguration()
                config.disable_all_constraints()
                getattr(config, tuning_attr).enabled = True
                errors, _warnings = config.validate_for_platform_detailed(platform)
                assert any(f"requested {tuning_attr} is not applied at execution" in e for e in errors), (
                    platform,
                    tuning_attr,
                )

    def test_bigquery_unique_and_clickhouse_fk_check_error(self) -> None:
        for platform, tuning_attr in (
            ("bigquery", "unique_constraints"),
            ("clickhouse", "foreign_keys"),
            ("clickhouse", "check_constraints"),
        ):
            config = UnifiedTuningConfiguration()
            config.disable_all_constraints()
            getattr(config, tuning_attr).enabled = True
            errors, _warnings = config.validate_for_platform_detailed(platform)
            assert any(f"requested {tuning_attr} is not applied at execution" in e for e in errors), (
                platform,
                tuning_attr,
            )

    def test_dropped_pk_fk_types_error(self) -> None:
        for platform, tuning_attr in (
            ("firebolt", "primary_keys"),
            ("firebolt", "foreign_keys"),
            ("questdb", "primary_keys"),
            ("questdb", "foreign_keys"),
            ("trino", "primary_keys"),
            ("presto", "primary_keys"),
            ("athena", "primary_keys"),
            ("athena", "foreign_keys"),
        ):
            config = UnifiedTuningConfiguration()
            config.disable_all_constraints()
            getattr(config, tuning_attr).enabled = True
            errors, _warnings = config.validate_for_platform_detailed(platform)
            assert any(f"requested {tuning_attr} is not applied at execution" in e for e in errors), (
                platform,
                tuning_attr,
            )

    def test_rendered_pk_fk_types_do_not_error(self) -> None:
        for platform, tuning_attr in (
            ("pg-duckdb", "primary_keys"),
            ("pg-duckdb", "foreign_keys"),
            ("pg-mooncake", "primary_keys"),
            ("pg-mooncake", "foreign_keys"),
            ("trino", "foreign_keys"),
            ("presto", "foreign_keys"),
        ):
            config = UnifiedTuningConfiguration()
            config.disable_all_constraints()
            getattr(config, tuning_attr).enabled = True
            errors, _warnings = config.validate_for_platform_detailed(platform)
            assert errors == [], (platform, tuning_attr, errors)


_NOT_RENDERED: dict[str, frozenset[str]] = {
    "athena": frozenset(
        {
            "check_constraints",
            "clustering",
            "distribution",
            "foreign_keys",
            "partitioning",
            "primary_keys",
            "sorting",
            "unique_constraints",
        }
    ),
    "azure-synapse": frozenset(
        {"check_constraints", "clustering", "distribution", "partitioning", "sorting", "unique_constraints"}
    ),
    "bigquery": frozenset(
        {"check_constraints", "clustering", "materialized_views", "partitioning", "unique_constraints"}
    ),
    "cedardb": frozenset(
        {"bloom_filters", "check_constraints", "clustering", "materialized_views", "partitioning", "unique_constraints"}
    ),
    "citus": frozenset(
        {"bloom_filters", "check_constraints", "clustering", "materialized_views", "partitioning", "unique_constraints"}
    ),
    "clickhouse": frozenset({"check_constraints", "foreign_keys", "materialized_views", "unique_constraints"}),
    "databend": frozenset(
        {
            "check_constraints",
            "distribution",
            "foreign_keys",
            "materialized_views",
            "partitioning",
            "primary_keys",
            "sorting",
            "unique_constraints",
        }
    ),
    "databricks": frozenset(
        {
            "bloom_filters",
            "check_constraints",
            "distribution",
            "materialized_views",
            "partitioning",
            "unique_constraints",
        }
    ),
    "datafusion": frozenset({"check_constraints", "foreign_keys", "primary_keys", "unique_constraints"}),
    "doris": frozenset(
        {
            "check_constraints",
            "distribution",
            "foreign_keys",
            "materialized_views",
            "partitioning",
            "primary_keys",
            "sorting",
            "unique_constraints",
        }
    ),
    "duckdb": frozenset({"check_constraints", "partitioning", "unique_constraints"}),
    "fabric-warehouse": frozenset({"check_constraints", "partitioning", "unique_constraints"}),
    "firebolt": frozenset(
        {
            "check_constraints",
            "clustering",
            "distribution",
            "foreign_keys",
            "partitioning",
            "primary_keys",
            "sorting",
            "unique_constraints",
        }
    ),
    "motherduck": frozenset(
        {
            "check_constraints",
            "clustering",
            "distribution",
            "foreign_keys",
            "materialized_views",
            "partitioning",
            "primary_keys",
            "unique_constraints",
        }
    ),
    "mysql": frozenset({"check_constraints", "partitioning", "unique_constraints"}),
    "paradedb": frozenset({"bloom_filters", "check_constraints", "materialized_views", "unique_constraints"}),
    "pg-duckdb": frozenset({"check_constraints", "unique_constraints"}),
    "pg-mooncake": frozenset({"check_constraints", "clustering", "partitioning", "sorting", "unique_constraints"}),
    "postgresql": frozenset({"bloom_filters", "check_constraints", "materialized_views", "unique_constraints"}),
    "presto": frozenset(
        {
            "check_constraints",
            "clustering",
            "distribution",
            "partitioning",
            "primary_keys",
            "sorting",
            "unique_constraints",
        }
    ),
    "questdb": frozenset(
        {
            "check_constraints",
            "distribution",
            "foreign_keys",
            "partitioning",
            "primary_keys",
            "sorting",
            "unique_constraints",
        }
    ),
    "redshift": frozenset(
        {"check_constraints", "distribution", "materialized_views", "partitioning", "unique_constraints"}
    ),
    "singlestore": frozenset({"check_constraints", "foreign_keys", "partitioning", "unique_constraints"}),
    "snowflake": frozenset({"check_constraints", "materialized_views", "unique_constraints"}),
    "spark": frozenset({"check_constraints", "partitioning", "unique_constraints"}),
    "sqlite": frozenset({"check_constraints", "unique_constraints"}),
    "starrocks": frozenset({"check_constraints", "foreign_keys", "unique_constraints"}),
    "timescaledb": frozenset({"check_constraints", "unique_constraints"}),
    "trino": frozenset(
        {
            "check_constraints",
            "clustering",
            "distribution",
            "partitioning",
            "primary_keys",
            "sorting",
            "unique_constraints",
        }
    ),
}
_RENDERED: dict[str, frozenset[str]] = {
    "athena": frozenset({}),
    "azure-synapse": frozenset({"foreign_keys", "primary_keys"}),
    "bigquery": frozenset({"foreign_keys", "primary_keys"}),
    "cedardb": frozenset({"foreign_keys", "primary_keys"}),
    "citus": frozenset({"foreign_keys", "primary_keys"}),
    "clickhouse": frozenset({"clustering", "partitioning", "primary_keys", "sorting"}),
    "databend": frozenset({"clustering"}),
    "databricks": frozenset(
        {
            "auto_compact",
            "auto_optimize",
            "clustering",
            "foreign_keys",
            "liquid_clustering",
            "primary_keys",
            "z_ordering",
        }
    ),
    "datafusion": frozenset({}),
    "doris": frozenset({}),
    "duckdb": frozenset({"foreign_keys", "primary_keys", "sorting"}),
    "fabric-warehouse": frozenset({"foreign_keys", "primary_keys"}),
    "firebolt": frozenset({}),
    "motherduck": frozenset({"sorting"}),
    "mysql": frozenset({"foreign_keys", "primary_keys"}),
    "paradedb": frozenset({"clustering", "foreign_keys", "partitioning", "primary_keys", "sorting"}),
    "pg-duckdb": frozenset({"clustering", "foreign_keys", "partitioning", "primary_keys", "sorting"}),
    "pg-mooncake": frozenset({"foreign_keys", "primary_keys"}),
    "postgresql": frozenset({"clustering", "foreign_keys", "partitioning", "primary_keys", "sorting"}),
    "presto": frozenset({"foreign_keys"}),
    "questdb": frozenset({}),
    "redshift": frozenset({"foreign_keys", "primary_keys", "sorting"}),
    "singlestore": frozenset({"primary_keys"}),
    "snowflake": frozenset({"clustering", "foreign_keys", "partitioning", "primary_keys"}),
    "spark": frozenset({"foreign_keys", "primary_keys", "sorting"}),
    "sqlite": frozenset({"foreign_keys", "primary_keys"}),
    "starrocks": frozenset({"distribution", "partitioning", "primary_keys", "sorting"}),
    "timescaledb": frozenset({"clustering", "distribution", "foreign_keys", "partitioning", "primary_keys", "sorting"}),
    "trino": frozenset({"foreign_keys"}),
}


_SOURCE_LOCATION = re.compile(r"\.py:\d+")

_LAYOUT_TYPES = frozenset({TuningType.PARTITIONING, TuningType.CLUSTERING, TuningType.DISTRIBUTION, TuningType.SORTING})


def _config_requesting(tuning_type: TuningType) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.platform_optimizations.sorted_ingestion_mode = "auto"
    for attribute, _checked in UnifiedTuningConfiguration._OPT_CHECKS:
        setattr(config.platform_optimizations, attribute, False)
    if tuning_type in _LAYOUT_TYPES:
        column = [TuningColumn(name="tuned_column", type="INTEGER", order=1)]
        config.table_tunings["orders"] = TableTuning(table_name="orders", **{tuning_type.value: column})
        return config
    for attribute, checked in UnifiedTuningConfiguration._CONSTRAINT_CHECKS:
        if checked == tuning_type:
            getattr(config, attribute).enabled = True
            return config
    for attribute, checked in UnifiedTuningConfiguration._OPT_CHECKS:
        if checked == tuning_type:
            setattr(config.platform_optimizations, attribute, True)
            return config
    raise AssertionError(f"no way to request {tuning_type}")


class TestMechanismPreconditions:
    def test_redshift_sorting_requires_sorted_ingestion(self) -> None:
        config = _config_requesting(TuningType.SORTING)
        config.platform_optimizations.sorted_ingestion_mode = "off"
        errors, _warnings = config.validate_for_platform_detailed("redshift")
        assert any("sorted_ingestion_mode" in error and error.startswith("requested sorting") for error in errors)

        config.platform_optimizations.sorted_ingestion_mode = "auto"
        errors, _warnings = config.validate_for_platform_detailed("redshift")
        assert not any(error.startswith("requested sorting") for error in errors)

    def test_only_redshift_sorting_is_gated_on_sorted_ingestion(self) -> None:
        gated = {
            (platform, tuning_type.value)
            for platform, entries in PLATFORM_TUNING_CAPABILITIES.items()
            for tuning_type, capability in entries.items()
            if capability.requires_sorted_ingestion
        }
        assert gated == {("redshift", "sorting")}

    @pytest.mark.parametrize("alias", ["delta", "databricks:sql", "Databricks"])
    def test_databricks_aliases_run_the_effective_layout_validator(self, alias: str) -> None:
        config = UnifiedTuningConfiguration()
        config.disable_all_constraints()
        config.platform_optimizations.databricks_clustering_strategy = "liquid_clustering"
        config.platform_optimizations.liquid_clustering_enabled = True
        config.table_tunings["orders"] = TableTuning(
            table_name="orders",
            clustering=[TuningColumn(name=f"c{index}", type="INTEGER", order=index) for index in range(1, 6)],
        )
        reference, _ = config.validate_for_platform_detailed("databricks")
        errors, _warnings = config.validate_for_platform_detailed(alias)
        assert any("four" in error or "4" in error for error in reference), reference
        assert [error for error in errors if "four" in error or "4" in error] == [
            error for error in reference if "four" in error or "4" in error
        ]


class TestRegistryTable:
    def test_table_covers_every_registry_platform(self) -> None:
        assert set(_NOT_RENDERED) == set(_RENDERED) == known_registry_platforms()

    @pytest.mark.parametrize("platform", sorted(_NOT_RENDERED))
    def test_registry_matches_table(self, platform: str) -> None:
        entries = PLATFORM_TUNING_CAPABILITIES[platform]
        not_rendered = frozenset(
            tuning_type.value for tuning_type, capability in entries.items() if is_not_rendered_at_execution(capability)
        )
        rendered = frozenset(tuning_type.value for tuning_type in entries) - not_rendered
        assert not_rendered == _NOT_RENDERED[platform], f"{platform}: not-rendered set changed"
        assert rendered == _RENDERED[platform], f"{platform}: rendered set changed"

    @pytest.mark.parametrize("platform", sorted(_NOT_RENDERED))
    def test_requested_types_error_exactly_when_table_says_not_rendered(self, platform: str) -> None:
        for tuning_value in sorted(_NOT_RENDERED[platform] | _RENDERED[platform]):
            errors, _warnings = _config_requesting(TuningType(tuning_value)).validate_for_platform_detailed(platform)
            refused = any(error.startswith(f"requested {tuning_value} is not applied at execution") for error in errors)
            assert refused == (tuning_value in _NOT_RENDERED[platform]), (platform, tuning_value, errors)

    @pytest.mark.parametrize("platform", sorted(_NOT_RENDERED))
    def test_error_text_names_mechanism_without_source_locations(self, platform: str) -> None:
        for tuning_value in sorted(_NOT_RENDERED[platform]):
            tuning_type = TuningType(tuning_value)
            capability = get_capability(platform, tuning_type)
            assert capability is not None
            errors, _warnings = _config_requesting(tuning_type).validate_for_platform_detailed(platform)
            refusal = next(error for error in errors if error.startswith(f"requested {tuning_value} is not applied"))
            assert capability.mechanism_id in refusal
            assert not _SOURCE_LOCATION.search(refusal), refusal
            assert not _SOURCE_LOCATION.search(not_rendered_reason(capability))

    def test_alias_platforms_resolve_to_registry_entries(self) -> None:
        for alias, target in PLATFORM_ALIASES.items():
            if target in known_registry_platforms():
                assert resolve_platform_key(alias) == target


_GENERATOR_QUIET_FIELDS: dict[tuple[str, str], str] = {
    ("databricks", "distribution"): "distribute_by",
    ("firebolt", "sorting"): "sort_by",
    ("firebolt", "clustering"): "cluster_by",
    ("questdb", "sorting"): "sort_by",
    ("questdb", "distribution"): "distribute_by",
    ("azure-synapse", "sorting"): "sort_by",
    ("azure-synapse", "clustering"): "cluster_by",
}


_DATABEND_QUIET_KINDS: frozenset[str] = frozenset({"partitioning", "sorting", "distribution"})


_VACUITY_SIGNATURE_ONLY: dict[tuple[str, str], str] = {
    ("tpcds_obt", "TPCDSOBT"): "get_create_tables_sql accepts no tuning_config",
    ("core.joinorder.benchmark", "JoinOrderSchema"): "schema helper; get_create_tables_sql accepts no tuning_config",
}


_DISCOVERY_IMPORT_SKIPS: dict[str, str] = {}


def _benchmark_classes_in(module: object, module_key: str, seen: set[int]) -> list[tuple[str, str, type]]:
    found: list[tuple[str, str, type]] = []
    for attribute in sorted(vars(module)):
        value = vars(module)[attribute]
        if (
            inspect.isclass(value)
            and hasattr(value, "get_create_tables_sql")
            and value.__module__.split(".")[0] == "benchbox"
            and id(value) not in seen
        ):
            seen.add(id(value))
            found.append((module_key, attribute, value))
    return found


def _discovered_benchmarks() -> list[tuple[str, str, type]]:
    import importlib
    import pkgutil

    import benchbox
    import benchbox.core

    found: list[tuple[str, str, type]] = []
    seen: set[int] = set()
    for module in sorted(pkgutil.iter_modules(benchbox.__path__), key=lambda entry: entry.name):
        imported = importlib.import_module(f"benchbox.{module.name}")
        found.extend(_benchmark_classes_in(imported, module.name, seen))
    failures: dict[str, str] = {}
    for module in sorted(pkgutil.walk_packages(benchbox.core.__path__, "benchbox.core."), key=lambda e: e.name):
        if module.name in _DISCOVERY_IMPORT_SKIPS:
            continue
        try:
            imported = importlib.import_module(module.name)
        except ImportError as exc:
            failures[module.name] = str(exc)
            continue
        found.extend(_benchmark_classes_in(imported, module.name.removeprefix("benchbox."), seen))
    assert not failures, f"benchbox.core modules failed to import; fix or list in _DISCOVERY_IMPORT_SKIPS: {failures}"
    return found


def _vacuous_capable_benchmarks() -> list[tuple[str, str, object]]:
    capable: list[tuple[str, str, object]] = []
    for module_name, class_name, benchmark_cls in _discovered_benchmarks():
        if (module_name, class_name) in _VACUITY_SIGNATURE_ONLY:
            parameters = inspect.signature(benchmark_cls.get_create_tables_sql).parameters
            assert "tuning_config" not in parameters, (
                f"{module_name}.{class_name} now accepts tuning_config; cover it behaviorally"
            )
            continue
        try:
            benchmark = benchmark_cls()
        except Exception as exc:
            pytest.fail(
                f"{module_name}.{class_name} needs explicit construction ({exc}); construct it in "
                "_vacuous_capable_benchmarks or classify it in _VACUITY_SIGNATURE_ONLY"
            )
        parameters = inspect.signature(benchmark.get_create_tables_sql).parameters
        assert "tuning_config" in parameters, (
            f"{module_name}.{class_name} accepts no tuning_config; classify it in _VACUITY_SIGNATURE_ONLY"
        )
        capable.append((module_name, class_name, benchmark))
    assert capable, "no behaviorally covered benchmarks; the vacuity proof is empty"
    return capable


class TestBenchmarkDdlAndAdapterBehavior:
    def test_unique_check_rendering_is_vacuous_in_tree(self) -> None:
        for module_name, class_name, benchmark in _vacuous_capable_benchmarks():
            plain = UnifiedTuningConfiguration()
            plain.disable_all_constraints()
            toggled = UnifiedTuningConfiguration()
            toggled.disable_all_constraints()
            toggled.unique_constraints.enabled = True
            toggled.check_constraints.enabled = True
            assert benchmark.get_create_tables_sql(tuning_config=plain) == benchmark.get_create_tables_sql(
                tuning_config=toggled
            ), (
                f"{module_name}.{class_name} DDL now varies with UNIQUE/CHECK flags; the not-rendered "
                "UNIQUE/CHECK registry entries must be re-audited against execution"
            )

    def test_benchmark_ddl_ignores_layout_flags_in_tree(self) -> None:
        for module_name, class_name, benchmark in _vacuous_capable_benchmarks():
            plain = UnifiedTuningConfiguration()
            plain.disable_all_constraints()
            toggled = UnifiedTuningConfiguration()
            toggled.disable_all_constraints()
            toggled.table_tunings["orders"] = TableTuning(
                table_name="orders",
                partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
                sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
                clustering=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
                distribution=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
            )
            assert benchmark.get_create_tables_sql(tuning_config=plain) == benchmark.get_create_tables_sql(
                tuning_config=toggled
            ), (
                f"{module_name}.{class_name} DDL now varies with layout tuning flags; the not-rendered "
                "layout registry entries must be re-audited against execution"
            )

    def test_vacuity_discovery_covers_all_benchmarks(self) -> None:
        found = {(module_name, class_name) for module_name, class_name, _ in _discovered_benchmarks()}
        assert found, "benchmark discovery is empty; the vacuity proof covers nothing"
        known = {
            ("tpch", "TPCH"),
            ("tpcds", "TPCDS"),
            ("ssb", "SSB"),
            ("clickbench", "ClickBench"),
            ("core.ai_primitives", "AIPrimitivesBenchmark"),
        }
        assert known <= found, f"discovery lost known benchmarks: {sorted(known - found)}"
        for key in _VACUITY_SIGNATURE_ONLY:
            assert key in found, f"signature-only benchmark {key} disappeared from discovery"
        covered = {(seen_module, seen_class) for seen_module, seen_class, _ in _vacuous_capable_benchmarks()}
        for module_name, class_name in sorted(found - set(_VACUITY_SIGNATURE_ONLY)):
            assert (module_name, class_name) in covered, (
                f"{module_name}.{class_name} is not covered by the vacuity proof; construct it in "
                "_vacuous_capable_benchmarks or classify it in _VACUITY_SIGNATURE_ONLY"
            )

    def test_questdb_create_schema_strips_constraints(self) -> None:
        from benchbox.platforms.base.ddl_helpers import strip_foreign_keys
        from benchbox.platforms.questdb import QuestDBAdapter

        adapter = QuestDBAdapter.__new__(QuestDBAdapter)
        create = "CREATE TABLE orders (o_orderkey INTEGER, o_custkey INTEGER, PRIMARY KEY (o_orderkey));"
        assert "PRIMARY KEY" not in QuestDBAdapter._strip_pk_constraints(adapter, create)
        assert "o_orderkey INTEGER" in QuestDBAdapter._strip_pk_constraints(adapter, create)
        foreign = "CREATE TABLE orders (o_orderkey INTEGER, FOREIGN KEY (o_orderkey) REFERENCES c (x));"
        assert "FOREIGN KEY" not in strip_foreign_keys(foreign)

    def test_trino_base_optimize_strips_primary_keys(self) -> None:
        from benchbox.platforms.presto_trino_adapter_base import PrestoTrinoAdapterBase

        adapter = PrestoTrinoAdapterBase.__new__(PrestoTrinoAdapterBase)
        adapter.table_format = "hive"
        adapter.catalog = "catalog"
        create = "CREATE TABLE orders (o_orderkey INTEGER, PRIMARY KEY (o_orderkey));"
        assert "PRIMARY KEY" not in PrestoTrinoAdapterBase._optimize_table_definition(adapter, create)
        foreign = "CREATE TABLE orders (o_orderkey INTEGER, FOREIGN KEY (o_orderkey) REFERENCES c (x));"
        assert "FOREIGN KEY" in PrestoTrinoAdapterBase._optimize_table_definition(adapter, foreign)

    def test_clickhouse_optimize_preserves_foreign_keys(self) -> None:
        from benchbox.platforms.clickhouse.workload import ClickHouseWorkloadMixin

        mixin = ClickHouseWorkloadMixin.__new__(ClickHouseWorkloadMixin)
        create = (
            "CREATE TABLE orders (o_orderkey INTEGER, FOREIGN KEY (o_orderkey) REFERENCES c (x)) "
            "ENGINE = MergeTree() ORDER BY tuple();"
        )
        assert "FOREIGN KEY" in ClickHouseWorkloadMixin._optimize_table_definition(mixin, create, None)

    def test_azure_synapse_optimize_uses_engine_default(self) -> None:
        from benchbox.platforms.azure_synapse import AzureSynapseAdapter

        adapter = AzureSynapseAdapter.__new__(AzureSynapseAdapter)
        adapter.schema = "dbo"
        adapter.distribution_default = "ROUND_ROBIN"
        create = "CREATE TABLE orders (o_orderkey INTEGER, tuned_dist INTEGER);"
        rendered = AzureSynapseAdapter._optimize_table_definition(adapter, create)
        assert "DISTRIBUTION = ROUND_ROBIN" in rendered
        assert "tuned_dist" not in rendered.split("WITH (DISTRIBUTION")[1]
        assert "PARTITION" not in rendered.upper()

    def test_questdb_enhancement_takes_no_tuning(self) -> None:
        from benchbox.platforms.questdb import QuestDBAdapter

        parameters = inspect.signature(QuestDBAdapter._apply_questdb_schema_enhancements).parameters
        assert "tuning_config" not in parameters and "table_tuning" not in parameters
        adapter = QuestDBAdapter.__new__(QuestDBAdapter)
        adapter.partition_by = None
        create = "CREATE TABLE lineitem (l_orderkey INTEGER, l_shipdate DATE);"
        rendered = QuestDBAdapter._apply_questdb_schema_enhancements(adapter, create)
        assert "PARTITION BY MONTH" in rendered.upper()

    def test_quiet_generators_emit_no_clause(self) -> None:
        from benchbox.core.tuning.ddl_generator import get_ddl_generator

        for (platform_key, tuning_value), field in sorted(_GENERATOR_QUIET_FIELDS.items()):
            tuning: dict[str, list[TuningColumn]] = {
                tuning_value: [TuningColumn(name="tuned_column", type="INTEGER", order=1)]
            }
            clauses = get_ddl_generator(platform_key).generate_tuning_clauses(TableTuning(table_name="t", **tuning))
            assert getattr(clauses, field) is None, (
                f"{platform_key}/{tuning_value} generator emits a {field} clause from tuned columns"
            )

    def test_databend_generate_ignores_non_clustering(self) -> None:
        from benchbox.platforms.databend.adapter import DatabendAdapter

        for tuning_value in sorted(_DATABEND_QUIET_KINDS):
            tuning: dict[str, list[TuningColumn]] = {
                tuning_value: [TuningColumn(name="tuned_column", type="INTEGER", order=1)]
            }
            assert DatabendAdapter.generate_tuning_clause(None, TableTuning(table_name="t", **tuning)) == ""

    def test_pk_fk_flags_drive_ddl_content(self) -> None:
        from benchbox.ssb import SSBBenchmark
        from benchbox.tpcds import TPCDSBenchmark
        from benchbox.tpch import TPCHBenchmark

        for benchmark in (TPCHBenchmark(), TPCDSBenchmark(), SSBBenchmark()):
            name = type(benchmark).__name__
            disabled = UnifiedTuningConfiguration()
            disabled.disable_all_constraints()
            pk_only = UnifiedTuningConfiguration()
            pk_only.disable_all_constraints()
            pk_only.primary_keys.enabled = True
            plain = benchmark.get_create_tables_sql(tuning_config=disabled)
            keyed = benchmark.get_create_tables_sql(tuning_config=pk_only)
            assert keyed != plain, f"{name} DDL ignores the primary_keys flag"
            assert "PRIMARY KEY" in keyed, f"{name} DDL carries no PRIMARY KEY clause"
            assert "PRIMARY KEY" not in plain, f"{name} DDL carries PRIMARY KEY with flags off"
        for benchmark in (TPCHBenchmark(), TPCDSBenchmark()):
            name = type(benchmark).__name__
            disabled = UnifiedTuningConfiguration()
            disabled.disable_all_constraints()
            fk_only = UnifiedTuningConfiguration()
            fk_only.disable_all_constraints()
            fk_only.foreign_keys.enabled = True
            plain = benchmark.get_create_tables_sql(tuning_config=disabled)
            keyed = benchmark.get_create_tables_sql(tuning_config=fk_only)
            assert keyed != plain, f"{name} DDL ignores the foreign_keys flag"
            assert "FOREIGN KEY" in keyed, f"{name} DDL carries no FOREIGN KEY clause"

    def test_athena_rewrite_takes_no_tuning(self) -> None:
        from benchbox.platforms.athena import AthenaAdapter

        parameters = inspect.signature(AthenaAdapter._convert_to_external_table).parameters
        assert "tuning_config" not in parameters and "table_tuning" not in parameters

    def test_athena_external_table_strips_constraints(self) -> None:
        from benchbox.platforms.athena import AthenaAdapter

        adapter = AthenaAdapter.__new__(AthenaAdapter)
        adapter.s3_bucket = "bucket"
        adapter.s3_prefix = "prefix"
        adapter.database = "db"
        adapter.data_format = "text"
        adapter.default_format = "TEXTFILE"
        create = (
            "CREATE TABLE orders (o_orderkey INTEGER NOT NULL PRIMARY KEY, o_custkey INTEGER, "
            "FOREIGN KEY (o_custkey) REFERENCES customer (c_custkey))"
        )
        for is_staging in (False, True):
            rendered = AthenaAdapter._convert_to_external_table(adapter, create, is_staging=is_staging)
            assert "PRIMARY KEY" not in rendered.upper()
            assert "FOREIGN KEY" not in rendered.upper()
            assert "REFERENCES" not in rendered.upper()
            assert "o_custkey INTEGER)" in rendered

    def test_datafusion_schema_ignores_constraints(self) -> None:
        from benchbox.platforms.datafusion import DataFusionAdapter

        parameters = inspect.signature(DataFusionAdapter._get_benchmark_schema).parameters
        assert "tuning_config" not in parameters and "enable_primary_keys" not in parameters
        assert "enable_foreign_keys" not in parameters


class TestShippedTemplatesValidateClean:
    @pytest.mark.parametrize("platform", ["clickhouse", "databricks", "duckdb", "snowflake"])
    def test_packaged_templates_validate_clean(self, platform: str) -> None:
        from benchbox.core.tuning.packaged_templates import list_packaged_templates

        templates = list_packaged_templates(platform)
        assert templates.get(platform), f"no packaged templates found for {platform}"
        for template_path in templates[platform]:
            config = UnifiedTuningConfiguration.from_dict(yaml.safe_load(template_path.read_text(encoding="utf-8")))
            errors, _warnings = config.validate_for_platform_detailed(platform)
            assert errors == [], f"{template_path}: {errors}"

    @pytest.mark.parametrize(
        "platform",
        [
            "duckdb",
            "duckdb:memory",
            "clickhouse",
            "snowflake",
            "bigquery",
            "postgresql",
            "databricks",
            "redshift",
            "athena",
            "doris",
        ],
    )
    def test_sample_configs_validate_clean(self, tmp_path, platform: str) -> None:
        from benchbox.cli.config import ConfigManager

        target = tmp_path / f"{platform}_tuning.yaml"
        ConfigManager().create_sample_unified_tuning_config(target, platform)
        config = UnifiedTuningConfiguration.from_dict(yaml.safe_load(target.read_text(encoding="utf-8")))
        errors, _warnings = config.validate_for_platform_detailed(platform)
        assert errors == [], f"sample for {platform}: {errors}"

    def test_sample_config_summary_lists_only_enabled_constraints(self, tmp_path, monkeypatch) -> None:
        from rich.console import Console

        import benchbox.cli.config as cli_config

        recorder = Console(record=True, width=200)
        monkeypatch.setattr(cli_config, "console", recorder)
        cli_config.ConfigManager().create_sample_unified_tuning_config(tmp_path / "duckdb.yaml", "duckdb")
        summary = recorder.export_text()
        assert "Schema constraints (primary keys, foreign keys)" in summary
        assert "unique" not in summary and "check" not in summary

        recorder = Console(record=True, width=200)
        monkeypatch.setattr(cli_config, "console", recorder)
        cli_config.ConfigManager().create_sample_unified_tuning_config(tmp_path / "doris.yaml", "doris")
        assert "Schema constraints" not in recorder.export_text()

    def test_platform_variant_validates_like_its_base(self) -> None:
        for tuning_type in TuningType:
            config = UnifiedTuningConfiguration()
            config.disable_all_constraints()
            constraint_attrs = {tt: attr for attr, tt in config._CONSTRAINT_CHECKS}
            option_attrs = {tt: attr for attr, tt in config._OPT_CHECKS}
            if tuning_type in constraint_attrs:
                getattr(config, constraint_attrs[tuning_type]).enabled = True
            elif tuning_type in option_attrs:
                setattr(config.platform_optimizations, option_attrs[tuning_type], True)
            else:
                config.table_tunings["orders"] = TableTuning(
                    table_name="orders",
                    **{tuning_type.value: [TuningColumn(name="o_orderdate", type="DATE", order=1)]},
                )
            assert config.get_enabled_tuning_types() == {tuning_type}, tuning_type
            base_errors, base_warnings = config.validate_for_platform_detailed("duckdb")
            variant_errors, variant_warnings = config.validate_for_platform_detailed("duckdb:memory")
            assert [e.replace("duckdb:memory", "duckdb") for e in variant_errors] == base_errors, tuning_type
            assert [w.replace("duckdb:memory", "duckdb") for w in variant_warnings] == base_warnings, tuning_type
        assert TuningType.is_known_platform("duckdb:memory")
        assert TuningType.SORTING.is_compatible_with_platform("DuckDB:memory")

    @pytest.mark.parametrize("platform", sorted(known_registry_platforms() | {"spark", "starburst"}))
    def test_fallback_config_validates_clean(self, tmp_path, platform: str) -> None:
        from benchbox.cli.tuning_runtime import build_default_unified_config

        config = build_default_unified_config(platform)
        errors, _warnings = config.validate_for_platform_detailed(platform)
        assert errors == [], f"fallback for {platform}: {errors}"
