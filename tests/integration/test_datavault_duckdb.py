# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from decimal import Decimal

import pytest

from benchbox.core.datavault import DataVaultBenchmark
from benchbox.core.datavault.queries import DataVaultQueryManager
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.schema import TABLES
from benchbox.utils.file_format import TRAILING_DUMMY_COLUMN

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


def _tpch_q1(params: dict[str, object]) -> str:
    delta = params["delta"]
    return f"""
                SELECT
                    l_returnflag,
                    l_linestatus,
                    SUM(l_quantity) AS sum_qty,
                    SUM(l_extendedprice) AS sum_base_price,
                    SUM(l_extendedprice * (1 - l_discount)) AS sum_disc_price,
                    SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)) AS sum_charge,
                    AVG(l_quantity) AS avg_qty,
                    AVG(l_extendedprice) AS avg_price,
                    AVG(l_discount) AS avg_disc,
                    COUNT(*) AS count_order
                FROM lineitem
                WHERE l_shipdate <= DATE '1998-12-01' - INTERVAL '{delta}' DAY
                GROUP BY l_returnflag, l_linestatus
                ORDER BY l_returnflag, l_linestatus
            """


def _tpch_q2(params: dict[str, object]) -> str:
    size = params["size"]
    type_suffix = params["type_suffix"]
    region = params["region"]
    return f"""
                SELECT
                    s_acctbal, s_name, n_name, p_partkey, p_mfgr,
                    s_address, s_phone, s_comment
                FROM part, supplier, partsupp, nation, region
                WHERE p_partkey = ps_partkey
                  AND s_suppkey = ps_suppkey
                  AND p_size = {size}
                  AND p_type LIKE '%{type_suffix}'
                  AND s_nationkey = n_nationkey
                  AND n_regionkey = r_regionkey
                  AND r_name = '{region}'
                  AND ps_supplycost = (
                      SELECT MIN(ps_supplycost)
                      FROM partsupp, supplier, nation, region
                      WHERE p_partkey = ps_partkey
                        AND s_suppkey = ps_suppkey
                        AND s_nationkey = n_nationkey
                        AND n_regionkey = r_regionkey
                        AND r_name = '{region}'
                  )
                ORDER BY s_acctbal DESC, n_name, s_name, p_partkey
                LIMIT 100
            """


def _tpch_q3(params: dict[str, object]) -> str:
    segment = params["segment"]
    date_str = params["date"]
    return f"""
                SELECT
                    l_orderkey,
                    SUM(l_extendedprice * (1 - l_discount)) AS revenue,
                    o_orderdate,
                    o_shippriority
                FROM customer, orders, lineitem
                WHERE c_mktsegment = '{segment}'
                  AND c_custkey = o_custkey
                  AND l_orderkey = o_orderkey
                  AND o_orderdate < DATE '{date_str}'
                  AND l_shipdate > DATE '{date_str}'
                GROUP BY l_orderkey, o_orderdate, o_shippriority
                ORDER BY revenue DESC, o_orderdate
                LIMIT 10
            """


def _tpch_q4(params: dict[str, object]) -> str:
    date_str = params["date"]
    return f"""
                SELECT
                    o_orderpriority,
                    COUNT(*) AS order_count
                FROM orders
                WHERE o_orderdate >= DATE '{date_str}'
                  AND o_orderdate < DATE '{date_str}' + INTERVAL '3' MONTH
                  AND EXISTS (
                      SELECT 1 FROM lineitem
                      WHERE l_orderkey = o_orderkey
                        AND l_commitdate < l_receiptdate
                  )
                GROUP BY o_orderpriority
                ORDER BY o_orderpriority
            """


def _tpch_q5(params: dict[str, object]) -> str:
    region = params["region"]
    date_str = params["date"]
    return f"""
                SELECT
                    n_name,
                    SUM(l_extendedprice * (1 - l_discount)) AS revenue
                FROM customer, orders, lineitem, supplier, nation, region
                WHERE c_custkey = o_custkey
                  AND l_orderkey = o_orderkey
                  AND l_suppkey = s_suppkey
                  AND c_nationkey = s_nationkey
                  AND s_nationkey = n_nationkey
                  AND n_regionkey = r_regionkey
                  AND r_name = '{region}'
                  AND o_orderdate >= DATE '{date_str}'
                  AND o_orderdate < DATE '{date_str}' + INTERVAL '1' YEAR
                GROUP BY n_name
                ORDER BY revenue DESC
            """


def _tpch_q6(params: dict[str, object]) -> str:
    date_str = params["date"]
    discount = params["discount"]
    quantity = params["quantity"]
    return f"""
                SELECT
                    SUM(l_extendedprice * l_discount) AS revenue
                FROM lineitem
                WHERE l_shipdate >= DATE '{date_str}'
                  AND l_shipdate < DATE '{date_str}' + INTERVAL '1' YEAR
                  AND l_discount BETWEEN {discount} - 0.01 AND {discount} + 0.01
                  AND l_quantity < {quantity}
            """


