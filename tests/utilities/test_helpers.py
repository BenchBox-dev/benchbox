# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import functools
import tempfile
import time
import uuid
import warnings
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

try:
    import duckdb

    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False
    duckdb = None


def assert_valid_sql(sql: str, connection=None) -> None:
    if not DUCKDB_AVAILABLE:
        raise ImportError("DuckDB is required for SQL validation. Install with: pip install duckdb")

    if connection is None:
        connection = duckdb.connect(":memory:")
        close_connection = True
    else:
        close_connection = False

    try:
        connection.execute(f"EXPLAIN {sql}")
    except Exception as e:
        raise AssertionError(f"Invalid SQL: {e}") from e
    finally:
        if close_connection:
            connection.close()


def assert_benchmark_compliance(benchmark) -> None:
    required_attributes = ["name", "queries", "schema"]
    for attr in required_attributes:
        if not hasattr(benchmark, attr):
            raise AssertionError(f"Benchmark missing required attribute: {attr}")

    required_methods = ["get_queries", "get_schema", "generate_data"]
    for method in required_methods:
        if not hasattr(benchmark, method):
            raise AssertionError(f"Benchmark missing required method: {method}")
        if not callable(getattr(benchmark, method)):
            raise AssertionError(f"Benchmark attribute {method} is not callable")

    queries = benchmark.get_queries()
    if not isinstance(queries, dict):
        raise AssertionError("Benchmark queries must return a dictionary")

    schema = benchmark.get_schema()
    if not isinstance(schema, (dict, list, str)):
        raise AssertionError("Benchmark schema must return dict, list, or string")


def generate_test_data(benchmark_type: str, scale: float = 0.1, output_dir: Optional[Path] = None) -> dict[str, Path]:
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp())

    benchmark_classes = {
        "tpch": "benchbox.TPCH",
        "tpcds": "benchbox.TPCDS",
        "read_primitives": "benchbox.Primitives",
        "amplab": "benchbox.AmpLab",
        "clickbench": "benchbox.ClickBench",
        "h2odb": "benchbox.H2ODB",
        "merge": "benchbox.Merge",
        "ssb": "benchbox.SSB",
        "tpcdi": "benchbox.TPCDI",
    }

    if benchmark_type.lower() not in benchmark_classes:
        raise ValueError(f"Unsupported benchmark type: {benchmark_type}")

    try:
        module_path, class_name = benchmark_classes[benchmark_type.lower()].rsplit(".", 1)
        module = __import__(module_path, fromlist=[class_name])
        benchmark_class = getattr(module, class_name)

        benchmark = benchmark_class(scale_factor=scale, output_dir=output_dir)

        benchmark.generate_data()

        data_files = {}
        for file_path in output_dir.glob("*.csv"):
            table_name = file_path.stem
            data_files[table_name] = file_path

        return data_files

    except Exception as e:
        raise RuntimeError(f"Data generation failed for {benchmark_type}: {e}") from e


def assert_olap_features_supported(connection, features: list[str]) -> None:
    feature_tests = {
        "window_functions": "SELECT ROW_NUMBER() OVER (ORDER BY 1) FROM (SELECT 1) t",
        "ctes": "WITH test AS (SELECT 1 as x) SELECT * FROM test",
        "grouping_sets": "SELECT COUNT(*) FROM (SELECT 1) t GROUP BY GROUPING SETS (())",
        "pivot": "PIVOT (SELECT 1 as val, 'a' as cat) ON cat",
        "array_agg": "SELECT ARRAY_AGG(x) FROM (SELECT 1 as x) t",
        "filter_clause": "SELECT COUNT(*) FILTER (WHERE 1=1) FROM (SELECT 1) t",
        "rollup": "SELECT COUNT(*) FROM (SELECT 1) t GROUP BY ROLLUP()",
        "cube": "SELECT COUNT(*) FROM (SELECT 1) t GROUP BY CUBE()",
        "recursive_cte": "WITH RECURSIVE test AS (SELECT 1 as x UNION ALL SELECT x+1 FROM test WHERE x < 3) SELECT * FROM test",
        "lateral_join": "SELECT * FROM (SELECT 1 as x) t1, LATERAL (SELECT t1.x + 1 as y) t2",
        "json_functions": "SELECT JSON_EXTRACT('{\"a\": 1}', '$.a')",
        "regexp_functions": "SELECT REGEXP_MATCHES('hello', 'h.*o')",
        "unnest": "SELECT UNNEST([1, 2, 3])",
        "quantile_functions": "SELECT QUANTILE(x, 0.5) FROM (SELECT 1 as x) t",
        "string_split": "SELECT STRING_SPLIT('a,b,c', ',')",
        "date_functions": "SELECT DATE_TRUNC('month', CURRENT_DATE)",
        "analyze_explain": "SELECT * FROM (EXPLAIN ANALYZE SELECT 1) t",
    }

    unsupported_features = []

    for feature in features:
        if feature not in feature_tests:
            warnings.warn(f"Unknown OLAP feature: {feature}", stacklevel=2)
            continue

        try:
            connection.execute(feature_tests[feature])
        except Exception as e:
            unsupported_features.append(f"{feature}: {str(e)}")

    if unsupported_features:
        raise AssertionError(f"OLAP features not supported: {', '.join(unsupported_features)}")


