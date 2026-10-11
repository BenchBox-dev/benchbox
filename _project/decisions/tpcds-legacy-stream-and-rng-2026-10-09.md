# Decision: TPC-DS legacy stream ordering and RNG handling

Date: 2026-10-09
Status: Decided and in place.
Related: `_project/decisions/tpcds-legacy-stream-and-rng-2026-10-09.md`,
`benchbox/core/tpcds/streams.py`, `benchbox/core/tpcds/power_test.py`,
`benchbox/core/tpcds/throughput_test.py`, `benchbox/core/tpcds/benchmark/runner.py`,
`benchbox/core/dataframe/query_resolution.py`, `benchbox/core/tpcds/c_tools.py`.

## Context

TPC-DS stream generation historically relied on Python-level permutation logic and
process-global RNG seeding (`random.seed`) rather than calling official `dsqgen -STREAMS`.
Item `tpcds-throughput-permutation-compliance` resolved this for the primary throughput
path by adopting `dsqgen -STREAMS`. However, legacy permutations and RNG calls remain across
several callers. This decision record documents the current state, caller matrix, empirical
divergence from official TPC-DS specification ordering, options considered, and approved
dispositions.

## Caller Matrix

| Caller | File and Line (Live Head) | Stream ID & Seed Behavior | Query Ordering & Substitution Strategy |
|---|---|---|---|
| Power Test | `benchbox/core/tpcds/power_test.py:212,373` | Calls `create_standard_streams(..., num_streams=1)`. Uses `stream_id` (default 0), seed `seed + stream_id`. | Uses Python `TPCDSPermutationGenerator` (deterministic swap seeded with 19620718). Does NOT match official `dsqgen` Appendix D stream 0. |
| Throughput Fallback | `benchbox/core/tpcds/throughput_test.py:308` | Reached only when `enable_preflight=False`. Default path (`line 236`) uses official `dsqgen -STREAMS`. | Calls `create_standard_streams` fallback with legacy permutation and per-stream Python parameter generation. |
| Stream Files Exporter | `benchbox/core/tpcds/benchmark/runner.py:645,674` | `generate_streams(num_streams, rng_seed)`. 0-based stream index. | Emits legacy stream query files. Line 674 writes header `-- Compliant with TPC-DS specification` (non-compliant historical label). |
| DataFrame Query Resolution | `benchbox/core/dataframe/query_resolution.py:146,257` | Line 146 (`get_tpcds_legacy_queries`) uses seed `42 + stream_id`. Line 257 (`_resolve_tpcds_dataframe_queries`) calls `create_standard_streams` with `base_seed = 42 + stream_id`. | Legacy 99-query ordering mapped to DataFrame implementations; parameter overrides bound via `production_binding.py`. |
| Global RNG Calls | `benchbox/core/tpcds/streams.py:52,112`, `benchbox/core/tpcds/c_tools.py:463` | `random.seed(seed)` mutates Python process-wide global random state. | Affects any concurrent caller in the same process relying on Python's built-in `random` module. |

## Empirical Evidence

A direct comparison of legacy stream 0 against official `dsqgen` stream 0 was measured
using `uv run` on the bundled binaries and generator tools:

```bash
uv run -- python -c "
from benchbox.core.tpcds.streams import create_standard_streams, generate_dsqgen_streams
from benchbox.core.tpcds.queries import TPCDSQueryManager
import hashlib

qm = TPCDSQueryManager()
sm = create_standard_streams(query_manager=qm, num_streams=1, base_seed=42)
legacy_s0 = sm.generate_streams()[0]
legacy_ids = [sq.query_id for sq in legacy_s0]

dsqgen_s0 = generate_dsqgen_streams(num_streams=1, scale_factor=1.0, seed=42)[0]
dsqgen_ids = [sq.query_id for sq in dsqgen_s0]

print('Differing query positions:', sum(1 for l, d in zip(legacy_ids, dsqgen_ids) if l != d), 'out of', len(legacy_ids))
print('Legacy Query 0 SHA256:', hashlib.sha256(legacy_s0[0].sql.encode()).hexdigest())
print('DSQGen Query 0 SHA256:', hashlib.sha256(dsqgen_s0[0].sql.encode()).hexdigest())
"
```

