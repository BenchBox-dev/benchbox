from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.query_catalog import get_sql_render, list_query_ids, query_source_path
from benchbox.utils.tpc_compilation import get_tpc_templates_dir

ROOT = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.unit, pytest.mark.fast]

CANONICAL_TPCH_EULA = ROOT / "_sources" / "tpc-h" / "EULA.txt"
CANONICAL_TPCDS_EULA = ROOT / "_sources" / "tpc-ds" / "EULA.txt"
SOURCE_TPCDS_EULA = ROOT / "_sources" / "tpc-ds" / "query_templates" / "EULA.txt"


def _runtime_tpch_eula() -> Path:
    return get_tpc_templates_dir("tpc-h") / "queries" / "EULA.txt"


def _runtime_tpcds_eula() -> Path:
    return get_tpc_templates_dir("tpc-ds") / "query_templates" / "EULA.txt"


@pytest.mark.parametrize(
    ("canonical", "adjacent"),
    [
        pytest.param(CANONICAL_TPCH_EULA, _runtime_tpch_eula, id="tpch-runtime"),
        pytest.param(CANONICAL_TPCDS_EULA, _runtime_tpcds_eula, id="tpcds-runtime"),
        pytest.param(CANONICAL_TPCDS_EULA, lambda: SOURCE_TPCDS_EULA, id="tpcds-source-tree"),
    ],
)
def test_adjacent_tpc_eulas_match_canonical_notices(canonical: Path, adjacent) -> None:
    adjacent_path = adjacent()
    assert canonical.is_file(), f"canonical EULA missing: {canonical}"
    assert adjacent_path.is_file(), f"adjacent EULA missing: {adjacent_path}"
    assert adjacent_path.read_bytes() == canonical.read_bytes(), (
        f"adjacent EULA drifted from canonical: {adjacent_path}"
    )


def test_runtime_tpcds_templates_dir_is_the_shipped_bundle() -> None:
    assert get_tpc_templates_dir("tpc-ds") == ROOT / "benchbox" / "_binaries" / "tpc-ds" / "templates"


def test_query_catalog_resolves_templates_beside_notices() -> None:
    assert len(list_query_ids("tpch")) == 22
    assert query_source_path("tpch", "1") == "benchbox/_binaries/tpc-h/templates/queries/1.sql"
    assert "select" in get_sql_render("tpch", "1").sql.lower()

    assert len(list_query_ids("tpcds")) == 99
    assert query_source_path("tpcds", "1") == "_sources/tpc-ds/query_templates/query1.tpl"
    assert "select" in get_sql_render("tpcds", "1").sql.lower()
