from __future__ import annotations

import argparse
from pathlib import Path

try:
    import duckdb
except ImportError as exc:
    raise SystemExit("DuckDB must be installed to run this example") from exc

from benchbox.core.coffeeshop.benchmark import CoffeeShopBenchmark


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CoffeeShop benchmark queries on DuckDB")

    parser.add_argument("--scale", type=float, default=0.001, help="Scale factor (default: 0.001)")

    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / "benchmark_runs" / "examples" / "coffeeshop",
        help="Directory to store generated CSV files",
    )

    parser.add_argument("--query", default="SA1", help="Query ID to execute (default: SA1)")

    parser.add_argument(
        "--start-date",
        dest="start_date",
        default="2023-12-01",
        help="Optional start date parameter for the query",
    )
    parser.add_argument(
        "--end-date",
        dest="end_date",
        default="2023-12-31",
        help="Optional end date parameter for the query",
    )
    return parser.parse_args()


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main(scale: float, output_dir: Path, query_id: str, start_date: str | None, end_date: str | None) -> None:
    ensure_output_dir(output_dir)

    benchmark = CoffeeShopBenchmark(scale_factor=scale, output_dir=output_dir)

    tables = benchmark.generate_data()
    print(f"Generated CoffeeShop tables: {tables}")

    conn = duckdb.connect(database=":memory:")

    benchmark.load_data_to_database(conn)

    params = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    sql = benchmark.get_query(query_id, params=params if params else None)
    print(f"\nExecuting {query_id}...\n{sql}\n")

    result = conn.execute(sql).fetchmany(5)
    for row in result:
        print(row)

    conn.close()

    print()
    print("Example complete!")
    print()
    print("Try other queries:")
    print("  python duckdb_coffeeshop.py --query SA1  # Sales by product")
    print("  python duckdb_coffeeshop.py --query SA2  # Revenue trends")
    print("  python duckdb_coffeeshop.py --query SA3  # Customer analytics")
    print()
    print("Try larger scale:")
    print("  python duckdb_coffeeshop.py --scale 0.01  # 10x more data")
    print("  python duckdb_coffeeshop.py --scale 0.1   # 100x more data")


if __name__ == "__main__":
    args = parse_args()
    main(args.scale, args.output, args.query, args.start_date, args.end_date)
