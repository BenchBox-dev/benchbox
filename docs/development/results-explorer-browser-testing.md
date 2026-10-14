# Results Explorer - browser testing

**Audience:** Contributors changing `results-explorer/` who need to know what
browser coverage exists, how to run it, and how to add tests.

The site repository, `BenchBox-dev/benchbox-site`, keeps a copy of
`results-explorer/` as `explorer/` and runs this suite against it in its own
CI. A change made here reaches benchbox.dev only when it is replayed into that
repository by pull request.

The suite follows four design choices. It tests the built app (`dist/`), not
the dev server. Each run generates its own fixture corpus and leaves the
curated public corpus untouched. Chromium runs the full suite and blocks;
Firefox and WebKit run the `@smoke` subset. Failure paths are injected with
Playwright routing, offline mode and permissions, never with a test seam in
production code.

## What's covered by automation

The Playwright suite under `results-explorer/e2e/` runs against the built
explorer (`dist/`) served via the static test server at
`results-explorer/scripts/serve-browser-tests.mjs`. Fixtures are generated
per run into `results-explorer/test-fixtures/.generated/` - the curated
public corpus is never modified.

Routes and behaviours with at least one browser-functional test:

| Route / surface | Happy path | Failure path |
| --- | --- | --- |
| Home | ✅ | - |
| BenchmarkIndex | ✅ | - |
| PlatformIndex | ✅ | - |
| ResultDetail | ✅ | ✅ (unreachable `results.duckdb`, sidecar fetch failure, unknown id) |
| Compare | ✅ (deep link, share URL, sticky-bar flow) | ✅ (benchmark mismatch, scale mismatch, unknown id) |
| Query workbench | ✅ (sort, column toggle, starter query, CSV + JSON download) | ✅ (read-only write surfaces error) |
| NotFound | ✅ (unknown `/results/...` path renders the 404 card) | - |
| DuckDB-WASM attach | ✅ (cold load, `waitForDataLoaded`) | ✅ (range-read byte budget via test server) |

## Running the suite locally

Prerequisites: a Python toolchain with `uv`, Node 20+, and the explorer's
dependencies installed. `test:e2e:install` is a one-time step that installs
Chromium, Firefox, and WebKit. `test:e2e:chromium` is the deterministic local
and CI entrypoint. `test:e2e:full` runs the full browser matrix locally.

```bash
cd results-explorer
npm ci
npm run test:e2e:install
npm run test:e2e:chromium
npm run test:e2e:full
```

On a clean machine, `npm run test:e2e:chromium:setup` wraps the one-time
browser install plus the same deterministic Chromium run.

Each browser script regenerates the fixture corpus and rebuilds `dist/`
before Playwright starts the static server, so the command stays aligned
with the shipped harness contract. `npm run test:e2e:full` is the
one-command local convenience path; CI stays split into per-browser jobs
so Chromium can block independently while Firefox/WebKit remain
non-blocking `@smoke`.

Cross-browser smoke passes:

```bash
npm run test:e2e:firefox
npm run test:e2e:webkit
```

Failure artifacts (traces, screenshots, video, HTML report) land under
`results-explorer/test-results/` and `results-explorer/playwright-report/`
and are both gitignored.

## Public-site visual comparison

CI also compares screenshots of the public site against a baseline captured
on `develop`. A pull request that changes the site's appearance shows up there;
a maintainer reviews and accepts intentional changes.

## What CI gates

The `explorer-e2e` job in [`.github/workflows/ci.yml`](https://github.com/BenchBox-dev/BenchBox/blob/develop/.github/workflows/ci.yml)
(Chromium full suite, blocking) runs on **pull requests**
when the change touches the explorer unit (with the `explorer` unit result
always reporting; the job is skipped when no relevant files change).

- **Blocking:** `chromium` job - full suite must pass.
- **Non-blocking:** `firefox-smoke` and `webkit-smoke` jobs - `@smoke`-tagged
  subset only, `continue-on-error: true`. These become blocking once their flake data
  supports it.

All three jobs upload Playwright reports on failure with a 3-day retention
so you can download a full trace from the PR checks page. The CI
jobs call the same shared browser scripts that you run locally,
rather than re-encoding fixture/build/test sequencing in the workflow.

## When a CI run fails

1. Open the failed job, download the `playwright-report-*` artifact, and
   open `index.html` in a browser. The trace viewer is the fastest path to
   understanding the failure.
2. If the failure is browser-specific and reproduces locally, keep the
   browser-specific fix scoped to that browser.
3. If the failure does not reproduce locally, report it as a flaky test
   rather than re-running the PR until it passes.

## Adding new tests

- Put happy paths under `results-explorer/e2e/routes/` and tag the primary
  spec per route with `@smoke`.
- Put failure paths under `results-explorer/e2e/failures/`. Assertions must
  target user-visible error states (a visible heading or message), not just
  thrown exceptions or console output.
- Put server/runtime contract checks that are not user failure paths
  under `results-explorer/e2e/capability/` (e.g. the range-read byte
  budget gate in `capability/range-read-budget.spec.ts`).
- If a test depends on fixture data that does not yet exist, add a variant
  to `results-explorer/scripts/generate-browser-fixtures.mjs` - do not
  mutate the curated public corpus.
- If a test needs failure injection, prefer Playwright's `page.route()`,
  `context.setOffline`, permission grants, and download events. Do not
  introduce a production-code test seam.
