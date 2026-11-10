from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

METHOD_FACT_INSERT = 1
METHOD_FACT_DELETE = 2
METHOD_INVENTORY_DELETE = 3

MYSQL_INTEGER_CAST_DIALECTS = frozenset({"mysql", "singlestore"})


@dataclass(frozen=True)
class MaintenanceFunction:
    function_id: int
    name: str
    method: int
    target_tables: tuple[str, ...]
    staging_tables: tuple[str, ...]
    statements: tuple[str, ...]
    expected_rows_table: str | None = None

    @property
    def operation_type(self) -> str:
        return "INSERT" if self.method == METHOD_FACT_INSERT else "DELETE"

    def statements_for(self, dialect: str | None) -> tuple[str, ...]:
        if dialect is not None and dialect.lower() in MYSQL_INTEGER_CAST_DIALECTS:
            return tuple(statement.replace("AS INTEGER)", "AS SIGNED)") for statement in self.statements)
        return self.statements


def _insert(target: str, columns: tuple[tuple[str, str], ...], source: str) -> str:
    column_list = ", ".join(name for name, _ in columns)
    select_list = ", ".join(f"{expression} AS {name}" for name, expression in columns)
    return f"INSERT INTO {target} ({column_list}) SELECT {select_list} FROM {source}"


def _delete_range(date_column_table: str) -> str:
    return f"d_date BETWEEN {date_column_table}1 AND {date_column_table}2"


_LF_SS = _insert(
    "store_sales",
    (
        ("ss_sold_date_sk", "d_date_sk"),
        ("ss_sold_time_sk", "t_time_sk"),
        ("ss_item_sk", "i_item_sk"),
        ("ss_customer_sk", "c_customer_sk"),
        ("ss_cdemo_sk", "c_current_cdemo_sk"),
        ("ss_hdemo_sk", "c_current_hdemo_sk"),
        ("ss_addr_sk", "c_current_addr_sk"),
        ("ss_store_sk", "s_store_sk"),
        ("ss_promo_sk", "p_promo_sk"),
        ("ss_ticket_number", "purc_purchase_id"),
        ("ss_quantity", "plin_quantity"),
        ("ss_wholesale_cost", "i_wholesale_cost"),
        ("ss_list_price", "i_current_price"),
        ("ss_sales_price", "plin_sale_price"),
        ("ss_ext_discount_amt", "(i_current_price - plin_sale_price) * plin_quantity"),
        ("ss_ext_sales_price", "plin_sale_price * plin_quantity"),
        ("ss_ext_wholesale_cost", "i_wholesale_cost * plin_quantity"),
        ("ss_ext_list_price", "i_current_price * plin_quantity"),
        ("ss_ext_tax", "i_current_price * s_tax_percentage"),
        ("ss_coupon_amt", "plin_coupon_amt"),
        ("ss_net_paid", "(plin_sale_price * plin_quantity) - plin_coupon_amt"),
        ("ss_net_paid_inc_tax", "((plin_sale_price * plin_quantity) - plin_coupon_amt) * (1 + s_tax_percentage)"),
        ("ss_net_profit", "((plin_sale_price * plin_quantity) - plin_coupon_amt) - (plin_quantity * i_wholesale_cost)"),
    ),
    "s_purchase "
    "LEFT OUTER JOIN customer ON (purc_customer_id = c_customer_id) "
    "LEFT OUTER JOIN store ON (purc_store_id = s_store_id) "
    "LEFT OUTER JOIN date_dim ON (purc_purchase_date = d_date) "
    "LEFT OUTER JOIN time_dim ON (purc_purchase_time = t_time) "
    "JOIN s_purchase_lineitem ON (purc_purchase_id = plin_purchase_id) "
    "LEFT OUTER JOIN promotion ON (plin_promotion_id = p_promo_id) "
    "LEFT OUTER JOIN item ON (plin_item_id = i_item_id) "
    "WHERE purc_purchase_id = plin_purchase_id AND i_rec_end_date IS NULL AND s_rec_end_date IS NULL",
)

