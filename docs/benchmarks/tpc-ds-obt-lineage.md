# TPC-DS OBT column lineage

This page is generated from `benchbox/core/tpcds_obt/model.yaml`, the declarative model that drives the ETL, the
`CREATE TABLE` statement and this page. Regenerate it with
`uv run -- python -m benchbox.core.tpcds_obt.lineage > docs/benchmarks/tpc-ds-obt-lineage.md`.

`tpcds_sales_returns_obt` has 715 columns at `dimension_mode=full`. Each channel contributes the rows of its
sales table, left-joined to the matching row of its returns table on the order or ticket number and the item.

## Channels

| Channel | Sales table | Returns table | Returns join |
| --- | --- | --- | --- |
| `store` | `store_sales` | `store_returns` | `ss.ss_ticket_number = sr.sr_ticket_number AND ss.ss_item_sk = sr.sr_item_sk` |
| `web` | `web_sales` | `web_returns` | `ws.ws_order_number = wr.wr_order_number AND ws.ws_item_sk = wr.wr_item_sk` |
| `catalog` | `catalog_sales` | `catalog_returns` | `cs.cs_order_number = cr.cr_order_number AND cs.cs_item_sk = cr.cr_item_sk` |

## Fact columns

A channel with no source writes NULL.

| Column | Type | Store | Web | Catalog |
| --- | --- | --- | --- | --- |
| `channel` | VARCHAR(10) | `'store'` | `'web'` | `'catalog'` |
| `sale_id` | INTEGER | `ss.ss_ticket_number` | `ws.ws_order_number` | `cs.cs_order_number` |
| `item_sk` | INTEGER | `ss.ss_item_sk` | `ws.ws_item_sk` | `cs.cs_item_sk` |
| `sold_date_sk` | INTEGER | `ss.ss_sold_date_sk` | `ws.ws_sold_date_sk` | `cs.cs_sold_date_sk` |
| `sold_time_sk` | INTEGER | `ss.ss_sold_time_sk` | `ws.ws_sold_time_sk` | `cs.cs_sold_time_sk` |
| `ship_date_sk` | INTEGER | NULL | `ws.ws_ship_date_sk` | `cs.cs_ship_date_sk` |
| `returned_date_sk` | INTEGER | `sr.sr_returned_date_sk` | `wr.wr_returned_date_sk` | `cr.cr_returned_date_sk` |
| `returned_time_sk` | INTEGER | `sr.sr_return_time_sk` | `wr.wr_returned_time_sk` | `cr.cr_returned_time_sk` |
| `bill_customer_sk` | INTEGER | `ss.ss_customer_sk` | `ws.ws_bill_customer_sk` | `cs.cs_bill_customer_sk` |
| `bill_cdemo_sk` | INTEGER | `ss.ss_cdemo_sk` | `ws.ws_bill_cdemo_sk` | `cs.cs_bill_cdemo_sk` |
| `bill_hdemo_sk` | INTEGER | `ss.ss_hdemo_sk` | `ws.ws_bill_hdemo_sk` | `cs.cs_bill_hdemo_sk` |
| `bill_addr_sk` | INTEGER | `ss.ss_addr_sk` | `ws.ws_bill_addr_sk` | `cs.cs_bill_addr_sk` |
| `ship_customer_sk` | INTEGER | `ss.ss_customer_sk` | `ws.ws_ship_customer_sk` | `cs.cs_ship_customer_sk` |
| `ship_cdemo_sk` | INTEGER | `ss.ss_cdemo_sk` | `ws.ws_ship_cdemo_sk` | `cs.cs_ship_cdemo_sk` |
| `ship_hdemo_sk` | INTEGER | `ss.ss_hdemo_sk` | `ws.ws_ship_hdemo_sk` | `cs.cs_ship_hdemo_sk` |
| `ship_addr_sk` | INTEGER | `ss.ss_addr_sk` | `ws.ws_ship_addr_sk` | `cs.cs_ship_addr_sk` |
| `store_sk` | INTEGER | `ss.ss_store_sk` | NULL | NULL |
| `web_site_sk` | INTEGER | NULL | `ws.ws_web_site_sk` | NULL |
| `web_page_sk` | INTEGER | NULL | `ws.ws_web_page_sk` | NULL |
| `call_center_sk` | INTEGER | NULL | NULL | `cs.cs_call_center_sk` |
| `catalog_page_sk` | INTEGER | NULL | NULL | `cs.cs_catalog_page_sk` |
| `ship_mode_sk` | INTEGER | NULL | `ws.ws_ship_mode_sk` | `cs.cs_ship_mode_sk` |
| `warehouse_sk` | INTEGER | NULL | `ws.ws_warehouse_sk` | `cs.cs_warehouse_sk` |
| `promo_sk` | INTEGER | `ss.ss_promo_sk` | `ws.ws_promo_sk` | `cs.cs_promo_sk` |
| `reason_sk` | INTEGER | `sr.sr_reason_sk` | `wr.wr_reason_sk` | `cr.cr_reason_sk` |
| `quantity` | INTEGER | `ss.ss_quantity` | `ws.ws_quantity` | `cs.cs_quantity` |
| `wholesale_cost` | DECIMAL(15,2) | `ss.ss_wholesale_cost` | `ws.ws_wholesale_cost` | `cs.cs_wholesale_cost` |
| `list_price` | DECIMAL(15,2) | `ss.ss_list_price` | `ws.ws_list_price` | `cs.cs_list_price` |
| `sales_price` | DECIMAL(15,2) | `ss.ss_sales_price` | `ws.ws_sales_price` | `cs.cs_sales_price` |
| `ext_discount_amt` | DECIMAL(15,2) | `ss.ss_ext_discount_amt` | `ws.ws_ext_discount_amt` | `cs.cs_ext_discount_amt` |
| `ext_sales_price` | DECIMAL(15,2) | `ss.ss_ext_sales_price` | `ws.ws_ext_sales_price` | `cs.cs_ext_sales_price` |
| `ext_wholesale_cost` | DECIMAL(15,2) | `ss.ss_ext_wholesale_cost` | `ws.ws_ext_wholesale_cost` | `cs.cs_ext_wholesale_cost` |
| `ext_list_price` | DECIMAL(15,2) | `ss.ss_ext_list_price` | `ws.ws_ext_list_price` | `cs.cs_ext_list_price` |
| `ext_tax` | DECIMAL(15,2) | `ss.ss_ext_tax` | `ws.ws_ext_tax` | `cs.cs_ext_tax` |
| `coupon_amt` | DECIMAL(15,2) | `ss.ss_coupon_amt` | `ws.ws_coupon_amt` | `cs.cs_coupon_amt` |
| `ext_ship_cost` | DECIMAL(15,2) | NULL | `ws.ws_ext_ship_cost` | `cs.cs_ext_ship_cost` |
| `net_paid` | DECIMAL(15,2) | `ss.ss_net_paid` | `ws.ws_net_paid` | `cs.cs_net_paid` |
| `net_paid_inc_tax` | DECIMAL(15,2) | `ss.ss_net_paid_inc_tax` | `ws.ws_net_paid_inc_tax` | `cs.cs_net_paid_inc_tax` |
| `net_paid_inc_ship` | DECIMAL(15,2) | NULL | `ws.ws_net_paid_inc_ship` | `cs.cs_net_paid_inc_ship` |
| `net_paid_inc_ship_tax` | DECIMAL(15,2) | NULL | `ws.ws_net_paid_inc_ship_tax` | `cs.cs_net_paid_inc_ship_tax` |
| `net_profit` | DECIMAL(15,2) | `ss.ss_net_profit` | `ws.ws_net_profit` | `cs.cs_net_profit` |
| `return_quantity` | INTEGER | `sr.sr_return_quantity` | `wr.wr_return_quantity` | `cr.cr_return_quantity` |
| `return_amount` | DECIMAL(15,2) | `sr.sr_return_amt` | `wr.wr_return_amt` | `cr.cr_return_amount` |
| `return_tax` | DECIMAL(15,2) | `sr.sr_return_tax` | `wr.wr_return_tax` | `cr.cr_return_tax` |
| `return_amount_inc_tax` | DECIMAL(15,2) | `sr.sr_return_amt_inc_tax` | `wr.wr_return_amt_inc_tax` | `cr.cr_return_amt_inc_tax` |
| `return_fee` | DECIMAL(15,2) | `sr.sr_fee` | `wr.wr_fee` | `cr.cr_fee` |
| `return_ship_cost` | DECIMAL(15,2) | `sr.sr_return_ship_cost` | `wr.wr_return_ship_cost` | `cr.cr_return_ship_cost` |
| `refunded_cash` | DECIMAL(15,2) | `sr.sr_refunded_cash` | `wr.wr_refunded_cash` | `cr.cr_refunded_cash` |
| `reversed_charge` | DECIMAL(15,2) | `sr.sr_reversed_charge` | `wr.wr_reversed_charge` | `cr.cr_reversed_charge` |
| `account_credit` | DECIMAL(15,2) | NULL | `wr.wr_account_credit` | NULL |
| `store_credit` | DECIMAL(15,2) | `sr.sr_store_credit` | NULL | `cr.cr_store_credit` |
| `return_net_loss` | DECIMAL(15,2) | `sr.sr_net_loss` | `wr.wr_net_loss` | `cr.cr_net_loss` |
| `has_return` | CHAR(1) | `CASE WHEN sr.sr_ticket_number IS NULL THEN 'N' ELSE 'Y' END` | `CASE WHEN wr.wr_order_number IS NULL THEN 'N' ELSE 'Y' END` | `CASE WHEN cr.cr_order_number IS NULL THEN 'N' ELSE 'Y' END` |

