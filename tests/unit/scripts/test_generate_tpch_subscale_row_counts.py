from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "_project/scripts/generate_tpch_subscale_row_counts.py"
if not SCRIPT_PATH.is_file():
    pytest.skip("the release tree has no row-count generator", allow_module_level=True)

spec = importlib.util.spec_from_file_location("generate_tpch_subscale_row_counts_test", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
generator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = generator
spec.loader.exec_module(generator)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_COUNTS = {query_id: int(query_id) for query_id in generator.QUERY_IDS}


def _observed(counts: dict[str, int], **overrides: set[int]) -> dict[str, set[int]]:
    return {query_id: overrides.get(f"q{query_id}", {count}) for query_id, count in counts.items()}


def _build(engine_counts, seed_counts=None):
    return generator.build_reference(
        0.01,
        engine_counts,
        seed_counts or {},
        {"duckdb": "1", "datafusion": "2"},
        "digest",
    )


def _both(counts: dict[str, set[int]]) -> dict[str, dict[str, set[int]]]:
    return {"duckdb": counts, "datafusion": counts}


def test_agreeing_engines_produce_the_reference_counts() -> None:
    reference = _build(_both(_observed(_COUNTS)))

    assert reference["row_counts"] == _COUNTS
    assert reference["reference_seed"] is None
    assert reference["parameter_sensitive_query_ids"] == ["11", "13", "16", "18", "20"]


def test_queries_that_change_under_a_probe_seed_are_parameter_sensitive() -> None:
    reference = _build(_both(_observed(_COUNTS)), {7: _both(_observed(_COUNTS, q2={3}))})

    assert reference["parameter_sensitive_query_ids"] == ["2", "11", "13", "16", "18", "20"]


def test_an_engine_that_never_returns_the_reference_count_is_refused() -> None:
    with pytest.raises(SystemExit, match="Q15"):
        _build({"duckdb": _observed(_COUNTS), "datafusion": _observed(_COUNTS, q15={0})})


def test_an_engine_that_sometimes_returns_another_count_is_refused() -> None:
    with pytest.raises(SystemExit, match="engines did not all return one identical count"):
        _build({"duckdb": _observed(_COUNTS), "datafusion": _observed(_COUNTS, q15={0, 15})})


def test_an_unstable_reference_engine_is_refused() -> None:
    with pytest.raises(SystemExit, match="Q3"):
        _build({"duckdb": _observed(_COUNTS, q3={3, 4}), "datafusion": _observed(_COUNTS)})


def test_engines_that_disagree_under_a_probe_seed_are_refused() -> None:
    with pytest.raises(SystemExit, match="seed 7"):
        _build(
            _both(_observed(_COUNTS)),
            {7: {"duckdb": _observed(_COUNTS, q2={3}), "datafusion": _observed(_COUNTS)}},
        )
