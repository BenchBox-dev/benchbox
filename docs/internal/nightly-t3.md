# Nightly T3 validation

**Audience:** maintainers who own a failing nightly domain, and release managers
checking whether open nightly failures block a release.
**Workflow:** `.github/workflows/nightly-v2.yml` (runs daily at 07:30 UTC and on
`workflow_dispatch`).

Tier 3 covers checks that are too slow, too broad, or too environment-specific
for pull requests. The workflow runs each check family as its own job, so one
red domain does not hide the others, and each domain uploads its own artifacts.

## Domains and labels

| Label | Job | What it checks |
|-------|-----|----------------|
| `t3:docker` | `docker` | PostgreSQL, ClickHouse, and Trino live suites against the compose stacks in `docker/`. |
| `t3:cloud` | `cloud` | Credentialed live plan-capture tests for Snowflake, BigQuery, Redshift, and MotherDuck. Off by default. |
| `t3:perf` | `perf` | DuckDB TPC-H SF=0.01 power phase compared with the committed baseline; fails on a regression above 10%. |
| `t3:matrix` | `matrix` | Wheel build and install smoke on Python 3.11 to 3.14 across Ubuntu, macOS, and Windows, plus one bundled `dbgen` and one `dsdgen` run per runner. |
| `t3:browser` | `browser` | Results Explorer Firefox and WebKit smoke suites. |
| `t3:extension` | `extension` | DuckDB `datasketches` community-extension smoke, which catches upstream rebuilds. |
| `t3:install` | `install` | Installs the latest PyPI release outside the checkout and imports it. |
| `t3:drift` | `drift` | Corpus drift and privacy scan, submission-validator drift, cross-surface baseline drift, and vendor pricing drift. Scheduled runs execute it on Mondays (UTC); a manual run executes it unless `run_drift` is cleared. |
| `t3:quarantine` | `quarantine` | Runs tests marked `quarantine`. It passes with a notice until the marker is registered in `pytest.ini`. |
| `t3:linkcheck` | `linkcheck` | Checks external documentation links with Sphinx and uploads the linkcheck report. |
| `t3:liveness` | `liveness` | Fails when a workflow with a `schedule:` trigger has no scheduled run inside its cadence window, and prints the newest ten scheduled runs of any workflow it flags. Runs `scripts/scheduled_workflow_liveness.py`. |
| `t3:windows` | `windows` | The fast unit tier on `windows-latest` with Python 3.12 (no coverage gate). The matrix domain only builds and smoke-tests on Windows; this is the Windows unit suite. |
| `t3:durations` | `durations-refresh` | Records pytest `--durations` and JUnit reports for the fast and slow tiers in the `t3-durations` artifact. It also regenerates the duration file there when the tooling is present. Failed sampled tests retain their artifacts and fail the domain; missing verdicts fail closed. |

## Live cloud tests are opt-in

The `cloud` job runs only when the repository variable
`BENCHBOX_T3_CLOUD_ENABLED` is exactly `true`. The variable is unset by default,
so the job is skipped. It runs trivial `SELECT 1` plan-capture queries only, to
stay inside free-tier and trial budgets. Setting the variable is a maintainer
decision; do not set it in a pull request.

## Issues

The final `report` job is the only job with `issues: write`. For each domain it
keeps at most one open issue carrying that domain's label:

- A failed domain opens the issue, or comments on it if one is already open.
- A passing domain closes its open issue with a link to the passing run.
- A skipped domain (cloud disabled, or drift on a non-Monday) changes nothing.

Each issue links to the failing run. Its artifacts are attached to that run.

## Owner response policy

- The owner of the failing area fixes the failure or reverts the change that
  caused it within one working day of the issue opening.
- If the cause is an external service, upstream release, or runner outage, the
  owner comments on the issue with the cause and the expected resolution date
  within the same working day.
- A maintainer adds the `release-blocking` label to an open `t3:*` issue when
  the failure affects what a release ships. An open issue with that label blocks
  the release until a passing nightly run or a merged fix closes it. The release
  manager checks for such issues (`gh issue list --label release-blocking
  --state open`) before cutting a release; `scripts/release_readiness_check.py`
  does not check them yet.
- Do not quarantine a test to clear a red domain unless the test has an owner,
  an expiry, and a tracking issue.
