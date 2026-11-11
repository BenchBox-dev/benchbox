from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_polars_df_smoke as smoke

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _payload(query_ids: list[str], *, failed: tuple[str, ...] = ()) -> dict:
    queries = [{"id": query_id, "run_type": "warmup", "status": "SUCCESS"} for query_id in query_ids]
    queries += [
        {"id": query_id, "run_type": "measurement", "status": "FAILED" if query_id in failed else "SUCCESS"}
        for query_id in query_ids
    ]
    return {"queries": queries}


def _write(results_dir: Path, benchmark: str, payload: dict) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{benchmark}_sf001_polars_df_20260101_000000_abcd.json").write_text(json.dumps(payload))


SSB_IDS = sorted(smoke.EXPECTED_QUERY_IDS["ssb"])


@pytest.mark.parametrize(("benchmark_id", "count"), [("tpch", 22), ("tpcds", 103), ("ssb", 13), ("clickbench", 43)])
def test_expected_query_sets_have_benchmark_sizes(benchmark_id: str, count: int) -> None:
    assert len(smoke.EXPECTED_QUERY_IDS[benchmark_id]) == count


def test_complete_successful_run_passes() -> None:
    assert smoke.check_result("ssb", _payload(SSB_IDS)) == []


def test_missing_queries_are_reported() -> None:
    problems = smoke.check_result("ssb", _payload(SSB_IDS[:-1]))

    assert problems == ["ssb: missing queries 4.3"]


def test_substituted_query_is_reported_even_when_the_count_matches() -> None:
    problems = smoke.check_result("ssb", _payload([*SSB_IDS[:-1], "9.9"]))

    assert problems == ["ssb: missing queries 4.3", "ssb: unexpected queries 9.9"]


def test_failed_queries_are_reported() -> None:
    problems = smoke.check_result("ssb", _payload(SSB_IDS, failed=("2.1",)))

    assert problems == ["ssb: non-successful queries 2.1"]


def test_main_fails_when_a_benchmark_result_is_missing(tmp_path: Path) -> None:
    _write(tmp_path, "ssb", _payload(SSB_IDS))

    assert smoke.main([str(tmp_path), "--benchmark", "ssb"]) == 0
    assert smoke.main([str(tmp_path), "--benchmark", "ssb", "--benchmark", "tpch"]) == 1


