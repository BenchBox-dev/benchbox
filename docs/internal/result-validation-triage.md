# Cross-Platform Validation Triage

When the nightly cross-platform validation workflow fails, this runbook
walks you from raw GitHub artifact to resolution.

See also: [Cross-Platform Result Validation](https://benchbox.dev/docs/development/result-validation.html) for
background on the comparator and tolerance model.

## 1. Locate the failure

On a failed run:

1. Open **Actions → Cross-Platform Validation** in the private repo.
2. Expand the failed matrix leg (e.g., `TPC-H SF=0.01 | DuckDB × datafusion`).
3. Click **Summary → Artifacts** and download the relevant artifact:

```
cross-platform-diff-tpch-<platform>.zip
```

The zip contains two files:

| File | Contents |
|------|----------|
| `validation-report-<platform>.json` | Full pytest-json-report - all tests, outcomes, tracebacks |
| `diff-artifact-<platform>.json` | Structured summary: failed query IDs + failure summaries |

## 2. Read the diff artifact

Open `diff-artifact-<platform>.json`:

```json
{
  "benchmark": "tpch",
  "scale_factor": 0.01,
  "reference_platform": "duckdb",
  "comparison_platform": "datafusion",
  "failed_queries": [
    {
      "query_id": "Q14",
      "longrepr": "AssertionError: DuckDB × DataFusion diverged for Q14:\nQ14: DIVERGED (duckdb vs datafusion, ref=1 rows, cmp=1 rows)\n  - row 0, col 0: reference=16.380... comparison=16.38..."
    }
  ]
}
```

Key fields:

- **`query_id`** - which TPC-H query failed (e.g. `Q14`)
- **`longrepr`** - the raw comparator output. Includes row/col locators and
  the first five cell mismatches (from `ComparisonReport.summary()`).

If `longrepr` shows `row -1, col -1` with messages like `"4 rows" vs "3 rows"`,
the row counts themselves diverged - a structural error, not a precision
issue. See §5 below.

## 3. Classify the divergence

There are three categories. Pick exactly one.

### A - Numeric precision, spec-permitted

The values agree within the TPC-H tolerance but the platforms use
different internal numeric types (e.g., DuckDB `float64` vs DataFusion
`Decimal(n,6)`).

Indicators:
- Divergence appears only in aggregate columns (SUM, AVG, percentage).
- The `reference_value` and `comparison_value` differ by less than `1e-4`
  (or proportionally by less than 0.01%).
- Query involves floating-point arithmetic: `100.00 *`, `SUM(...) / SUM(...)`,
  `AVG(...)` over DECIMAL columns.

**Resolution**: register a tolerance override - see §4.

### B - Bug in a platform adapter or query transformer

The values differ significantly, or row counts don't match, or a wrong
column value appears.

Indicators:
- Differences are large (e.g., an order of magnitude).
- Row counts differ.
- A date, string, or integer column is wrong (not just floating-point drift).
- The failure appeared after a recent adapter commit.

**Resolution**: roll back or fix the adapter - see §5.

### C - New unsupported query on a non-SQL platform

A Polars-DF or expression-runner test fails because the expression
translation doesn't yet support a particular SQL construct.

Indicators:
- The test fails with an exception, not a comparator assertion.
- `longrepr` contains `polars-df runner raised on Q<n>:` or similar.
- The query is newly added or the expression runner was modified.

**Resolution**: mark the query as `xfail` or `pytest.skip` in the test
class until the expression translation is implemented. Do not register
a tolerance override for a structural gap.

## 4. Registering a tolerance override (Category A)

A tolerance override is the correct fix when the divergence is numeric
precision permitted by the TPC-H specification.

**Step 1 - Identify the spec rule.**

| Column type | Spec rule |
|-------------|-----------|
| AVG / SUM of DECIMAL | TPC-H §2.6.4 - aggregate floating-point epsilon |
| Percentage ratio (`100.00 * SUM(a) / SUM(b)`) | TPC-H §2.6.4 - percentage aggregate |
| Unordered query result | TPC-H §2.6.3 - no ORDER BY implies no required ordering |

If you cannot map the divergence to a spec rule, it is Category B.

**Step 2 - Measure the actual delta.**

Run the two queries directly and record the maximum absolute difference:

```python
import duckdb, datafusion
duck_val = float(duck_rows[0][0])
df_val   = float(df_rows[0][0])
print(abs(duck_val - df_val))
```

Set up the two connections and fetch `duck_rows` and `df_rows` before this code (see the fixtures in `test_cross_platform.py`). A typical printed delta is `6.2e-07`.

Round the epsilon up to the nearest power of ten that comfortably covers
the observed delta:

| Observed delta | Use epsilon |
|----------------|-------------|
| < 1e-6         | 1e-6        |
| < 1e-4         | 1e-4        |
| < 1e-2         | 1e-2        |
| ≥ 1e-2         | re-classify as Category B |

**Step 3 - Add the override in the test module.**

Edit `tests/integration/validation/test_cross_platform.py`, in the
*Tolerance overrides* section at the top of the file:

```python
register_query_tolerance(
    "tpch",
    "Q14",
    Tolerance(
        epsilon=1e-4,
        rationale="TPC-H §2.6.4: percentage aggregate floating-point epsilon; "
                  "CASE SUM ratio differs in last 4-5 decimal places between "
                  "DuckDB float64 and DataFusion Decimal result.",
    ),
)
```

Rules:
- `rationale` MUST start with a spec section reference (`TPC-H §...`).
- Include the maximum observed delta in the rationale comment.
- Do NOT raise `epsilon` above what is needed to cover the observed delta.

**Step 4 - Open a pull request.**

```
git checkout -b fix/tpch-q14-datafusion-tolerance
# edit the test file
git add tests/integration/validation/test_cross_platform.py
git commit -m "fix(validation): register Q14 tolerance override for DuckDB × DataFusion"
gh pr create --repo joeharris76/benchbox-private \
  --title "fix(validation): Q14 DataFusion tolerance override" \
  --body "$(cat <<'EOF'
## Summary
- Registers epsilon=1e-4 tolerance for Q14 DuckDB × DataFusion.
- Observed delta: <cite actual value>.
- Spec anchor: TPC-H §2.6.4 (percentage aggregate floating-point epsilon).

## Verification
Run the nightly matrix locally:
    uv run -- python -m pytest tests/integration/validation/test_cross_platform.py \
        -v -m live_integration -k TestDuckDBDataFusion
EOF
)"
```

## 5. Rolling back an adapter change (Category B)

When a correct query produces wrong values or wrong row counts, a recent
adapter or query-transformer change broke something.

**Step 1 - Identify the culprit commit.**

```bash
git log --oneline --since="7 days ago" -- benchbox/platforms/datafusion/ \
    benchbox/platforms/clickhouse/ benchbox/platforms/dataframe/polars_df.py
```

This lists the commits that touched the comparison platform's adapter. Narrow the list to the date the failure first appeared; the nightly run history shows it.

**Step 2 - Reproduce locally.**

```bash
uv run -- python -m pytest \
    "tests/integration/validation/test_cross_platform.py::TestDuckDBDataFusion::test_query_results_match[Q14]" \
    -v -m live_integration

uv run -- python -m pytest tests/integration/validation/test_cross_platform.py \
    -v -m live_integration -k "TestDuckDBDataFusion and Q14"
```

The first command uses the full node ID, which is fastest because it runs one parametrized case. The second uses `-k` keyword matching and runs all Q14 cases across classes.

The comparator output shows which cell is wrong:
```
Q14: DIVERGED (duckdb vs datafusion, ref=1 rows, cmp=1 rows)
  - row 0, col 0: reference=16.380... comparison=0.0
```

**Step 3 - Fix forward or revert.**

Prefer a targeted fix in the adapter. Only revert if the fix is not
straightforward or the change needs more context.

Option A, fix forward, is preferred: edit the relevant platform module, verify locally, and open a PR. Option B reverts the offending commit:

```bash
git revert <commit-sha> --no-edit
git push private fix/revert-datafusion-regression
gh pr create --repo joeharris76/benchbox-private \
    --title "fix(datafusion): revert Q14 regression from <commit>"
```

**Step 4 - Add a regression test.**

Once the fix is in, add a focused unit test to
`tests/unit/core/test_cross_platform_validation.py` (or the platform's
own unit suite) that pins the specific row/column values that regressed.
The nightly matrix catches systemic drift; targeted unit tests catch this
specific regression from coming back.

## 6. After the fix lands

1. Trigger the nightly workflow manually via **Actions → Cross-Platform
   Validation → Run workflow** to confirm the fix.
2. If the fix is a tolerance override, updating
   `docs/internal/duplication-residuals.md` is NOT required, but
   consider updating the [result validation](https://benchbox.dev/docs/development/result-validation.html)
   tolerance table if the pattern is new.
3. Monitor the next two automated nightly runs to confirm stability.

## 7. When to file a TODO instead of fixing inline

File a TODO (see the `_project/TODO/` directory) rather than merging
an inline fix when:

- The divergence reveals a fundamental incompatibility in how a platform
  handles a SQL construct (e.g., `INTERVAL` literals, `EXTRACT`, date
  arithmetic) - the right fix is a new named rewrite in the bundled SQLGlot
  runtime applied through `apply_rewrite`, which deserves its own scoped item.
- The fix requires changes outside the validation layer (e.g., the
  platform adapter's `execute_query` path or the `TPCHDataFrameQueries`
  translation).
- The investigation uncovers multiple affected queries - batch the fix
  into a single scoped TODO rather than sprinkling tolerances.

TODO format for a comparator divergence:

```yaml
title: "Fix Q<n> <platform> divergence: <root cause>"
category: Testing and Quality Assurance
description: |
  Nightly validation (DuckDB × <platform>, TPC-H SF=0.01) reports divergence
  on Q<n>.  Root cause: <describe>.
  Spec anchor: TPC-H §<section>.
  Observed delta: <value>.
  Workaround: epsilon=<X> tolerance override in test_cross_platform.py
              (must be removed when this is fixed).
anti_patterns:
  - "DO NOT leave the epsilon workaround in place after the root cause is fixed"
```
