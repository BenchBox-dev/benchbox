from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
import yaml

from benchbox.core.amplab.schema import get_all_create_table_sql as amplab_ddl
from benchbox.core.clickbench.schema import get_create_table_sql as clickbench_ddl
from benchbox.core.h2odb.schema import get_all_create_table_sql as h2odb_ddl
from benchbox.core.joinorder.schema import JoinOrderSchema
from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.ssb.schema import get_all_create_table_sql as ssb_ddl
from benchbox.core.tpcds.schema import get_create_all_tables_sql as tpcds_ddl
from benchbox.core.tpch.schema import get_create_all_tables_sql as tpch_ddl
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.core.tuning.packaged_templates import packaged_template_path

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
TPCDS_SPEC_SQL = "_sources/tpc-ds/tools/tpcds.sql"
TPCH_SPEC_RI = "_sources/tpc-h/dbgen/dss.ri"

TPCH_KEYS: dict[str, tuple[str, ...]] = {
    "region": ("r_regionkey",),
    "nation": ("n_nationkey",),
    "part": ("p_partkey",),
    "supplier": ("s_suppkey",),
    "partsupp": ("ps_partkey", "ps_suppkey"),
    "customer": ("c_custkey",),
    "orders": ("o_orderkey",),
    "lineitem": ("l_orderkey", "l_linenumber"),
}

TPCDS_KEYS: dict[str, tuple[str, ...]] = {
    "call_center": ("cc_call_center_sk",),
    "catalog_page": ("cp_catalog_page_sk",),
    "catalog_returns": ("cr_item_sk", "cr_order_number"),
    "catalog_sales": ("cs_item_sk", "cs_order_number"),
    "customer": ("c_customer_sk",),
    "customer_address": ("ca_address_sk",),
    "customer_demographics": ("cd_demo_sk",),
    "date_dim": ("d_date_sk",),
    "dbgen_version": (),
    "household_demographics": ("hd_demo_sk",),
    "income_band": ("ib_income_band_sk",),
    "inventory": ("inv_date_sk", "inv_item_sk", "inv_warehouse_sk"),
    "item": ("i_item_sk",),
    "promotion": ("p_promo_sk",),
    "reason": ("r_reason_sk",),
    "ship_mode": ("sm_ship_mode_sk",),
    "store": ("s_store_sk",),
    "store_returns": ("sr_item_sk", "sr_ticket_number"),
    "store_sales": ("ss_item_sk", "ss_ticket_number"),
    "time_dim": ("t_time_sk",),
    "warehouse": ("w_warehouse_sk",),
    "web_page": ("wp_web_page_sk",),
    "web_returns": ("wr_item_sk", "wr_order_number"),
    "web_sales": ("ws_item_sk", "ws_order_number"),
    "web_site": ("web_site_sk",),
}

SSB_KEYS: dict[str, tuple[str, ...]] = {
    "date": ("d_datekey",),
    "customer": ("c_custkey",),
    "supplier": ("s_suppkey",),
    "part": ("p_partkey",),
    "lineorder": ("lo_orderkey", "lo_linenumber"),
}

JOINORDER_TABLES = (
    "aka_name",
    "aka_title",
    "cast_info",
    "char_name",
    "comp_cast_type",
    "company_name",
    "company_type",
    "complete_cast",
    "info_type",
    "keyword",
    "kind_type",
    "link_type",
    "movie_companies",
    "movie_info",
    "movie_info_idx",
    "movie_keyword",
    "movie_link",
    "name",
    "person_info",
    "role_type",
    "title",
)
JOINORDER_KEYS: dict[str, tuple[str, ...]] = dict.fromkeys(JOINORDER_TABLES, ("id",))

CLICKBENCH_KEYS: dict[str, tuple[str, ...]] = {
    "hits": ("CounterID", "EventDate", "UserID", "EventTime", "WatchID"),
}

AMPLAB_KEYS: dict[str, tuple[str, ...]] = {
    "rankings": ("pageURL",),
    "documents": ("url",),
    "uservisits": (),
}

H2ODB_KEYS: dict[str, tuple[str, ...]] = {
    "trips": (),
}

EXPECTED_KEYS: dict[str, dict[str, tuple[str, ...]]] = {
    "tpch": TPCH_KEYS,
    "tpcds": TPCDS_KEYS,
    "ssb": SSB_KEYS,
    "joinorder": JOINORDER_KEYS,
    "clickbench": CLICKBENCH_KEYS,
    "amplab": AMPLAB_KEYS,
    "h2odb": H2ODB_KEYS,
    "read_primitives": TPCH_KEYS,
    "tpchavoc": TPCH_KEYS,
}

TPCH_DERIVED_BENCHMARKS = ("read_primitives", "tpchavoc")

KEY_SOURCES: dict[str, str] = {
    "tpch": TPCH_SPEC_RI,
    "tpcds": TPCDS_SPEC_SQL,
    "ssb": "O'Neil et al., Star Schema Benchmark: LINEORDER is keyed by order and line number; dimensions by surrogate key",
    "joinorder": "gregrahn/join-order-benchmark schema.sql: every table has an integer id primary key",
    "clickbench": "ClickHouse/ClickBench duckdb/create.sql: hits primary key over the five listed columns",
    "amplab": "BenchBox definition: rankings and documents keyed by URL; the AMPLab uservisits table has no key",
    "h2odb": "BenchBox definition: the H2O trips table has no key",
    "read_primitives": "benchbox/core/read_primitives/schema.py builds TABLES from the TPC-H schema; keys are _sources/tpc-h/dbgen/dss.ri",
    "tpchavoc": "benchbox/core/tpchavoc inherits the TPC-H schema unchanged; keys are _sources/tpc-h/dbgen/dss.ri",
}


