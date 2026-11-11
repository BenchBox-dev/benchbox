import pytest

from benchbox.core.tpcds.schema import Column, DataType, Table

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_column_get_sql_type_handles_sizes_and_defaults():
    assert Column("c1", DataType.VARCHAR, size=10).get_sql_type() == "VARCHAR(10)"
    assert Column("c2", DataType.CHAR, size=3).get_sql_type() == "CHAR(3)"

    assert Column("c3", DataType.DECIMAL).get_sql_type() == "DECIMAL(15,2)"

    assert Column("c4", DataType.INTEGER).get_sql_type() == "INTEGER"


def make_sample_table():
    cols = [
        Column("ID", DataType.INTEGER, primary_key=True),
        Column("NAME", DataType.VARCHAR, size=20, nullable=True),
        Column("REF", DataType.INTEGER, foreign_key=("T2", "ID")),
    ]
    return Table("T1", cols)


def test_table_primary_and_foreign_key_helpers():
    t = make_sample_table()
    assert t.get_primary_key() == ["ID"]
    fks = t.get_foreign_keys()
    assert fks == {"REF": ("T2", "ID")}


def test_create_table_sql_with_constraints():
    t = make_sample_table()
    sql = t.get_create_table_sql(enable_primary_keys=True, enable_foreign_keys=True)

    assert "CREATE TABLE T1" in sql
    assert "ID INTEGER NOT NULL" in sql
    assert "NAME VARCHAR(20)" in sql and "NOT NULL" not in sql.split("NAME VARCHAR(20)")[-1][:10]
    assert "REF INTEGER NOT NULL" in sql
    assert "PRIMARY KEY (ID)" in sql
    assert "FOREIGN KEY (REF) REFERENCES T2(ID)" in sql


def test_create_table_sql_disable_pk_or_fk():
    t = make_sample_table()
    sql_no_pk = t.get_create_table_sql(enable_primary_keys=False, enable_foreign_keys=True)
    assert "PRIMARY KEY" not in sql_no_pk
    assert "FOREIGN KEY (REF) REFERENCES T2(ID)" in sql_no_pk

    sql_no_fk = t.get_create_table_sql(enable_primary_keys=True, enable_foreign_keys=False)
    assert "PRIMARY KEY (ID)" in sql_no_fk
    assert "FOREIGN KEY" not in sql_no_fk


