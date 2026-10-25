import pytest

from benchbox.core.tpc_disclaimer import derived_results_disclaimer, official_mode_disclaimer

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    ("benchmark_id", "label"),
    [("tpch", "TPC-H"), ("TPCDS", "TPC-DS"), ("tpcdi", "TPC-DI")],
)
def test_official_mode_disclaimer_names_running_benchmark(benchmark_id, label):
    text = official_mode_disclaimer(benchmark_id)
    assert text == derived_results_disclaimer(label)
    assert f"do not comply with the {label} Specification" in text


@pytest.mark.parametrize("benchmark_id", [None, "", "tpcxyz"])
def test_official_mode_disclaimer_is_generic_for_unknown_benchmarks(benchmark_id):
    text = official_mode_disclaimer(benchmark_id)
    assert "TPC-H" not in text
    assert "do not comply with the TPC Specification" in text