def _tuned_benchmark_ddl(benchmark_class: type, name: str) -> str:
    template = packaged_template_path("duckdb", name)
    tuning = UnifiedTuningConfiguration.from_dict(yaml.safe_load(template.read_text(encoding="utf-8")))
    return benchmark_class(scale_factor=0.01).get_create_tables_sql(dialect="duckdb", tuning_config=tuning)


DDL_BUILDERS: dict[str, Callable[[], str]] = {
    "tpch": tpch_ddl,
    "tpcds": tpcds_ddl,
    "ssb": ssb_ddl,
    "joinorder": lambda: JoinOrderSchema().get_create_tables_sql("duckdb"),
    "clickbench": clickbench_ddl,
    "amplab": amplab_ddl,
    "h2odb": h2odb_ddl,
    "read_primitives": lambda: _tuned_benchmark_ddl(ReadPrimitivesBenchmark, "read_primitives"),
    "tpchavoc": lambda: _tuned_benchmark_ddl(TPCHavocBenchmark, "tpchavoc"),
}

DUCKDB_TEMPLATE_BENCHMARKS = {
    "tpch",
    "tpcds",
    "ssb",
    "joinorder",
    "clickbench",
    "amplab",
    "h2odb",
    "read_primitives",
    "tpchavoc",
}


def _duckdb_primary_keys(ddl: str) -> dict[str, tuple[str, ...]]:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(ddl)
        names = [name for (name,) in connection.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
        rows = connection.execute(
            "SELECT table_name, constraint_column_names FROM duckdb_constraints() WHERE constraint_type = 'PRIMARY KEY'"
        ).fetchall()
    finally:
        connection.close()
    keys: dict[str, tuple[str, ...]] = dict.fromkeys(names, ())
    keys.update({name: tuple(columns) for name, columns in rows})
    return keys


def _spec_tpcds_keys() -> dict[str, tuple[str, ...]]:
    text = (REPO_ROOT / TPCDS_SPEC_SQL).read_text(encoding="utf-8")
    keys: dict[str, tuple[str, ...]] = {}
    for block in re.split(r"(?i)\bcreate\s+table\s+", text)[1:]:
        name = re.match(r"(\w+)", block).group(1).lower()
        match = re.search(r"(?i)primary\s+key\s*\(([^)]*)\)", block)
        keys[name] = tuple(column.strip().lower() for column in match.group(1).split(",")) if match else ()
    return keys


def _spec_tpch_keys() -> dict[str, tuple[str, ...]]:
    text = (REPO_ROOT / TPCH_SPEC_RI).read_text(encoding="utf-8")
    pattern = re.compile(r"(?im)^ALTER TABLE TPCD\.(\w+)\s*\n\s*ADD PRIMARY KEY\s*\(([^)]*)\)")
    return {
        name.lower(): tuple(column.strip().lower() for column in columns.split(","))
        for name, columns in pattern.findall(text)
    }


def test_every_duckdb_template_benchmark_is_pinned() -> None:
    templates = {
        path.name.removesuffix("_tuned.yaml")
        for path in (REPO_ROOT / "benchbox/core/tuning/templates/duckdb").glob("*_tuned.yaml")
    }
    assert templates == DUCKDB_TEMPLATE_BENCHMARKS
    assert set(EXPECTED_KEYS) == set(DDL_BUILDERS) == set(KEY_SOURCES) == DUCKDB_TEMPLATE_BENCHMARKS


@pytest.mark.parametrize("suite", sorted(EXPECTED_KEYS))
def test_generated_ddl_declares_the_pinned_primary_keys(suite: str) -> None:
    assert _duckdb_primary_keys(DDL_BUILDERS[suite]()) == EXPECTED_KEYS[suite]


def test_tpcds_keys_match_the_vendored_specification_ddl() -> None:
    assert _spec_tpcds_keys() == TPCDS_KEYS


def test_tpch_keys_match_the_vendored_specification_ddl() -> None:
    assert _spec_tpch_keys() == TPCH_KEYS


@pytest.mark.parametrize("suite", TPCH_DERIVED_BENCHMARKS)
def test_tpch_derived_benchmark_keys_match_the_vendored_specification_ddl(suite: str) -> None:
    assert EXPECTED_KEYS[suite] == _spec_tpch_keys()


@pytest.mark.parametrize("suite", ["tpch", "tpcds"])
def test_vendored_specification_sources_exist(suite: str) -> None:
    assert (REPO_ROOT / KEY_SOURCES[suite]).is_file()


@pytest.mark.parametrize(
    "table",
    ["store_sales", "store_returns", "catalog_sales", "catalog_returns", "web_sales", "web_returns", "inventory"],
)
def test_tpcds_rows_sharing_an_order_number_but_not_an_item_are_distinct(table: str) -> None:
    keys = TPCDS_KEYS[table]
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(tpcds_ddl(enable_foreign_keys=False))
        names = ", ".join(keys)
        connection.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('1' for _ in keys)})")
        connection.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join(['2'] + ['1'] * (len(keys) - 1))})")
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (2,)
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('1' for _ in keys)})")
    finally:
        connection.close()