_LF_SR = _insert(
    "store_returns",
    (
        ("sr_returned_date_sk", "d_date_sk"),
        ("sr_return_time_sk", "t_time_sk"),
        ("sr_item_sk", "i_item_sk"),
        ("sr_customer_sk", "c_customer_sk"),
        ("sr_cdemo_sk", "c_current_cdemo_sk"),
        ("sr_hdemo_sk", "c_current_hdemo_sk"),
        ("sr_addr_sk", "c_current_addr_sk"),
        ("sr_store_sk", "s_store_sk"),
        ("sr_reason_sk", "r_reason_sk"),
        ("sr_ticket_number", "CAST(sret_ticket_number AS INTEGER)"),
        ("sr_return_quantity", "sret_return_qty"),
        ("sr_return_amt", "sret_return_amt"),
        ("sr_return_tax", "sret_return_tax"),
        ("sr_return_amt_inc_tax", "sret_return_amt + sret_return_tax"),
        ("sr_fee", "sret_return_fee"),
        ("sr_return_ship_cost", "sret_return_ship_cost"),
        ("sr_refunded_cash", "sret_refunded_cash"),
        ("sr_reversed_charge", "sret_reversed_charge"),
        ("sr_store_credit", "sret_store_credit"),
        (
            "sr_net_loss",
            "sret_return_amt + sret_return_tax + sret_return_fee - sret_refunded_cash - sret_reversed_charge"
            " - sret_store_credit",
        ),
    ),
    "s_store_returns "
    "LEFT OUTER JOIN date_dim ON (sret_return_date = d_date) "
    "LEFT OUTER JOIN time_dim ON ((CAST(SUBSTR(sret_return_time, 1, 2) AS INTEGER) * 3600"
    " + CAST(SUBSTR(sret_return_time, 4, 2) AS INTEGER) * 60"
    " + CAST(SUBSTR(sret_return_time, 7, 2) AS INTEGER)) = t_time) "
    "LEFT OUTER JOIN item ON (sret_item_id = i_item_id) "
    "LEFT OUTER JOIN customer ON (sret_customer_id = c_customer_id) "
    "LEFT OUTER JOIN store ON (sret_store_id = s_store_id) "
    "LEFT OUTER JOIN reason ON (sret_reason_id = r_reason_id) "
    "WHERE i_rec_end_date IS NULL AND s_rec_end_date IS NULL",
)

_LF_WS = _insert(
    "web_sales",
    (
        ("ws_sold_date_sk", "d1.d_date_sk"),
        ("ws_sold_time_sk", "t_time_sk"),
        ("ws_ship_date_sk", "d2.d_date_sk"),
        ("ws_item_sk", "i_item_sk"),
        ("ws_bill_customer_sk", "c1.c_customer_sk"),
        ("ws_bill_cdemo_sk", "c1.c_current_cdemo_sk"),
        ("ws_bill_hdemo_sk", "c1.c_current_hdemo_sk"),
        ("ws_bill_addr_sk", "c1.c_current_addr_sk"),
        ("ws_ship_customer_sk", "c2.c_customer_sk"),
        ("ws_ship_cdemo_sk", "c2.c_current_cdemo_sk"),
        ("ws_ship_hdemo_sk", "c2.c_current_hdemo_sk"),
        ("ws_ship_addr_sk", "c2.c_current_addr_sk"),
        ("ws_web_page_sk", "wp_web_page_sk"),
        ("ws_web_site_sk", "web_site_sk"),
        ("ws_ship_mode_sk", "sm_ship_mode_sk"),
        ("ws_warehouse_sk", "w_warehouse_sk"),
        ("ws_promo_sk", "p_promo_sk"),
        ("ws_order_number", "word_order_id"),
        ("ws_quantity", "wlin_quantity"),
        ("ws_wholesale_cost", "i_wholesale_cost"),
        ("ws_list_price", "i_current_price"),
        ("ws_sales_price", "wlin_sales_price"),
        ("ws_ext_discount_amt", "(i_current_price - wlin_sales_price) * wlin_quantity"),
        ("ws_ext_sales_price", "wlin_sales_price * wlin_quantity"),
        ("ws_ext_wholesale_cost", "i_wholesale_cost * wlin_quantity"),
        ("ws_ext_list_price", "i_current_price * wlin_quantity"),
        ("ws_ext_tax", "i_current_price * web_tax_percentage"),
        ("ws_coupon_amt", "wlin_coupon_amt"),
        ("ws_ext_ship_cost", "wlin_ship_cost * wlin_quantity"),
        ("ws_net_paid", "(wlin_sales_price * wlin_quantity) - wlin_coupon_amt"),
        ("ws_net_paid_inc_tax", "((wlin_sales_price * wlin_quantity) - wlin_coupon_amt) * (1 + web_tax_percentage)"),
        (
            "ws_net_paid_inc_ship",
            "((wlin_sales_price * wlin_quantity) - wlin_coupon_amt) - (wlin_quantity * i_wholesale_cost)",
        ),
        (
            "ws_net_paid_inc_ship_tax",
            "(wlin_sales_price * wlin_quantity) - wlin_coupon_amt + (wlin_ship_cost * wlin_quantity)"
            " + i_current_price * web_tax_percentage",
        ),
        (
            "ws_net_profit",
            "((wlin_sales_price * wlin_quantity) - wlin_coupon_amt) - (i_wholesale_cost * wlin_quantity)",
        ),
    ),
    "s_web_order "
    "LEFT OUTER JOIN date_dim d1 ON (word_order_date = d1.d_date) "
    "LEFT OUTER JOIN time_dim ON (word_order_time = t_time) "
    "LEFT OUTER JOIN customer c1 ON (word_bill_customer_id = c1.c_customer_id) "
    "LEFT OUTER JOIN customer c2 ON (word_ship_customer_id = c2.c_customer_id) "
    "LEFT OUTER JOIN web_site ON (word_web_site_id = web_site_id AND web_rec_end_date IS NULL) "
    "LEFT OUTER JOIN ship_mode ON (word_ship_mode_id = sm_ship_mode_id) "
    "JOIN s_web_order_lineitem ON (word_order_id = wlin_order_id) "
    "LEFT OUTER JOIN date_dim d2 ON (wlin_ship_date = d2.d_date) "
    "LEFT OUTER JOIN item ON (wlin_item_id = i_item_id AND i_rec_end_date IS NULL) "
    "LEFT OUTER JOIN web_page ON (wlin_web_page_id = wp_web_page_id AND wp_rec_end_date IS NULL) "
    "LEFT OUTER JOIN warehouse ON (wlin_warehouse_id = w_warehouse_id) "
    "LEFT OUTER JOIN promotion ON (wlin_promotion_id = p_promo_id)",
)