def load_tpch_data_to_duckdb(connection, data_paths: dict[str, Path]) -> None:
    if not DUCKDB_AVAILABLE:
        raise ImportError("DuckDB is required for data loading. Install with: pip install duckdb")

    tpch_schemas = {
        "nation": """
            CREATE TABLE nation (
                n_nationkey INTEGER,
                n_name VARCHAR(25),
                n_regionkey INTEGER,
                n_comment VARCHAR(152)
            )
        """,
        "region": """
            CREATE TABLE region (
                r_regionkey INTEGER,
                r_name VARCHAR(25),
                r_comment VARCHAR(152)
            )
        """,
        "part": """
            CREATE TABLE part (
                p_partkey INTEGER,
                p_name VARCHAR(55),
                p_mfgr VARCHAR(25),
                p_brand VARCHAR(10),
                p_type VARCHAR(25),
                p_size INTEGER,
                p_container VARCHAR(10),
                p_retailprice DECIMAL(15,2),
                p_comment VARCHAR(23)
            )
        """,
        "supplier": """
            CREATE TABLE supplier (
                s_suppkey INTEGER,
                s_name VARCHAR(25),
                s_address VARCHAR(40),
                s_nationkey INTEGER,
                s_phone VARCHAR(15),
                s_acctbal DECIMAL(15,2),
                s_comment VARCHAR(101)
            )
        """,
        "partsupp": """
            CREATE TABLE partsupp (
                ps_partkey INTEGER,
                ps_suppkey INTEGER,
                ps_availqty INTEGER,
                ps_supplycost DECIMAL(15,2),
                ps_comment VARCHAR(199)
            )
        """,
        "customer": """
            CREATE TABLE customer (
                c_custkey INTEGER,
                c_name VARCHAR(25),
                c_address VARCHAR(40),
                c_nationkey INTEGER,
                c_phone VARCHAR(15),
                c_acctbal DECIMAL(15,2),
                c_mktsegment VARCHAR(10),
                c_comment VARCHAR(117)
            )
        """,
        "orders": """
            CREATE TABLE orders (
                o_orderkey INTEGER,
                o_custkey INTEGER,
                o_orderstatus VARCHAR(1),
                o_totalprice DECIMAL(15,2),
                o_orderdate DATE,
                o_orderpriority VARCHAR(15),
                o_clerk VARCHAR(15),
                o_shippriority INTEGER,
                o_comment VARCHAR(79)
            )
        """,
        "lineitem": """
            CREATE TABLE lineitem (
                l_orderkey INTEGER,
                l_partkey INTEGER,
                l_suppkey INTEGER,
                l_linenumber INTEGER,
                l_quantity DECIMAL(15,2),
                l_extendedprice DECIMAL(15,2),
                l_discount DECIMAL(15,2),
                l_tax DECIMAL(15,2),
                l_returnflag VARCHAR(1),
                l_linestatus VARCHAR(1),
                l_shipdate DATE,
                l_commitdate DATE,
                l_receiptdate DATE,
                l_shipinstruct VARCHAR(25),
                l_shipmode VARCHAR(10),
                l_comment VARCHAR(44)
            )
        """,
    }

    try:
        for table_name, file_path in data_paths.items():
            table_name_lower = table_name.lower()

            if table_name_lower in tpch_schemas:
                connection.execute(tpch_schemas[table_name_lower])

                copy_sql = f"""
                    COPY {table_name_lower}
                    FROM '{file_path}'
                    WITH (DELIMITER '|', HEADER false)
                """
                connection.execute(copy_sql)
            else:
                connection.execute(f"""
                    CREATE TABLE {table_name_lower} AS
                    SELECT * FROM read_csv_auto('{file_path}', delim='|', header=false)
                """)

        for table_name in data_paths:
            table_name_lower = table_name.lower()
            result = connection.execute(f"SELECT COUNT(*) FROM {table_name_lower}").fetchone()
            if result[0] == 0:
                warnings.warn(f"Table {table_name_lower} appears to be empty", stacklevel=2)

    except Exception as e:
        raise RuntimeError(f"Failed to load TPC-H data into DuckDB: {e}") from e


