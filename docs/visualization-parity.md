# Visualization Parity - CLI↔Explorer Contract

BenchBox has two visualization layers that must produce identical numbers:

| Layer | Location | Language |
|-------|----------|----------|
| CLI charts | `benchbox/core/visualization/ascii/` | Python |
| Explorer charts | `explorer/src/` in `BenchBox-dev/benchbox-site` | TypeScript/Preact |

The parity infrastructure ensures these never drift apart.

## Contract files

Fixtures live in `tests/parity/fixtures/` and are **checked into git**. They
reach the site repository in the site-inputs bundle (`explorer/parity`), where
the Explorer's Vitest parity suite (`chartMath.parity.test.ts`) asserts them.
Each file is a JSON contract for one chart math helper:

| Fixture | TS helper | Python reference |
|---------|-----------|-----------------|
| `heatmap_color.json` | `colorForCell` | `benchbox/core/visualization/ascii/heatmap.py` |
| `lightness_for_cell.json` | `lightnessForCell` | same |
| `speedup_ratio.json` | `speedupRatio` | `benchbox/core/visualization/ascii/normalized_speedup.py` |
| `delta_pct.json` | `deltaPct` | `benchbox/core/visualization/ascii/diverging_bar.py` |
| `sort_by_magnitude_desc.json` | `sortByMagnitudeDesc` | same |
| `per_query_speedup.json` | `perQuerySpeedup` | N/A - no Python CLI chart; originated in `Compare.tsx`, unified via `chartMath.ts` |
| `geomean_ms.json` | `geomeanMs` | Explorer publish pipeline, `transformer.py` `_display_geomean_ms` |
| `box_stats.json` | `computeBoxStats` | Explorer publish pipeline, `transformer.py` `_compute_box_stats` |
| `cdf_ecdf.json` | `computeECDFPoints` | Explorer publish pipeline, `transformer.py` `_compute_ecdf` |
| `rank_table.json` | `computeRankTable` | Explorer publish pipeline, `transformer.py` `_compute_ranks` |
| `percentile_ladder.json` | `computePercentile` | Explorer publish pipeline, `transformer.py` `_compute_percentile` |

## Policy

**New chart types are added to `chart_types.py` first; the explorer follows.**

When you add a new chart type:
1. Register it in `benchbox/core/visualization/chart_types.py`.
2. Add the corresponding Python math helper to `generate_visualization_fixtures.py`.
3. Run `make parity-fixtures` to regenerate fixtures.
4. In `BenchBox-dev/benchbox-site`, implement the TS helper in
   `explorer/src/lib/chartMath.ts` and add cases to the Vitest parity suite
   (`chartMath.parity.test.ts`).
5. Core CI runs `make parity-check` on every PR; the site's CI runs the Vitest
   parity suite against the fixtures in the newest bundle.

## Workflow for changing a computation

If you change how a number is calculated (either Python or TS side), follow these steps:

1. Modify the Python implementation.
2. Regenerate fixtures with `make parity-fixtures`. This changes the contract.
3. Run `make parity-check` to confirm that the fixtures now match.
4. Commit the fixture diff. Reviewers must approve the numeric change.
5. After the change reaches `develop`, run the site's parity suite against the
   new bundle (`npm test -w explorer -- chartMath.parity` in
   `BenchBox-dev/benchbox-site`). The new fixture values must pass on the TS
   side too.

```bash
make parity-fixtures
make parity-check
git add tests/parity/fixtures/
```

**Never regenerate fixtures silently.** The diff is part of the review.

## Verifying parity without regenerating

`make parity-check` is the Python side: it regenerates into a temporary directory, diffs, and fails if different. The
site repository's `npm test -w explorer -- chartMath.parity` is the TS side: all fixture cases must pass.

```bash
make parity-check
```

## Float tolerance

The Vitest suite uses `Math.abs(result - expected) < 1e-9`.
Python and JavaScript IEEE-754 agree at this precision for the formulas used
here (log10, exp, division). If you add a formula where the tolerance is
insufficient, document it in the fixture case and increase the tolerance
in `assertParity()` for that specific case only.

## Null handling

Python `None` maps to JSON `null`, which TypeScript receives as `null`.
Every helper has at least one null-input case in its fixture. The parity
suite asserts `result === null` (not `NaN` or `undefined`).

## Failure modes

When `make parity-check` fails in CI, use this decision tree:

**1. Which side changed?**

Check `git diff` to see if the Python function or the TS helper was modified.

**2. Python changed intentionally** (you updated the math in `generate_visualization_fixtures.py` or the referenced Python module): regenerate the fixtures from the new Python source, confirm that no drift remains, and check that the TS side still passes.
```bash
make parity-fixtures
make parity-check
```

**3. TypeScript changed intentionally** (you updated a helper in `chartMath.ts`):
- The fixture is the Python source of truth - do NOT regenerate fixtures.
- Fix the TS helper in the site repository until its parity suite passes.
- If the TS change was intentional AND correct, update the Python reference to match, then regenerate.

**4. Neither changed (CI flake)**:
- Re-run the job.
- If persistent: check for float platform differences (rare; tolerance is 1e-9).
