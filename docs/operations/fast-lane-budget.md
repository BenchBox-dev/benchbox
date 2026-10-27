# Fast-lane guards

The "fast lane" is every test collected under `pytest -m fast` (excluding
`slow`/`stress`/`resource_heavy`/`live_integration`) -- the required,
sub-few-minutes suite every develop PR runs in `code-test`
(`.github/workflows/ci.yml`).

## No test-count ceiling

There is no limit on how many tests the fast lane may collect. The
`code-test` job's `timeout-minutes` cap is a hard cancel, not a detection
mechanism; it does not notice a slowdown that stays under the cap.

## Fast-tier duration report

The `core` job runs `verify-fast-duration` on pull requests in `block` mode
(`VERIFY_MODE` in `scripts/build_fast_duration_baseline.py`); a `would-block`
verdict fails `core`. The thresholds are provisional: no false-block rate has
been measured, so a runner-noise block is investigated and, if it is noise,
the mode is set back to `report`, which is the rollback. The step downloads
only the PR head's `fast-junit` artifact and compares it with that head's committed
baseline in `tests/fixtures/fast_tier_baseline`. Calibration scales test times
to a reference CPU speed; it does not establish that runner noise is removed.
A missing calibration is rejected and a missing baseline is neutral.

The job summary shows the normalized summed time over tests present in both
runs, the delta, the summed-time ratio, the median per-test ratio, coverage of
the baseline ids, the runner speed factor and the pytest collection time. The
verdict is `would-block` when the ratio exceeds 1.30 and the delta exceeds 360
seconds, both strictly (provisional thresholds). New tests receive 0.5 seconds
each; only excess time counts toward the comparison. Job wall time is not a
metric. In report mode the command returns success; in block mode a
`would-block` verdict fails `core` at the duration step. Baseline refreshes are
visible in the PR diff and change the comparison data; changing a baseline
file does not exempt additional regressions. The required status contexts do
not change.

`quality/fast-duration-calibration.json` retains artifact IDs, exact heads,
run IDs, attempts, comparison paths and verdicts for a bounded convenience
sample. One no-test-change PR verdict and one pair of repeated-head verdicts
pass the provisional thresholds. The repeated head ranges from ratio 0.976
to 1.156 as the runner-speed factor ranges from 1.242 to 1.843. The second
run trips the original 1.15/+60-second thresholds. This does not establish
the cause of the variation or a false-block rate below 1%. Assorted PR
verdicts are not unchanged-code controls, and baseline source runs overlap
the observed PR runs rather than adding independent verdicts.

## Marker and path guards

A guard script stops the wrong kind of test from entering the lane. It reads
the fast-lane policy file:

- `forbidden_marker_expressions`: for each expression, the script collects
  `pytest -m "fast and <expression>"`. A non-empty selection is a
  `FAST_LANE_VIOLATION` (currently `resource_heavy`, `stress` and
  `live_integration`).
- `forbidden_path_substrings`: any fast-lane test whose node ID contains one
  of these substrings is a `FAST_LANE_VIOLATION`. The list is empty today.
- `enabled`: set to `false` to switch the guards off.

The guards run as `guard-fast-lane-markers` in the `code-lint` job of
`ci.yml`, in `lint.yml` for release branches, in `make ci-lint`, and in the
`timing-policy-fast-lane` pre-push hook, all with `--strict`. If pytest
cannot collect, the script reports `FAST_LANE_ENVIRONMENT_ERROR` instead of a
violation, because nothing is known to be wrong with the lane; run it from the
project environment (for example through `make ci-lint`). To fix a violation,
remove the forbidden marker from the test or move the test out of the fast
tier.

## Medium tier

The `medium-test` job in `.github/workflows/ci.yml` splits the medium tier
across four Linux runners. Its `timeout-minutes` (25 minutes) is a hard cancel
that backstops a hung tier, not a policy check. `make test-medium` runs the
full medium suite locally.