_LF_WR = _insert(
    "web_returns",
    (
        ("wr_returned_date_sk", "d_date_sk"),
        ("wr_returned_time_sk", "t_time_sk"),
        ("wr_item_sk", "i_item_sk"),
        ("wr_refunded_customer_sk", "c1.c_customer_sk"),
        ("wr_refunded_cdemo_sk", "c1.c_current_cdemo_sk"),
        ("wr_refunded_hdemo_sk", "c1.c_current_hdemo_sk"),
        ("wr_refunded_addr_sk", "c1.c_current_addr_sk"),
        ("wr_returning_customer_sk", "c2.c_customer_sk"),
        ("wr_returning_cdemo_sk", "c2.c_current_cdemo_sk"),
        ("wr_returning_hdemo_sk", "c2.c_current_hdemo_sk"),
        ("wr_returning_addr_sk", "c2.c_current_addr_sk"),
        ("wr_web_page_sk", "wp_web_page_sk"),
        ("wr_reason_sk", "r_reason_sk"),
        ("wr_order_number", "wret_order_id"),
        ("wr_return_quantity", "wret_return_qty"),
        ("wr_return_amt", "wret_return_amt"),
        ("wr_return_tax", "wret_return_tax"),
        ("wr_return_amt_inc_tax", "wret_return_amt + wret_return_tax"),
        ("wr_fee", "wret_return_fee"),
        ("wr_return_ship_cost", "wret_return_ship_cost"),
        ("wr_refunded_cash", "wret_refunded_cash"),
        ("wr_reversed_charge", "wret_reversed_charge"),
        ("wr_account_credit", "wret_account_credit"),
        (
            "wr_net_loss",
            "wret_return_amt + wret_return_tax + wret_return_fee - wret_refunded_cash - wret_reversed_charge"
            " - wret_account_credit",
        ),
    ),
    "s_web_returns "
    "LEFT OUTER JOIN date_dim ON (wret_return_date = d_date) "
    "LEFT OUTER JOIN time_dim ON ((CAST(SUBSTR(wret_return_time, 1, 2) AS INTEGER) * 3600"
    " + CAST(SUBSTR(wret_return_time, 4, 2) AS INTEGER) * 60"
    " + CAST(SUBSTR(wret_return_time, 7, 2) AS INTEGER)) = t_time) "
    "LEFT OUTER JOIN item ON (wret_item_id = i_item_id) "
    "LEFT OUTER JOIN customer c1 ON (wret_return_customer_id = c1.c_customer_id) "
    "LEFT OUTER JOIN customer c2 ON (wret_refund_customer_id = c2.c_customer_id) "
    "LEFT OUTER JOIN reason ON (wret_reason_id = r_reason_id) "
    "LEFT OUTER JOIN web_page ON (wret_web_page_id = wp_web_page_id) "
    "WHERE i_rec_end_date IS NULL AND wp_rec_end_date IS NULL",
)

