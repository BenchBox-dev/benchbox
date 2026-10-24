import json
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.tpcds.schema.models import DataType
from benchbox.core.tpcds.schema.registry import TABLES_BY_NAME
from benchbox.core.tpcds.schema.tables import (
    CUSTOMER,
    CUSTOMER_ADDRESS,
    CUSTOMER_DEMOGRAPHICS,
    DATE_DIM,
    HOUSEHOLD_DEMOGRAPHICS,
    INCOME_BAND,
    ITEM,
    PROMOTION,
    REASON,
    SHIP_MODE,
    STORE,
    TIME_DIM,
    WAREHOUSE,
    WEB_PAGE,
    WEB_SITE,
)
from benchbox.core.tpcds_obt.etl.transformer import TPCDSOBTTransformer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


def literal_for_type(dtype: DataType) -> str:
    if dtype == DataType.INTEGER:
        return "1"
    if dtype == DataType.DECIMAL:
        return "1.00"
    if dtype == DataType.DATE:
        return "DATE '2000-01-01'"
    if dtype == DataType.TIME:
        return "TIME '00:00:00'"
    if dtype == DataType.TIMESTAMP:
        return "TIMESTAMP '2000-01-01 00:00:00'"
    return "'x'"


def create_table(conn: Any, table_name: str, overrides: dict[str, str] | None = None) -> None:
    table = TABLES_BY_NAME[table_name]
    overrides = overrides or {}
    columns: list[str] = []
    for col in table.columns:
        value = overrides.get(col.name, literal_for_type(col.data_type))
        columns.append(f"CAST({value} AS {col.get_sql_type()}) AS {col.name}")
    conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT {', '.join(columns)}")


def test_transform_runs_end_to_end_with_duckdb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("duckdb")
    import duckdb

    transformer = TPCDSOBTTransformer()

    def loader(conn: Any, _: Path, channels: list[str], mode: str) -> None:

        for table in [
            DATE_DIM,
            TIME_DIM,
            ITEM,
            PROMOTION,
            REASON,
            STORE,
            WEB_SITE,
            WEB_PAGE,
            SHIP_MODE,
            WAREHOUSE,
            CUSTOMER,
            CUSTOMER_DEMOGRAPHICS,
            HOUSEHOLD_DEMOGRAPHICS,
            INCOME_BAND,
            CUSTOMER_ADDRESS,
        ]:
            create_table(conn, table.name)

        if "store" in channels:
            create_table(
                conn,
                "store_sales",
                {
                    "ss_ticket_number": "1",
                    "ss_item_sk": "1",
                    "ss_customer_sk": "1",
                    "ss_cdemo_sk": "1",
                    "ss_hdemo_sk": "1",
                    "ss_addr_sk": "1",
                    "ss_store_sk": "1",
                    "ss_promo_sk": "1",
                    "ss_sold_date_sk": "1",
                    "ss_sold_time_sk": "1",
                },
            )
            create_table(
                conn,
                "store_returns",
                {
                    "sr_ticket_number": "1",
                    "sr_item_sk": "1",
                    "sr_customer_sk": "1",
                    "sr_cdemo_sk": "1",
                    "sr_hdemo_sk": "1",
                    "sr_addr_sk": "1",
                    "sr_store_sk": "1",
                    "sr_reason_sk": "1",
                    "sr_returned_date_sk": "1",
                    "sr_return_time_sk": "1",
                },
            )

        if "web" in channels:
            create_table(
                conn,
                "web_sales",
                {
                    "ws_order_number": "2",
                    "ws_item_sk": "1",
                    "ws_bill_customer_sk": "1",
                    "ws_bill_cdemo_sk": "1",
                    "ws_bill_hdemo_sk": "1",
                    "ws_bill_addr_sk": "1",
                    "ws_ship_customer_sk": "1",
                    "ws_ship_cdemo_sk": "1",
                    "ws_ship_hdemo_sk": "1",
                    "ws_ship_addr_sk": "1",
                    "ws_web_site_sk": "1",
                    "ws_web_page_sk": "1",
                    "ws_ship_mode_sk": "1",
                    "ws_warehouse_sk": "1",
                    "ws_promo_sk": "1",
                    "ws_sold_date_sk": "1",
                    "ws_sold_time_sk": "1",
                    "ws_ship_date_sk": "1",
                },
            )
            create_table(
                conn,
                "web_returns",
                {
                    "wr_order_number": "2",
                    "wr_item_sk": "1",
                    "wr_returning_customer_sk": "1",
                    "wr_returning_cdemo_sk": "1",
                    "wr_returning_hdemo_sk": "1",
                    "wr_returning_addr_sk": "1",
                    "wr_refunded_customer_sk": "1",
                    "wr_refunded_cdemo_sk": "1",
                    "wr_refunded_hdemo_sk": "1",
                    "wr_refunded_addr_sk": "1",
                    "wr_web_page_sk": "1",
                    "wr_reason_sk": "1",
                    "wr_returned_date_sk": "1",
                    "wr_returned_time_sk": "1",
                },
            )

    monkeypatch.setattr(transformer, "_load_source_tables", loader)
    result = transformer.transform(
        tpcds_dir=tmp_path,
        output_dir=tmp_path / "out",
        mode="minimal",
        channels=["store", "web"],
        output_format="dat",
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))

    assert result["table"].exists()
    assert manifest["rows_total"] == 2
    assert manifest["rows_with_returns"] == 2
    assert set(manifest["rows_by_channel"]) == {"store", "web"}
    with open(result["table"], encoding="utf-8") as f:
        lines = [line for line in f.read().splitlines() if line]
    assert len(lines) == 2


