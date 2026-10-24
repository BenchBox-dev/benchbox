from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.tuning.workload_profiles import (
    ACCEPTED,
    DROPPED_LOW_EVIDENCE,
    EXISTING_BASELINE,
    load_tpc_tuning_profile,
    load_workload_tuning_profile,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_tpc_profile_loads_recovered_audit_candidates() -> None:
    profile = load_tpc_tuning_profile()

    tpch = {(candidate.table, candidate.column): candidate for candidate in profile.candidates("tpch")}
    tpcds = {(candidate.table, candidate.column): candidate for candidate in profile.candidates("tpcds")}

    assert profile.id == "tpc-v1"
    assert tpch[("LINEITEM", "L_SUPPKEY")].status == ACCEPTED
    assert tpch[("LINEITEM", "L_SUPPKEY")].query_ids == ("5", "7", "8", "9", "15", "20", "21")
    assert tpch[("ORDERS", "O_ORDERKEY")].status == EXISTING_BASELINE
    assert tpcds[("DATE_DIM", "D_YEAR")].query_count == 58
    assert tpcds[("WEB_RETURNS", "WR_WEB_PAGE_SK")].status == DROPPED_LOW_EVIDENCE
    assert tpcds[("CATALOG_RETURNS", "CR_SHIP_MODE_SK")].query_count == 0


def test_tpc_profile_required_candidates_include_existing_baseline_columns() -> None:
    profile = load_tpc_tuning_profile()
    required = {(candidate.table, candidate.column) for candidate in profile.required_candidates("tpcds")}

    assert ("STORE_SALES", "SS_SOLD_DATE_SK") in required
    assert ("STORE_SALES", "SS_ITEM_SK") in required
    assert ("CUSTOMER", "C_CUSTOMER_SK") in required


def test_workload_profile_rejects_duplicate_candidates(tmp_path: Path) -> None:
    profile_path = tmp_path / "duplicate.yaml"
    profile_path.write_text(
        """
profile:
  id: test
  version: "1"
  description: "duplicate test"
  benchmarks:
    tpch:
      evidence_source: "tests"
      fact_tables: [LINEITEM]
      candidates:
      - table: LINEITEM
        column: L_ORDERKEY
        type: INTEGER
        roles: [join_locality]
        query_count: 3
        query_ids: ["1", "2", "3"]
        status: accepted
        rationale: "first"
      - table: LINEITEM
        column: L_ORDERKEY
        type: INTEGER
        roles: [join_locality]
        query_count: 3
        query_ids: ["1", "2", "3"]
        status: accepted
        rationale: "second"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate tuning profile candidate"):
        load_workload_tuning_profile(profile_path)


def test_workload_profile_rejects_accepted_candidates_without_minimum_evidence(tmp_path: Path) -> None:
    profile_path = tmp_path / "low-evidence.yaml"
    profile_path.write_text(
        """
profile:
  id: test
  version: "1"
  description: "low evidence test"
  benchmarks:
    tpcds:
      evidence_source: "tests"
      fact_tables: [WEB_RETURNS]
      candidates:
      - table: WEB_RETURNS
        column: WR_WEB_PAGE_SK
        type: INTEGER
        roles: [fact_dimension_join]
        query_count: 1
        query_ids: ["77"]
        status: accepted
        rationale: "too little evidence"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="accepted candidates require query_count >= 3"):
        load_workload_tuning_profile(profile_path)


def test_tpc_profile_ssb_extension() -> None:
    profile = load_tpc_tuning_profile()

    assert "ssb" in profile.benchmarks()
    ssb = {(candidate.table, candidate.column): candidate for candidate in profile.candidates("ssb")}
    assert len(ssb) == 8
    assert ssb[("LINEORDER", "LO_ORDERDATE")].status == EXISTING_BASELINE
    assert ssb[("LINEORDER", "LO_ORDERDATE")].query_count == 13
    assert ssb[("LINEORDER", "LO_SUPPKEY")].query_count == 10
    assert ssb[("LINEORDER", "LO_CUSTKEY")].query_count == 7
    assert ssb[("LINEORDER", "LO_PARTKEY")].query_count == 6

    assert ("LINEORDER", "LO_ORDERKEY") not in ssb
    required = {(candidate.table, candidate.column) for candidate in profile.required_candidates("ssb")}
    assert ("LINEORDER", "LO_ORDERDATE") in required
    assert ("LINEORDER", "LO_SUPPKEY") in required
    assert ("LINEORDER", "LO_PARTKEY") in required
    assert ("DATE", "D_DATEKEY") in required
    assert ("SUPPLIER", "S_SUPPKEY") in required