Results:
- Total query entries: 103 queries (99 base queries plus multi-part variants 14, 23, 24, 39).
- Total unique query IDs: 99 across both generators.
- Differing positions: 103 out of 103 positions differ between legacy stream 0 and official `dsqgen` stream 0.
- Legacy Stream 0 first 10 query IDs: `[31, 29, 20, 25, 21, 23, 23, 19, 17, 27]`
- DSQGen Stream 0 first 10 query IDs: `[96, 7, 75, 44, 39, 39, 80, 32, 19, 25]`
- Legacy Query 0 (Q31) SQL text SHA-256: `22e6b7bc6068c3834cb063501366a10a321db3645fcee3be0075f520df493041`
- DSQGen Query 0 (Q96) SQL text SHA-256: `9e19c62679e98ea9d3edd661b7b7504cb23451725af167f52cb5e04d91da1e39`

The legacy stream order is completely distinct from the official TPC-DS specification Appendix D sequence.

## Options Considered

1. **Option A — Retain legacy defaults and disclose them honestly.** No new binary dependency for legacy Power, DataFrame resolution, or exported stream files. If the legacy query manager cannot generate SQL, its stream-generation error propagates; Power records generation/execution failure and does not silently switch to `dsqgen`. Timing consequence: Power begins its `total_time` clock before building its legacy sequence, so permutation and query SQL generation are inside its reported elapsed interval; the database query loop follows. Throughput's Python fallback builds streams inside `_execute_stream`, after the test clock starts, while the default official path generates and translates `dsqgen` streams before `StreamRunner.execute` starts its worker window. Stream-file generation happens in the export call, not a Power/Throughput timed window. DataFrame resolution builds the ordering and binds variants before query execution; it is not included in Power/Throughput metrics unless an embedding caller wraps resolution in its own timer.
2. **Option B — Correct the legacy permutation in place to Appendix D order.** Could use an in-process order without invoking a binary, but would change Power's existing measured sequence and comparability. Replacing only order would not, by itself, establish that generated substitutions, seed behavior, or raw SQL match `dsqgen`; the generated query path and its failure semantics would remain distinct. It inherits the current callers' timing boundaries: Power and fallback generation remain inside their respective timers; stream-file export and DataFrame resolution remain outside benchmark phase timing.
3. **Option C — Use official `dsqgen -STREAMS` output everywhere.** Requires the platform-appropriate dsqgen executable plus its staged templates and data; missing executable/templates, process launch failure, timeout, or nonzero exit raises `DSQGenStreamsError` rather than returning a legacy stream. Throughput's default path wraps generation failure as a runtime error and records failed test execution; it does not fall back to legacy. A universal switch would introduce this dependency and fail-closed behavior to Power, DataFrame resolution, and stream-file export, where the current legacy path can run through the query manager without dsqgen. DataFrame engines have no dsqgen stream path of their own; adopting this option would require resolving generated SQL to DataFrame implementations and preserving their parameter-binding contract. Timing would also shift if official generation were done within a caller's measured window; the approved spec sequence explicitly pre-generates streams before its timed windows.

## Evidence: Order, SQL Identity, and Comparability

A direct comparison was run with the bundled tools and the same seed (42), scale factor 1.0, and query manager:

```bash
uv run -- python -c "
from benchbox.core.tpcds.streams import create_standard_streams, generate_dsqgen_streams
from benchbox.core.tpcds.queries import TPCDSQueryManager
import hashlib
qm = TPCDSQueryManager()
legacy = create_standard_streams(query_manager=qm, num_streams=1, base_seed=42).generate_streams()[0]
dsqgen = generate_dsqgen_streams(num_streams=1, scale_factor=1.0, seed=42)[0]
legacy_ids = [sq.query_id for sq in legacy]
dsqgen_ids = [sq.query_id for sq in dsqgen]
print('Differing query positions:', sum(a != b for a, b in zip(legacy_ids, dsqgen_ids)), 'out of', len(legacy_ids))
print('Legacy Query 0 SHA256:', hashlib.sha256(legacy[0].sql.encode()).hexdigest())
print('DSQGen Query 0 SHA256:', hashlib.sha256(dsqgen[0].sql.encode()).hexdigest())
"
```

- Both streams contain 103 entries (99 base query IDs plus the multi-part variants for 14, 23, 24, and 39) and 99 unique query IDs.
- All 103 positions differ. The first ten legacy IDs are `[31, 29, 20, 25, 21, 23, 23, 19, 17, 27]`; dsqgen's are `[96, 7, 75, 44, 39, 39, 80, 32, 19, 25]`.
- The first SQL text SHA-256 digests differ: legacy `22e6b7bc6068c3834cb063501366a10a321db3645fcee3be0075f520df493041`; dsqgen `9e19c62679e98ea9d3edd661b7b7504cb23451725af167f52cb5e04d91da1e39`.
- A raw SQL digest establishes byte-for-byte identity only for the exact encoded text and hashed bytes. A mismatch proves those texts differ; it does not isolate whether order, query ID, variant, formatting, dialect translation, or parameter substitution caused the mismatch, and it does not prove semantic result divergence. A matching digest would establish identical bytes for that query text, not equivalent stream order, complete stream identity, or comparable benchmark conditions. These two first entries are different query IDs (Q31 versus Q96), so their digest mismatch is not a same-query substitution comparison.

