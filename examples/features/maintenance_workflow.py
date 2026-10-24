#!/usr/bin/env python3
# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_EXAMPLES_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_EXAMPLES_DIR))

from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.tpch import TPCH


def print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def check_row_counts(adapter: DuckDBAdapter) -> dict[str, int]:
    orders_count = adapter.execute("SELECT COUNT(*) FROM orders")[0][0]
    lineitem_count = adapter.execute("SELECT COUNT(*) FROM lineitem")[0][0]

    return {"orders": orders_count, "lineitem": lineitem_count}


def main() -> int:

    print_section("TPC-H MAINTENANCE WORKFLOW DEMONSTRATION")
    print("""
This example shows the complete workflow for running TPC-H benchmarks
with the Maintenance Test, including proper database reload handling.

We will:
  1. Generate TPC-H data at small scale (SF=0.01 for speed)
  2. Load fresh database and check initial row counts
  3. Run Power Test (read-only queries)
  4. Run Throughput Test (concurrent read-only queries)
  5. Run Maintenance Test (INSERT and DELETE operations)
  6. Show database modifications (row counts changed)
  7. Demonstrate reload requirement for additional tests
""")

    scale_factor = 0.01
    output_dir = Path("./benchmark_runs/features/maintenance_workflow")
    db_path = output_dir / "tpch_maintenance_demo.duckdb"

    output_dir.mkdir(parents=True, exist_ok=True)

    print_section("STEP 1: Generate Benchmark Data")
    print(f"Scale Factor: {scale_factor}")
    print(f"Output Directory: {output_dir}")
    print()

    benchmark = TPCH(
        scale_factor=scale_factor,
        output_dir=output_dir,
        force_regenerate=False,
    )

    print("Generating TPC-H data files (this may take a moment)...")
    benchmark.generate_data()
    print("✓ Data generation complete!")

    print_section("STEP 2: Load Fresh Database and Check Initial State")
    print("Creating new database with clean data...")

    adapter = DuckDBAdapter(database_path=str(db_path), force_recreate=True)

    print("Loading TPC-H tables...")
    adapter.load_benchmark_data(benchmark)

    print("\nChecking initial row counts...")
    initial_counts = check_row_counts(adapter)
    print(f"  ORDERS table:   {initial_counts['orders']:,} rows")
    print(f"  LINEITEM table: {initial_counts['lineitem']:,} rows")

    print_section("STEP 3: Run Power Test (Sequential Queries)")
    print("Running Power Test - all queries execute sequentially...")
    print("This is a read-only test that does NOT modify the database.")
    print()

    power_result = adapter.run_benchmark(benchmark, test_execution_type="power")

    print("✓ Power Test Complete!")
    print(f"  Queries executed: {power_result.total_queries}")
    print(f"  Total time: {power_result.total_execution_time:.2f}s")
    print(f"  Average per query: {power_result.average_query_time:.2f}s")

    counts_after_power = check_row_counts(adapter)
    print("\nDatabase state after Power Test:")
    print(f"  ORDERS:   {counts_after_power['orders']:,} rows (unchanged ✓)")
    print(f"  LINEITEM: {counts_after_power['lineitem']:,} rows (unchanged ✓)")

    print_section("STEP 4: Run Throughput Test (Concurrent Queries)")
    print("Running Throughput Test - queries execute concurrently...")
    print("This is also a read-only test that does NOT modify the database.")
    print("Note: No reload needed between Power and Throughput!")
    print()

    throughput_result = adapter.run_benchmark(benchmark, test_execution_type="throughput")

    print("✓ Throughput Test Complete!")
    print(f"  Total queries: {throughput_result.total_queries}")
    print(f"  Total time: {throughput_result.total_execution_time:.2f}s")

    counts_after_throughput = check_row_counts(adapter)
    print("\nDatabase state after Throughput Test:")
    print(f"  ORDERS:   {counts_after_throughput['orders']:,} rows (unchanged ✓)")
    print(f"  LINEITEM: {counts_after_throughput['lineitem']:,} rows (unchanged ✓)")

    print_section("STEP 5: Run Maintenance Test (Data Modifications)")
    print("Running Maintenance Test - executes RF1 and RF2...")
    print()
    print("⚠️  WARNING: This test will PERMANENTLY MODIFY the database!")
    print()
    print("What Maintenance Test does:")
    print("  • RF1 (Refresh Function 1): INSERT new orders and lineitems (~0.1% of scale)")
    print("  • RF2 (Refresh Function 2): DELETE old orders and lineitems (same volume)")
    print()
    print("Note: No reload needed before Maintenance!")
    print("      Maintenance can run immediately after Power/Throughput.")
    print()

    input("Press Enter to run Maintenance Test and modify the database...")

    maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")

    print("\n✓ Maintenance Test Complete!")
    print(f"  Operations executed: {maintenance_result.total_queries}")
    print(f"  Total time: {maintenance_result.total_execution_time:.2f}s")

    print_section("STEP 6: Verify Database Modifications")
    print("Checking row counts after Maintenance Test...")

    final_counts = check_row_counts(adapter)

    print("\nRow count comparison:")
    print(f"  {'Table':<12} {'Before':<12} {'After':<12} {'Change':<12}")
    print(f"  {'-' * 50}")

    orders_change = final_counts["orders"] - initial_counts["orders"]
    lineitem_change = final_counts["lineitem"] - initial_counts["lineitem"]

    print(f"  {'ORDERS':<12} {initial_counts['orders']:<12,} {final_counts['orders']:<12,} {orders_change:+,}")
    print(f"  {'LINEITEM':<12} {initial_counts['lineitem']:<12,} {final_counts['lineitem']:<12,} {lineitem_change:+,}")

    print("\n⚠️  DATABASE HAS BEEN MODIFIED!")
    print()
    print("What happened:")
    print(f"  1. RF1 inserted ~{abs(orders_change):,} new orders (if positive) or")
    print(f"     RF2 deleted {abs(orders_change):,} orders (if negative)")
    print(f"  2. Associated lineitem changes: {lineitem_change:+,} rows")
    print()
    print("Note: Row counts may be unchanged if RF1 inserts ≈ RF2 deletes,")
    print("      but the ACTUAL DATA is different (different order IDs)!")

    print_section("STEP 7: Demonstrate Reload Requirement")
    print("The database now contains modified data from Maintenance Test.")
    print()
    print("If you try to run Power or Throughput tests now, they will execute")
    print("against the modified database, producing INCORRECT results!")
    print()
    print("Example of the problem:")
    print()

    print("Query: SELECT COUNT(*), SUM(o_totalprice) FROM orders")
    print()

    print("  Current result (after Maintenance):")
    query_result = adapter.execute("SELECT COUNT(*), SUM(o_totalprice) FROM orders")
    count, total = query_result[0]
    print(f"    COUNT(*): {count:,}")
    print(f"    SUM(o_totalprice): ${total:,.2f}")
    print()
    print("  ⚠️  These values are DIFFERENT from the initial clean data!")
    print("     Running Power/Throughput tests now would use these wrong values.")

    print("\n" + "=" * 80)
    print("  TO RUN POWER OR THROUGHPUT AGAIN: MUST RELOAD DATABASE")
    print("=" * 80)
    print()
    print("Correct workflow to run tests again:")
    print()
    print("  # Reload database with clean data")
    print("  adapter = DuckDBAdapter(database_path=db_path, force_recreate=True)")
    print("  adapter.load_benchmark_data(benchmark)")
    print()
    print("  # Now safe to run Power/Throughput again")
    print("  power_result = adapter.run_benchmark(benchmark, test_execution_type='power')")

    print_section("WORKFLOW SUMMARY")
    print("""
✓ CORRECT SEQUENCES:

  1. Power + Throughput only (no Maintenance):
     generate → load → power → throughput
     (Can repeat power/throughput without reload)

  2. Complete benchmark with Maintenance:
     generate → load → power → throughput → maintenance
     (No reload needed before Maintenance!)

  3. Rerunning tests after Maintenance:
     generate → load → power → throughput → maintenance → [RELOAD] → power → throughput
     (MUST reload after Maintenance!)

✗ INCORRECT SEQUENCES:

  1. Maintenance between Power and Throughput:
     generate → load → power → maintenance → throughput ❌
     (Throughput runs on modified data!)

  2. Maintenance before Power/Throughput:
     generate → load → maintenance → power → throughput ❌
     (Both run on modified data!)

  3. No reload after Maintenance:
     generate → load → power → throughput → maintenance → power ❌
     (Second Power test runs on modified data!)

KEY TAKEAWAY:
  • Maintenance can run immediately after Power/Throughput (no reload needed before)
  • Maintenance permanently modifies the database
  • MUST reload after Maintenance before running Power/Throughput again
""")

    print_section("DEMONSTRATION COMPLETE")
    print()
    print("You have learned:")
    print("  ✓ How to run the complete TPC-H benchmark workflow")
    print("  ✓ When Maintenance tests modify the database")
    print("  ✓ Why database reload is required after Maintenance")
    print("  ✓ The correct test execution sequence")
    print()
    print(f"Database location: {db_path}")
    print("(Database contains modified data from Maintenance Test)")
    print()
    print("Next steps:")
    print("  • See https://benchbox.dev/docs/guides/tpc/maintenance-phase-guide.html for complete reference")
    print("  • Try running with larger scale factors to see realistic data volumes")
    print("  • Explore test_types.py for individual test type demonstrations")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
