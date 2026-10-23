import re

import pytest

from benchbox.core.tpcds_obt.catalog.loader import load_catalog
from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS, TPCDSOBTQueryManager
from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME, get_obt_columns

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def test_get_query_returns_formatted_sql() -> None:
    manager = TPCDSOBTQueryManager()
    sql = manager.get_query(3)

    assert OBT_TABLE_NAME in sql
    assert "channel = 'store'" in sql


def test_query_two_filters_multiple_channels() -> None:
    manager = TPCDSOBTQueryManager()
    sql = manager.get_query(2)

    assert re.search(
        r"FROM\s+tpcds_sales_returns_obt\s+AS\s+obt\s+WHERE\s+channel\s*=\s*'web'\s+UNION\s+ALL",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"UNION\s+ALL\s+SELECT\s+.*FROM\s+tpcds_sales_returns_obt\s+AS\s+obt\s+WHERE\s+channel\s*=\s*'catalog'",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(r"channel\s+IN\s*\(\s*'catalog'\s*,\s*'web'\s*\)", sql, flags=re.IGNORECASE)


def test_get_query_by_int_uses_index_order() -> None:
    manager = TPCDSOBTQueryManager()
    assert manager.get_query(3) == manager.get_query("3")


def test_invalid_query_id_raises() -> None:
    manager = TPCDSOBTQueryManager()

    with pytest.raises(ValueError):
        manager.get_query("missing")

    with pytest.raises(ValueError):
        manager.get_query(150)
    with pytest.raises(ValueError):
        manager.get_query(21)


def test_queries_reference_only_obt_table() -> None:
    manager = TPCDSOBTQueryManager()
    queries = manager.get_queries()

    assert len(queries) == len(CONVERTIBLE_QUERY_IDS)
    for sql in queries.values():
        assert not re.search(r"\bFROM\s+store_sales\b", sql, re.IGNORECASE)
        assert not re.search(r"\bFROM\s+web_sales\b", sql, re.IGNORECASE)
        assert not re.search(r"\bFROM\s+catalog_sales\b", sql, re.IGNORECASE)
        assert not re.search(r"\bJOIN\s+store_sales\b", sql, re.IGNORECASE)
        assert not re.search(r"\bJOIN\s+web_sales\b", sql, re.IGNORECASE)
        assert not re.search(r"\bJOIN\s+catalog_sales\b", sql, re.IGNORECASE)
        assert OBT_TABLE_NAME in sql


def _extract_column_references(sql: str, valid_columns: set[str]) -> set[str]:
    sql_lower = sql.lower()

    sql_no_aliases = re.sub(r"\bAS\s+[a-z_][a-z0-9_]*", "AS _alias_", sql_lower, flags=re.IGNORECASE)

    identifiers = set(re.findall(r"\b([a-z][a-z0-9_]*)\b", sql_no_aliases))

    dimension_column_prefixes = (
        "sold_date_d_",
        "sold_date_t_",
        "sold_time_t_",
        "ship_date_d_",
        "return_date_d_",
        "return_time_t_",
        "item_i_",
        "promo_p_",
        "reason_r_",
        "store_s_",
        "web_site_web_",
        "web_page_wp_",
        "call_center_cc_",
        "catalog_page_cp_",
        "ship_mode_sm_",
        "warehouse_w_",
        "bill_customer_c_",
        "ship_customer_c_",
        "returning_customer_c_",
        "refunded_customer_c_",
        "bill_cdemo_cd_",
        "ship_cdemo_cd_",
        "returning_cdemo_cd_",
        "refunded_cdemo_cd_",
        "bill_hdemo_hd_",
        "ship_hdemo_hd_",
        "returning_hdemo_hd_",
        "refunded_hdemo_hd_",
        "bill_addr_ca_",
        "ship_addr_ca_",
        "returning_addr_ca_",
        "refunded_addr_ca_",
    )

    column_refs = set()
    for ident in identifiers:
        if ident in valid_columns or any(ident.startswith(prefix) for prefix in dimension_column_prefixes):
            column_refs.add(ident)

    return column_refs


def test_all_query_column_references_exist_in_schema() -> None:

    columns = get_obt_columns("full")
    column_names = {col.name for col in columns}

    manager = TPCDSOBTQueryManager()
    queries = manager.get_queries()

    missing_columns: dict[str, list[str]] = {}
    for query_id, sql in queries.items():
        refs = _extract_column_references(sql, column_names)
        missing = [ref for ref in refs if ref not in column_names]
        if missing:
            missing_columns[query_id] = missing

    assert not missing_columns, f"Queries reference non-existent columns: {missing_columns}"


def test_queries_parse_in_duckdb() -> None:

    duckdb = pytest.importorskip("duckdb")

    manager = TPCDSOBTQueryManager()
    queries = manager.get_queries()

    conn = duckdb.connect(":memory:")
    columns = get_obt_columns("full")

    col_defs = ", ".join(f"{col.name} {col.sql_type()}" for col in columns)
    conn.execute(f"CREATE TABLE {OBT_TABLE_NAME} ({col_defs})")

    conn.execute("""
        CREATE TABLE customer (
            c_customer_sk INTEGER PRIMARY KEY,
            c_customer_id VARCHAR(16),
            c_current_cdemo_sk INTEGER,
            c_current_hdemo_sk INTEGER,
            c_current_addr_sk INTEGER,
            c_first_shipto_date_sk INTEGER,
            c_first_sales_date_sk INTEGER,
            c_salutation VARCHAR(10),
            c_first_name VARCHAR(20),
            c_last_name VARCHAR(30),
            c_preferred_cust_flag CHAR(1),
            c_birth_day INTEGER,
            c_birth_month INTEGER,
            c_birth_year INTEGER,
            c_birth_country VARCHAR(20),
            c_login VARCHAR(13),
            c_email_address VARCHAR(50)
        )
    """)
    conn.execute("""
        CREATE TABLE customer_address (
            ca_address_sk INTEGER PRIMARY KEY,
            ca_address_id VARCHAR(16),
            ca_street_number VARCHAR(10),
            ca_street_name VARCHAR(60),
            ca_street_type VARCHAR(15),
            ca_suite_number VARCHAR(10),
            ca_city VARCHAR(60),
            ca_county VARCHAR(30),
            ca_state CHAR(2),
            ca_zip VARCHAR(10),
            ca_country VARCHAR(20),
            ca_gmt_offset DECIMAL(5,2),
            ca_location_type VARCHAR(20)
        )
    """)
    conn.execute("""
        CREATE TABLE household_demographics (
            hd_demo_sk INTEGER PRIMARY KEY,
            hd_income_band_sk INTEGER,
            hd_buy_potential VARCHAR(15),
            hd_dep_count INTEGER,
            hd_vehicle_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE income_band (
            ib_income_band_sk INTEGER PRIMARY KEY,
            ib_lower_bound INTEGER,
            ib_upper_bound INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE customer_demographics (
            cd_demo_sk INTEGER PRIMARY KEY,
            cd_gender CHAR(1),
            cd_marital_status CHAR(1),
            cd_education_status VARCHAR(20),
            cd_purchase_estimate INTEGER,
            cd_credit_rating VARCHAR(10),
            cd_dep_count INTEGER,
            cd_dep_employed_count INTEGER,
            cd_dep_college_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE date_dim (
            d_date_sk INTEGER PRIMARY KEY,
            d_date_id VARCHAR(16),
            d_date DATE,
            d_month_seq INTEGER,
            d_week_seq INTEGER,
            d_quarter_seq INTEGER,
            d_year INTEGER,
            d_dow INTEGER,
            d_moy INTEGER,
            d_dom INTEGER,
            d_qoy INTEGER,
            d_fy_year INTEGER,
            d_fy_quarter_seq INTEGER,
            d_fy_week_seq INTEGER,
            d_day_name VARCHAR(9),
            d_quarter_name VARCHAR(6),
            d_holiday CHAR(1),
            d_weekend CHAR(1),
            d_following_holiday CHAR(1),
            d_first_dom INTEGER,
            d_last_dom INTEGER,
            d_same_day_ly INTEGER,
            d_same_day_lq INTEGER,
            d_current_day CHAR(1),
            d_current_week CHAR(1),
            d_current_month CHAR(1),
            d_current_quarter CHAR(1),
            d_current_year CHAR(1)
        )
    """)

    parse_errors: dict[str, str] = {}
    for query_id, sql in queries.items():
        try:
            conn.execute(f"EXPLAIN {sql}")
        except Exception as e:
            parse_errors[query_id] = str(e)

    conn.close()

    assert not parse_errors, f"Queries failed to parse: {parse_errors}"


def test_q8_has_diverse_zip_codes() -> None:
    numbers = re.findall(r"'(\d{5})'", TPCDSOBTQueryManager().get_query(8))
    assert len(set(numbers)) > 100


def test_rendering_is_deterministic_for_a_seed() -> None:
    assert TPCDSOBTQueryManager().get_query(8, seed=7) == TPCDSOBTQueryManager().get_query(8, seed=7)
    assert TPCDSOBTQueryManager().get_query(8, seed=7) != TPCDSOBTQueryManager().get_query(8, seed=8)


def test_every_parameter_renders_the_dsqgen_draw() -> None:
    manager = TPCDSOBTQueryManager()
    for query_id, query in load_catalog().items():
        sql = manager.get_query(query_id)
        assert not re.search(r"\[[A-Z_]+(?:\.\d+)?\]", sql), query_id
        drawn = manager.drawn_parameters(query_id)
        for name, indices in query.parameters.items():
            if name in query.identifier_parameters:
                continue
            for index in indices:
                assert drawn[f"{name}.{index:02d}"] in sql, (query_id, name, index)


def test_q93_binds_the_drawn_reason() -> None:
    manager = TPCDSOBTQueryManager()
    reason = manager.drawn_parameters(93)["REASON.01"]
    assert reason != "0"
    assert f"reason_r_reason_desc = '{reason}'" in manager.get_query(93)


def test_explicit_parameters_override_the_draw() -> None:
    sql = TPCDSOBTQueryManager().get_query(93, {"REASON": "Parts missing"})
    assert "reason_r_reason_desc = 'Parts missing'" in sql


@pytest.mark.parametrize("key", ["REASON.1", "REASON.01"])
def test_indexed_overrides_accept_template_and_log_spellings(key: str) -> None:
    sql = TPCDSOBTQueryManager().get_query(93, {key: "Parts missing"})
    assert "reason_r_reason_desc = 'Parts missing'" in sql


@pytest.mark.parametrize("query_id", [1, 30, 81, 83])
def test_return_queries_use_return_date_role(query_id: int) -> None:
    sql = TPCDSOBTQueryManager().get_query(query_id)
    assert "return_date_d_date_sk" in sql
    assert "returned_date_sk = obt.sold_date_d_date_sk" not in sql


@pytest.mark.parametrize("query_id", [6, 8, 15, 19, 35, 45, 69])
def test_customer_queries_drop_current_pointer_equalities(query_id: int) -> None:
    sql = TPCDSOBTQueryManager().get_query(query_id)
    assert "c_current_addr_sk =" not in sql
    assert "= obt.bill_customer_c_current_addr_sk" not in sql
    assert "= bill_customer_c_current_addr_sk" not in sql


@pytest.mark.parametrize("query_id", [10, 35, 69])
def test_cross_channel_exists_correlates_on_bill_customer(query_id: int) -> None:
    sql = TPCDSOBTQueryManager().get_query(query_id)
    assert "ex_obt.bill_customer_c_customer_sk = obt.bill_customer_c_customer_sk" in sql


@pytest.mark.parametrize("query_id", [30, 81])
def test_return_state_uses_returning_address_role(query_id: int) -> None:
    assert "returning_addr_ca_state" in TPCDSOBTQueryManager().get_query(query_id)


def test_q44_resolves_product_names_from_ranked_items() -> None:
    sql = TPCDSOBTQueryManager().get_query(44)
    assert "(SELECT MAX(best_obt.item_i_product_name)" in sql
    assert "(SELECT MAX(worst_obt.item_i_product_name)" in sql
    assert "best_obt.item_sk = asceding.item_sk" in sql
    assert "worst_obt.item_sk = descending.item_sk" in sql


def test_q54_revenue_joins_my_customers() -> None:
    sql = TPCDSOBTQueryManager().get_query(54)
    assert "obt.bill_customer_c_customer_sk = my_customers.bill_customer_c_customer_sk" in sql


def test_q58_averages_week_lookup() -> None:
    assert "MAX(" in TPCDSOBTQueryManager().get_query(58)


def test_q78_excludes_returned_sales() -> None:
    assert "has_return = 'N'" in TPCDSOBTQueryManager().get_query(78)


def test_q91_uses_returning_demographics() -> None:
    sql = TPCDSOBTQueryManager().get_query(91)
    assert "returning_cdemo_cd_marital_status" in sql
    assert "returning_customer_c_customer_sk = obt.returning_customer_c_customer_sk" in sql


@pytest.mark.parametrize("query_id", [16, 94])
def test_multi_warehouse_exists_is_correlated(query_id: int) -> None:
    sql = TPCDSOBTQueryManager().get_query(query_id)
    assert "other_obt.sale_id = obt.sale_id" in sql
    assert "other_obt.warehouse_sk <> obt.warehouse_sk" in sql
    assert "ret_obt.sale_id = obt.sale_id" in sql
    assert "ret_obt.has_return = 'Y'" in sql


def test_q95_multi_warehouse_and_return_sets_are_correlated() -> None:
    sql = TPCDSOBTQueryManager().get_query(95)
    assert "other_obt.warehouse_sk" in sql
    assert "ret_obt.sale_id = obt.sale_id" in sql


@pytest.mark.parametrize("query_id", [18, 27, 36])
def test_grouped_queries_can_legitimately_return_one_group(query_id: int) -> None:
    sql = TPCDSOBTQueryManager().get_query(query_id)
    assert "GROUP BY" in sql or "GROUPING" in sql


@pytest.mark.parametrize("query_id", [16, 28, 88, 90, 94, 95, 96])
def test_global_aggregates_legitimately_return_one_row(query_id: int) -> None:
    sql = TPCDSOBTQueryManager().get_query(query_id)
    assert "COUNT(" in sql or "SUM(" in sql


def _obt_connection():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    columns = get_obt_columns("full")
    col_defs = ", ".join(f"{col.name} {col.sql_type()}" for col in columns)
    conn.execute(f"CREATE TABLE {OBT_TABLE_NAME} ({col_defs})")
    return conn


def _insert_obt_row(conn, **values) -> None:
    names = list(values)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO {OBT_TABLE_NAME} ({', '.join(names)}) VALUES ({placeholders})",
        [values[name] for name in names],
    )