_LF_CS = _insert(
    "catalog_sales",
    (
        ("cs_sold_date_sk", "d1.d_date_sk"),
        ("cs_sold_time_sk", "t_time_sk"),
        ("cs_ship_date_sk", "d2.d_date_sk"),
        ("cs_bill_customer_sk", "c1.c_customer_sk"),
        ("cs_bill_cdemo_sk", "c1.c_current_cdemo_sk"),
        ("cs_bill_hdemo_sk", "c1.c_current_hdemo_sk"),
        ("cs_bill_addr_sk", "c1.c_current_addr_sk"),
        ("cs_ship_customer_sk", "c2.c_customer_sk"),
        ("cs_ship_cdemo_sk", "c2.c_current_cdemo_sk"),
        ("cs_ship_hdemo_sk", "c2.c_current_hdemo_sk"),
        ("cs_ship_addr_sk", "c2.c_current_addr_sk"),
        ("cs_call_center_sk", "cc_call_center_sk"),
        ("cs_catalog_page_sk", "cp_catalog_page_sk"),
        ("cs_ship_mode_sk", "sm_ship_mode_sk"),
        ("cs_warehouse_sk", "w_warehouse_sk"),
        ("cs_item_sk", "i_item_sk"),
        ("cs_promo_sk", "p_promo_sk"),
        ("cs_order_number", "cord_order_id"),
        ("cs_quantity", "clin_quantity"),
        ("cs_wholesale_cost", "i_wholesale_cost"),
        ("cs_list_price", "i_current_price"),
        ("cs_sales_price", "clin_sales_price"),
        ("cs_ext_discount_amt", "(i_current_price - clin_sales_price) * clin_quantity"),
        ("cs_ext_sales_price", "clin_sales_price * clin_quantity"),
        ("cs_ext_wholesale_cost", "i_wholesale_cost * clin_quantity"),
        ("cs_ext_list_price", "i_current_price * clin_quantity"),
        ("cs_ext_tax", "i_current_price * cc_tax_percentage"),
        ("cs_coupon_amt", "clin_coupon_amt"),
        ("cs_ext_ship_cost", "clin_ship_cost * clin_quantity"),
        ("cs_net_paid", "(clin_sales_price * clin_quantity) - clin_coupon_amt"),
        ("cs_net_paid_inc_tax", "((clin_sales_price * clin_quantity) - clin_coupon_amt) * (1 + cc_tax_percentage)"),
        (
            "cs_net_paid_inc_ship",
            "(clin_sales_price * clin_quantity) - clin_coupon_amt + (clin_ship_cost * clin_quantity)",
        ),
        (
            "cs_net_paid_inc_ship_tax",
            "(clin_sales_price * clin_quantity) - clin_coupon_amt + (clin_ship_cost * clin_quantity)"
            " + i_current_price * cc_tax_percentage",
        ),
        (
            "cs_net_profit",
            "((clin_sales_price * clin_quantity) - clin_coupon_amt) - (clin_quantity * i_wholesale_cost)",
        ),
    ),
    "s_catalog_order "
    "LEFT OUTER JOIN date_dim d1 ON (cord_order_date = d1.d_date) "
    "LEFT OUTER JOIN time_dim ON (cord_order_time = t_time) "
    "LEFT OUTER JOIN customer c1 ON (cord_bill_customer_id = c1.c_customer_id) "
    "LEFT OUTER JOIN customer c2 ON (cord_ship_customer_id = c2.c_customer_id) "
    "LEFT OUTER JOIN call_center ON (cord_call_center_id = cc_call_center_id AND cc_rec_end_date IS NULL) "
    "LEFT OUTER JOIN ship_mode ON (cord_ship_mode_id = sm_ship_mode_id) "
    "JOIN s_catalog_order_lineitem ON (cord_order_id = clin_order_id) "
    "LEFT OUTER JOIN date_dim d2 ON (clin_ship_date = d2.d_date) "
    "LEFT OUTER JOIN catalog_page ON (clin_catalog_page_number = cp_catalog_page_number"
    " AND clin_catalog_number = cp_catalog_number) "
    "LEFT OUTER JOIN warehouse ON (clin_warehouse_id = w_warehouse_id) "
    "LEFT OUTER JOIN item ON (clin_item_id = i_item_id AND i_rec_end_date IS NULL) "
    "LEFT OUTER JOIN promotion ON (clin_promotion_id = p_promo_id)",
)