def test_all_queries_execute_against_obt_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    duckdb = pytest.importorskip("duckdb")

    from benchbox.core.tpcds_obt.queries import TPCDSOBTQueryManager
    from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME

    transformer = TPCDSOBTTransformer()
    conn = duckdb.connect(":memory:")

    def create_dimensions(conn: Any) -> None:

        conn.execute("""
            CREATE TABLE date_dim AS SELECT
                1 AS d_date_sk,
                '2023-01-15' AS d_date_id,
                DATE '2023-01-15' AS d_date,
                15 AS d_month_seq,
                202301 AS d_week_seq,
                202302 AS d_quarter_seq,
                2023 AS d_year,
                0 AS d_dow,
                15 AS d_moy,
                15 AS d_dom,
                1 AS d_qoy,
                1 AS d_fy_year,
                202302 AS d_fy_quarter_seq,
                15 AS d_fy_week_seq,
                'Sunday' AS d_day_name,
                'Jan' AS d_quarter_name,
                'N' AS d_holiday,
                'N' AS d_weekend,
                'N' AS d_following_holiday,
                1 AS d_first_dom,
                31 AS d_last_dom,
                1 AS d_same_day_ly,
                1 AS d_same_day_lq,
                'current' AS d_current_day,
                'current' AS d_current_week,
                'current' AS d_current_month,
                'current' AS d_current_quarter,
                'current' AS d_current_year
        """)

        conn.execute("""
            CREATE TABLE time_dim AS SELECT
                1 AS t_time_sk,
                '10:30:00' AS t_time_id,
                37800 AS t_time,
                10 AS t_hour,
                30 AS t_minute,
                0 AS t_second,
                'AM' AS t_am_pm,
                'shift1' AS t_shift,
                'sub1' AS t_sub_shift,
                'meal1' AS t_meal_time
        """)

        conn.execute("""
            CREATE TABLE item AS SELECT
                1 AS i_item_sk,
                'ITEM001' AS i_item_id,
                DATE '2020-01-01' AS i_rec_start_date,
                DATE '2025-12-31' AS i_rec_end_date,
                'Test Item' AS i_item_desc,
                10.00 AS i_current_price,
                8.00 AS i_wholesale_cost,
                1 AS i_brand_id,
                'TestBrand' AS i_brand,
                1 AS i_class_id,
                'TestClass' AS i_class,
                1 AS i_category_id,
                'Electronics' AS i_category,
                1 AS i_manufact_id,
                'TestManuf' AS i_manufact,
                'M' AS i_size,
                'XXXXXX' AS i_formulation,
                'Red' AS i_color,
                100 AS i_units,
                'Box' AS i_container,
                1 AS i_manager_id,
                'Test Product' AS i_product_name
        """)

        conn.execute("""
            CREATE TABLE store AS SELECT
                1 AS s_store_sk,
                'STORE001' AS s_store_id,
                DATE '2020-01-01' AS s_rec_start_date,
                DATE '2025-12-31' AS s_rec_end_date,
                1 AS s_closed_date_sk,
                'Test Store' AS s_store_name,
                100 AS s_number_employees,
                1000 AS s_floor_space,
                '08:00' AS s_hours,
                'John' AS s_manager,
                1 AS s_market_id,
                'US' AS s_geography_class,
                'Market Desc' AS s_market_desc,
                'Manager' AS s_market_manager,
                1 AS s_division_id,
                'Div Name' AS s_division_name,
                1 AS s_company_id,
                'Company' AS s_company_name,
                '123 Main St' AS s_street_number,
                'Main' AS s_street_name,
                'St' AS s_street_type,
                'Ste' AS s_suite_number,
                'New York' AS s_city,
                'NY' AS s_county,
                'NY' AS s_state,
                '10001' AS s_zip,
                'US' AS s_country,
                -5.0 AS s_gmt_offset,
                0.07 AS s_tax_percentage
        """)

        conn.execute("""
            CREATE TABLE customer AS SELECT
                1 AS c_customer_sk,
                'CUST001' AS c_customer_id,
                1 AS c_current_cdemo_sk,
                1 AS c_current_hdemo_sk,
                1 AS c_current_addr_sk,
                1 AS c_first_shipto_date_sk,
                1 AS c_first_sales_date_sk,
                'Dr' AS c_salutation,
                'John' AS c_first_name,
                'Doe' AS c_last_name,
                'N' AS c_preferred_cust_flag,
                1990 AS c_birth_day,
                6 AS c_birth_month,
                1980 AS c_birth_year,
                'US' AS c_birth_country,
                'user@test.com' AS c_login,
                'user@test.com' AS c_email_address,
                1 AS c_last_review_date_sk
        """)

        conn.execute("""
            CREATE TABLE customer_demographics AS SELECT
                1 AS cd_demo_sk,
                'M' AS cd_gender,
                'M' AS cd_marital_status,
                'Primary' AS cd_education_status,
                50000 AS cd_purchase_estimate,
                'Good' AS cd_credit_rating,
                2 AS cd_dep_count,
                0 AS cd_dep_employed_count,
                1 AS cd_dep_college_count
        """)

        conn.execute("""
            CREATE TABLE household_demographics AS SELECT
                1 AS hd_demo_sk,
                1 AS hd_income_band_sk,
                'Own' AS hd_buy_potential,
                2 AS hd_dep_count,
                1 AS hd_vehicle_count
        """)

        conn.execute("""
            CREATE TABLE customer_address AS SELECT
                1 AS ca_address_sk,
                'ADDR001' AS ca_address_id,
                '123' AS ca_street_number,
                'Oak' AS ca_street_name,
                'Ave' AS ca_street_type,
                '1A' AS ca_suite_number,
                'Boston' AS ca_city,
                'Suffolk' AS ca_county,
                'MA' AS ca_state,
                '02101' AS ca_zip,
                'US' AS ca_country,
                -5.0 AS ca_gmt_offset,
                'residential' AS ca_location_type
        """)

        conn.execute("""
            CREATE TABLE promotion AS SELECT
                1 AS p_promo_sk,
                'PROMO001' AS p_promo_id,
                1 AS p_start_date_sk,
                1 AS p_end_date_sk,
                1 AS p_item_sk,
                100.0 AS p_cost,
                100 AS p_response_target,
                'Promo Name' AS p_promo_name,
                'Y' AS p_channel_dmail,
                'Y' AS p_channel_email,
                'Y' AS p_channel_catalog,
                'Y' AS p_channel_tv,
                'Y' AS p_channel_radio,
                'Y' AS p_channel_press,
                'Y' AS p_channel_event,
                'Y' AS p_channel_demo,
                'Details' AS p_channel_details,
                'General' AS p_purpose,
                'Y' AS p_discount_active
        """)

        conn.execute("""
            CREATE TABLE reason AS SELECT
                1 AS r_reason_sk,
                'REASON001' AS r_reason_id,
                'Product Defective' AS r_reason_desc
        """)

        conn.execute("""
            CREATE TABLE web_site AS SELECT
                1 AS web_site_sk,
                'SITE001' AS web_site_id,
                DATE '2020-01-01' AS web_rec_start_date,
                DATE '2025-12-31' AS web_rec_end_date,
                'Test Site' AS web_name,
                1 AS web_open_date_sk,
                1 AS web_close_date_sk,
                'Test Class' AS web_class,
                'Manager' AS web_manager,
                1 AS web_mkt_id,
                'Mkt Class' AS web_mkt_class,
                'Mkt Desc' AS web_mkt_desc,
                'Mkt Manager' AS web_market_manager,
                1 AS web_company_id,
                'Company' AS web_company_name,
                '456' AS web_street_number,
                'Tech' AS web_street_name,
                'Blvd' AS web_street_type,
                '2B' AS web_suite_number,
                'San Jose' AS web_city,
                'Santa Clara' AS web_county,
                'CA' AS web_state,
                '95101' AS web_zip,
                'US' AS web_country,
                -8.0 AS web_gmt_offset,
                0.08 AS web_tax_percentage
        """)

        conn.execute("""
            CREATE TABLE web_page AS SELECT
                1 AS wp_web_page_sk,
                'PAGE001' AS wp_web_page_id,
                DATE '2020-01-01' AS wp_rec_start_date,
                DATE '2025-12-31' AS wp_rec_end_date,
                1 AS wp_creation_date_sk,
                1 AS wp_access_date_sk,
                'N' AS wp_autogen_flag,
                1 AS wp_customer_sk,
                'http://test.com' AS wp_url,
                'homepage' AS wp_type,
                10 AS wp_char_count,
                5 AS wp_link_count,
                2 AS wp_image_count,
                100 AS wp_max_ad_count
        """)

        conn.execute("""
            CREATE TABLE ship_mode AS SELECT
                1 AS sm_ship_mode_sk,
                'SHIP001' AS sm_ship_mode_id,
                'Ground' AS sm_type,
                'Standard' AS sm_code,
                'UPS' AS sm_carrier,
                'Contract' AS sm_contract
        """)

        conn.execute("""
            CREATE TABLE warehouse AS SELECT
                1 AS w_warehouse_sk,
                'WH001' AS w_warehouse_id,
                'Main Warehouse' AS w_warehouse_name,
                100000 AS w_warehouse_sq_ft,
                '789' AS w_street_number,
                'Industrial' AS w_street_name,
                'Way' AS w_street_type,
                '3C' AS w_suite_number,
                'Chicago' AS w_city,
                'Cook' AS w_county,
                'IL' AS w_state,
                '60601' AS w_zip,
                'US' AS w_country,
                -6.0 AS w_gmt_offset
        """)

        conn.execute("""
            CREATE TABLE call_center AS SELECT
                1 AS cc_call_center_sk,
                'CC001' AS cc_call_center_id,
                DATE '2020-01-01' AS cc_rec_start_date,
                DATE '2025-12-31' AS cc_rec_end_date,
                1 AS cc_closed_date_sk,
                1 AS cc_open_date_sk,
                'Call Center' AS cc_name,
                'Service' AS cc_class,
                10 AS cc_employees,
                1000 AS cc_sq_ft,
                '24/7' AS cc_hours,
                'Manager' AS cc_manager,
                1 AS cc_mkt_id,
                'Mkt Class' AS cc_mkt_class,
                'Mkt Desc' AS cc_mkt_desc,
                'Mkt Manager' AS cc_market_manager,
                1 AS cc_division,
                'Div Name' AS cc_division_name,
                1 AS cc_company,
                'Company' AS cc_company_name,
                '101' AS cc_street_number,
                'Center' AS cc_street_name,
                'Dr' AS cc_street_type,
                '4D' AS cc_suite_number,
                'Denver' AS cc_city,
                'Denver' AS cc_county,
                'CO' AS cc_state,
                '80201' AS cc_zip,
                'US' AS cc_country,
                -7.0 AS cc_gmt_offset,
                0.05 AS cc_tax_percentage
        """)

        conn.execute("""
            CREATE TABLE catalog_page AS SELECT
                1 AS cp_catalog_page_sk,
                'CP001' AS cp_catalog_page_id,
                1 AS cp_start_date_sk,
                1 AS cp_end_date_sk,
                'Dept' AS cp_department,
                1 AS cp_catalog_number,
                1 AS cp_catalog_page_number,
                'Description' AS cp_description,
                'page' AS cp_type
        """)

        conn.execute("""
            CREATE TABLE income_band AS SELECT
                1 AS ib_income_band_sk,
                0 AS ib_lower_bound,
                50000 AS ib_upper_bound
        """)

    def create_fact_tables(conn: Any) -> None:

        conn.execute("""
            CREATE TABLE store_sales AS
            SELECT
                1 AS ss_sold_date_sk,
                1 AS ss_sold_time_sk,
                1 AS ss_item_sk,
                1 AS ss_customer_sk,
                1 AS ss_cdemo_sk,
                1 AS ss_hdemo_sk,
                1 AS ss_addr_sk,
                1 AS ss_store_sk,
                1 AS ss_promo_sk,
                100 AS ss_ticket_number,
                10 AS ss_quantity,
                100.00 AS ss_wholesale_cost,
                150.00 AS ss_list_price,
                140.00 AS ss_sales_price,
                10.00 AS ss_ext_discount_amt,
                1400.00 AS ss_ext_sales_price,
                1000.00 AS ss_ext_wholesale_cost,
                1500.00 AS ss_ext_list_price,
                140.00 AS ss_ext_tax,
                0.00 AS ss_coupon_amt,
                1400.00 AS ss_net_paid,
                1540.00 AS ss_net_paid_inc_tax,
                400.00 AS ss_net_profit
        """)

        conn.execute("""
            CREATE TABLE store_returns AS
            SELECT
                1 AS sr_returned_date_sk,
                1 AS sr_return_time_sk,
                1 AS sr_item_sk,
                1 AS sr_customer_sk,
                1 AS sr_cdemo_sk,
                1 AS sr_hdemo_sk,
                1 AS sr_addr_sk,
                1 AS sr_store_sk,
                1 AS sr_reason_sk,
                100 AS sr_ticket_number,
                5 AS sr_return_quantity,
                70.00 AS sr_return_amt,
                7.00 AS sr_return_tax,
                77.00 AS sr_return_amt_inc_tax,
                10.00 AS sr_fee,
                0.00 AS sr_return_ship_cost,
                50.00 AS sr_refunded_cash,
                10.00 AS sr_reversed_charge,
                10.00 AS sr_store_credit,
                30.00 AS sr_net_loss
        """)

        conn.execute("""
            CREATE TABLE web_sales AS
            SELECT
                1 AS ws_sold_date_sk,
                1 AS ws_sold_time_sk,
                1 AS ws_ship_date_sk,
                1 AS ws_item_sk,
                1 AS ws_bill_customer_sk,
                1 AS ws_bill_cdemo_sk,
                1 AS ws_bill_hdemo_sk,
                1 AS ws_bill_addr_sk,
                1 AS ws_ship_customer_sk,
                1 AS ws_ship_cdemo_sk,
                1 AS ws_ship_hdemo_sk,
                1 AS ws_ship_addr_sk,
                1 AS ws_web_page_sk,
                1 AS ws_web_site_sk,
                1 AS ws_ship_mode_sk,
                1 AS ws_warehouse_sk,
                1 AS ws_promo_sk,
                200 AS ws_order_number,
                8 AS ws_quantity,
                80.00 AS ws_wholesale_cost,
                120.00 AS ws_list_price,
                110.00 AS ws_sales_price,
                10.00 AS ws_ext_discount_amt,
                880.00 AS ws_ext_sales_price,
                640.00 AS ws_ext_wholesale_cost,
                960.00 AS ws_ext_list_price,
                88.00 AS ws_ext_tax,
                0.00 AS ws_coupon_amt,
                20.00 AS ws_ext_ship_cost,
                880.00 AS ws_net_paid,
                968.00 AS ws_net_paid_inc_tax,
                900.00 AS ws_net_paid_inc_ship,
                988.00 AS ws_net_paid_inc_ship_tax,
                240.00 AS ws_net_profit
        """)

        conn.execute("""
            CREATE TABLE web_returns AS
            SELECT
                1 AS wr_returned_date_sk,
                1 AS wr_returned_time_sk,
                1 AS wr_item_sk,
                1 AS wr_refunded_customer_sk,
                1 AS wr_refunded_cdemo_sk,
                1 AS wr_refunded_hdemo_sk,
                1 AS wr_refunded_addr_sk,
                1 AS wr_returning_customer_sk,
                1 AS wr_returning_cdemo_sk,
                1 AS wr_returning_hdemo_sk,
                1 AS wr_returning_addr_sk,
                1 AS wr_web_page_sk,
                1 AS wr_reason_sk,
                200 AS wr_order_number,
                3 AS wr_return_quantity,
                55.00 AS wr_return_amt,
                5.50 AS wr_return_tax,
                60.50 AS wr_return_amt_inc_tax,
                8.00 AS wr_fee,
                5.00 AS wr_return_ship_cost,
                40.00 AS wr_refunded_cash,
                5.00 AS wr_reversed_charge,
                10.00 AS wr_account_credit,
                15.00 AS wr_net_loss
        """)

        conn.execute("""
            CREATE TABLE catalog_sales AS
            SELECT
                1 AS cs_sold_date_sk,
                1 AS cs_sold_time_sk,
                1 AS cs_ship_date_sk,
                1 AS cs_bill_customer_sk,
                1 AS cs_bill_cdemo_sk,
                1 AS cs_bill_hdemo_sk,
                1 AS cs_bill_addr_sk,
                1 AS cs_ship_customer_sk,
                1 AS cs_ship_cdemo_sk,
                1 AS cs_ship_hdemo_sk,
                1 AS cs_ship_addr_sk,
                1 AS cs_call_center_sk,
                1 AS cs_catalog_page_sk,
                1 AS cs_ship_mode_sk,
                1 AS cs_warehouse_sk,
                1 AS cs_item_sk,
                1 AS cs_promo_sk,
                300 AS cs_order_number,
                12 AS cs_quantity,
                120.00 AS cs_wholesale_cost,
                180.00 AS cs_list_price,
                170.00 AS cs_sales_price,
                10.00 AS cs_ext_discount_amt,
                2040.00 AS cs_ext_sales_price,
                1440.00 AS cs_ext_wholesale_cost,
                2160.00 AS cs_ext_list_price,
                204.00 AS cs_ext_tax,
                0.00 AS cs_coupon_amt,
                30.00 AS cs_ext_ship_cost,
                2040.00 AS cs_net_paid,
                2244.00 AS cs_net_paid_inc_tax,
                2070.00 AS cs_net_paid_inc_ship,
                2274.00 AS cs_net_paid_inc_ship_tax,
                600.00 AS cs_net_profit
        """)

        conn.execute("""
            CREATE TABLE catalog_returns AS
            SELECT
                1 AS cr_returned_date_sk,
                1 AS cr_returned_time_sk,
                1 AS cr_item_sk,
                1 AS cr_refunded_customer_sk,
                1 AS cr_refunded_cdemo_sk,
                1 AS cr_refunded_hdemo_sk,
                1 AS cr_refunded_addr_sk,
                1 AS cr_returning_customer_sk,
                1 AS cr_returning_cdemo_sk,
                1 AS cr_returning_hdemo_sk,
                1 AS cr_returning_addr_sk,
                1 AS cr_call_center_sk,
                1 AS cr_catalog_page_sk,
                1 AS cr_ship_mode_sk,
                1 AS cr_warehouse_sk,
                1 AS cr_reason_sk,
                300 AS cr_order_number,
                4 AS cr_return_quantity,
                85.00 AS cr_return_amount,
                8.50 AS cr_return_tax,
                93.50 AS cr_return_amt_inc_tax,
                12.00 AS cr_fee,
                8.00 AS cr_return_ship_cost,
                60.00 AS cr_refunded_cash,
                10.00 AS cr_reversed_charge,
                15.00 AS cr_store_credit,
                0.00 AS cr_account_credit,
                25.00 AS cr_net_loss
        """)

    def loader(conn_arg: Any, _: Path, channels: list[str], mode: str) -> None:
        create_dimensions(conn_arg)
        create_fact_tables(conn_arg)

    monkeypatch.setattr(transformer, "_load_source_tables", loader)
    result = transformer.transform(
        tpcds_dir=tmp_path,
        output_dir=tmp_path / "out",
        mode="full",
        channels=["store", "web", "catalog"],
        output_format="parquet",
        scale_factor=0.01,
    )

    conn.execute(f"CREATE TABLE {OBT_TABLE_NAME} AS SELECT * FROM '{result['table']}'")

    create_dimensions(conn)

    manager = TPCDSOBTQueryManager()
    queries = manager.get_queries()

    execution_errors: dict[str, str] = {}
    for query_id, sql in queries.items():
        try:
            result_rows = conn.execute(sql).fetchall()

            assert result_rows is not None
        except Exception as e:
            execution_errors[query_id] = str(e)

    conn.close()

    assert not execution_errors, f"Queries failed to execute: {execution_errors}"
