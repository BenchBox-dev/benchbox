from __future__ import annotations

import pytest

from benchbox.core.dataframe.schema_utils import (
    column_name,
    column_sql_type,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_DDL_CASES = [
    ("id INTEGER", "id", "INTEGER"),
    ("note TEXT", "note", "TEXT"),
    ("name VARCHAR", "name", "VARCHAR"),
    ("imdb_index VARCHAR(12)", "imdb_index", "VARCHAR(12)"),
    ("country_code VARCHAR(255)", "country_code", "VARCHAR(255)"),
    ("price DECIMAL(10, 2)", "price", "DECIMAL(10, 2)"),
    ("price2 DECIMAL(10,2)", "price2", "DECIMAL(10,2)"),
    ("amount NUMERIC(18, 4)", "amount", "NUMERIC(18, 4)"),
    ("val DOUBLE PRECISION", "val", "DOUBLE PRECISION"),
    ("ts TIMESTAMP WITH TIME ZONE", "ts", "TIMESTAMP WITH TIME ZONE"),
    ("ts2 TIMESTAMP WITHOUT TIME ZONE", "ts2", "TIMESTAMP WITHOUT TIME ZONE"),
    ("cv CHARACTER VARYING", "cv", "CHARACTER VARYING"),
    ("cv2 CHARACTER VARYING(64)", "cv2", "CHARACTER VARYING(64)"),
    ('"odd name" INTEGER', "odd name", "INTEGER"),
    ("[odd name] INT", "odd name", "INT"),
    ("`odd name` INT", "odd name", "INT"),
    ('"id" BIGINT', "id", "BIGINT"),
    ('"a""b" INT', 'a"b', "INT"),
    ("[a]]b] INT", "a]b", "INT"),
    ("id INTEGER PRIMARY KEY", "id", "INTEGER"),
    ("title TEXT NOT NULL", "title", "TEXT"),
    ("kind VARCHAR(15) NOT NULL", "kind", "VARCHAR(15)"),
    ("amount NUMERIC(18, 4) DEFAULT 0", "amount", "NUMERIC(18, 4)"),
    ("flag BOOLEAN NOT NULL DEFAULT TRUE", "flag", "BOOLEAN"),
    ("u VARCHAR(8) UNIQUE", "u", "VARCHAR(8)"),
    ("qty INT REFERENCES other(id)", "qty", "INT"),
    ("ts TIMESTAMP WITH TIME ZONE NOT NULL", "ts", "TIMESTAMP WITH TIME ZONE"),
    ("val DOUBLE PRECISION DEFAULT 0.0", "val", "DOUBLE PRECISION"),
]


@pytest.mark.parametrize("ddl,expected_name,expected_type", _DDL_CASES)
def test_column_name_from_ddl(ddl: str, expected_name: str, expected_type: str) -> None:
    assert column_name(ddl) == expected_name


@pytest.mark.parametrize("ddl,expected_name,expected_type", _DDL_CASES)
def test_column_sql_type_from_ddl(ddl: str, expected_name: str, expected_type: str) -> None:
    assert column_sql_type(ddl) == expected_type


def test_empty_string_has_no_name() -> None:
    assert column_name("") is None
    assert column_name("   ") is None


def test_empty_string_falls_back_to_default_type() -> None:
    assert column_sql_type("") == "VARCHAR"
    assert column_sql_type("", default="TEXT") == "TEXT"


def test_name_only_falls_back_to_default_type() -> None:

    assert column_name("just_name") == "just_name"
    assert column_sql_type("just_name") == "VARCHAR"
    assert column_sql_type("just_name", default="TEXT") == "TEXT"


def test_dict_column_unaffected() -> None:
    col = {"name": "amount", "type": "DECIMAL(10, 2)"}
    assert column_name(col) == "amount"
    assert column_sql_type(col) == "DECIMAL(10, 2)"


def test_object_column_unaffected() -> None:
    class _Col:
        name = "id"
        data_type = "BIGINT"

    assert column_name(_Col()) == "id"
    assert column_sql_type(_Col()) == "BIGINT"


def test_lowercase_constraints_are_stripped() -> None:

    assert column_sql_type("id integer primary key") == "integer"
    assert column_sql_type("title text not null") == "text"


def test_real_joinorder_synthetic_shapes() -> None:

    shapes = {
        "id INTEGER PRIMARY KEY": ("id", "INTEGER"),
        "title TEXT NOT NULL": ("title", "TEXT"),
        "imdb_index VARCHAR(12)": ("imdb_index", "VARCHAR(12)"),
        "production_year INTEGER": ("production_year", "INTEGER"),
        "md5sum VARCHAR(32)": ("md5sum", "VARCHAR(32)"),
    }
    for ddl, (name, sql_type) in shapes.items():
        assert column_name(ddl) == name
        assert column_sql_type(ddl) == sql_type


