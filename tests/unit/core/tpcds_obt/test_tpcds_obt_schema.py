import pytest

from benchbox.core.tpcds_obt import schema

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_table_name_and_defaults() -> None:
    table = schema.TPCDS_OBT_TABLE
    assert table.name == schema.OBT_TABLE_NAME
    assert schema.DEFAULT_MODE == "full"
    column_names = {col.name for col in table.columns}
    assert {"channel", "sale_id", "item_sk"}.issubset(column_names)


def test_full_mode_width_and_uniqueness() -> None:
    columns = schema.get_obt_columns()
    names = [col.name for col in columns]
    assert len(columns) == 715
    assert len(names) == len(set(names))
    assert {"channel", "sale_id", "has_return", "return_amount"}.issubset(set(names))


def test_minimal_mode_is_subset_without_returning_roles() -> None:
    full_names = {col.name for col in schema.get_obt_columns()}
    minimal_columns = schema.get_obt_columns("minimal")
    minimal_names = {col.name for col in minimal_columns}

    assert len(minimal_columns) < len(full_names)
    assert minimal_names.issubset(full_names)
    assert "bill_customer_c_customer_id" in minimal_names
    assert {"returning_customer_c_customer_sk", "refunded_customer_c_customer_sk"}.isdisjoint(minimal_names)


def test_create_table_sql_contains_key_columns() -> None:
    ddl = schema.TPCDS_OBT_TABLE.get_create_table_sql()
    assert ddl.startswith("CREATE TABLE tpcds_sales_returns_obt")
    assert "channel VARCHAR(10) NOT NULL" in ddl
    assert "return_amount DECIMAL" in ddl
    assert "has_return CHAR(1) NOT NULL" in ddl


def test_column_lineage_exposes_sources() -> None:
    lineage = schema.get_column_lineage()
    assert lineage["channel"]["source_table"] is None
    assert lineage["return_amount"]["source_table"] == "store_returns|web_returns|catalog_returns"
    assert lineage["promo_sk"]["source_table"] == "store_sales|web_sales|catalog_sales"
    assert lineage["bill_customer_c_customer_id"]["source_table"] == "customer"
    assert lineage["bill_customer_c_customer_id"]["role"] == "bill_customer"


def test_invalid_mode_raises() -> None:
    with pytest.raises(ValueError):
        schema.get_obt_columns("unsupported")