def test_q1_returns_customer_above_store_average() -> None:
    conn = _obt_connection()
    for customer_sk, fee in [(1, 100.0), (2, 10.0)]:
        _insert_obt_row(
            conn,
            channel="store",
            returning_customer_c_customer_sk=customer_sk,
            bill_customer_c_customer_sk=customer_sk,
            bill_customer_c_customer_id=f"C{customer_sk}",
            store_sk=1,
            store_s_store_sk=1,
            store_s_state="TN",
            return_fee=fee,
            returned_date_sk=10,
            return_date_d_date_sk=10,
            return_date_d_year=2000,
        )
    rows = conn.execute(TPCDSOBTQueryManager().get_query(1)).fetchall()
    conn.close()
    assert rows == [("C1",)]


def test_q16_counts_multi_warehouse_order_without_return() -> None:
    conn = _obt_connection()
    for warehouse_sk in [1, 2]:
        _insert_obt_row(
            conn,
            channel="catalog",
            sale_id=7,
            warehouse_sk=warehouse_sk,
            has_return="N",
            ship_date_sk=10,
            ship_date_d_date_sk=10,
            ship_date_d_date="1999-02-10",
            ship_addr_sk=1,
            ship_addr_ca_address_sk=1,
            ship_addr_ca_state="IL",
            call_center_sk=1,
            call_center_cc_call_center_sk=1,
            call_center_cc_county="Williamson County",
            ext_ship_cost=3.0,
            net_profit=4.0,
        )
    rows = conn.execute(TPCDSOBTQueryManager().get_query(16)).fetchall()
    conn.close()
    assert rows[0][0] == 1


def test_q44_returns_ranked_items() -> None:
    conn = _obt_connection()
    for item_sk, profit in [(1, 100.0), (2, 1.0)]:
        _insert_obt_row(
            conn,
            channel="store",
            store_sk=2,
            item_sk=item_sk,
            item_i_item_sk=item_sk,
            item_i_product_name=f"P{item_sk}",
            net_profit=profit,
        )
    _insert_obt_row(conn, channel="store", store_sk=2, item_sk=1, net_profit=50.0)
    rows = conn.execute(TPCDSOBTQueryManager().get_query(44)).fetchall()
    conn.close()
    assert rows


def test_q78_returns_store_sales_with_other_channel_sales() -> None:
    conn = _obt_connection()
    for channel in ["store", "web"]:
        _insert_obt_row(
            conn,
            channel=channel,
            has_return="N",
            sold_date_sk=10,
            sold_date_d_date_sk=10,
            sold_date_d_year=2000,
            item_sk=1,
            bill_customer_sk=1,
            ship_customer_sk=1,
            quantity=2,
            wholesale_cost=3.0,
            sales_price=5.0,
        )
    rows = conn.execute(TPCDSOBTQueryManager().get_query(78)).fetchall()
    conn.close()
    assert rows