def _tpch_q7(params: dict[str, object]) -> str:
    nation1 = params["nation1"]
    nation2 = params["nation2"]
    return f"""
                SELECT
                    supp_nation, cust_nation, l_year,
                    SUM(volume) AS revenue
                FROM (
                    SELECT
                        n1.n_name AS supp_nation,
                        n2.n_name AS cust_nation,
                        EXTRACT(YEAR FROM l_shipdate) AS l_year,
                        l_extendedprice * (1 - l_discount) AS volume
                    FROM supplier, lineitem, orders, customer, nation n1, nation n2
                    WHERE s_suppkey = l_suppkey
                      AND o_orderkey = l_orderkey
                      AND c_custkey = o_custkey
                      AND s_nationkey = n1.n_nationkey
                      AND c_nationkey = n2.n_nationkey
                      AND ((n1.n_name = '{nation1}' AND n2.n_name = '{nation2}')
                        OR (n1.n_name = '{nation2}' AND n2.n_name = '{nation1}'))
                      AND l_shipdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
                ) AS shipping
                GROUP BY supp_nation, cust_nation, l_year
                ORDER BY supp_nation, cust_nation, l_year
            """


def _tpch_q8(params: dict[str, object]) -> str:
    nation = params["nation"]
    region = params["region"]
    p_type = params["type"]
    return f"""
                SELECT
                    o_year,
                    SUM(CASE WHEN nation = '{nation}' THEN volume ELSE 0 END)
                        / SUM(volume) AS mkt_share
                FROM (
                    SELECT
                        EXTRACT(YEAR FROM o_orderdate) AS o_year,
                        l_extendedprice * (1 - l_discount) AS volume,
                        n2.n_name AS nation
                    FROM part, supplier, lineitem, orders, customer,
                         nation n1, nation n2, region
                    WHERE p_partkey = l_partkey
                      AND s_suppkey = l_suppkey
                      AND l_orderkey = o_orderkey
                      AND o_custkey = c_custkey
                      AND c_nationkey = n1.n_nationkey
                      AND n1.n_regionkey = r_regionkey
                      AND r_name = '{region}'
                      AND s_nationkey = n2.n_nationkey
                      AND o_orderdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
                      AND p_type = '{p_type}'
                ) AS all_nations
                GROUP BY o_year
                ORDER BY o_year
            """


def _tpch_q9(params: dict[str, object]) -> str:
    color = params["color"]
    return f"""
                SELECT
                    nation, o_year,
                    SUM(amount) AS sum_profit
                FROM (
                    SELECT
                        n_name AS nation,
                        EXTRACT(YEAR FROM o_orderdate) AS o_year,
                        l_extendedprice * (1 - l_discount)
                            - ps_supplycost * l_quantity AS amount
                    FROM part, supplier, lineitem, partsupp, orders, nation
                    WHERE s_suppkey = l_suppkey
                      AND ps_suppkey = l_suppkey
                      AND ps_partkey = l_partkey
                      AND p_partkey = l_partkey
                      AND o_orderkey = l_orderkey
                      AND s_nationkey = n_nationkey
                      AND p_name LIKE '%{color}%'
                ) AS profit
                GROUP BY nation, o_year
                ORDER BY nation, o_year DESC
            """


def _tpch_q10(params: dict[str, object]) -> str:
    date_str = params["date"]
    return f"""
                SELECT
                    c_custkey, c_name,
                    SUM(l_extendedprice * (1 - l_discount)) AS revenue,
                    c_acctbal, n_name, c_address, c_phone, c_comment
                FROM customer, orders, lineitem, nation
                WHERE c_custkey = o_custkey
                  AND l_orderkey = o_orderkey
                  AND o_orderdate >= DATE '{date_str}'
                  AND o_orderdate < DATE '{date_str}' + INTERVAL '3' MONTH
                  AND l_returnflag = 'R'
                  AND c_nationkey = n_nationkey
                GROUP BY c_custkey, c_name, c_acctbal, c_phone,
                         n_name, c_address, c_comment
                ORDER BY revenue DESC
                LIMIT 20
            """