def setup_duckdb_extensions(connection) -> None:
    if not DUCKDB_AVAILABLE:
        return

    extensions = [
        "json",
        "httpfs",
        "parquet",
        "excel",
        "icu",
        "fts",
        "tpch",
        "tpcds",
    ]

    for ext in extensions:
        try:
            connection.execute(f"INSTALL {ext}")
            connection.execute(f"LOAD {ext}")
        except Exception:
            pass


def create_test_database(connection_type: str = "memory", data_paths: Optional[dict[str, Path]] = None) -> Any:
    if not DUCKDB_AVAILABLE:
        raise ImportError("DuckDB is required for test database creation. Install with: pip install duckdb")

    if connection_type == "memory":
        conn = duckdb.connect(":memory:")
    elif connection_type == "file":
        temp_db = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
        conn = duckdb.connect(temp_db.name)
    else:
        conn = duckdb.connect(":memory:")

    setup_duckdb_extensions(conn)

    if data_paths:
        load_tpch_data_to_duckdb(conn, data_paths)

    return conn


def validate_query_result(
    connection,
    query: str,
    expected_rows: Optional[int] = None,
    expected_columns: Optional[list[str]] = None,
) -> None:
    try:
        result = connection.execute(query).fetchall()

        if expected_rows is not None:
            actual_rows = len(result)
            if actual_rows != expected_rows:
                raise AssertionError(f"Expected {expected_rows} rows, got {actual_rows}")

        if expected_columns is not None:
            description = connection.description
            if description:
                actual_columns = [col[0] for col in description]
                if actual_columns != expected_columns:
                    raise AssertionError(f"Expected columns {expected_columns}, got {actual_columns}")

    except Exception as e:
        raise AssertionError(f"Query validation failed: {e}") from e


def _benchmark_query_performance(connection, query: str, iterations: int = 3) -> dict[str, float]:
    execution_times = []

    for _ in range(iterations):
        start_time = time.time()
        connection.execute(query)
        end_time = time.time()
        execution_times.append(end_time - start_time)

    return {
        "min_time": min(execution_times),
        "max_time": max(execution_times),
        "avg_time": sum(execution_times) / len(execution_times),
        "total_time": sum(execution_times),
        "iterations": iterations,
    }


def create_temp_directory(prefix: str = "benchbox_test") -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{prefix}_"))
    return temp_dir


def measure_execution_time(func: Callable[..., T]) -> Callable[..., tuple[float, T]]:

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> tuple[float, T]:
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        return execution_time, result

    return wrapper


def generate_test_id(prefix: str = "test") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def assert_no_sql_injection(query: str) -> None:
    dangerous_patterns = [
        r";\s*drop\s+table",
        r";\s*delete\s+from",
        r";\s*update\s+.*\s+set",
        r";\s*insert\s+into",
        r"union\s+select.*from\s+information_schema",
        r"exec\s*\(",
        r"execute\s*\(",
        r"sp_executesql",
        r"xp_cmdshell",
        r"into\s+outfile",
        r"load_file\s*\(",
        r"@@version",
        r"user\s*\(",
        r"database\s*\(",
        r"schema\s*\(",
    ]

    import re

    query_lower = query.lower()

    for pattern in dangerous_patterns:
        if re.search(pattern, query_lower):
            raise AssertionError(f"Potential SQL injection detected: {pattern}")

    if query.count("--") > 5 or query.count("/*") > 5:
        warnings.warn("Query contains excessive comments, potential obfuscation", stacklevel=2)

    if "||" in query or "+" in query:
        warnings.warn("Query contains string concatenation operators", stacklevel=2)


