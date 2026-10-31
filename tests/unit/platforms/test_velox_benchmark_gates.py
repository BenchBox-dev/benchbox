import pytest

from benchbox.core.platform_registry import PlatformRegistry

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(scope="module")
def unsupported() -> dict[str, str]:
    caps = PlatformRegistry.get_platform_capabilities("velox")
    assert caps is not None
    return caps.unsupported_benchmarks


def test_metadata_primitives_is_blocked_for_missing_information_schema(unsupported):
    assert "INFORMATION_SCHEMA" in unsupported["metadata_primitives"]


@pytest.mark.parametrize("benchmark_name", ["write_primitives", "transaction_primitives"])
def test_row_level_dml_benchmarks_are_blocked(unsupported, benchmark_name):
    assert "row-level DML" in unsupported[benchmark_name]
    assert "Gluten" in unsupported[benchmark_name]


def test_supported_benchmarks_are_not_blocked(unsupported):
    for benchmark in ("tpch", "read_primitives", "tpchavoc"):
        assert benchmark not in unsupported
