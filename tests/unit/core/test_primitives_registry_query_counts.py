import pytest

from benchbox.core.benchmark_registry import BENCHMARK_METADATA
from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    ("benchmark_id", "benchmark_class"),
    [
        ("write_primitives", WritePrimitivesBenchmark),
        ("transaction_primitives", TransactionPrimitivesBenchmark),
    ],
)
def test_registry_num_queries_matches_loaded_queries(benchmark_id, benchmark_class, tmp_path):
    benchmark = benchmark_class(scale_factor=0.01, output_dir=tmp_path)

    assert BENCHMARK_METADATA[benchmark_id]["num_queries"] == len(benchmark.get_queries())
