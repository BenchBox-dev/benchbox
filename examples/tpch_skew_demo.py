from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import duckdb
except ImportError as exc:
    raise SystemExit("DuckDB must be installed to run this example: pip install duckdb") from exc

from benchbox import TPCHSkew


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TPC-H Skew benchmark demonstration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with moderate skew
  python tpch_skew_demo.py

  # Heavy skew to stress join performance
  python tpch_skew_demo.py --preset heavy

  # Run specific queries
  python tpch_skew_demo.py --queries 1,6,14

  # Show all available presets
  python tpch_skew_demo.py --list-presets
        """,
    )

    parser.add_argument(
        "--scale",
        type=float,
        default=0.01,
        help="Scale factor (default: 0.01 = ~10MB data)",
    )

    parser.add_argument(
        "--preset",
        choices=["none", "light", "moderate", "heavy", "extreme", "realistic"],
        default="moderate",
        help="Skew preset to use (default: moderate)",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / "benchmark_runs" / "examples" / "tpch_skew",
        help="Directory to store generated data files",
    )

    parser.add_argument(
        "--queries",
        type=str,
        default="1,6",
        help="Comma-separated query IDs to run (default: 1,6)",
    )

    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="Show all available skew presets and exit",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    return parser.parse_args()


def show_presets() -> None:
    print("Available TPC-H Skew presets:")
    print()
    for preset in TPCHSkew.get_available_presets():
        desc = TPCHSkew.get_preset_description(preset)
        print(f"  {preset:12} - {desc}")
    print()


def main() -> None:
    args = parse_args()

    if args.list_presets:
        show_presets()
        sys.exit(0)

    query_ids = [int(q.strip()) for q in args.queries.split(",")]

    print("=" * 70)
    print("TPC-H Skew Benchmark Demo")
    print("=" * 70)
    print()

    print("Step 1: Creating TPC-H Skew benchmark")
    print(f"  Scale factor: {args.scale}")
    print(f"  Skew preset: {args.preset}")
    print(f"  Output dir: {args.output}")
    print()

    benchmark = TPCHSkew(
        scale_factor=args.scale,
        output_dir=args.output,
        skew_preset=args.preset,
        verbose=args.verbose,
    )

    print("Step 2: Skew configuration details")
    skew_info = benchmark.get_skew_info()
    print(f"  Preset: {skew_info['preset']}")
    print(f"  Skew factor: {skew_info['skew_factor']}")
    print(f"  Distribution type: {skew_info['distribution_type']}")
    print(f"  Attribute skew: {skew_info['attribute_skew_enabled']}")
    print(f"  Join skew: {skew_info['join_skew_enabled']}")
    print(f"  Temporal skew: {skew_info['temporal_skew_enabled']}")
    print()

    print("Step 3: Generating skewed TPC-H data...")
    args.output.mkdir(parents=True, exist_ok=True)

    try:
        data_files = benchmark.generate_data()
        print(f"  Generated {len(data_files)} table files")
        for table_name, file_path in benchmark.tables.items():
            print(f"    - {table_name}: {file_path.name}")
    except Exception as e:
        print(f"  Error generating data: {e}")
        print("  (Data generation requires TPC-H dbgen binary)")
        print()
        print("Continuing with query inspection only...")
        data_files = []

    print()

    print("Step 4: Available TPC-H queries")
    queries = benchmark.get_queries()
    print(f"  Total queries: {len(queries)}")
    print("  Query IDs: 1-22")
    print()

    print(f"Step 5: Sample query (Q{query_ids[0]})")
    sample_query = benchmark.get_query(query_ids[0])
    preview = sample_query[:500] + "..." if len(sample_query) > 500 else sample_query
    print("-" * 60)
    print(preview)
    print("-" * 60)
    print()

    if data_files:
        print("Step 6: Executing queries on skewed data")
        print(f"  Running queries: {query_ids}")
        print()

        conn = duckdb.connect(":memory:")

        try:
            schema_sql = benchmark.get_create_tables_sql()
            for stmt in schema_sql.strip().split(";"):
                if stmt.strip():
                    conn.execute(stmt.strip())

            for table_name, file_path in benchmark.tables.items():
                conn.execute(f"""
                    COPY {table_name} FROM '{file_path}'
                    (DELIMITER '|', HEADER FALSE)
                """)

            for qid in query_ids:
                query_sql = benchmark.get_query(qid, dialect="duckdb")
                print(f"  Query {qid}:")

                try:
                    start = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
                    result = conn.execute(query_sql).fetchmany(3)
                    end = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]

                    elapsed_ms = (end - start).total_seconds() * 1000
                    print(f"    Execution time: {elapsed_ms:.2f}ms")
                    print(f"    Sample rows: {len(result)}")
                    if result:
                        print(f"    First row: {result[0][:3]}...")
                except Exception as e:
                    print(f"    Error: {e}")
                print()

        finally:
            conn.close()
    else:
        print("Step 6: Skipping query execution (no data generated)")
        print()

    print("=" * 70)
    print("Demo complete!")
    print()
    print("What to try next:")
    print("  # Compare skewed vs uniform performance:")
    print("  # (requires DuckDBAdapter for full comparison)")
    print()
    print("  # Try different skew presets:")
    print("  python tpch_skew_demo.py --preset heavy")
    print("  python tpch_skew_demo.py --preset extreme")
    print("  python tpch_skew_demo.py --preset realistic")
    print()
    print("  # Run more queries:")
    print("  python tpch_skew_demo.py --queries 1,3,5,6,14")
    print()
    print("  # Larger scale factor:")
    print("  python tpch_skew_demo.py --scale 0.1")
    print("=" * 70)


if __name__ == "__main__":
    main()