def _tpch_q11(params: dict[str, object]) -> str:
    nation = params["nation"]
    fraction = params["fraction"]
    return f"""
                SELECT
                    ps_partkey,
                    SUM(ps_supplycost * ps_availqty) AS value
                FROM partsupp, supplier, nation
                WHERE ps_suppkey = s_suppkey
                  AND s_nationkey = n_nationkey
                  AND n_name = '{nation}'
                GROUP BY ps_partkey
                HAVING SUM(ps_supplycost * ps_availqty) > (
                    SELECT SUM(ps_supplycost * ps_availqty) * {fraction}
                    FROM partsupp, supplier, nation
                    WHERE ps_suppkey = s_suppkey
                      AND s_nationkey = n_nationkey
                      AND n_name = '{nation}'
                )
                ORDER BY value DESC
            """


def _tpch_q12(params: dict[str, object]) -> str:
    shipmode1 = params["shipmode1"]
    shipmode2 = params["shipmode2"]
    date_str = params["date"]
    return f"""
                SELECT
                    l_shipmode,
                    SUM(CASE
                        WHEN o_orderpriority = '1-URGENT' OR o_orderpriority = '2-HIGH'
                        THEN 1 ELSE 0 END) AS high_line_count,
                    SUM(CASE
                        WHEN o_orderpriority <> '1-URGENT' AND o_orderpriority <> '2-HIGH'
                        THEN 1 ELSE 0 END) AS low_line_count
                FROM orders, lineitem
                WHERE o_orderkey = l_orderkey
                  AND l_shipmode IN ('{shipmode1}', '{shipmode2}')
                  AND l_commitdate < l_receiptdate
                  AND l_shipdate < l_commitdate
                  AND l_receiptdate >= DATE '{date_str}'
                  AND l_receiptdate < DATE '{date_str}' + INTERVAL '1' YEAR
                GROUP BY l_shipmode
                ORDER BY l_shipmode
            """


def _tpch_q13(params: dict[str, object]) -> str:
    word1 = params["word1"]
    word2 = params["word2"]
    return f"""
                SELECT
                    c_count,
                    COUNT(*) AS custdist
                FROM (
                    SELECT
                        c_custkey,
                        COUNT(o_orderkey) AS c_count
                    FROM customer
                    LEFT OUTER JOIN orders ON c_custkey = o_custkey
                        AND o_comment NOT LIKE '%{word1}%{word2}%'
                    GROUP BY c_custkey
                ) AS c_orders
                GROUP BY c_count
                ORDER BY custdist DESC, c_count DESC
            """


def _tpch_q14(params: dict[str, object]) -> str:
    date_str = params["date"]
    return f"""
                SELECT
                    100.00 * SUM(CASE WHEN p_type LIKE 'PROMO%'
                        THEN l_extendedprice * (1 - l_discount) ELSE 0 END)
                    / SUM(l_extendedprice * (1 - l_discount)) AS promo_revenue
                FROM lineitem, part
                WHERE l_partkey = p_partkey
                  AND l_shipdate >= DATE '{date_str}'
                  AND l_shipdate < DATE '{date_str}' + INTERVAL '1' MONTH
            """


def _tpch_q15(params: dict[str, object]) -> str:
    date_str = params["date"]
    return f"""
                WITH revenue AS (
                    SELECT
                        l_suppkey AS supplier_no,
                        SUM(l_extendedprice * (1 - l_discount)) AS total_revenue
                    FROM lineitem
                    WHERE l_shipdate >= DATE '{date_str}'
                      AND l_shipdate < DATE '{date_str}' + INTERVAL '3' MONTH
                    GROUP BY l_suppkey
                )
                SELECT
                    s_suppkey, s_name, s_address, s_phone,
                    total_revenue
                FROM supplier, revenue
                WHERE s_suppkey = supplier_no
                  AND total_revenue = (SELECT MAX(total_revenue) FROM revenue)
                ORDER BY s_suppkey
            """


def _tpch_q16(params: dict[str, object]) -> str:
    brand = params["brand"]
    type_prefix = params["type_prefix"]
    sizes = params["sizes"]
    assert isinstance(sizes, (list, tuple))
    sizes_str = ", ".join(str(s) for s in sizes)
    return f"""
                SELECT
                    p_brand, p_type, p_size,
                    COUNT(DISTINCT ps_suppkey) AS supplier_cnt
                FROM partsupp, part
                WHERE p_partkey = ps_partkey
                  AND p_brand <> '{brand}'
                  AND p_type NOT LIKE '{type_prefix}%'
                  AND p_size IN ({sizes_str})
                  AND ps_suppkey NOT IN (
                      SELECT s_suppkey FROM supplier
                      WHERE s_comment LIKE '%Customer%Complaints%'
                  )
                GROUP BY p_brand, p_type, p_size
                ORDER BY supplier_cnt DESC, p_brand, p_type, p_size
            """


