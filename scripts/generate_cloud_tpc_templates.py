#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import yaml

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from benchbox.core.tuning.platform_capabilities import (
    map_candidate_to_platform,
)
from benchbox.core.tuning.workload_profiles import (
    TEMPORAL_PARTITION,
    load_tpc_tuning_profile,
)

CLI_DESCRIPTION = (
    "Generate cloud TPC tuned templates from the logical tuning profile.\n"
    "\n"
    "Renders one `<benchmark>_tuned.yaml` per platform/benchmark from the\n"
    "required candidates in `benchbox/core/tuning/profiles/tpc.yaml`, using each\n"
    "platform's logical-to-physical mapping\n"
    "(`benchbox.core.tuning.platform_capabilities.map_candidate_to_platform`).\n"
    "\n"
    "Platform rules honored here (mirroring the mappers, not reimplementing them):\n"
    "\n"
    "- BigQuery: at most 4 clustering columns per table; partitioning first.\n"
    "- Redshift: single DISTKEY per table (first distribution candidate);\n"
    "  remaining locality roles become compound sortkey entries.\n"
    "- Snowflake: fact tables only (TPC-H LINEITEM and ORDERS; TPC-DS sales, returns, and inventory facts),\n"
    "  at most 3 clustering columns per table so the adapter resumes automatic reclustering. Keys come\n"
    "  from the profile's existing candidates for the table, ordered lowest to highest cardinality at SF1:\n"
    "  date columns and date surrogate keys first, then foreign keys in ascending order of the referenced\n"
    "  table's row count, then the table's own key last.\n"
    "\n"
    "Only platforms whose mapped tuning types reach the physical layout at\n"
    "execution time are generated here. BigQuery partitioning/clustering and\n"
    "Redshift distribution/sorting are preview-only or inspect-and-log in the\n"
    "current adapters (see benchbox/core/tuning/capability_registry.py), so they\n"
    "stay out of the certified set until the adapters render them for real;\n"
    "Snowflake clustering renders post-load via ALTER TABLE ... CLUSTER BY and\n"
    "is the one certified platform in this generator today.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python scripts/generate_cloud_tpc_templates.py --write\n"
    "    uv run -- python scripts/generate_cloud_tpc_templates.py --check\n"
    "    uv run -- python scripts/generate_cloud_tpc_templates.py --write --output-root examples/tunings\n"
)

PLATFORMS = ("snowflake",)
BENCHMARKS = ("tpch", "tpcds")

SNOWFLAKE_FACT_TABLES = {
    "tpch": frozenset({"LINEITEM", "ORDERS"}),
    "tpcds": frozenset(
        {
            "STORE_SALES",
            "STORE_RETURNS",
            "CATALOG_SALES",
            "CATALOG_RETURNS",
            "WEB_SALES",
            "WEB_RETURNS",
            "INVENTORY",
        }
    ),
}

SNOWFLAKE_SF1_REFERENCE_ROWS = {
    "tpch": {
        "CUSTOMER": 150000,
        "ORDERS": 1500000,
        "PART": 200000,
        "SUPPLIER": 10000,
    },
    "tpcds": {
        "CUSTOMER": 100000,
        "ITEM": 18000,
        "PROMOTION": 300,
        "SHIP_MODE": 20,
        "STORE": 12,
        "WEB_PAGE": 60,
        "WEB_SITE": 30,
    },
}

SNOWFLAKE_FK_REFERENCE_TABLES = {
    ("LINEITEM", "L_ORDERKEY"): "ORDERS",
    ("LINEITEM", "L_PARTKEY"): "PART",
    ("LINEITEM", "L_SUPPKEY"): "SUPPLIER",
    ("ORDERS", "O_CUSTKEY"): "CUSTOMER",
    ("STORE_SALES", "SS_ITEM_SK"): "ITEM",
    ("STORE_SALES", "SS_CUSTOMER_SK"): "CUSTOMER",
    ("STORE_SALES", "SS_STORE_SK"): "STORE",
    ("STORE_SALES", "SS_PROMO_SK"): "PROMOTION",
    ("STORE_RETURNS", "SR_ITEM_SK"): "ITEM",
    ("STORE_RETURNS", "SR_CUSTOMER_SK"): "CUSTOMER",
    ("STORE_RETURNS", "SR_STORE_SK"): "STORE",
    ("CATALOG_SALES", "CS_ITEM_SK"): "ITEM",
    ("CATALOG_SALES", "CS_SHIP_MODE_SK"): "SHIP_MODE",
    ("CATALOG_RETURNS", "CR_ITEM_SK"): "ITEM",
    ("WEB_SALES", "WS_ITEM_SK"): "ITEM",
    ("WEB_SALES", "WS_WEB_PAGE_SK"): "WEB_PAGE",
    ("WEB_SALES", "WS_WEB_SITE_SK"): "WEB_SITE",
    ("WEB_SALES", "WS_SHIP_MODE_SK"): "SHIP_MODE",
    ("WEB_RETURNS", "WR_ITEM_SK"): "ITEM",
}


