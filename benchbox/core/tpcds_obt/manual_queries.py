from __future__ import annotations

from dataclasses import dataclass

Q14_TEMPLATE = """\
WITH cross_items AS (
  SELECT
    item_i_item_sk,
    item_i_brand_id,
    item_i_class_id,
    item_i_category_id
  FROM (
    SELECT
      item_i_item_sk,
      item_i_brand_id,
      item_i_class_id,
      item_i_category_id,
      channel
    FROM tpcds_sales_returns_obt
    WHERE sold_date_d_year BETWEEN [YEAR] AND [YEAR] + 2
    GROUP BY item_i_item_sk, item_i_brand_id, item_i_class_id, item_i_category_id, channel
  ) items_by_channel
  GROUP BY item_i_item_sk, item_i_brand_id, item_i_class_id, item_i_category_id
  HAVING COUNT(DISTINCT channel) = 3
),
avg_sales AS (
  SELECT AVG(quantity * list_price) AS average_sales
  FROM tpcds_sales_returns_obt
  WHERE sold_date_d_year BETWEEN [YEAR] AND [YEAR] + 2
)
SELECT
  channel,
  i_brand_id,
  i_class_id,
  i_category_id,
  SUM(sales),
  SUM(number_sales)
FROM (
  SELECT
    channel,
    item_i_brand_id AS i_brand_id,
    item_i_class_id AS i_class_id,
    item_i_category_id AS i_category_id,
    SUM(quantity * list_price) AS sales,
    COUNT(*) AS number_sales
  FROM tpcds_sales_returns_obt
  WHERE item_sk IN (SELECT item_i_item_sk FROM cross_items)
    AND sold_date_d_year = [YEAR] + 2
    AND sold_date_d_moy = 11
  GROUP BY channel, item_i_brand_id, item_i_class_id, item_i_category_id
  HAVING SUM(quantity * list_price) > (SELECT average_sales FROM avg_sales)
) y
GROUP BY ROLLUP (channel, i_brand_id, i_class_id, i_category_id)
ORDER BY channel, i_brand_id, i_class_id, i_category_id
LIMIT 100
"""

Q46_TEMPLATE = """\
SELECT
  c_last_name,
  c_first_name,
  current_addr.ca_city,
  dn.bought_city,
  dn.ss_ticket_number,
  dn.amt,
  dn.profit
FROM (
  SELECT
    sale_id AS ss_ticket_number,
    bill_customer_sk AS ss_customer_sk,
    bill_addr_ca_city AS bought_city,
    SUM(coupon_amt) AS amt,
    SUM(net_profit) AS profit
  FROM tpcds_sales_returns_obt
  WHERE channel = 'store'
    AND sold_date_d_dow IN (6, 0)
    AND sold_date_d_year IN ([YEAR], [YEAR] + 1, [YEAR] + 2)
    AND store_s_city IN ('[CITY_A]', '[CITY_B]', '[CITY_C]', '[CITY_D]', '[CITY_E]')
    AND (bill_hdemo_hd_dep_count = [DEPCNT] OR bill_hdemo_hd_vehicle_count = [VEHCNT])
  GROUP BY sale_id, bill_customer_sk, bill_addr_sk, bill_addr_ca_city
) dn
INNER JOIN customer ON dn.ss_customer_sk = customer.c_customer_sk
INNER JOIN customer_address current_addr ON customer.c_current_addr_sk = current_addr.ca_address_sk
WHERE current_addr.ca_city <> dn.bought_city
ORDER BY c_last_name, c_first_name, current_addr.ca_city, dn.bought_city, dn.ss_ticket_number
LIMIT 100
"""

