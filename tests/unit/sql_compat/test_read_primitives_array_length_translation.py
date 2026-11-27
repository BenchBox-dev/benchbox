import pytest

from benchbox.core.query_catalog import _benchmark_instance

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(scope="module")
def read_primitives():
    return _benchmark_instance("read_primitives")


def test_doris_array_length_uses_array_size(read_primitives):
    sql = read_primitives.get_queries(dialect="doris")["array_length"]
    assert "ARRAY_SIZE(`parts`) AS `num_parts`" in sql
    assert "ARRAY_LENGTH" not in sql
    assert "CARDINALITY" not in sql


def test_starrocks_array_length_keeps_array_length(read_primitives):
    sql = read_primitives.get_queries(dialect="starrocks")["array_length"]
    assert "ARRAY_LENGTH(`parts`) AS `num_parts`" in sql
    assert "ARRAY_SIZE" not in sql
