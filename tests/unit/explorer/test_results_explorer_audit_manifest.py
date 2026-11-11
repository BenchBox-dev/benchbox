from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
HIERARCHY_AUDIT = REPO_ROOT / "_project" / "audits" / "results-explorer-hierarchy-usability-data-audit-20260507.md"


def test_pr246_hierarchy_audit_retains_duckdb_aws_compare_evidence() -> None:
    audit = HIERARCHY_AUDIT.read_text(encoding="utf-8")

    assert (
        "/results/compare?ids=tpch-duckdb-sf0.01-20260403-b6d2e142,tpch-fixture-aws-sql-sf0.01-20260403-e73da6ce"
    ) in audit
    assert "Compare overweights the Comparability Receipt before the decision summary and charts" in audit
    assert "compare-duckdb-aws-390.png" in audit
    assert "compare-duckdb-aws-1280.png" in audit
    assert "Raw captures were produced during the audit but are no longer retained in git" in audit