Q49_TEMPLATE = """\
SELECT
  channel,
  item,
  return_ratio,
  return_rank,
  currency_rank
FROM (
  SELECT
    'web' AS channel,
    web.item,
    web.return_ratio,
    web.return_rank,
    web.currency_rank
  FROM (
    SELECT
      item,
      return_ratio,
      currency_ratio,
      RANK() OVER (ORDER BY return_ratio) AS return_rank,
      RANK() OVER (ORDER BY currency_ratio) AS currency_rank
    FROM (
      SELECT
        item_sk AS item,
        CAST(SUM(COALESCE(return_quantity, 0)) AS DECIMAL(15, 4))
          / NULLIF(CAST(SUM(COALESCE(quantity, 0)) AS DECIMAL(15, 4)), 0) AS return_ratio,
        CAST(SUM(COALESCE(return_amount, 0)) AS DECIMAL(15, 4))
          / NULLIF(CAST(SUM(COALESCE(net_paid, 0)) AS DECIMAL(15, 4)), 0) AS currency_ratio
      FROM tpcds_sales_returns_obt
      WHERE channel = 'web'
        AND return_amount > 10000
        AND net_profit > 1
        AND net_paid > 0
        AND quantity > 0
        AND sold_date_d_year = [YEAR]
        AND sold_date_d_moy = [MONTH]
      GROUP BY item_sk
    ) in_web
  ) web
  WHERE web.return_rank <= 10 OR web.currency_rank <= 10
  UNION
  SELECT
    'catalog' AS channel,
    catalog.item,
    catalog.return_ratio,
    catalog.return_rank,
    catalog.currency_rank
  FROM (
    SELECT
      item,
      return_ratio,
      currency_ratio,
      RANK() OVER (ORDER BY return_ratio) AS return_rank,
      RANK() OVER (ORDER BY currency_ratio) AS currency_rank
    FROM (
      SELECT
        item_sk AS item,
        CAST(SUM(COALESCE(return_quantity, 0)) AS DECIMAL(15, 4))
          / NULLIF(CAST(SUM(COALESCE(quantity, 0)) AS DECIMAL(15, 4)), 0) AS return_ratio,
        CAST(SUM(COALESCE(return_amount, 0)) AS DECIMAL(15, 4))
          / NULLIF(CAST(SUM(COALESCE(net_paid, 0)) AS DECIMAL(15, 4)), 0) AS currency_ratio
      FROM tpcds_sales_returns_obt
      WHERE channel = 'catalog'
        AND return_amount > 10000
        AND net_profit > 1
        AND net_paid > 0
        AND quantity > 0
        AND sold_date_d_year = [YEAR]
        AND sold_date_d_moy = [MONTH]
      GROUP BY item_sk
    ) in_cat
  ) catalog
  WHERE catalog.return_rank <= 10 OR catalog.currency_rank <= 10
  UNION
  SELECT
    'store' AS channel,
    store.item,
    store.return_ratio,
    store.return_rank,
    store.currency_rank
  FROM (
    SELECT
      item,
      return_ratio,
      currency_ratio,
      RANK() OVER (ORDER BY return_ratio) AS return_rank,
      RANK() OVER (ORDER BY currency_ratio) AS currency_rank
    FROM (
      SELECT
        item_sk AS item,
        CAST(SUM(COALESCE(return_quantity, 0)) AS DECIMAL(15, 4))
          / NULLIF(CAST(SUM(COALESCE(quantity, 0)) AS DECIMAL(15, 4)), 0) AS return_ratio,
        CAST(SUM(COALESCE(return_amount, 0)) AS DECIMAL(15, 4))
          / NULLIF(CAST(SUM(COALESCE(net_paid, 0)) AS DECIMAL(15, 4)), 0) AS currency_ratio
      FROM tpcds_sales_returns_obt
      WHERE channel = 'store'
        AND return_amount > 10000
        AND net_profit > 1
        AND net_paid > 0
        AND quantity > 0
        AND sold_date_d_year = [YEAR]
        AND sold_date_d_moy = [MONTH]
      GROUP BY item_sk
    ) in_store
  ) store
  WHERE store.return_rank <= 10 OR store.currency_rank <= 10
) combined
ORDER BY 1, 4, 5, 2
LIMIT 100
"""

