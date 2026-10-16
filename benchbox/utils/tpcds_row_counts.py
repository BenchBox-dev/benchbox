from __future__ import annotations

TPCDS_SPEC_SCALES: tuple[int, ...] = (1, 10, 100)

TPCDS_ROW_COUNTS: dict[str, tuple[int, int, int]] = {
    "call_center": (6, 24, 30),
    "catalog_page": (11_718, 12_000, 20_400),
    "catalog_returns": (144_067, 1_439_749, 14_404_374),
    "catalog_sales": (1_441_548, 14_401_261, 143_997_065),
    "customer": (100_000, 500_000, 2_000_000),
    "customer_address": (50_000, 250_000, 1_000_000),
    "customer_demographics": (1_920_800, 1_920_800, 1_920_800),
    "date_dim": (73_049, 73_049, 73_049),
    "household_demographics": (7_200, 7_200, 7_200),
    "income_band": (20, 20, 20),
    "inventory": (11_745_000, 133_110_000, 399_330_000),
    "item": (18_000, 102_000, 204_000),
    "promotion": (300, 500, 1_000),
    "reason": (75, 75, 75),
    "ship_mode": (20, 20, 20),
    "store": (12, 102, 402),
    "store_returns": (287_514, 2_875_432, 28_795_080),
    "store_sales": (2_880_404, 28_800_991, 287_997_024),
    "time_dim": (86_400, 86_400, 86_400),
    "warehouse": (5, 10, 15),
    "web_page": (60, 200, 2_040),
    "web_returns": (71_763, 719_217, 7_197_670),
    "web_sales": (719_384, 7_197_566, 72_001_237),
    "web_site": (30, 42, 24),
}

TPCDS_LINEAR_TABLES: frozenset[str] = frozenset(
    {"store_sales", "catalog_sales", "web_sales", "store_returns", "catalog_returns", "web_returns"}
)


def tpcds_spec_row_count(table: str, scale_factor: float) -> int | None:
    rows = TPCDS_ROW_COUNTS.get(table.lower())
    scale = float(scale_factor)
    if rows is None or not scale.is_integer() or int(scale) not in TPCDS_SPEC_SCALES:
        return None
    return rows[TPCDS_SPEC_SCALES.index(int(scale))]


TPCDS_SUBSCALE_FIXED_TABLES: frozenset[str] = frozenset(
    {"catalog_page", "date_dim", "household_demographics", "income_band", "ship_mode", "time_dim"}
)

TPCDS_INVENTORY_WEEKS = 261


def tpcds_subscale_row_count(table: str, scale_factor: float) -> int | None:
    name = table.lower()
    rows = TPCDS_ROW_COUNTS.get(name)
    scale = float(scale_factor)
    if rows is None or not 0 < scale < 1:
        return None
    if name in TPCDS_SUBSCALE_FIXED_TABLES:
        return rows[0]
    if name == "inventory":
        items = tpcds_subscale_row_count("item", scale) // 2
        return items * tpcds_subscale_row_count("warehouse", scale) * TPCDS_INVENTORY_WEEKS
    return max(1, int(rows[0] * scale))