def _tpch_q17(params: dict[str, object]) -> str:
    brand = params["brand"]
    container = params["container"]
    return f"""
                SELECT
                    SUM(l_extendedprice) / 7.0 AS avg_yearly
                FROM lineitem, part
                WHERE p_partkey = l_partkey
                  AND p_brand = '{brand}'
                  AND p_container = '{container}'
                  AND l_quantity < (
                      SELECT 0.2 * AVG(l_quantity)
                      FROM lineitem
                      WHERE l_partkey = p_partkey
                  )
            """


def _tpch_q18(params: dict[str, object]) -> str:
    quantity = params["quantity"]
    return f"""
                SELECT
                    c_name, c_custkey, o_orderkey,
                    o_orderdate, o_totalprice,
                    SUM(l_quantity) AS total_qty
                FROM customer, orders, lineitem
                WHERE o_orderkey IN (
                    SELECT l_orderkey FROM lineitem
                    GROUP BY l_orderkey
                    HAVING SUM(l_quantity) > {quantity}
                )
                  AND c_custkey = o_custkey
                  AND o_orderkey = l_orderkey
                GROUP BY c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice
                ORDER BY o_totalprice DESC, o_orderdate
                LIMIT 100
            """


def _tpch_q19(params: dict[str, object]) -> str:
    brand1 = params["brand1"]
    brand2 = params["brand2"]
    brand3 = params["brand3"]
    qty1 = params["quantity1"]
    qty2 = params["quantity2"]
    qty3 = params["quantity3"]
    return f"""
                SELECT
                    SUM(l_extendedprice * (1 - l_discount)) AS revenue
                FROM lineitem, part
                WHERE (
                    p_partkey = l_partkey
                    AND p_brand = '{brand1}'
                    AND p_container IN ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')
                    AND l_quantity >= {qty1} AND l_quantity <= {qty1} + 10
                    AND p_size BETWEEN 1 AND 5
                    AND l_shipmode IN ('AIR', 'AIR REG')
                    AND l_shipinstruct = 'DELIVER IN PERSON'
                ) OR (
                    p_partkey = l_partkey
                    AND p_brand = '{brand2}'
                    AND p_container IN ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')
                    AND l_quantity >= {qty2} AND l_quantity <= {qty2} + 10
                    AND p_size BETWEEN 1 AND 10
                    AND l_shipmode IN ('AIR', 'AIR REG')
                    AND l_shipinstruct = 'DELIVER IN PERSON'
                ) OR (
                    p_partkey = l_partkey
                    AND p_brand = '{brand3}'
                    AND p_container IN ('LG CASE', 'LG BOX', 'LG PACK', 'LG PKG')
                    AND l_quantity >= {qty3} AND l_quantity <= {qty3} + 10
                    AND p_size BETWEEN 1 AND 15
                    AND l_shipmode IN ('AIR', 'AIR REG')
                    AND l_shipinstruct = 'DELIVER IN PERSON'
                )
            """


def _tpch_q20(params: dict[str, object]) -> str:
    color = params["color"]
    date_str = params["date"]
    nation = params["nation"]
    return f"""
                SELECT
                    s_name, s_address
                FROM supplier, nation
                WHERE s_suppkey IN (
                    SELECT ps_suppkey
                    FROM partsupp
                    WHERE ps_partkey IN (
                        SELECT p_partkey FROM part
                        WHERE p_name LIKE '{color}%'
                    )
                    AND ps_availqty > (
                        SELECT 0.5 * SUM(l_quantity)
                        FROM lineitem
                        WHERE l_partkey = ps_partkey
                          AND l_suppkey = ps_suppkey
                          AND l_shipdate >= DATE '{date_str}'
                          AND l_shipdate < DATE '{date_str}' + INTERVAL '1' YEAR
                    )
                )
                  AND s_nationkey = n_nationkey
                  AND n_name = '{nation}'
                ORDER BY s_name
            """


def _tpch_q21(params: dict[str, object]) -> str:
    nation = params["nation"]
    return f"""
                SELECT
                    s_name,
                    COUNT(*) AS numwait
                FROM supplier, lineitem l1, orders, nation
                WHERE s_suppkey = l1.l_suppkey
                  AND o_orderkey = l1.l_orderkey
                  AND o_orderstatus = 'F'
                  AND l1.l_receiptdate > l1.l_commitdate
                  AND EXISTS (
                      SELECT 1 FROM lineitem l2
                      WHERE l2.l_orderkey = l1.l_orderkey
                        AND l2.l_suppkey <> l1.l_suppkey
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM lineitem l3
                      WHERE l3.l_orderkey = l1.l_orderkey
                        AND l3.l_suppkey <> l1.l_suppkey
                        AND l3.l_receiptdate > l3.l_commitdate
                  )
                  AND s_nationkey = n_nationkey
                  AND n_name = '{nation}'
                GROUP BY s_name
                ORDER BY numwait DESC, s_name
                LIMIT 100
            """