Q64_TEMPLATE = """\
WITH cs_ui AS (
  -- Items from catalog channel where total sales > 2x total refunds
  SELECT
    item_sk AS cs_item_sk,
    SUM(ext_list_price) AS sale,
    SUM(COALESCE(return_amount_inc_tax, 0)) AS refund
  FROM tpcds_sales_returns_obt
  WHERE channel = 'catalog'
    AND has_return = 'Y'
  GROUP BY item_sk
  HAVING SUM(ext_list_price) > 2 * SUM(COALESCE(return_amount_inc_tax, 0))
),
cross_sales AS (
  SELECT
    obt.item_i_product_name AS product_name,
    obt.item_sk AS item_sk,
    obt.store_s_store_name AS store_name,
    obt.store_s_zip AS store_zip,
    obt.bill_addr_ca_street_number AS b_street_number,
    obt.bill_addr_ca_street_name AS b_street_name,
    obt.bill_addr_ca_city AS b_city,
    obt.bill_addr_ca_zip AS b_zip,
    ad2.ca_street_number AS c_street_number,
    ad2.ca_street_name AS c_street_name,
    ad2.ca_city AS c_city,
    ad2.ca_zip AS c_zip,
    obt.sold_date_d_year AS syear,
    d2.d_year AS fsyear,
    d3.d_year AS s2year,
    COUNT(*) AS cnt,
    SUM(obt.wholesale_cost) AS s1,
    SUM(obt.list_price) AS s2,
    SUM(obt.coupon_amt) AS s3
  FROM tpcds_sales_returns_obt obt
  INNER JOIN cs_ui ON obt.item_sk = cs_ui.cs_item_sk
  INNER JOIN customer c ON obt.bill_customer_sk = c.c_customer_sk
  INNER JOIN customer_demographics cd2 ON c.c_current_cdemo_sk = cd2.cd_demo_sk
  INNER JOIN household_demographics hd2 ON c.c_current_hdemo_sk = hd2.hd_demo_sk
  INNER JOIN customer_address ad2 ON c.c_current_addr_sk = ad2.ca_address_sk
  INNER JOIN income_band ib2 ON hd2.hd_income_band_sk = ib2.ib_income_band_sk
  INNER JOIN date_dim d2 ON c.c_first_sales_date_sk = d2.d_date_sk
  INNER JOIN date_dim d3 ON c.c_first_shipto_date_sk = d3.d_date_sk
  WHERE obt.channel = 'store'
    AND obt.has_return = 'Y'
    AND obt.bill_cdemo_cd_marital_status <> cd2.cd_marital_status
    AND obt.item_i_color IN ('[COLOR_1]', '[COLOR_2]', '[COLOR_3]', '[COLOR_4]', '[COLOR_5]', '[COLOR_6]')
    AND obt.item_i_current_price BETWEEN [PRICE] AND [PRICE] + 10
    AND obt.item_i_current_price BETWEEN [PRICE] + 1 AND [PRICE] + 15
  GROUP BY
    obt.item_i_product_name,
    obt.item_sk,
    obt.store_s_store_name,
    obt.store_s_zip,
    obt.bill_addr_ca_street_number,
    obt.bill_addr_ca_street_name,
    obt.bill_addr_ca_city,
    obt.bill_addr_ca_zip,
    ad2.ca_street_number,
    ad2.ca_street_name,
    ad2.ca_city,
    ad2.ca_zip,
    obt.sold_date_d_year,
    d2.d_year,
    d3.d_year
)
SELECT
  cs1.product_name,
  cs1.store_name,
  cs1.store_zip,
  cs1.b_street_number,
  cs1.b_street_name,
  cs1.b_city,
  cs1.b_zip,
  cs1.c_street_number,
  cs1.c_street_name,
  cs1.c_city,
  cs1.c_zip,
  cs1.syear,
  cs1.cnt,
  cs1.s1 AS s11,
  cs1.s2 AS s21,
  cs1.s3 AS s31,
  cs2.s1 AS s12,
  cs2.s2 AS s22,
  cs2.s3 AS s32,
  cs2.syear,
  cs2.cnt
FROM cross_sales cs1
INNER JOIN cross_sales cs2 ON cs1.item_sk = cs2.item_sk
  AND cs1.store_name = cs2.store_name
  AND cs1.store_zip = cs2.store_zip
WHERE cs1.syear = [YEAR]
  AND cs2.syear = [YEAR] + 1
  AND cs2.cnt <= cs1.cnt
ORDER BY cs1.product_name, cs1.store_name, cs2.cnt, cs1.s1, cs2.s1
"""

Q68_TEMPLATE = """\
SELECT
  c_last_name,
  c_first_name,
  current_addr.ca_city,
  dn.bought_city,
  dn.ss_ticket_number,
  dn.extended_price,
  dn.extended_tax,
  dn.list_price
FROM (
  SELECT
    sale_id AS ss_ticket_number,
    bill_customer_sk AS ss_customer_sk,
    bill_addr_ca_city AS bought_city,
    SUM(ext_sales_price) AS extended_price,
    SUM(ext_list_price) AS list_price,
    SUM(ext_tax) AS extended_tax
  FROM tpcds_sales_returns_obt
  WHERE channel = 'store'
    AND sold_date_d_dom BETWEEN 1 AND 2
    AND sold_date_d_year IN ([YEAR], [YEAR] + 1, [YEAR] + 2)
    AND store_s_city IN ('[CITY_A]', '[CITY_B]')
    AND (bill_hdemo_hd_dep_count = [DEPCNT] OR bill_hdemo_hd_vehicle_count = [VEHCNT])
  GROUP BY sale_id, bill_customer_sk, bill_addr_sk, bill_addr_ca_city
) dn
INNER JOIN customer ON dn.ss_customer_sk = customer.c_customer_sk
INNER JOIN customer_address current_addr ON customer.c_current_addr_sk = current_addr.ca_address_sk
WHERE current_addr.ca_city <> dn.bought_city
ORDER BY c_last_name, dn.ss_ticket_number
LIMIT 100
"""


