from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.platforms.base.ddl_helpers import strip_foreign_keys, strip_primary_keys

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]
_REPO_ROOT = Path(__file__).resolve().parents[4]


class TestStripPrimaryKeys:
    def test_simple_table_level_pk(self):
        sql = "CREATE TABLE t (a INT, b INT, PRIMARY KEY (a))"
        result = strip_primary_keys(sql)
        assert "PRIMARY KEY" not in result.upper()
        assert "a INT" in result

    def test_composite_pk_with_expression(self):

        sql = 'CREATE TABLE t (a TEXT, b INT, PRIMARY KEY ("a", coalesce(b, 0)))'
        result = strip_primary_keys(sql)
        assert "PRIMARY KEY" not in result.upper()
        assert "a TEXT" in result

    @pytest.mark.parametrize("constraint_name", ["pk_t", '"pk_t"', "`pk_t`"])
    def test_named_table_level_pk(self, constraint_name):
        sql = f"CREATE TABLE t (a INT, CONSTRAINT {constraint_name} PRIMARY KEY (a))"
        result = strip_primary_keys(sql)
        result_upper = result.upper()
        assert "PRIMARY KEY" not in result_upper
        assert "CONSTRAINT" not in result_upper
        assert "a INT" in result
        assert ",)" not in result

    def test_composite_pk_multiple_columns(self):
        sql = "CREATE TABLE t (a INT, b INT, c INT, PRIMARY KEY (a, b, c))"
        result = strip_primary_keys(sql)
        assert "PRIMARY KEY" not in result.upper()

    def test_inline_pk(self):
        sql = "CREATE TABLE t (a INT PRIMARY KEY, b TEXT)"
        result = strip_primary_keys(sql)
        assert "PRIMARY KEY" not in result.upper()
        assert "a INT" in result

    def test_named_inline_pk(self):
        sql = "CREATE TABLE t (a INT CONSTRAINT pk_t PRIMARY KEY, b TEXT)"
        result = strip_primary_keys(sql)
        result_upper = result.upper()
        assert "PRIMARY KEY" not in result_upper
        assert "CONSTRAINT" not in result_upper
        assert "a INT" in result
        assert "b TEXT" in result

    def test_no_pk_passthrough(self):
        sql = "CREATE TABLE t (a INT, b TEXT)"
        assert strip_primary_keys(sql) == sql

    def test_trailing_comma_cleaned(self):
        sql = "CREATE TABLE t (a INT, b INT, PRIMARY KEY (a))"
        result = strip_primary_keys(sql)
        assert ",)" not in result

    def test_pk_with_leading_comma_cleaned(self):

        sql = "CREATE TABLE t (a INT, PRIMARY KEY (a))"
        result = strip_primary_keys(sql)
        assert "PRIMARY KEY" not in result.upper()
        assert ",)" not in result

    def test_table_level_pk_first_does_not_leave_leading_comma(self):
        sql = "CREATE TABLE t (PRIMARY KEY (a), a INT)"
        result = strip_primary_keys(sql)
        assert result == "CREATE TABLE t (a INT)"

    def test_primary_key_helper_input_constraint_is_in_compat_reference(self):
        reference = (_REPO_ROOT / "benchbox" / "sql_compat" / "README.md").read_text(encoding="utf-8")
        assert "not SQL string-literal parsers" in reference
        assert "Callers must apply them only to schema statements" in reference


class TestStripForeignKeys:
    def test_table_level_fk(self):
        sql = "CREATE TABLE orders (  o_id INT,  c_id INT,  FOREIGN KEY (c_id) REFERENCES customer(c_id))"
        result = strip_foreign_keys(sql)
        assert "FOREIGN KEY" not in result.upper()
        assert "REFERENCES" not in result.upper()

    def test_inline_references(self):
        sql = "CREATE TABLE foo (a INT, customer_id INT REFERENCES customer(c_id))"
        result = strip_foreign_keys(sql)
        assert "REFERENCES" not in result.upper()

    def test_fk_with_on_delete_cascade(self):
        sql = "CREATE TABLE t (a INT, b INT, FOREIGN KEY (b) REFERENCES other(b) ON DELETE CASCADE)"
        result = strip_foreign_keys(sql)
        assert "FOREIGN KEY" not in result.upper()

    def test_no_fk_passthrough(self):
        sql = "CREATE TABLE t (a INT, b TEXT)"
        assert strip_foreign_keys(sql) == sql

    def test_trailing_comma_cleaned(self):
        sql = "CREATE TABLE t (a INT, FOREIGN KEY (a) REFERENCES r(a))"
        result = strip_foreign_keys(sql)
        assert ",)" not in result