_LF_CR = _insert(
    "catalog_returns",
    (
        ("cr_returned_date_sk", "d_date_sk"),
        ("cr_returned_time_sk", "t_time_sk"),
        ("cr_item_sk", "i_item_sk"),
        ("cr_refunded_customer_sk", "c1.c_customer_sk"),
        ("cr_refunded_cdemo_sk", "c1.c_current_cdemo_sk"),
        ("cr_refunded_hdemo_sk", "c1.c_current_hdemo_sk"),
        ("cr_refunded_addr_sk", "c1.c_current_addr_sk"),
        ("cr_returning_customer_sk", "c2.c_customer_sk"),
        ("cr_returning_cdemo_sk", "c2.c_current_cdemo_sk"),
        ("cr_returning_hdemo_sk", "c2.c_current_hdemo_sk"),
        ("cr_returning_addr_sk", "c2.c_current_addr_sk"),
        ("cr_call_center_sk", "cc_call_center_sk"),
        ("cr_catalog_page_sk", "cp_catalog_page_sk"),
        ("cr_ship_mode_sk", "sm_ship_mode_sk"),
        ("cr_warehouse_sk", "w_warehouse_sk"),
        ("cr_reason_sk", "r_reason_sk"),
        ("cr_order_number", "cret_order_id"),
        ("cr_return_quantity", "cret_return_qty"),
        ("cr_return_amount", "cret_return_amt"),
        ("cr_return_tax", "cret_return_tax"),
        ("cr_return_amt_inc_tax", "cret_return_amt + cret_return_tax"),
        ("cr_fee", "cret_return_fee"),
        ("cr_return_ship_cost", "cret_return_ship_cost"),
        ("cr_refunded_cash", "cret_refunded_cash"),
        ("cr_reversed_charge", "cret_reversed_charge"),
        ("cr_store_credit", "cret_merchant_credit"),
        (
            "cr_net_loss",
            "cret_return_amt + cret_return_tax + cret_return_fee - cret_refunded_cash - cret_reversed_charge"
            " - cret_merchant_credit",
        ),
    ),
    "s_catalog_returns "
    "LEFT OUTER JOIN date_dim ON (cret_return_date = d_date) "
    "LEFT OUTER JOIN time_dim ON ((CAST(SUBSTR(cret_return_time, 1, 2) AS INTEGER) * 3600"
    " + CAST(SUBSTR(cret_return_time, 4, 2) AS INTEGER) * 60"
    " + CAST(SUBSTR(cret_return_time, 7, 2) AS INTEGER)) = t_time) "
    "LEFT OUTER JOIN item ON (cret_item_id = i_item_id) "
    "LEFT OUTER JOIN customer c1 ON (cret_return_customer_id = c1.c_customer_id) "
    "LEFT OUTER JOIN customer c2 ON (cret_refund_customer_id = c2.c_customer_id) "
    "LEFT OUTER JOIN reason ON (cret_reason_id = r_reason_id) "
    "LEFT OUTER JOIN call_center ON (cret_call_center_id = cc_call_center_id) "
    "LEFT OUTER JOIN catalog_page ON (cret_catalog_page_id = cp_catalog_page_id) "
    "LEFT OUTER JOIN ship_mode ON (cret_shipmode_id = sm_ship_mode_id) "
    "LEFT OUTER JOIN warehouse ON (cret_warehouse_id = w_warehouse_id) "
    "WHERE i_rec_end_date IS NULL AND cc_rec_end_date IS NULL",
)

_LF_I = _insert(
    "inventory",
    (
        ("inv_date_sk", "d_date_sk"),
        ("inv_item_sk", "i_item_sk"),
        ("inv_warehouse_sk", "w_warehouse_sk"),
        ("inv_quantity_on_hand", "invn_qty_on_hand"),
    ),
    "s_inventory "
    "LEFT OUTER JOIN warehouse ON (invn_warehouse_id = w_warehouse_id) "
    "LEFT OUTER JOIN item ON (invn_item_id = i_item_id AND i_rec_end_date IS NULL) "
    "LEFT OUTER JOIN date_dim ON (d_date = invn_date)",
)


