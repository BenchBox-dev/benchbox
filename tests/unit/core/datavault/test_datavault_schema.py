# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.datavault.schema import (
    HUBS,
    LINKS,
    LOADING_ORDER,
    SATELLITES,
    TABLES,
    TABLES_BY_NAME,
    Column,
    DataType,
    get_create_all_tables_sql,
    get_table,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataVaultSchema:
    def test_table_collections_not_empty(self):

        assert len(HUBS) > 0, "Should have at least one Hub table"
        assert len(LINKS) > 0, "Should have at least one Link table"
        assert len(SATELLITES) > 0, "Should have at least one Satellite table"
        assert len(TABLES) > 0, "Should have at least one table"

    def test_tables_sum_equals_total(self):

        combined = set(HUBS) | set(LINKS) | set(SATELLITES)
        assert set(TABLES) == combined, "TABLES should equal HUBS + LINKS + SATELLITES"

    def test_hub_tables_have_required_columns(self):
        required_columns = {"load_dts", "record_source"}

        for hub in HUBS:
            column_names = {col.name for col in hub.columns}

            hk_columns = [name for name in column_names if name.startswith("hk_")]
            assert len(hk_columns) >= 1, f"{hub.name} must have a hash key column"

            for req_col in required_columns:
                assert req_col in column_names, f"{hub.name} missing required column: {req_col}"

            assert hub.table_type == "hub", f"{hub.name} should have table_type='hub'"

    def test_link_tables_have_required_columns(self):
        required_columns = {"load_dts", "record_source"}

        for link in LINKS:
            column_names = {col.name for col in link.columns}

            hk_columns = [name for name in column_names if name.startswith("hk_")]
            assert len(hk_columns) >= 2, f"{link.name} must have link HK and at least one FK HK"

            for req_col in required_columns:
                assert req_col in column_names, f"{link.name} missing required column: {req_col}"

            assert link.table_type == "link", f"{link.name} should have table_type='link'"

    def test_satellite_tables_have_required_columns(self):
        required_columns = {"load_dts", "load_end_dts", "record_source", "hashdiff"}

        for sat in SATELLITES:
            column_names = {col.name for col in sat.columns}

            hk_columns = [name for name in column_names if name.startswith("hk_")]
            assert len(hk_columns) >= 1, f"{sat.name} must have a hash key column"

            for req_col in required_columns:
                assert req_col in column_names, f"{sat.name} missing required column: {req_col}"

            assert sat.table_type == "satellite", f"{sat.name} should have table_type='satellite'"

    def test_link_tables_reference_valid_hubs(self):
        hub_names = {hub.name for hub in HUBS}

        for link in LINKS:
            fk_refs = link.get_foreign_keys()
            for col_name, (ref_table, _ref_col) in fk_refs.items():
                assert ref_table in hub_names or ref_table in TABLES_BY_NAME, (
                    f"{link.name}.{col_name} references non-existent table: {ref_table}"
                )

    def test_get_table_case_insensitive(self):
        hub = get_table("hub_customer")
        assert hub.name == "hub_customer"

        hub_upper = get_table("HUB_CUSTOMER")
        assert hub_upper.name == "hub_customer"

    def test_get_table_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            get_table("nonexistent_table")

    def test_loading_order_complete(self):
        assert len(LOADING_ORDER) == len(TABLES)
        assert set(LOADING_ORDER) == set(TABLES_BY_NAME.keys())

    def test_loading_order_hubs_first(self):
        hub_names = {hub.name for hub in HUBS}
        link_names = {link.name for link in LINKS}

        first_link_idx = None
        for i, name in enumerate(LOADING_ORDER):
            if name in link_names:
                first_link_idx = i
                break

        if first_link_idx is not None:
            for i in range(first_link_idx):
                assert LOADING_ORDER[i] in hub_names or LOADING_ORDER[i] in link_names

    def test_loading_order_links_before_satellites(self):
        sat_names = {sat.name for sat in SATELLITES}

        first_sat_idx = None
        for i, name in enumerate(LOADING_ORDER):
            if name in sat_names:
                first_sat_idx = i
                break

        if first_sat_idx is not None:
            for i in range(first_sat_idx):
                assert LOADING_ORDER[i] not in sat_names


class TestDataVaultDDL:
    def test_get_create_all_tables_sql(self):
        sql = get_create_all_tables_sql()
        assert sql is not None
        assert len(sql) > 0
        assert sql.count("CREATE TABLE") == 21

    def test_create_table_sql_with_pk(self):
        hub = TABLES_BY_NAME["hub_customer"]
        sql = hub.get_create_table_sql(enable_primary_keys=True, enable_foreign_keys=False)
        assert "PRIMARY KEY" in sql

    def test_create_table_sql_without_pk(self):
        hub = TABLES_BY_NAME["hub_customer"]
        sql = hub.get_create_table_sql(enable_primary_keys=False, enable_foreign_keys=False)
        assert "PRIMARY KEY" not in sql

    def test_create_table_sql_with_fk(self):
        link = TABLES_BY_NAME["link_customer_nation"]
        sql = link.get_create_table_sql(enable_primary_keys=True, enable_foreign_keys=True)
        assert "FOREIGN KEY" in sql

    def test_create_table_sql_without_fk(self):
        link = TABLES_BY_NAME["link_customer_nation"]
        sql = link.get_create_table_sql(enable_primary_keys=True, enable_foreign_keys=False)
        assert "FOREIGN KEY" not in sql


class TestDataTypeHandling:
    def test_hashkey_type(self):
        col = Column("test_hk", DataType.HASHKEY)
        assert col.get_sql_type() == "VARCHAR(64)"

    def test_timestamp_type(self):
        col = Column("test_ts", DataType.TIMESTAMP)
        assert col.get_sql_type() == "TIMESTAMP"

    def test_varchar_with_size(self):
        col = Column("test_vc", DataType.VARCHAR, size=100)
        assert col.get_sql_type() == "VARCHAR(100)"

    def test_decimal_type(self):
        col = Column("test_dec", DataType.DECIMAL)
        assert "DECIMAL" in col.get_sql_type()

    def test_hashkey_column_width_in_ddl(self):
        hub = TABLES_BY_NAME["hub_customer"]
        ddl = hub.get_create_table_sql(enable_primary_keys=False, enable_foreign_keys=False)
        assert "VARCHAR(64)" in ddl
        assert "VARCHAR(32)" not in ddl


