# Outline: Rewriting TPC-DS Q10 and Q35 for ClickHouse

**Post type:** Technical challenge (Style Guide post type 4), platform deep-dive series.
**Target length:** 1,800-2,400 words plus two SQL snippets (before/after) and one results table.
**Audience:** Data engineers who run TPC-DS, or write dialect adapters, against
ClickHouse. They know SQL and `EXISTS`; they may not know how ClickHouse plans a
correlated subquery or why a benchmark tool rewrites a standard query.
**Status:** Plan only. No measurements for this post exist yet beyond the ones
listed under "Evidence ledger". Do not draft until the experiments below run.

## Thesis

At TPC-DS SF1 on ClickHouse 25.8 under a 5.25 GiB container cap, Q10 fails as
written. Q35 has the same predicate shape. The post will test whether rewriting
each of the three correlated `EXISTS` predicates to an `IN` semi-join fits the
cap and preserves the result, then explain what the evidence establishes, why
the rewrite is safe for these two queries, and where it stops being safe.

## Planning-depth record (REVIEW-DEPTH-001)

- **L1, the obvious post:** "ClickHouse can't run Q10 and Q35, here are the
  rewrites." Show the diff, show the speedup, done.
- **L2, blind spots the L1 post would carry:**
  - "Required" is conditional. The UAT notes say Q10 passes un-rewritten with a
    12 GiB cap and fails at 4.67 and 7.10 GiB. The rewrite is required for a
    memory envelope, not for the engine. The post must say so in the TL;DR.
  - The test that landed with the Q10 rewrite checks SQL shape across three seeds.
    It does not compare answers between the original and the rewritten query. The
    only parity test (#2720) compares tuned and untuned loads. The post cannot
    claim "same answer" until an equivalence run exists.
  - A rewritten query is not the TPC-DS text. The post needs a plain statement of
    how BenchBox labels it, checked against the specification (clause on query
    modifications in `_sources/tpc-ds/specification/specification_4.0.0.pdf`).
  - `EXISTS` to `IN` is only mechanical without `NOT`. Q69 has two `NOT EXISTS`
    predicates next to one `EXISTS` and the UAT notes put its floor at 4.7-7.1 GiB.
    `NOT IN` against a nullable key returns no rows when the set contains NULL.
    This is the most useful teachable point, and it is the scope boundary.
  - The fact keys (`ss_customer_sk` and friends) are nullable in TPC-DS, so the
    `IN` sets can contain NULL. Whether that preserves this query's result depends
    on its boolean context and ClickHouse's `transform_null_in` behavior. Check
    both before claiming equivalence; E3 is the end-to-end check.
  - Version drift. ClickHouse has been adding correlated-subquery support, and the
    platform config audit notes the experimental setting is on by default. The
    rewrite may be unnecessary on a newer release. Measure before generalizing.
- **L3, reframe:** The stated problem is "the rewrites ClickHouse requires." The
  real constraint is a memory envelope meeting a plan shape. Q35 was rewritten in
  August (#1906); Q10 had the identical shape and was fixed on 2026-10-10 (#2879),
  after the UAT run caught it. That gap is the better story: a fix applied per
  query instead of per predicate shape, and what a shape-level audit across all
  99 templates might reveal. Frame the post around the shape, with Q10 and Q35
  as the two instances.

## Evidence ledger

Selected code and evidence paths were checked in this worktree based on
`origin/develop`. Re-check commit-sensitive claims against merged `develop`;
unsupported items remain listed below.

| Claim | Source |
| --- | --- |
| Three correlated `EXISTS` per query: store, web, catalog sales, joined to `date_dim` | `_sources/tpc-ds/query_templates/query10.tpl`, `query35.tpl` |
| Both rewrites replace each `EXISTS (...)` with `c.c_customer_sk IN (SELECT <key> FROM <fact>, date_dim WHERE ...)` | `benchbox/core/tpcds/benchmark/clickhouse_overrides.py` (after #2879) |
| Rewrites apply only to the base query (no variant) and only when the target dialect is ClickHouse | `benchbox/core/tpcds/benchmark/runner.py`, `_apply_target_dialect_overrides` |
| Rewrites raise `ValueError` if the generated text has not exactly three matching predicates | `clickhouse_overrides.py`, `test_tpcds_clickhouse_overrides.py` |
| Q35 rewrite landed 2026-08-25; Q10 on 2026-10-10 | commits `0f915fd5e`, `3c1862f57` |
| Q10 failed with "memory limit exceeded ... maximum: 4.67 GiB" in all warm-up and measured iterations, in all three tuned/untuned cells; it was the only failing TPC-DS query | `docs/development/tuning-adr-003-baseline-and-single-renderer.md` |
| Q10 needs more than 7.1 GiB, passes at 12 GiB; Q69 needs 4.7-7.1 GiB (ClickHouse 25.8, SF1, seed 42) | `docs/operations/uat-framework.md`, "TPC-DS SF1 query-memory floor" |
| Q10 after rewrite: peak about 0.3 GiB, about 0.1 s, against a failing 4 s | commit message of `3c1862f57` only. Re-measure; do not cite the commit. |

Not yet supported. Each needs a measurement or a primary source before the draft:

1. Why the original plan uses so much memory (the commit message says "a join
   against the full outer customer result"). Needs `EXPLAIN` output.
2. Q35 before and after numbers. No measurement was found.
3. Answer equivalence of original and rewritten forms.
4. Behavior on a current ClickHouse release.
5. TPC-DS specification position on this class of rewrite.

## Experiments (run before drafting)

Use a fixed SF1 dataset and identical seeded SQL forms on ClickHouse 25.8. The
runner applies Q10/Q35 overrides automatically, with no supported `--no-rewrite`
flag in this path. Before E1/E3, generate the translated SQL before the override
and the rewritten SQL, then run both against the same loaded tables through a
research-only direct-execution harness. Do not add a product bypass. Wrap each
command with `tests/uat/clickhouse_memory.py` so peak memory comes from one source.

| ID | Question | Method | Output |
| --- | --- | --- | --- |
| E1 | Does each form fit each cap? | Q10 and Q35, original and rewritten, caps 4, 5.25, 8, 12 GiB | table: pass/fail, peak GiB, median ms |
| E2 | What does the planner do differently? | `EXPLAIN PLAN`, `EXPLAIN PIPELINE`, and `system.query_log` rows read for each form | two trimmed plans, one sentence each |
| E3 | Same answer? | Original at 12 GiB vs rewritten at 5.25 GiB, full result multiset comparison for seeds 1-3 | pass/fail per seed, ignoring row order but preserving duplicate counts; retain the result-diff evidence |
| E4 | Does the rewrite still matter on a current release? | Repeat E1 at 5.25 GiB on the latest stable ClickHouse | one row per version |
| E5 | Where does it stop? | Q69 un-rewritten at 5.25 and 8 GiB; a hand-written `NOT IN` against a fact key containing NULL | one paragraph with the NULL result |

If E1 shows Q35 passes un-rewritten at 5.25 GiB, change the thesis: Q35 is then a
consistency rewrite, and Q10 is the memory case. The outline below still works.

## Post outline

### Title and metadata
- Title (43 characters): "Rewriting TPC-DS Q10 and Q35 for ClickHouse"
- Summary: one sentence naming the memory cap, version, and scale factor.
- TL;DR: the cap-conditional finding, whether E3 supports an equivalence claim
  and its bound, and the one query where the same trick would give a wrong answer.
- meta_description (150-160 chars), written after E1.
- Tags: tpc-ds, clickhouse, query-rewrite, semi-join, memory, dialect-translation,
  benchmarking, methodology.

### 1. Introduction (150-200 words)
- Open with the failure: Q10, Code 241, 4.67 GiB, ClickHouse 25.8, SF1.
- Define in-line: correlated subquery (inner query refers to the outer row),
  semi-join (keep outer rows that have at least one match, never duplicate them).
- Say what the reader gets: the rewrite, equivalence evidence if E3 passes, and
  the limits.

### 2. The two queries (250-300 words)
- Side-by-side structure of Q10 and Q35: same three `EXISTS`, same
  `store AND (web OR catalog)` shape, same `date_dim` join.
- What differs: Q10 filters the outer side by five counties and uses
  `d_moy BETWEEN m AND m+3`; Q35 uses `d_qoy < 4` and picks its aggregates from a
  list. Include a short table; use E1 numbers once available.
- Do not add a full-query SQL listing; the before/after predicate snippets in
  section 4 carry the `[YEAR]` dsqgen parameter.

### 3. What failed and why (300-350 words)
- E1 table: the cap sweep, original vs rewritten.
- E2: the plan difference in plain terms. Quote only what `EXPLAIN` shows. If the
  mechanism is not visible in the plan, say that and give rows read and peak
  memory instead.
- Neutral framing: report the measurement and its conditions. No verdict on
  ClickHouse. State that at 12 GiB Q10 passes as written.

### 4. The rewrite (250-300 words)
- Show a before/after SQL snippet for one predicate; describe the other two
  substitutions in prose.
- What BenchBox does: regex over the generated text, applies to the base query
  only, applies for every ClickHouse deployment mode, fails closed on a changed
  shape. Explain fail-closed with the test that proves it.
- Why not a SQL-rewriting library or a `SETTINGS` change: record what was tried
  or ruled out only if the repository has evidence. Ask the author before writing.

### 5. Why the answer does not change, if E3 passes (250-300 words)
- Explain the `EXISTS` to `IN` equivalence only within the query's filtering
  context, then the NULL argument: a non-matching `IN` against a set containing
  NULL is unknown, `WHERE` treats unknown as false, and no `NOT` flips it.
- Report E3 with the seeds and scale factor it covers. If results differ, do not
  make the equivalence claim; investigate before drafting this section.
- Where it breaks: `NOT EXISTS` (Q69, and Q16/Q94 if confirmed) against
  `NOT IN`. Show the E5 NULL result. Say plainly that BenchBox leaves those
  queries alone.

### 6. What "official" means here (100-150 words)
- BenchBox emits this as a ClickHouse dialect rendering. State how results are
  labeled, with the specification clause cited from the repo PDF.
- Keep it to what the specification says and what BenchBox does. No compliance
  verdict on any vendor.

### 7. Same shape, two fixes six weeks apart (150-200 words)
- Q35 in August, Q10 in October, found by a memory-floor run.
- The shape-level check: list which of the 99 templates contain a correlated
  `EXISTS`, and which ran in the last full UAT. Include it only if an existing
  inventory can produce it; do not add a script solely for the post.

### 8. Methodology and limitations (200-250 words)
- Hardware, ClickHouse version and build, container cap mechanism, seeds, runs,
  cold-start protocol, BenchBox commit.
- Limits: SF1 only, one engine version unless E4 covers more, single node.
  The ADR itself marks SF10 and above as unmeasured.

### 9. Reproduce it (100-150 words)
- Show the BenchBox command for the rewritten Q10/Q35 power subset and a
  research-only command or script for the original translated SQL. Do not imply
  that `--queries` disables the override.
- Link raw result files and the result-multiset comparison report.
- Invite issues for other engines that plan correlated `EXISTS` the same way.

### 10. Citations
- TPC-DS specification 4.0.0 (repo PDF), clause on query modifications.
- ClickHouse documentation: `IN` operators and `transform_null_in`; correlated
  subqueries and the experimental setting; release notes for the version where
  support changed. Verify each URL and record the access date.
- Internal: tuning ADR 003 and the UAT framework note, linked as repository files.

## Open decisions for the author

1. **Series.** This outline sits in `platform-deep-dives` because it centers on a
   ClickHouse plan. `building-benchbox` fits if you would rather lead with the
   dialect-override design and the shape-audit story. Section 7 moves up.
2. **Scope of section 5.** Include the Q69 and `NOT IN` demonstration, or leave
   it as one sentence. The demonstration is the strongest teaching content and
   costs one extra experiment (E5).
3. **Timing.** Hold publication until E3 passes. The unsupported equivalence
   claim is the main risk to the post.

## Next step

Run E1-E3 and attach the results under
`_blog/platform-deep-dives/research/clickhouse-tpcds-q10-q35-verification-<date>.md`.
Then run the `research` action to fill the ledger, and `draft`.