Q84_TEMPLATE = """\
SELECT DISTINCT
  obt.bill_customer_c_customer_id AS customer_id,
  COALESCE(obt.bill_customer_c_last_name, '') || ', ' || COALESCE(obt.bill_customer_c_first_name, '') AS customername
FROM tpcds_sales_returns_obt obt
INNER JOIN customer_address ca ON obt.bill_customer_c_current_addr_sk = ca.ca_address_sk
INNER JOIN household_demographics hd ON obt.bill_customer_c_current_hdemo_sk = hd.hd_demo_sk
INNER JOIN income_band ib ON hd.hd_income_band_sk = ib.ib_income_band_sk
WHERE obt.channel = 'store'
  AND obt.has_return = 'Y'
  AND ca.ca_city = '[CITY]'
  AND ib.ib_lower_bound >= [INCOME]
  AND ib.ib_upper_bound <= [INCOME] + 50000
  AND obt.returning_cdemo_cd_demo_sk = obt.bill_customer_c_current_cdemo_sk
ORDER BY customer_id
LIMIT 100
"""


@dataclass(frozen=True)
class ManualQueryDefinition:
    query_id: int
    template_sql: str
    parameters: dict[str, tuple[str, str]]
    description: str


MANUAL_QUERY_DEFS: dict[int, ManualQueryDefinition] = {
    14: ManualQueryDefinition(
        query_id=14,
        template_sql=Q14_TEMPLATE,
        parameters={
            "YEAR": ("1998", "numeric"),
            "DAY": ("1", "numeric"),
        },
        description="Cross-channel item analysis with INTERSECT semantic",
    ),
    46: ManualQueryDefinition(
        query_id=46,
        template_sql=Q46_TEMPLATE,
        parameters={
            "DEPCNT": ("0", "numeric"),
            "YEAR": ("1998", "numeric"),
            "VEHCNT": ("-1", "numeric"),
            "CITY_A": ("Fairview", "string"),
            "CITY_B": ("Midway", "string"),
            "CITY_C": ("Fairview", "string"),
            "CITY_D": ("Fairview", "string"),
            "CITY_E": ("Fairview", "string"),
        },
        description="Store sales profitability by demographics (current vs purchase city)",
    ),
    49: ManualQueryDefinition(
        query_id=49,
        template_sql=Q49_TEMPLATE,
        parameters={
            "YEAR": ("1998", "numeric"),
            "MONTH": ("11", "numeric"),
        },
        description="Ranked item returns by channel",
    ),
    64: ManualQueryDefinition(
        query_id=64,
        template_sql=Q64_TEMPLATE,
        parameters={
            "YEAR": ("1999", "numeric"),
            "PRICE": ("0", "numeric"),
            "COLOR_1": ("purple", "string"),
            "COLOR_2": ("burlywood", "string"),
            "COLOR_3": ("indian", "string"),
            "COLOR_4": ("spring", "string"),
            "COLOR_5": ("floral", "string"),
            "COLOR_6": ("medium", "string"),
        },
        description="Cross-channel year-over-year analysis (catalog->store self-join)",
    ),
    68: ManualQueryDefinition(
        query_id=68,
        template_sql=Q68_TEMPLATE,
        parameters={
            "DEPCNT": ("0", "numeric"),
            "YEAR": ("1998", "numeric"),
            "VEHCNT": ("-1", "numeric"),
            "CITY_A": ("Fairview", "string"),
            "CITY_B": ("Midway", "string"),
        },
        description="Customer demographics and purchase history (current vs purchase city)",
    ),
    84: ManualQueryDefinition(
        query_id=84,
        template_sql=Q84_TEMPLATE,
        parameters={
            "CITY": ("Fairview", "string"),
            "INCOME": ("38128", "numeric"),
        },
        description="Customer income band filtering with store returns",
    ),
}

MANUAL_QUERY_IDS = frozenset({14, 49})


def get_manual_query(query_id: int) -> ManualQueryDefinition:
    if query_id not in MANUAL_QUERY_DEFS:
        raise ValueError(f"No manual query defined for query {query_id}")
    return MANUAL_QUERY_DEFS[query_id]


def render_manual_query(query_id: int, values: dict[str, str]) -> str:
    defn = get_manual_query(query_id)
    sql = defn.template_sql
    for name in defn.parameters:
        sql = sql.replace(f"[{name}]", str(values[name]))
    return sql


__all__ = [
    "MANUAL_QUERY_DEFS",
    "MANUAL_QUERY_IDS",
    "ManualQueryDefinition",
    "get_manual_query",
    "render_manual_query",
]