def _sales_returns_delete(
    sales: str,
    returns: str,
    sales_date_sk: str,
    sales_key: str,
    sales_item: str,
    returns_key: str,
    returns_item: str,
) -> tuple[str, str]:
    in_range = _delete_range("delete_date")
    return (
        f"DELETE FROM {returns} WHERE EXISTS "
        f"(SELECT 1 FROM {sales}, date_dim, s_delete WHERE {sales_date_sk} = d_date_sk AND {in_range} "
        f"AND {sales_key} = {returns_key} AND {sales_item} = {returns_item})",
        f"DELETE FROM {sales} WHERE {sales_date_sk} IN (SELECT d_date_sk FROM date_dim, s_delete WHERE {in_range})",
    )


_DF_SS = _sales_returns_delete(
    "store_sales",
    "store_returns",
    "ss_sold_date_sk",
    "ss_ticket_number",
    "ss_item_sk",
    "sr_ticket_number",
    "sr_item_sk",
)
_DF_CS = _sales_returns_delete(
    "catalog_sales",
    "catalog_returns",
    "cs_sold_date_sk",
    "cs_order_number",
    "cs_item_sk",
    "cr_order_number",
    "cr_item_sk",
)
_DF_WS = _sales_returns_delete(
    "web_sales", "web_returns", "ws_sold_date_sk", "ws_order_number", "ws_item_sk", "wr_order_number", "wr_item_sk"
)
_DF_I = (
    "DELETE FROM inventory WHERE inv_date_sk IN "
    f"(SELECT d_date_sk FROM date_dim, s_inventory_delete WHERE {_delete_range('inv_delete_date')})",
)

MAINTENANCE_FUNCTIONS: tuple[MaintenanceFunction, ...] = (
    MaintenanceFunction(8, "DF_CS", METHOD_FACT_DELETE, ("catalog_returns", "catalog_sales"), ("s_delete",), _DF_CS),
    MaintenanceFunction(9, "DF_SS", METHOD_FACT_DELETE, ("store_returns", "store_sales"), ("s_delete",), _DF_SS),
    MaintenanceFunction(10, "DF_WS", METHOD_FACT_DELETE, ("web_returns", "web_sales"), ("s_delete",), _DF_WS),
    MaintenanceFunction(11, "DF_I", METHOD_INVENTORY_DELETE, ("inventory",), ("s_inventory_delete",), _DF_I),
    MaintenanceFunction(
        1,
        "LF_CR",
        METHOD_FACT_INSERT,
        ("catalog_returns",),
        ("s_catalog_returns",),
        (_LF_CR,),
        "s_catalog_returns",
    ),
    MaintenanceFunction(
        2,
        "LF_CS",
        METHOD_FACT_INSERT,
        ("catalog_sales",),
        ("s_catalog_order", "s_catalog_order_lineitem"),
        (_LF_CS,),
        "s_catalog_order_lineitem",
    ),
    MaintenanceFunction(3, "LF_I", METHOD_FACT_INSERT, ("inventory",), ("s_inventory",), (_LF_I,), "s_inventory"),
    MaintenanceFunction(
        4, "LF_SR", METHOD_FACT_INSERT, ("store_returns",), ("s_store_returns",), (_LF_SR,), "s_store_returns"
    ),
    MaintenanceFunction(
        5,
        "LF_SS",
        METHOD_FACT_INSERT,
        ("store_sales",),
        ("s_purchase", "s_purchase_lineitem"),
        (_LF_SS,),
        "s_purchase_lineitem",
    ),
    MaintenanceFunction(
        6, "LF_WR", METHOD_FACT_INSERT, ("web_returns",), ("s_web_returns",), (_LF_WR,), "s_web_returns"
    ),
    MaintenanceFunction(
        7,
        "LF_WS",
        METHOD_FACT_INSERT,
        ("web_sales",),
        ("s_web_order", "s_web_order_lineitem"),
        (_LF_WS,),
        "s_web_order_lineitem",
    ),
)

MAINTENANCE_FUNCTIONS_BY_NAME: Mapping[str, MaintenanceFunction] = {
    function.name: function for function in MAINTENANCE_FUNCTIONS
}
