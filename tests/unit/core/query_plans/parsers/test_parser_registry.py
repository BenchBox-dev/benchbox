import pytest

from benchbox.core.query_plans.parsers.base import QueryPlanParser
from benchbox.core.query_plans.parsers.registry import (
    ParserRegistry,
    get_parser_for_platform,
    get_parser_registry,
    reset_global_registry,
)
from benchbox.core.results.query_plan_models import QueryPlanDAG

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockParserV1(QueryPlanParser):
    def __init__(self):
        super().__init__("mock")
        self.version = "v1"

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        raise NotImplementedError


class MockParserV2(QueryPlanParser):
    def __init__(self):
        super().__init__("mock")
        self.version = "v2"

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        raise NotImplementedError


class MockParserV3(QueryPlanParser):
    def __init__(self):
        super().__init__("mock")
        self.version = "v3"

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        raise NotImplementedError


class TestParserRegistry:
    def test_empty_registry(self) -> None:

        registry = ParserRegistry()
        assert registry.get_parser("nonexistent", "1.0.0") is None

    def test_register_single_parser(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)

        parser = registry.get_parser("testdb", "1.0.0")
        assert parser is not None
        assert parser.version == "v1"

    def test_register_multiple_versions(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)
        registry.register("testdb", "2.0.0", MockParserV2)
        registry.register("testdb", "3.0.0", MockParserV3)

        parser = registry.get_parser("testdb", "1.5.0")
        assert parser is not None
        assert parser.version == "v1"

        parser = registry.get_parser("testdb", "2.5.0")
        assert parser is not None
        assert parser.version == "v2"

        parser = registry.get_parser("testdb", "3.0.0")
        assert parser is not None
        assert parser.version == "v3"

        parser = registry.get_parser("testdb", "4.0.0")
        assert parser is not None
        assert parser.version == "v3"

    def test_version_too_old(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "2.0.0", MockParserV2)

        parser = registry.get_parser("testdb", "1.0.0")
        assert parser is None

    def test_case_insensitive_platform(self) -> None:

        registry = ParserRegistry()
        registry.register("TestDB", "1.0.0", MockParserV1)

        parser = registry.get_parser("testdb", "1.0.0")
        assert parser is not None

        parser = registry.get_parser("TESTDB", "1.0.0")
        assert parser is not None

    def test_get_parser_no_version(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)
        registry.register("testdb", "2.0.0", MockParserV2)

        parser = registry.get_parser("testdb")
        assert parser is not None
        assert parser.version == "v2"

    def test_invalid_version_string(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)
        registry.register("testdb", "2.0.0", MockParserV2)

        parser = registry.get_parser("testdb", "invalid-version")
        assert parser is not None
        assert parser.version == "v2"

    def test_get_all_platforms(self) -> None:

        registry = ParserRegistry()
        registry.register("platforma", "1.0.0", MockParserV1)
        registry.register("platformb", "1.0.0", MockParserV2)

        platforms = registry.get_all_platforms()
        assert set(platforms) == {"platforma", "platformb"}

    def test_get_parser_versions(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)
        registry.register("testdb", "2.0.0", MockParserV2)

        versions = registry.get_parser_versions("testdb")
        assert len(versions) == 2
        assert ("1.0.0", "MockParserV1") in versions
        assert ("2.0.0", "MockParserV2") in versions

    def test_get_parser_versions_unknown_platform(self) -> None:

        registry = ParserRegistry()
        versions = registry.get_parser_versions("unknown")
        assert versions == []

    def test_clear_registry(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)

        assert registry.get_parser("testdb", "1.0.0") is not None

        registry.clear()

        assert registry.get_parser("testdb", "1.0.0") is None
        assert registry.get_all_platforms() == []

    def test_semver_comparison(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "0.9.0", MockParserV1)
        registry.register("testdb", "0.10.0", MockParserV2)
        registry.register("testdb", "1.0.0", MockParserV3)

        parser = registry.get_parser("testdb", "0.9.5")
        assert parser is not None
        assert parser.version == "v1"

        parser = registry.get_parser("testdb", "0.10.0")
        assert parser is not None
        assert parser.version == "v2"

        parser = registry.get_parser("testdb", "0.11.0")
        assert parser is not None
        assert parser.version == "v2"


class TestGlobalRegistry:
    def setup_method(self) -> None:
        reset_global_registry()

    def teardown_method(self) -> None:
        reset_global_registry()

    def test_get_parser_registry(self) -> None:

        registry = get_parser_registry()
        assert isinstance(registry, ParserRegistry)

    def test_registry_singleton(self) -> None:

        registry1 = get_parser_registry()
        registry2 = get_parser_registry()
        assert registry1 is registry2

    def test_default_duckdb_parser(self) -> None:

        parser = get_parser_for_platform("duckdb", "1.0.0")
        assert parser is not None
        assert parser.platform_name == "duckdb"

    def test_default_postgresql_parser(self) -> None:

        parser = get_parser_for_platform("postgresql", "14.0")
        assert parser is not None
        assert parser.platform_name == "postgresql"

    def test_default_postgres_alias(self) -> None:

        parser = get_parser_for_platform("postgres", "14.0")
        assert parser is not None
        assert parser.platform_name == "postgresql"

    def test_default_redshift_parser(self) -> None:

        parser = get_parser_for_platform("redshift", "1.0.0")
        assert parser is not None
        assert parser.platform_name == "redshift"

    def test_default_datafusion_parser(self) -> None:

        parser = get_parser_for_platform("datafusion", "35.0.0")
        assert parser is not None
        assert parser.platform_name == "datafusion"

    def test_default_sqlite_parser(self) -> None:

        parser = get_parser_for_platform("sqlite", "3.40.0")
        assert parser is not None
        assert parser.platform_name == "sqlite"

    def test_clickhouse_deployment_mode_keys_resolve(self) -> None:
        for platform in (
            "clickhouse",
            "clickhouse-local",
            "clickhouse-server",
            "clickhouse-cloud",
        ):
            parser = get_parser_for_platform(platform)
            assert parser is not None, f"no parser resolved for {platform}"
            assert parser.platform_name == "clickhouse"

    def test_unknown_platform(self) -> None:

        parser = get_parser_for_platform("unknowndb", "1.0.0")
        assert parser is None

    def test_reset_global_registry(self) -> None:

        registry1 = get_parser_registry()
        reset_global_registry()
        registry2 = get_parser_registry()
        assert registry1 is not registry2


class TestParserRegistryVersionSelection:
    def test_exact_version_match(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)

        parser = registry.get_parser("testdb", "1.0.0")
        assert parser is not None
        assert parser.version == "v1"

    def test_version_with_prerelease(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)
        registry.register("testdb", "2.0.0", MockParserV2)

        parser = registry.get_parser("testdb", "2.0.0-beta")
        assert parser is not None
        assert parser.version == "v1"

    def test_version_with_build_metadata(self) -> None:

        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)

        parser = registry.get_parser("testdb", "1.0.0+build123")
        assert parser is not None
        assert parser.version == "v1"

    def test_short_version_string(self) -> None:
        registry = ParserRegistry()
        registry.register("testdb", "1.0.0", MockParserV1)
        registry.register("testdb", "2.0.0", MockParserV2)

        parser = registry.get_parser("testdb", "1")
        assert parser is not None
        assert parser.version == "v1"

        parser = registry.get_parser("testdb", "2")
        assert parser is not None
        assert parser.version == "v2"