def _tpch_q22(params: dict[str, object]) -> str:
    codes = params["country_codes"]
    assert isinstance(codes, (list, tuple))
    codes_str = ", ".join(f"'{c}'" for c in codes)
    return f"""
                SELECT
                    cntrycode,
                    COUNT(*) AS numcust,
                    SUM(c_acctbal) AS totacctbal
                FROM (
                    SELECT
                        SUBSTRING(c_phone FROM 1 FOR 2) AS cntrycode,
                        c_acctbal
                    FROM customer
                    WHERE SUBSTRING(c_phone FROM 1 FOR 2) IN ({codes_str})
                      AND c_acctbal > (
                          SELECT AVG(c_acctbal) FROM customer
                          WHERE c_acctbal > 0.00
                            AND SUBSTRING(c_phone FROM 1 FOR 2) IN ({codes_str})
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM orders
                          WHERE o_custkey = c_custkey
                      )
                ) AS custsale
                GROUP BY cntrycode
                ORDER BY cntrycode
            """


_TPCH_QUERY_BUILDERS = {
    1: _tpch_q1,
    2: _tpch_q2,
    3: _tpch_q3,
    4: _tpch_q4,
    5: _tpch_q5,
    6: _tpch_q6,
    7: _tpch_q7,
    8: _tpch_q8,
    9: _tpch_q9,
    10: _tpch_q10,
    11: _tpch_q11,
    12: _tpch_q12,
    13: _tpch_q13,
    14: _tpch_q14,
    15: _tpch_q15,
    16: _tpch_q16,
    17: _tpch_q17,
    18: _tpch_q18,
    19: _tpch_q19,
    20: _tpch_q20,
    21: _tpch_q21,
    22: _tpch_q22,
}


@pytest.mark.integration
@pytest.mark.datavault
class TestDataVaultDuckDBIntegration:
    @pytest.fixture
    def datavault_benchmark(self, tmp_path):
        return DataVaultBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "datavault_data",
        )

    def test_schema_generation(self, datavault_benchmark):
        import duckdb

        ddl = datavault_benchmark.get_create_tables_sql(dialect="duckdb")
        conn = duckdb.connect(":memory:")

        conn.execute(ddl)

        tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
        table_names = {t[0] for t in tables}

        assert len(table_names) == 21
        assert "hub_customer" in table_names
        assert "link_lineitem" in table_names
        assert "sat_order" in table_names

        conn.close()

    def test_query_syntax_valid(self, datavault_benchmark):
        import duckdb

        conn = duckdb.connect(":memory:")

        ddl = datavault_benchmark.get_create_tables_sql(dialect="duckdb")
        conn.execute(ddl)

        for qid in range(1, 23):
            query = datavault_benchmark.get_query(qid)
            try:
                conn.execute(f"EXPLAIN {query}")
            except Exception as e:
                pytest.fail(f"Query {qid} failed to parse: {e}")

        conn.close()

    def test_parameterized_queries_syntax_valid(self, datavault_benchmark):
        import duckdb

        conn = duckdb.connect(":memory:")

        ddl = datavault_benchmark.get_create_tables_sql(dialect="duckdb")
        conn.execute(ddl)

        query_manager = datavault_benchmark.query_manager

        for stream_id in [0, 1, 2]:
            for qid in range(1, 23):
                sql, params = query_manager.get_parameterized_query(qid, stream_id=stream_id)
                try:
                    conn.execute(f"EXPLAIN {sql}")
                except Exception as e:
                    pytest.fail(f"Query {qid} stream {stream_id} failed: {e}")

        conn.close()

    def test_validation_utilities_work(self, datavault_benchmark):
        from benchbox.core.datavault import get_expected_row_count

        assert get_expected_row_count("hub_region", 0.01) == 5
        assert get_expected_row_count("hub_nation", 0.01) == 25
        assert get_expected_row_count("hub_customer", 0.01) == 1500

    def test_table_loading_order(self, datavault_benchmark):
        order = datavault_benchmark.get_table_loading_order()

        def idx(name):
            return order.index(name)

        assert idx("hub_customer") < idx("sat_customer")
        assert idx("hub_order") < idx("sat_order")

        assert idx("hub_customer") < idx("link_order_customer")
        assert idx("hub_order") < idx("link_order_customer")


