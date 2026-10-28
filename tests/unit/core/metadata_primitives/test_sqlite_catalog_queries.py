from __future__ import annotations

import sqlite3

import pytest

from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark
from benchbox.core.metadata_primitives.catalog.loader import load_metadata_catalog

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_SCHEMA = """
CREATE TABLE lineitem (l_orderkey INTEGER NOT NULL, l_price DECIMAL(15,2), l_comment VARCHAR(44), PRIMARY KEY (l_orderkey));
CREATE TABLE benchbox_fk_parent (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE benchbox_fk_child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES benchbox_fk_parent (id));
CREATE VIEW benchbox_view_x_d1 AS SELECT * FROM benchbox_fk_parent;
"""


@pytest.fixture()
def connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    yield conn
    conn.close()


def _sqlite_queries() -> dict[str, str]:
    return MetadataPrimitivesBenchmark(scale_factor=1.0).get_queries(dialect="sqlite")


def test_every_non_acl_query_has_a_sqlite_variant() -> None:
    catalog = load_metadata_catalog().queries
    missing = [
        qid
        for qid, query in catalog.items()
        if query.category != "acl" and "sqlite" not in (query.variants or {}) and "sqlite" not in (query.skip_on or [])
    ]

    assert missing == []


def test_sqlite_variants_execute_and_report_real_catalog_rows(connection) -> None:
    queries = _sqlite_queries()
    assert queries

    results = {qid: connection.execute(sql).fetchall() for qid, sql in queries.items()}

    assert any(row[0] == "lineitem" and row[1] == "BASE TABLE" for row in results["schema_list_tables"])
    assert results["schema_table_count"] == [(4,)]
    assert any(row[0] == "benchbox_view_x_d1" for row in results["view_hierarchy_list"])
    assert [row[:3] for row in results["constraint_fk_list"]] == [
        ("benchbox_fk_child", "fk_benchbox_fk_child_0", "FOREIGN KEY")
    ]
    assert {row[0] for row in results["constraint_pk_list"]} == {"benchbox_fk_child", "benchbox_fk_parent"}
    assert ("l_price", "DECIMAL(15,2)", 15, 2) in {
        (row[1], row[2], row[3], row[4]) for row in results["column_numeric_columns"]
    }
