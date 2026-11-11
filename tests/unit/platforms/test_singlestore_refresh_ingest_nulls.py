from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.platforms.singlestore import SingleStoreAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

COLUMNS = [("ss_item_sk", "INTEGER"), ("ss_net_paid", "DECIMAL(7,2)")]


def _ingest(null_marker: str | None) -> str:
    cursor = Mock()
    cursor.rowcount = 3
    raw = Mock()
    raw.cursor.return_value = cursor
    SingleStoreAdapter.__new__(SingleStoreAdapter).ingest_refresh_file(
        Mock(connection=raw), "store_sales", COLUMNS, Path("/data/s.dat"), null_marker=null_marker
    )
    return cursor.execute.call_args.args[0]


def test_null_marker_maps_empty_fields_to_null_and_keeps_null_decimals():
    sql = _ingest("")

    assert "FIELDS TERMINATED BY '|' LINES TERMINATED BY '\\n' NULL DEFINED BY '' (" in sql
    assert "`ss_net_paid` = IF(@ss_net_paid IS NULL OR @ss_net_paid REGEXP" in sql


def test_without_a_null_marker_the_load_statement_is_unchanged():
    sql = _ingest(None)

    assert "NULL DEFINED BY" not in sql
    assert "IS NULL OR" not in sql
    assert "FIELDS TERMINATED BY '|' LINES TERMINATED BY" in sql