@pytest.mark.integration
@pytest.mark.datavault
@pytest.mark.slow
class TestDataVaultDataGeneration:
    @pytest.fixture
    def datavault_with_data(self, tmp_path):
        benchmark = DataVaultBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "datavault_data",
        )

        try:
            benchmark.generate_data()
        except FileNotFoundError as e:
            if "dbgen" in str(e) or "dsdgen" in str(e):
                pytest.skip("TPC-H binaries not available")
            raise

        return benchmark

    def test_data_generation_creates_21_tables(self, datavault_with_data):
        output_dir = datavault_with_data.output_dir

        tbl_files = list(output_dir.glob("*.tbl"))
        assert len(tbl_files) >= 21, f"Expected 21 tables, found {len(tbl_files)}"

    def test_manifest_created(self, datavault_with_data):
        import json

        manifest_path = datavault_with_data.output_dir / "_datagen_manifest.json"
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert manifest["benchmark"] == "datavault"
        assert manifest["scale_factor"] == 0.01
        assert len(manifest["tables"]) == 21

    def test_query_execution_on_generated_data(self, datavault_with_data):
        import duckdb

        conn = duckdb.connect(":memory:")

        ddl = datavault_with_data.get_create_tables_sql(dialect="duckdb")
        conn.execute(ddl)

        output_dir = datavault_with_data.output_dir
        for table_name in datavault_with_data.get_table_loading_order():
            tbl_path = output_dir / f"{table_name}.tbl"
            if tbl_path.exists():
                conn.execute(f"""
                    INSERT INTO {table_name}
                    SELECT * FROM read_csv('{tbl_path}', delim='|', header=false)
                """)

        query = datavault_with_data.get_query(1)
        result = conn.execute(query).fetchall()

        assert len(result) > 0

        conn.close()

    def test_row_count_validation(self, datavault_with_data):
        from benchbox.core.datavault import validate_row_counts

        report = validate_row_counts(
            datavault_with_data.output_dir,
            scale_factor=0.01,
        )

        assert report.tables_validated == 21
        assert report.tables_failed <= 2, f"Too many validation failures: {report}"


