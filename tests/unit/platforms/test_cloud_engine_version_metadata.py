import pytest

from benchbox.core.results.platform_info import PlatformInfoInput, merge_platform_info
from benchbox.core.results.schema import ENGINE_VERSION_KEYS, _collect_engine_version_metadata

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestEngineVersionMetadataFields:
    def test_platform_info_input_has_engine_version_fields(self):
        info = PlatformInfoInput(
            name="Snowflake",
            engine_version="8.42.0",
            engine_version_source="sql_query",
        )
        assert info.engine_version == "8.42.0"
        assert info.engine_version_source == "sql_query"

    def test_platform_info_input_defaults_to_none(self):
        info = PlatformInfoInput(name="DuckDB")
        assert info.engine_version is None
        assert info.engine_version_source is None

    def test_merge_preserves_engine_version(self):
        base = PlatformInfoInput(
            name="Snowflake",
            engine_version="8.42.0",
            engine_version_source="sql_query",
        )
        merged = merge_platform_info(base, {"extra": "config"})
        assert merged.engine_version == "8.42.0"
        assert merged.engine_version_source == "sql_query"

    def test_engine_version_keys_tuple(self):
        assert "engine_version" in ENGINE_VERSION_KEYS
        assert "engine_version_source" in ENGINE_VERSION_KEYS


class TestCollectEngineVersionMetadata:
    def test_from_direct_result_fields(self):
        class FakeResult:
            engine_version = "8.42.0"
            engine_version_source = "sql_query"
            execution_metadata = None
            platform_info = None

        meta = _collect_engine_version_metadata(FakeResult())
        assert meta == {"engine_version": "8.42.0", "engine_version_source": "sql_query"}

    def test_from_execution_metadata_fallback(self):
        class FakeResult:
            engine_version = None
            engine_version_source = None
            execution_metadata = {"engine_version": "9.0.0", "engine_version_source": "api"}
            platform_info = None

        meta = _collect_engine_version_metadata(FakeResult())
        assert meta == {"engine_version": "9.0.0", "engine_version_source": "api"}

    def test_from_platform_info_fallback(self):
        class FakeResult:
            engine_version = None
            engine_version_source = None
            execution_metadata = None
            platform_info = {"engine_version": "7.1.0", "engine_version_source": "connection_metadata"}

        meta = _collect_engine_version_metadata(FakeResult())
        assert meta == {"engine_version": "7.1.0", "engine_version_source": "connection_metadata"}

    def test_direct_fields_take_precedence(self):
        class FakeResult:
            engine_version = "direct"
            engine_version_source = "direct_source"
            execution_metadata = {"engine_version": "fallback"}
            platform_info = {"engine_version": "fallback2"}

        meta = _collect_engine_version_metadata(FakeResult())
        assert meta["engine_version"] == "direct"
        assert meta["engine_version_source"] == "direct_source"

    def test_empty_when_no_engine_version(self):
        class FakeResult:
            engine_version = None
            engine_version_source = None
            execution_metadata = None
            platform_info = None

        meta = _collect_engine_version_metadata(FakeResult())
        assert meta == {}

    def test_execution_metadata_takes_precedence_over_platform_info(self):

        class FakeResult:
            engine_version = None
            engine_version_source = None
            execution_metadata = {"engine_version": "exec_ver", "engine_version_source": "api"}
            platform_info = {"engine_version": "plat_ver", "engine_version_source": "connection_metadata"}

        meta = _collect_engine_version_metadata(FakeResult())
        assert meta["engine_version"] == "exec_ver"
        assert meta["engine_version_source"] == "api"