## Per-Caller Effects and Decision

The existing caller matrix above identifies each path and seed policy. Its timing implications are distinct: Power starts its timer before sequence construction; the fallback throughput path constructs streams after the throughput timer starts, whereas the default dsqgen path pregenerates before the runner's measurement window; stream-file writing is an explicit export operation outside those benchmark windows; DataFrame ordering/resolution and binding happen before DataFrame query execution. Thus generation cost is included for legacy Power and fallback throughput, excluded from the default throughput worker window, and not part of stream-file/DataFrame benchmark phase timing.

- **Approved: Option A for every present default path.** Keep default ordering, seeds and substitution behavior unchanged. Power@Size remains the legacy permutation and is not official Appendix D order; this preserves the historical corpus baseline. DataFrame substitutions remain resolved through the existing binding path.
- **Approved: item 8 opt-in specification sequence, not a default migration.** Only when `tpcds_spec_run_sequence=true`, Power uses dsqgen stream 0 and the throughput tests use dsqgen streams. This is a separately requested sequence with generation before its timed windows; all present defaults above remain unchanged.
- **Rejected for now: Option C as a default for Power, DataFrame engines and stream-file export.** The executable/template dependency and fail-closed behavior would change availability and failure semantics; Power results would no longer share the legacy sequence baseline, and DataFrame resolution has no native dsqgen stream execution path. Official dsqgen remains a recommended direction for a separately authorized default migration, not authorization to make one here.
- **Option B is not selected.** Changing only permutation order is not a complete substitution/seed equivalence migration and would still alter Power's baseline.

## Downstream Contract if a Default Algorithm Migration Is Later Approved

This decision makes no contract change. A later approved migration must update the TPC-DS Power/Throughput and DataFrame guidance to identify the new order, parameter/substitution and seed semantics, binary prerequisite and failure behavior, and whether generation time is inside each reported interval. It must correct the stream-file export's false compliance label, state that legacy and official-path results are not directly comparable, and update compatibility/corpus interpretation so historical legacy Power results are not represented as the new sequence. DataFrame resolution and parameter binding must be revised together if that caller migrates; changes to result schemas or metric definitions are not implied by this ordering decision. Query 46 templates, parameter mapping, SQL/DataFrame implementations, and reference behavior are unchanged by the current decision. No separate tracker item is created here.

## Query 46 and Historical Item Reconciliation

Query 46's parameter template and SQL/DataFrame implementations remain unchanged; this decision neither changes its reference behavior nor claims equivalence from the stream-order/hash measurement. The completed `tpcds-throughput-permutation-compliance` claim remains supported for the primary default throughput path: `_pregenerate_stream_queries` calls `generate_dsqgen_streams` (official `dsqgen -STREAMS`), while the documented legacy fallback is reached only with `enable_preflight=False`. The completed `dataframe-parameter-substitution-parity` claim remains supported by the current DataFrame resolution path: it builds legacy streams and binds parameter overrides through `production_binding.bind_power_stream_queries`. These completed items are reconciled, not reopened.

## What This Does Not Do

- Does not alter default query ordering, seeds, or parameters in `power_test.py`, `throughput_test.py`, or `query_resolution.py`.
- Does not modify Query 46 behavior or parameter templates (`_q46` in `parameter_adapters.py` and `q46_expression_impl`/`q46_pandas_impl` in `queries.py` remain unchanged).
- Does not reopen completed tracker items `tpcds-throughput-permutation-compliance` or `dataframe-parameter-substitution-parity`.
- Does not implement the item 8 opt-in sequence; it records that distinct approved proposal separately from present defaults.

## Recommended Follow-ups (Recorded, not created as tasks)

1. Remove the misleading `-- Compliant with TPC-DS specification` comment header from `benchbox/core/tpcds/benchmark/runner.py:674`.
2. Replace process-wide `random.seed` calls in `streams.py` and `c_tools.py` with an isolated instance (`random.Random(seed)`).
3. Evaluate a future major-version migration of default Power@Size to official `dsqgen` stream 0, with the downstream documentation, compatibility and timing changes described above.