def snowflake_cluster_rank(candidate, benchmark: str) -> tuple[int, int, str]:
    if TEMPORAL_PARTITION in candidate.roles:
        return (0, 0, candidate.column)
    referenced = SNOWFLAKE_FK_REFERENCE_TABLES.get((candidate.table, candidate.column))
    if referenced is not None:
        return (1, SNOWFLAKE_SF1_REFERENCE_ROWS[benchmark][referenced], candidate.column)
    return (2, 0, candidate.column)


def snowflake_table_candidates(benchmark: str, table: str, candidates: list) -> list:
    if table not in SNOWFLAKE_FACT_TABLES.get(benchmark, frozenset()):
        return []
    return sorted(candidates, key=lambda candidate: snowflake_cluster_rank(candidate, benchmark))


HEADER = """primary_keys:
  enabled: true
  enforce_uniqueness: true
  nullable: false

foreign_keys:
  enabled: true
  enforce_referential_integrity: true
  on_delete_action: CASCADE
  on_update_action: RESTRICT

unique_constraints:
  enabled: true
  ignore_nulls: false

check_constraints:
  enabled: true
  enforce_on_insert: true
  enforce_on_update: true

platform_optimizations:
  z_ordering_enabled: false
  z_ordering_columns: []
  auto_optimize_enabled: false
  auto_compact_enabled: false
  bloom_filters_enabled: false
  bloom_filter_columns: []
  materialized_views_enabled: false

table_tunings:
"""


def _order_entries(entries: list[dict]) -> list[dict]:
    for position, entry in enumerate(entries, start=1):
        entry["order"] = position
    return entries


def _append_mapped_entry(
    partitioning: list[dict],
    clustering: list[dict],
    distribution: list[dict],
    sorting: list[dict],
    tuning_type: str,
    entry: dict,
    max_columns: int | None,
) -> None:
    if tuning_type == "partitioning":
        partitioning.append(entry)
    elif tuning_type == "clustering":
        if max_columns is not None and len(clustering) >= max_columns:
            return
        clustering.append(entry)
    elif tuning_type == "distribution":
        if not distribution:
            distribution.append(entry)
    elif tuning_type == "sorting":
        sorting.append(entry)


def render_table(
    platform: str,
    benchmark: str,
    table: str,
    candidates: list,
) -> dict:
    partitioning: list[dict] = []
    clustering: list[dict] = []
    distribution: list[dict] = []
    sorting: list[dict] = []

    if platform == "snowflake":
        candidates = snowflake_table_candidates(benchmark, table, candidates)

    for candidate in candidates:
        mapping = map_candidate_to_platform(platform, candidate)
        if mapping.decision != "mapped":
            continue
        for tuning_type in mapping.tuning_types:
            _append_mapped_entry(
                partitioning,
                clustering,
                distribution,
                sorting,
                tuning_type,
                {"name": candidate.column, "type": candidate.type},
                mapping.max_columns,
            )

    block: dict = {"table_name": table}
    if partitioning:
        block["partitioning"] = _order_entries(partitioning)
    if platform == "redshift":
        if distribution:
            block["distribution"] = _order_entries(distribution)
        if sorting:
            block["sorting"] = _order_entries(sorting)
    else:
        if clustering:
            block["clustering"] = _order_entries(clustering)
        if sorting and platform == "snowflake":
            extra = [e for e in sorting if e["name"] not in {c["name"] for c in clustering}]
            if extra:
                block["sorting"] = _order_entries(extra)
        elif sorting:
            block["sorting"] = _order_entries(sorting)
    return block


def render_template(platform: str, benchmark: str) -> str:
    profile = load_tpc_tuning_profile()
    by_table: dict[str, list] = defaultdict(list)
    for candidate in profile.required_candidates(benchmark):
        by_table[candidate.table].append(candidate)

    lines = [HEADER.rstrip()]
    for table in sorted(by_table):
        block = render_table(platform, benchmark, table, by_table[table])
        dumped = yaml.safe_dump({table: block}, sort_keys=False, default_flow_style=False)
        lines.append("  " + dumped.replace("\n", "\n  ").rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def template_path(output_root: Path, platform: str, benchmark: str) -> Path:
    return output_root / platform / f"{benchmark}_tuned.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--write", action="store_true", help="Write generated templates.")
    parser.add_argument("--check", action="store_true", help="Fail when generated output differs.")
    parser.add_argument(
        "--output-root",
        default="examples/tunings",
        help="Template tree root (default: examples/tunings).",
    )
    args = parser.parse_args(argv)
    if not args.write and not args.check:
        parser.error("pass --write and/or --check")

    root = Path(args.output_root)
    if not root.is_absolute():
        root = CHECKOUT_ROOT / args.output_root
    failures: list[str] = []
    for platform in PLATFORMS:
        for benchmark in BENCHMARKS:
            expected = render_template(platform, benchmark)
            path = template_path(root, platform, benchmark)
            if args.check and (not path.is_file() or path.read_text(encoding="utf-8") != expected):
                failures.append(str(path))
            if args.write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(expected, encoding="utf-8")
                print(f"wrote {path}")
    if failures:
        print("FAIL: cloud TPC templates out of date:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print("Run: uv run -- python scripts/generate_cloud_tpc_templates.py --write", file=sys.stderr)
        return 1
    if args.check:
        print(f"OK: cloud TPC templates up-to-date ({len(PLATFORMS) * len(BENCHMARKS)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
