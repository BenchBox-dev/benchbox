# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) DS (TPC-DS) - Copyright (c) Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS, adapter_query_ids
from tests.unit.core.tpcds.test_literal_fallback_lint import PENDING_ADAPTER, _supplies
from tests.unit.core.tpcds.test_parameter_binding_coverage import _RecordingValues, _values_in_sql

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

STRUCTURAL_NAMES: dict[tuple[int, str], str] = {}


@pytest.fixture(scope="module")
def dsqgen():
    from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError

    try:
        return DSQGenBinary()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")


@pytest.fixture(scope="module")
def draws(dsqgen):
    query_ids = range(1, 100)
    with ThreadPoolExecutor(max_workers=4) as pool:
        logs = pool.map(
            lambda query_id: dict(dsqgen.generate_parameter_log(query_id, scale_factor=1).substitutions), query_ids
        )
        logged_by_query = dict(zip(query_ids, logs))
    return {
        query_id: (logged, _values_in_sql(dsqgen, query_id, logged)) for query_id, logged in logged_by_query.items()
    }


@pytest.mark.parametrize("query_id", adapter_query_ids())
def test_adapter_reads_every_drawn_name_that_reaches_the_sql(draws, query_id):
    logged, reaching = draws[query_id]
    recording = _RecordingValues(logged)
    ADAPTERS[query_id](recording)
    declared = {name for (query, name) in STRUCTURAL_NAMES if query == query_id}
    unbound = reaching - recording.read - declared
    assert not unbound, f"Q{query_id}: drawn values reach the SQL but the adapter never reads {sorted(unbound)}"


@pytest.mark.parametrize("query_id", sorted(PENDING_ADAPTER))
def test_pending_queries_draw_values_that_reach_the_sql(draws, query_id):
    assert query_id not in ADAPTERS
    assert draws[query_id][1], f"Q{query_id} draws nothing that reaches the SQL, so it needs no adapter"


def test_structural_declarations_are_still_needed(draws):
    stale = []
    for (query_id, name), reason in sorted(STRUCTURAL_NAMES.items()):
        assert reason, f"Q{query_id} {name}: a structural declaration needs a reason"
        logged, reaching = draws[query_id]
        recording = _RecordingValues(logged)
        ADAPTERS[query_id](recording)
        if name not in reaching or name in recording.read:
            stale.append((query_id, name))
    assert not stale, f"declared structural but now read by the adapter or no longer reaching the SQL: {stale}"


@pytest.mark.parametrize("query_id", adapter_query_ids())
def test_lint_sees_every_key_the_adapter_returns(draws, query_id):
    returned = ADAPTERS[query_id](draws[query_id][0])
    unseen = sorted(key for key in returned if not _supplies(query_id, key))
    assert not unseen, f"Q{query_id}: the adapter returns {unseen}, which the lint's reading of its source misses"


