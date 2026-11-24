from __future__ import annotations

from benchbox.core.results.corpus_rule import derived_results_disclaimer

TPCH_DERIVED_DISCLAIMER = derived_results_disclaimer("TPC-H")
TPCDS_DERIVED_DISCLAIMER = derived_results_disclaimer("TPC-DS")


_BENCHMARK_LABELS = {"tpch": "TPC-H", "tpcds": "TPC-DS", "tpcdi": "TPC-DI"}


def official_mode_disclaimer(benchmark: str | None) -> str:
    label = _BENCHMARK_LABELS.get((benchmark or "").lower())
    if label is None:
        return (
            "Derived from the TPC benchmark. These results do not comply with the TPC Specification "
            "and must not be compared with official TPC results."
        )
    return derived_results_disclaimer(label)
