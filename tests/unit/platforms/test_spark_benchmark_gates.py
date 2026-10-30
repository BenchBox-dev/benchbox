import pytest

from benchbox.core.platform_registry import PlatformRegistry

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(scope="module")
def unsupported() -> dict[str, str]:
    caps = PlatformRegistry.get_platform_capabilities("spark")
    assert caps is not None
    return caps.unsupported_benchmarks


def test_metadata_primitives_is_blocked_for_missing_information_schema(unsupported):
    assert "INFORMATION_SCHEMA" in unsupported["metadata_primitives"]


@pytest.mark.parametrize("benchmark", ["write_primitives", "transaction_primitives"])
def test_row_level_dml_benchmarks_are_blocked(unsupported, benchmark):
    assert "UNSUPPORTED_FEATURE.TABLE_OPERATION" in unsupported[benchmark]


def test_supported_benchmarks_are_not_blocked(unsupported):
    for benchmark in ("tpch", "read_primitives", "tpchavoc"):
        assert benchmark not in unsupported


def test_dataframe_pyspark_platform_is_not_gated():
    caps = PlatformRegistry.get_platform_capabilities("pyspark-df")
    assert caps is None or "write_primitives" not in caps.unsupported_benchmarks