## Dimension roles

Each role adds every column of its source table under the role prefix. Roles marked with an income band also add `<prefix>ib_lower_bound` and `<prefix>ib_upper_bound` from `income_band`. A role that joins through another role reads its key from that role's columns, so it needs no key on the fact row.

| Role | Prefix | Source table | Join key | Columns |
| --- | --- | --- | --- | --- |
| `sold_date` | `sold_date_` | `date_dim` | store: `ss.ss_sold_date_sk`<br>web: `ws.ws_sold_date_sk`<br>catalog: `cs.cs_sold_date_sk` | 28 |
| `sold_time` | `sold_time_` | `time_dim` | store: `ss.ss_sold_time_sk`<br>web: `ws.ws_sold_time_sk`<br>catalog: `cs.cs_sold_time_sk` | 10 |
| `ship_date` | `ship_date_` | `date_dim` | web: `ws.ws_ship_date_sk`<br>catalog: `cs.cs_ship_date_sk` | 28 |
| `return_date` | `return_date_` | `date_dim` | store: `sr.sr_returned_date_sk`<br>web: `wr.wr_returned_date_sk`<br>catalog: `cr.cr_returned_date_sk` | 28 |
| `return_time` | `return_time_` | `time_dim` | store: `sr.sr_return_time_sk`<br>web: `wr.wr_returned_time_sk`<br>catalog: `cr.cr_returned_time_sk` | 10 |
| `item` | `item_` | `item` | store: `ss.ss_item_sk`<br>web: `ws.ws_item_sk`<br>catalog: `cs.cs_item_sk` | 22 |
| `promotion` | `promo_` | `promotion` | store: `ss.ss_promo_sk`<br>web: `ws.ws_promo_sk`<br>catalog: `cs.cs_promo_sk` | 19 |
| `reason` | `reason_` | `reason` | store: `sr.sr_reason_sk`<br>web: `wr.wr_reason_sk`<br>catalog: `cr.cr_reason_sk` | 3 |
| `store` | `store_` | `store` | store: `ss.ss_store_sk` | 29 |
| `web_site` | `web_site_` | `web_site` | web: `ws.ws_web_site_sk` | 26 |
| `web_page` | `web_page_` | `web_page` | web: `ws.ws_web_page_sk` | 14 |
| `call_center` | `call_center_` | `call_center` | catalog: `cs.cs_call_center_sk` | 31 |
| `catalog_page` | `catalog_page_` | `catalog_page` | catalog: `cs.cs_catalog_page_sk` | 9 |
| `ship_mode` | `ship_mode_` | `ship_mode` | web: `ws.ws_ship_mode_sk`<br>catalog: `cs.cs_ship_mode_sk` | 6 |
| `warehouse` | `warehouse_` | `warehouse` | web: `ws.ws_warehouse_sk`<br>catalog: `cs.cs_warehouse_sk` | 14 |
| `bill_customer` | `bill_customer_` | `customer` | store: `ss.ss_customer_sk`<br>web: `ws.ws_bill_customer_sk`<br>catalog: `cs.cs_bill_customer_sk` | 18 |
| `ship_customer` | `ship_customer_` | `customer` | store: `ss.ss_customer_sk`<br>web: `ws.ws_ship_customer_sk`<br>catalog: `cs.cs_ship_customer_sk` | 18 |
| `returning_customer` | `returning_customer_` | `customer` | store: `sr.sr_customer_sk`<br>web: `wr.wr_returning_customer_sk`<br>catalog: `cr.cr_returning_customer_sk` | 18 |
| `refunded_customer` | `refunded_customer_` | `customer` | store: `sr.sr_customer_sk`<br>web: `wr.wr_refunded_customer_sk`<br>catalog: `cr.cr_refunded_customer_sk` | 18 |
| `bill_cdemo` | `bill_cdemo_` | `customer_demographics` | store: `ss.ss_cdemo_sk`<br>web: `ws.ws_bill_cdemo_sk`<br>catalog: `cs.cs_bill_cdemo_sk` | 9 |
| `ship_cdemo` | `ship_cdemo_` | `customer_demographics` | store: `ss.ss_cdemo_sk`<br>web: `ws.ws_ship_cdemo_sk`<br>catalog: `cs.cs_ship_cdemo_sk` | 9 |
| `returning_cdemo` | `returning_cdemo_` | `customer_demographics` | store: `sr.sr_cdemo_sk`<br>web: `wr.wr_returning_cdemo_sk`<br>catalog: `cr.cr_returning_cdemo_sk` | 9 |
| `refunded_cdemo` | `refunded_cdemo_` | `customer_demographics` | store: `sr.sr_cdemo_sk`<br>web: `wr.wr_refunded_cdemo_sk`<br>catalog: `cr.cr_refunded_cdemo_sk` | 9 |
| `bill_hdemo` | `bill_hdemo_` | `household_demographics` | store: `ss.ss_hdemo_sk`<br>web: `ws.ws_bill_hdemo_sk`<br>catalog: `cs.cs_bill_hdemo_sk` | 7 |
| `ship_hdemo` | `ship_hdemo_` | `household_demographics` | store: `ss.ss_hdemo_sk`<br>web: `ws.ws_ship_hdemo_sk`<br>catalog: `cs.cs_ship_hdemo_sk` | 7 |
| `returning_hdemo` | `returning_hdemo_` | `household_demographics` | store: `sr.sr_hdemo_sk`<br>web: `wr.wr_returning_hdemo_sk`<br>catalog: `cr.cr_returning_hdemo_sk` | 7 |
| `refunded_hdemo` | `refunded_hdemo_` | `household_demographics` | store: `sr.sr_hdemo_sk`<br>web: `wr.wr_refunded_hdemo_sk`<br>catalog: `cr.cr_refunded_hdemo_sk` | 7 |
| `bill_address` | `bill_addr_` | `customer_address` | store: `ss.ss_addr_sk`<br>web: `ws.ws_bill_addr_sk`<br>catalog: `cs.cs_bill_addr_sk` | 13 |
| `ship_address` | `ship_addr_` | `customer_address` | store: `ss.ss_addr_sk`<br>web: `ws.ws_ship_addr_sk`<br>catalog: `cs.cs_ship_addr_sk` | 13 |
| `returning_address` | `returning_addr_` | `customer_address` | store: `sr.sr_addr_sk`<br>web: `wr.wr_returning_addr_sk`<br>catalog: `cr.cr_returning_addr_sk` | 13 |
| `refunded_address` | `refunded_addr_` | `customer_address` | store: `sr.sr_addr_sk`<br>web: `wr.wr_refunded_addr_sk`<br>catalog: `cr.cr_refunded_addr_sk` | 13 |
| `bill_cur_addr` | `bill_cur_addr_` | `customer_address` | `bill_customer.c_current_addr_sk` | 13 |
| `bill_cur_cdemo` | `bill_cur_cdemo_` | `customer_demographics` | `bill_customer.c_current_cdemo_sk` | 9 |
| `bill_cur_hdemo` | `bill_cur_hdemo_` | `household_demographics` | `bill_customer.c_current_hdemo_sk` | 7 |
| `bill_first_sales_date` | `bill_first_sales_date_` | `date_dim` | `bill_customer.c_first_sales_date_sk` | 28 |
| `bill_first_shipto_date` | `bill_first_shipto_date_` | `date_dim` | `bill_customer.c_first_shipto_date_sk` | 28 |
| `returning_cur_addr` | `returning_cur_addr_` | `customer_address` | `returning_customer.c_current_addr_sk` | 13 |
| `returning_cur_cdemo` | `returning_cur_cdemo_` | `customer_demographics` | `returning_customer.c_current_cdemo_sk` | 9 |
| `returning_cur_hdemo` | `returning_cur_hdemo_` | `household_demographics` | `returning_customer.c_current_hdemo_sk` | 7 |
| `return_store` | `return_store_` | `store` | store: `sr.sr_store_sk` | 29 |
| `return_call_center` | `return_call_center_` | `call_center` | catalog: `cr.cr_call_center_sk` | 31 |
| `return_catalog_page` | `return_catalog_page_` | `catalog_page` | catalog: `cr.cr_catalog_page_sk` | 9 |
| `return_web_page` | `return_web_page_` | `web_page` | web: `wr.wr_web_page_sk` | 14 |

## Coverage

The ETL fails when a full-width build at scale factor 1 or above breaks the coverage the queries rely on: every calendar day from 1998-01-01 to 2002-12-31 has a sale and every item appears in at least one row.