@pytest.mark.integration
@pytest.mark.datavault
@pytest.mark.reference_comparison
class TestDataVaultTPCHEquivalence:
    @staticmethod
    def _load_tbl_simple(conn, table_name: str, file_path: str) -> None:
        result = conn.execute(f"SELECT name FROM pragma_table_info('{table_name}') ORDER BY cid").fetchall()
        col_names = [row[0] for row in result]

        all_names = col_names + [TRAILING_DUMMY_COLUMN]
        names_param = ", ".join([f"'{col}'" for col in all_names])
        conn.execute(
            f"""
            INSERT INTO {table_name}
            SELECT * EXCLUDE ({TRAILING_DUMMY_COLUMN}) FROM read_csv('{file_path}', delim='|', header=false, null_padding=true, names=[{names_param}])
            """
        )

    @staticmethod
    def _normalize_value(value):
        if isinstance(value, Decimal):
            return round(float(value), 8)
        if isinstance(value, float):
            return round(value, 8)
        return value

    @classmethod
    def _normalize_rows(cls, rows):
        normalized = [tuple(cls._normalize_value(v) for v in row) for row in rows]
        return sorted(normalized)

    @staticmethod
    def _tpch_query_for_params(query_id: int, params: dict[str, object]) -> str:
        builder = _TPCH_QUERY_BUILDERS.get(query_id)
        if builder is None:
            raise ValueError(f"Unsupported query ID for TPCH equivalence: {query_id}")
        return builder(params)

    def test_datavault_queries_execute_successfully(self, tmp_path):
        import duckdb

        scale_factor = 0.01
        output_dir = tmp_path / "datavault_test"

        dv_benchmark = DataVaultBenchmark(
            scale_factor=scale_factor,
            output_dir=output_dir,
        )

        try:
            dv_benchmark.generate_data()
        except FileNotFoundError as e:
            if "dbgen" in str(e) or "dsdgen" in str(e):
                pytest.skip("TPC-H dbgen binary not available")
            raise

        conn = duckdb.connect(":memory:")

        conn.execute(dv_benchmark.get_create_tables_sql(dialect="duckdb"))

        for table_name in dv_benchmark.get_table_loading_order():
            tbl_path = output_dir / f"{table_name}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table_name, str(tbl_path))

        dv_query_manager = DataVaultQueryManager()
        failed_queries = []

        for qid in range(1, 23):
            sql = dv_query_manager.get_query(qid)
            try:
                results = conn.execute(sql).fetchall()
                if len(results) == 0 and qid not in {2, 17, 21}:
                    pass
            except Exception as e:
                failed_queries.append((qid, str(e)))

        conn.close()

        if failed_queries:
            msg = "\n".join(f"Q{qid}: {err}" for qid, err in failed_queries)
            pytest.fail(f"Data Vault queries failed:\n{msg}")

    def test_datavault_row_counts_match_source(self, tmp_path):
        import duckdb

        scale_factor = 0.01
        output_dir = tmp_path / "datavault_counts"

        dv_benchmark = DataVaultBenchmark(
            scale_factor=scale_factor,
            output_dir=output_dir,
        )

        try:
            dv_benchmark.generate_data()
        except FileNotFoundError as e:
            if "dbgen" in str(e) or "dsdgen" in str(e):
                pytest.skip("TPC-H dbgen binary not available")
            raise

        tpch_benchmark = TPCHBenchmark(scale_factor=scale_factor, output_dir=output_dir)

        conn = duckdb.connect(":memory:")

        conn.execute(tpch_benchmark.get_create_tables_sql(dialect="duckdb"))
        for table in [t.name for t in TABLES]:
            tbl_path = output_dir / f"{table}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table, str(tbl_path))

        conn.execute(dv_benchmark.get_create_tables_sql(dialect="duckdb"))
        for table_name in dv_benchmark.get_table_loading_order():
            tbl_path = output_dir / f"{table_name}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table_name, str(tbl_path))

        count_checks = [
            ("hub_region", "region", 1.0, "Hub region = source region"),
            ("hub_nation", "nation", 1.0, "Hub nation = source nation"),
            ("hub_customer", "customer", 1.0, "Hub customer = source customer"),
            ("hub_supplier", "supplier", 1.0, "Hub supplier = source supplier"),
            ("hub_part", "part", 1.0, "Hub part = source part"),
            ("hub_order", "orders", 1.0, "Hub order = source orders"),
            ("hub_lineitem", "lineitem", 1.0, "Hub lineitem = source lineitem"),
            ("link_part_supplier", "partsupp", 1.0, "Link part_supplier = source partsupp"),
            ("sat_customer", "customer", 1.0, "Sat customer = source customer"),
            ("sat_lineitem", "lineitem", 1.0, "Sat lineitem = source lineitem"),
        ]

        failures = []
        for dv_table, tpch_table, expected_ratio, desc in count_checks:
            dv_count = conn.execute(f"SELECT COUNT(*) FROM {dv_table}").fetchone()[0]
            tpch_count = conn.execute(f"SELECT COUNT(*) FROM {tpch_table}").fetchone()[0]

            if tpch_count == 0:
                continue

            actual_ratio = dv_count / tpch_count
            tolerance = 0.01 if "lineitem" in dv_table else 0.001
            if abs(actual_ratio - expected_ratio) > tolerance:
                failures.append(f"{desc}: {dv_count} vs {tpch_count} (ratio: {actual_ratio:.4f})")

        conn.close()

        if failures:
            pytest.fail("Row count mismatches:\n" + "\n".join(failures))

    def test_datavault_aggregation_equivalence(self, tmp_path):
        import duckdb

        scale_factor = 0.01
        output_dir = tmp_path / "datavault_agg"

        dv_benchmark = DataVaultBenchmark(
            scale_factor=scale_factor,
            output_dir=output_dir,
        )

        try:
            dv_benchmark.generate_data()
        except FileNotFoundError as e:
            if "dbgen" in str(e) or "dsdgen" in str(e):
                pytest.skip("TPC-H dbgen binary not available")
            raise

        tpch_benchmark = TPCHBenchmark(scale_factor=scale_factor, output_dir=output_dir)

        conn = duckdb.connect(":memory:")

        conn.execute(tpch_benchmark.get_create_tables_sql(dialect="duckdb"))
        tpch_data_dir = dv_benchmark.tpch_source_dir
        for table in [t.name for t in TABLES]:
            tbl_path = tpch_data_dir / f"{table}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table, str(tbl_path))

        conn.execute(dv_benchmark.get_create_tables_sql(dialect="duckdb"))
        for table_name in dv_benchmark.get_table_loading_order():
            tbl_path = output_dir / f"{table_name}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table_name, str(tbl_path))

        dv_query_manager = DataVaultQueryManager()

        tpch_q6 = """
            SELECT SUM(l_extendedprice * l_discount) AS revenue
            FROM lineitem
            WHERE l_shipdate >= DATE '1994-01-01'
              AND l_shipdate < DATE '1994-01-01' + INTERVAL '1' YEAR
              AND l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01
              AND l_quantity < 24
        """

        dv_q6 = dv_query_manager.get_query(6)

        tpch_result = conn.execute(tpch_q6).fetchone()[0]
        dv_result = conn.execute(dv_q6).fetchone()[0]

        tpch_revenue = float(tpch_result) if tpch_result else 0.0
        dv_revenue = float(dv_result) if dv_result else 0.0

        if tpch_revenue > 0:
            relative_diff = abs(tpch_revenue - dv_revenue) / tpch_revenue
            assert relative_diff < 0.0001, f"Q6 revenue mismatch: TPC-H={tpch_revenue:.2f}, Data Vault={dv_revenue:.2f}"
        else:
            assert abs(dv_revenue) < 0.01, f"DV Q6 non-zero when TPC-H is zero: {dv_revenue}"

        conn.close()

    @pytest.mark.slow
    def test_datavault_matches_tpch_results_for_q12_q18_q21(self, tmp_path):
        import duckdb

        scale_factor = 0.01
        output_dir = tmp_path / "datavault_exact_equivalence"

        dv_benchmark = DataVaultBenchmark(
            scale_factor=scale_factor,
            output_dir=output_dir,
        )

        try:
            dv_benchmark.generate_data()
        except FileNotFoundError as e:
            if "dbgen" in str(e) or "dsdgen" in str(e):
                pytest.skip("TPC-H binaries not available")
            raise

        tpch_benchmark = TPCHBenchmark(scale_factor=scale_factor, output_dir=output_dir)
        conn = duckdb.connect(":memory:")

        conn.execute(tpch_benchmark.get_create_tables_sql(dialect="duckdb"))
        tpch_data_dir = dv_benchmark.tpch_source_dir
        for table in [t.name for t in TABLES]:
            tbl_path = tpch_data_dir / f"{table}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table, str(tbl_path))

        conn.execute(dv_benchmark.get_create_tables_sql(dialect="duckdb"))
        for table_name in dv_benchmark.get_table_loading_order():
            tbl_path = output_dir / f"{table_name}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table_name, str(tbl_path))

        dv_query_manager = DataVaultQueryManager(seed=12345, scale_factor=scale_factor)
        query_ids = [12, 18, 21]
        stream_ids = [0, 1, 2]

        mismatches = []
        for stream_id in stream_ids:
            for qid in query_ids:
                dv_sql, qp = dv_query_manager.get_parameterized_query(qid, stream_id=stream_id)
                tpch_sql = self._tpch_query_for_params(qid, qp.params)

                dv_rows = self._normalize_rows(conn.execute(dv_sql).fetchall())
                tpch_rows = self._normalize_rows(conn.execute(tpch_sql).fetchall())

                if dv_rows != tpch_rows:
                    mismatches.append(
                        f"Q{qid} stream {stream_id} params={qp.params} "
                        f"rows_dv={len(dv_rows)} rows_tpch={len(tpch_rows)}"
                    )

        conn.close()

        if mismatches:
            pytest.fail("Data Vault != TPC-H result mismatch:\n" + "\n".join(mismatches))

    @pytest.mark.slow
    def test_datavault_matches_tpch_results_all_queries(self, tmp_path):
        import duckdb

        scale_factor = 0.01
        output_dir = tmp_path / "datavault_full_equivalence"

        dv_benchmark = DataVaultBenchmark(
            scale_factor=scale_factor,
            output_dir=output_dir,
        )

        try:
            dv_benchmark.generate_data()
        except FileNotFoundError as e:
            if "dbgen" in str(e) or "dsdgen" in str(e):
                pytest.skip("TPC-H binaries not available")
            raise

        tpch_benchmark = TPCHBenchmark(scale_factor=scale_factor, output_dir=output_dir)
        conn = duckdb.connect(":memory:")

        conn.execute(tpch_benchmark.get_create_tables_sql(dialect="duckdb"))
        tpch_data_dir = dv_benchmark.tpch_source_dir
        for table in [t.name for t in TABLES]:
            tbl_path = tpch_data_dir / f"{table}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table, str(tbl_path))

        conn.execute(dv_benchmark.get_create_tables_sql(dialect="duckdb"))
        for table_name in dv_benchmark.get_table_loading_order():
            tbl_path = output_dir / f"{table_name}.tbl"
            if tbl_path.exists():
                self._load_tbl_simple(conn, table_name, str(tbl_path))

        dv_query_manager = DataVaultQueryManager(seed=12345, scale_factor=scale_factor)

        mismatches = []
        execution_errors = []

        for qid in range(1, 23):
            dv_sql, qp = dv_query_manager.get_parameterized_query(qid, stream_id=0)
            tpch_sql = self._tpch_query_for_params(qid, qp.params)

            try:
                dv_rows = self._normalize_rows(conn.execute(dv_sql).fetchall())
            except Exception as e:
                execution_errors.append(f"Q{qid} DV execution error: {e}")
                continue

            try:
                tpch_rows = self._normalize_rows(conn.execute(tpch_sql).fetchall())
            except Exception as e:
                execution_errors.append(f"Q{qid} TPC-H execution error: {e}")
                continue

            if dv_rows != tpch_rows:
                mismatches.append(f"Q{qid} rows_dv={len(dv_rows)} rows_tpch={len(tpch_rows)}")

        conn.close()

        failures = execution_errors + mismatches
        if failures:
            pytest.fail("Data Vault full equivalence failures:\n" + "\n".join(failures))
