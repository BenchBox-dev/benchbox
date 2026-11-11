# TPC-DS Unofficial Subscale Data Contract

**Date:** 2026-04-14
**Source:** `patch-and-redistribute-tpcds-dsdgen-subscale-support` Gate Zero probe (w3, w4)
**Status:** Binding for implementation of `support-unofficial-tpcds-scales-with-methodology-guardrails` (w7)

---

## Scope

This document defines the exact expectations for TPC-DS data generated at scale factors below 1.0
using the BenchBox patched `dsdgen` binaries. It is derived from measured Gate Zero evidence in
`_sources/tpcds-subscale-probe.md`, not from assumptions.

"Subscale" means `scale_factor < 1.0`. The currently supported subscale range is `0.01 <= sf < 1.0`.
Scale factors at or above 1.0 are governed by the existing official TPC-DS data contract, which
this document does not replace.

---

## Table Presence Contract

**All 25 tables must be present** in a complete subscale TPC-DS dataset:

- 24 data tables (same set as official SF >= 1.0 runs)
- 1 metadata table (`dbgen_version`)

No table is permitted to be absent ("missing") under the subscale contract. Tables that would
round to zero rows instead produce **exactly 1 row** (the min-row floor enforced by the
BenchBox dsdgen patch). A missing table is a generation failure, not a subscale contract
exception.

---

## Row Count Formula

For each table, the expected row count at subscale is:

```
expected_rows(table, sf) = max(1, floor(sf1_baseline(table) * sf))
```

where `sf1_baseline(table)` is the standard SF=1.0 row count for that table.

### Critical Distinction: "Fixed" Tables Do NOT Apply Below SF=1.0

The official TPC-DS data contract designates a set of "fixed-size" tables whose row counts do
not grow with scale factor for `sf >= 1.0`:

```
call_center, income_band, promotion, reason, ship_mode,
store, time_dim, warehouse, web_site
```

**At SF < 1.0, these tables are NOT fixed.** The patched generator applies the proportional
formula above to all tables, including the normally-fixed ones. This is intentional and matches
DuckDB's TPC-DS extension behavior (see `_sources/tpcds-subscale-probe.md` w10 findings).

Validation code must not apply the `fixed_size_tables` exemption at subscale.

### Measured Row Counts (Gate Zero Evidence)

The four Gate Zero tables at their SF=1.0 baselines and measured subscale outputs:

| Table | SF=1.0 (baseline) | SF=0.5 | SF=0.1 | SF=0.01 |
|-------|------------------|--------|--------|---------|
| `call_center` | 6 | 3 | 1 (floor) | 1 (floor) |
| `store` | 12 | 6 | 1 (floor) | 1 (floor) |
| `warehouse` | 5 | 2 | 1 (floor) | 1 (floor) |
| `web_site` | 30 | 15 | 3 | 1 (floor) |

"floor" indicates `floor(baseline * sf) = 0`, overridden to 1 by the min-row floor.

All checksums byte-identical across darwin-arm64, darwin-x86_64, linux-arm64, linux-x86_64
for every SF×table combination (see probe document for full SHA-256 table).

---

## Validation Rules

### Row Count Tolerance

Apply the **same 5% tolerance** used for official runs:

```
valid if: abs(actual_rows - expected_rows) <= max(1, int(expected_rows * 0.05))
```

The minimum-1-row floor means `expected_rows` is never 0, so the tolerance is always
at least 1 row. No special-casing of floor-clamped tables is required.

### Missing-Table Behavior

A missing table (file not present) must be treated as a **validation failure**, not a subscale
contract exception. The min-row floor ensures all tables should be generated.

### Manifest Fields

The `_datagen_manifest.json` for a subscale run must include these fields,
alongside the other standard manifest fields:

```json
{
  "benchmark": "tpcds",
  "scale_factor": 0.1,
  "compliance_class": "unofficial_subscale",
  "expected_table_count": 25
}
```

The `compliance_class` field is new and must be written by the generator and preserved through
schema export, import, and storage (see w5 for pipeline requirements).

---

## Comparability Rules

Subscale TPC-DS runs are **never comparable** to official TPC-DS results:

- `power_at_size`, `throughput_at_size`, and `qph_at_size` metrics must not be computed or
  displayed for `compliance_class == "unofficial_subscale"` runs.
- Subscale results must not appear in official TPC-DS rankings or cohorts.
- The `compliance_class` field propagates through result JSON, schema export, historical
  database storage, and publication metadata.

---

## Platform Capability Matrix

As of 2026-04-14, per the Gate Zero probe:

| Bundle | Subscale Capable | Status |
|--------|-----------------|--------|
| `darwin-arm64` | Yes | Patched binary deployed, Gate Zero passed |
| `darwin-x86_64` | Yes | Patched binary deployed, Gate Zero passed |
| `linux-arm64` | Yes | Patched binary deployed, Gate Zero passed |
| `linux-x86_64` | Yes | Patched binary deployed, Gate Zero passed |
| `windows-arm64` | No | Unpatched binary; exits 139 for sf < 1.0 |
| `windows-x86_64` | No | Unpatched binary; exits 139 for sf < 1.0 |

BenchBox must surface a clear error (not a crash) for subscale generation attempts on Windows.

---

## Open Questions Resolved

**Q: Which tables are mandatory vs optional-empty?**
A: All 25 tables are mandatory. The min-row floor ensures all tables have at least 1 row.
No table is expected to be absent or empty.

**Q: Should non-official SF>=1.0 TPC-DS runs require opt-in?**
A: No. Existing behavior for non-official SF>=1.0 runs (plain `benchbox run` without
`--official`) remains unchanged. SF<1.0 runs are also allowed without opt-in as a
development convenience; they are tagged `unofficial_subscale` and excluded from
official metrics. (Historical note: an `--allow-unofficial-scale` flag originally gated
SF<1 runs and is retained as a deprecated no-op for script compatibility.)

---

## References

- Gate Zero probe evidence: `_sources/tpcds-subscale-probe.md`
- DuckDB cross-check: `_sources/tpcds-duckdb-diagnostic.md`
- Patched source description: `_sources/tpc-ds/PATCHES.md` - "Sub-SF1 Scale Factor Support"
- Validation code: `benchbox/utils/data_validation.py`
- Generator manager: `benchbox/core/tpcds/generator/manager.py`
- Compliance classifier (to be added in w3): `benchbox/core/tpcds/compliance.py`
