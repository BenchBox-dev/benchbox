<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox Test Suite

This directory contains the BenchBox test suite. Directories group tests by purpose;
pytest markers select the execution lanes.

**See also**: [`AGENTS.md`](../AGENTS.md) for the contributor/agent guide, and [`docs/internal/`](../docs/internal/) for maintainer notes.

## Test Structure

```
tests/
├── contracts/                # Shared contract fixtures
├── databases/                # Local test database helpers
├── docs/                     # Documentation checks and test plans
├── e2e/                      # End-to-end CLI workflow tests
├── examples/                 # Example usage checks
├── fixtures/                 # Shared test fixtures
├── integration/              # Integration tests
├── parity/                   # Parity fixtures and generators
├── performance/              # Performance tests
├── system/                   # Repository and CI system checks
├── uat/                      # User acceptance checks and support code
├── unit/                     # Unit tests
├── utilities/                # Test utilities and helpers
├── validation/               # Data and query validation checks
├── test_*.py                 # Root-level benchmark and runner tests
├── conftest.py              # Global pytest configuration
└── README.md               # This file
```

Pytest uses the root `pytest.ini` by default (fast local runs). CI-oriented
targets such as `make test-ci` and `make coverage-fast` explicitly select the
root `pytest-ci.ini` profile with `pytest -c pytest-ci.ini`.

## Test Categories

### Unit Tests (`unit/`)
Tests of individual components:
- **benchmarks/**: Core benchmark functionality
- **core/**: Base classes and utilities
- **generators/**: Data generation components

Many are isolated and fast, but the directory does not itself guarantee a
runtime or absence of external dependencies. Select the `fast` marker for the
curated fast lane.

Unit tests derive checkout paths from `tests.utilities.paths.REPO_ROOT`, not
the caller's working directory. The early pytest plugin acquires the shared
test lock before creating a disposable HOME, so conftest imports and collection
cannot read the caller's BenchBox configuration. Each unit test then receives
a fresh HOME. Child pytest processes share only a verified ancestor's live
lock, including collection-only subprocesses.

The required state detector checks cwd, every environment key, and registered
raw quiet/config-provider globals after fixture teardown. It restores leaked
state and reports a teardown error without printing environment values. Use
`monkeypatch` or an owned context for intentional changes. Existing reset safety
nets defer to that final check rather than hiding leaks. Unit CLI invocations
own their quiet/provider state because CliRunner does not exit a real CLI
process. Patch wall clocks at the consuming module, not globally, so timeout
and watchdog clocks keep running.

### Skew Generator Reference Snapshots

The [skew-generator byte test](unit/core/tpch_skew/test_skew_generator_coverage.py)
compares streamed table output with SHA256 snapshots captured from the original
in-memory transformer at seed 42. These snapshots detect changes from that
implementation baseline; they do not independently establish the correctness of
the skew distribution. The original provenance statement records no separate
transformer version.

### E2E Tests (`e2e/`)
End-to-end tests that validate complete CLI workflows:
- CLI option validation
- Error handling coverage
- Result schema validation
- Local platform execution
- Cloud platform dry-run workflows
- DataFrame platform workflows

**Characteristics:**
- Tests full CLI workflow from command to results
- Validates all platforms (local, cloud, DataFrame)
- Uses dry-run mode for cloud platforms (no credentials needed)
- Includes result file schema validation

**Markers:**
- `e2e`: All E2E tests
- `e2e_quick`: Quick dry-run tests
- `e2e_local`: Local platform tests (full execution)
- `e2e_cloud`: Cloud platform dry-run tests
- `e2e_dataframe`: DataFrame platform tests

### Integration Tests (`integration/`)
Tests that verify component interactions:
- Database connectivity and query execution
- End-to-end benchmark workflows
- Cross-component data flow

**Characteristics:**
- Real database connections
- File system operations

Integration tests may use local services; live network tests use the
`live_integration` marker and are excluded from the default local lanes.

### Performance Tests (`performance/`)
Tests focused on performance characteristics:
- Query execution benchmarks
- Data generation performance
- Memory usage analysis
- Scalability testing

This directory includes resource monitoring, statistical analysis, and
baseline comparisons. Use the `fast`, `medium`, `slow`, `stress`, and
`resource_heavy` markers to select tests by execution cost rather than by
directory name.

## Test Execution

### Automated Gate Contract

The standard gates are intentionally split by the risk they are meant to catch:

- `make test-fast`: quick developer and develop-PR feedback for code-impacting
  changes. The matching selection in `.github/workflows/ci.yml` also collects
  coverage.
- `make test-correctness-gate`: bounded develop-PR real-result gate. It runs the
  DuckDB TPC-H matrix slice (SF=1, pinned reference qgen seed) through generate,
  load, and execute, then validates the emitted stream-0 results against the stored
  TPC-H answers with EXACT row-count checking **and** stored VALUE digests. The
  gated subset is the 18 TPC-H queries whose answer-set cardinalities are stable
  across dbgen builds; Q11/Q16/Q18/Q20 are excluded because their HAVING/threshold
  boundaries make the stored row count vary with the generated data. The subset is
  deliberately *discriminating* — it is not dominated by one-row queries and
  includes multiple high-cardinality answer-backed queries (e.g. Q9=175, Q2/Q21=100)
  — so a wrong join/filter/aggregate that still emits one row is caught. The subset
  shape is ratcheted in `tests/unit/test_standardized_test_commands.py`
  (`TestCorrectnessGateOracle`). What the gate proves and what it does not:
  - **Value + cardinality at SF=1/pinned-seed**: with `BENCHBOX_EMIT_RESULT_DIGEST=1`
    the runner emits an order-normalized digest of each stream-0 query's *full*
    result set (reusing `benchbox.core.tpchavoc.validation.calculate_checksum`, the
    same primitive the TPC-Havoc gates use), which the gate asserts against a stored
    reference digest (`benchbox/core/expected_results/reference_digests/tpch_value_digests_sf1.json`)
    in addition to the row count. So a wrong-but-same-cardinality answer — a perturbed
    Q1 aggregate, a swapped column, a changed rounding — turns the gate RED, not just
    a wrong row count. Sensitivity is proven in
    `tests/unit/test_correctness_gate_value_oracle.py`.
  - **Regression snapshot, NOT an independent oracle**: the reference digests were
    produced by running benchbox-on-DuckDB and frozen, so the value check detects
    *change* from that DuckDB-pinned baseline (a regression tripwire), not correctness
    against an external authority. A conceptual value bug present at *freeze time* is
    enshrined in the reference, not caught. Read a green `value+cardinality` cell as
    "unchanged from the frozen DuckDB answer", never "values proven correct". The
    reference is regenerated (never hand-copied) by `make correctness-gate-digests-regen`,
    which reruns the same gate config and writes the file idempotently. The independence
    gap and the deferred cross-engine upgrade are analyzed in
    `_project/analysis/value-digest-cross-engine-independence-decision.md`. Two further
    fidelity properties are pinned in `test_correctness_gate_value_oracle.py`: the
    sensitivity floor is **relative** (~1e-6 per cell via significant-figure rounding,
    uniform across column magnitude), and the digest is a **value+type** digest
    (DuckDB-pinned), which is why cross-engine reuse is deferred.
  - **Values are UNGUARDED above SF=1**: stored answers and digests exist only at
    SF=1 (the expected-results loader raises for other scales), so the value
    guarantee holds at SF=1 only. There is no expected-results value or cardinality
    oracle above SF=1.
  - **Strict arming (both axes)**: with `BENCHBOX_STRICT_EXPECTED_RESULTS=1`, every
    configured query *must* produce a non-SKIP row-count validation **and**, where a
    reference digest exists, an evaluated value digest, or the run fails. A missing
    digest disarms RED, never green. This is not gated on benchmark name or scale, so
    a future CI speedup that retargets the gate (a different benchmark, or SF<1 where
    no answers exist) cannot silently disarm either oracle.
  - **No-skip guard**: the Makefile target emits a JUnit report and fails unless
    exactly one node ran with zero skips. `pytest` exits 0 when a *selected* node
    SKIPs (e.g. duckdb unavailable, or the case dropped from the stable matrix),
    which would otherwise pass the gate without executing anything.
  - **Required CI job composition**: the `correctness-gate` job in
    `.github/workflows/ci.yml` runs more than this row-count+value gate. It also runs
    the value-level cross-surface and TPC-Havoc equivalence gates
    (`tpchavoc-equivalence-report`, `tpchavoc-dataframe-equivalence-report`, and the
    ssb/amplab/coffeeshop/clickbench/joinorder-synthetic cross-surface reports), so
    the required job proves value-level equivalence across several benchmarks, not
    only TPC-H row counts. This composition is ratcheted in
    `tests/unit/test_standardized_test_commands.py`.
- `make test-integration`: non-live, non-stress integration coverage for broader
  local and main/release validation.
- `make test-local-matrix`: opt-in stress matrix for the full local platform
  benchmark sweep.
- Release canary, live cloud, Docker, and UAT evidence remain separate release
  signals until their cost, credential, and flake policies are suitable for
  blocking routine PRs.

The `medium-test` job in `.github/workflows/ci.yml` runs `make test-medium`,
but only when the heavy tier is needed: code-routed pull requests that touch
soundness paths, packaging, `pytest.ini`, or a `conftest.py` or test-package
`__init__.py` that covers more than 200 test modules
(`scripts/heavy_tier_needed.py` reports `heavy-needed == 'true'`). Ordinary
code-change PRs skip it, so do not assume medium coverage ran on a routine PR.
Its marker selection excludes slow, stress, resource-heavy, and live
integration tests. Product-critical tests that need a different selection
belong in an explicit workflow or correctness gate.

Every code PR still runs the medium tests that `make pr-preflight` maps from
its changed paths (`_project/scripts/preflight_targets.py`), in the
`code-test` job's "Run medium tests for changed paths" step. A changed test
module selects itself; a source module selects `test_<module>.py` plus every test that imports
it or `mock.patch`/`monkeypatch.setattr`s a module that imports it (one hop,
static scan of import statements and string patch targets, so
`benchbox/base.py` selects `tests/test_benchbox.py`); a test
helper selects the tests that import it; a `conftest.py` selects its directory
and a test-package `__init__.py` selects its package and importers, when that
directory holds at most 200 test modules (a broader one runs the full medium
shards through the heavy tier); a platform module selects its
`tests/integration/platforms` smoke test; and `SOURCE_GUARD_TESTS` names the
tests that guard a source or test-helper prefix, such as the module-size guard
for any `benchbox/` change and the oracle scenario harness
(`tests/integration/oracle/`), which runs the oracle code in subprocesses that
coverage cannot attribute, for any change to the oracle reviewers, the gate,
`soundness_merge_digest.py`, `scripts/pr_arm.py` or the harness itself.
Everything else in the medium tier runs after merge in
`trunk.yml` and on release PRs. The selection cannot see dynamic imports,
string-built patch targets, tests reaching the changed module through
attribute access or inheritance more than one hop from a patched module, or
non-Python inputs; the post-merge medium tier in `trunk.yml` covers those.

### Quick Development Testing
Where a `make` target is followed by a `pytest` command, the two are equivalent. `make test-fast` runs the curated fast
lane. The `tests/unit/benchmarks/test_tpch_core.py` command runs one benchmark's tests. For coverage, `make
coverage-fast` covers fast tests only, for quick feedback. `make coverage-all` is routine coverage, which excludes
stress, resource-heavy and live tests. `make coverage-opt-in-all` covers the full tree, including opt-in stress,
resource-heavy and live tests, and needs services and credentials. The final `pytest --cov` command is a direct
equivalent that writes an HTML report.
```bash
make test-fast
uv run -- python -m pytest -m fast

uv run -- python -m pytest tests/unit/benchmarks/test_tpch_core.py

make coverage-fast
make coverage-all
make coverage-opt-in-all
uv run -- python -m pytest --cov=benchbox --cov-report=html
```

### E2E Testing
`e2e_quick` runs the quick E2E tests in dry-run mode, and `e2e_local` runs the local platform E2E tests with full
execution. The last two commands run all E2E tests and one E2E module.
```bash
make test-e2e-quick
uv run -- python -m pytest -m e2e_quick

uv run -- python -m pytest -m e2e_local

uv run -- python -m pytest tests/e2e/ -v

uv run -- python -m pytest tests/e2e/test_cli_options.py -v
```

### Comprehensive Testing
Each `make` target is equivalent to the `pytest` command after it. The targets run all tests, run tests in parallel,
and run integration tests. The performance tests have no `make` target.
```bash
make test-all
uv run -- python -m pytest

make test-parallel
uv run -- python -m pytest -n auto

make test-integration
uv run -- python -m pytest -m "integration and not live_integration and not stress"

uv run -- python -m pytest tests/performance/ -m performance
```

### Using the Unified Test Runner
The commands run, in order: optimized development tests, CI-optimized tests, specific benchmarks with parallel
execution, tests with coverage reporting, and benchmark validation.
```bash
uv run -- python tests/utilities/unified_test_runner.py --strategy development

uv run -- python tests/utilities/unified_test_runner.py --strategy ci

uv run -- python tests/utilities/unified_test_runner.py --benchmark tpch tpcds --parallel --workers 4

uv run -- python tests/utilities/unified_test_runner.py --coverage --report

uv run -- python tests/utilities/benchmark_validator.py --benchmark all --quick-check
```

## Performance Profiling

### Basic Profiling
The commands profile a test run, profile with detailed output written to a report, and check for performance
regressions.
```bash
uv run -- python tests/utilities/performance_profiler.py python -m pytest tests/unit/

uv run -- python tests/utilities/performance_profiler.py --output performance_report.md python -m pytest tests/unit/

uv run -- python tests/utilities/performance_profiler.py --check-regressions python -m pytest tests/unit/
```

### Advanced Profiling
The first command updates the performance baselines. The second profiles a specific test category.
```bash
uv run -- python tests/utilities/performance_profiler.py --update-baseline python -m pytest tests/unit/

uv run -- python tests/utilities/performance_profiler.py python -m pytest tests/integration/ -m "integration and not slow"
```

## Test Markers

Tests are organized using pytest markers for selective execution:

### Execution Characteristics
- `fast`: Quick tests suitable for development
- `slow`: Tests that take significant time
- `memory_intensive`: Tests using significant memory
- `cpu_intensive`: Tests using significant CPU
- `io_intensive`: Tests performing heavy I/O

### Test Categories
- `unit`: Unit tests
- `integration`: Integration tests
- `e2e`: End-to-end CLI tests
- `e2e_quick`: Quick E2E tests (dry-run mode)
- `e2e_local`: Local platform E2E tests
- `e2e_cloud`: Cloud platform E2E tests (dry-run)
- `e2e_dataframe`: DataFrame platform E2E tests
- `performance`: Performance tests

### Database Support
- `sqlite`: Tests requiring SQLite
- `duckdb`: Tests using DuckDB
- `database`: Tests requiring database connections

### Benchmark Types
- `tpch`: TPC-H benchmark tests
- `tpcds`: TPC-DS benchmark tests
- `ssb`: Star Schema Benchmark tests
- `amplab`: AMPLab Big Data Benchmark tests
- `clickbench`: ClickBench tests
- `h2odb`: H2O Database benchmark tests
- `primitives`: Primitive operations tests

### Example Usage
Each `make` target is equivalent to the `pytest` command after it. The examples run only fast unit tests, run
integration tests excluding slow ones, and run TPC-H related tests. The last command runs all tests except memory
intensive ones.
```bash
make test-dev
uv run -- python -m pytest -m "unit and fast"

make test-integration
uv run -- python -m pytest -m "integration and not slow"

make test-tpch
uv run -- python -m pytest -m tpch

uv run -- python -m pytest -m "not memory_intensive"
```

## Parallel Run Mutual Exclusion (File Lock)

`tests/conftest.py` acquires a single inter-process file lock at
`~/.benchbox/test.lock` for the duration of each parallel test session. This
prevents two concurrent `pytest -n auto` runs from fighting for CPU, which
would otherwise double runtime and produce flaky timing assertions.

The default is intentionally global across worktrees: ten retained worktrees
can otherwise start ten independent `pytest -n auto` runs and saturate a
developer workstation. For isolated debugging or sandboxed CI, set
`BENCHBOX_TEST_LOCK_DIR=/path/to/dir`; BenchBox will use
`$BENCHBOX_TEST_LOCK_DIR/test.lock` instead. `make test-unlock` resolves the path through the
same module, so it honors the override and the real-home default.

**Contract** (`benchbox/utils/host_lock.py`, called from `tests/conftest.py` and
`tests/utilities/session_isolation.py`):

- Only the **xdist controller** process locks - workers do not. `pytest_configure`
  checks `hasattr(config, "workerinput")` to distinguish them.
- A parallel session (`-n` above 0, including the default `-n auto`) always locks. A serial session
  (`-n 0`) locks only when its selected items, after deselection and before the first test, carry a heavy
  marker: `slow`, `stress`, `resource_heavy`, `live_integration`, `docker_integration` or any `live_*`.
  `make pr-preflight` runs only default-marker `-n 0` targets and a `fast and not (slow or stress ...)`
  lane, so it never waits. `BENCHBOX_SKIP_TEST_LOCK=1` skips locking entirely.
- Admission and the watchdog run only in sessions whose selected items include a heavy marker. A parallel
  session is judged from its `-m` expression, since the controller does not collect: it is heavy when a heavy
  marker it names can be selected, or when a test carrying only an unnamed heavy marker would be (`not stress`
  is heavy; `unit` or `integration and not live_integration and not stress` are not). The admission report
  is printed to stderr, so CI logs show the real readings. A refused lock exits **75**, refused admission **76**.
- `python -m tests.uat._cli` takes the lock with admission and watchdog for every subcommand except the light
  set (`preflight`, `gate-check`, `report`, `replay-classify`, `package`, `validate`,
  `verify-tuning-matrix`, `docker-cleanup`) and `--dry-run`; inside an isolated pytest session it does not
  acquire. Subprocesses inherit `BENCHBOX_TEST_SESSION_OWNER`; code that builds an environment from scratch
  must pass it through or use its own `BENCHBOX_TEST_LOCK_DIR`.
- Hosted CI sets `BENCHBOX_HEAVY_ADMIT_MIN_GIB` and `BENCHBOX_HEAVY_MIN_FREE_DISK_GIB` in each workflow `env:`.
- The Linux guest (`make ci-linux`, `scripts/linux_preflight_pytest.sh`) uses
  `BENCHBOX_TEST_LOCK_DIR=/tmp/benchbox-lock` on guest-local storage and guest-sized floors, so guest sessions
  serialize among themselves and never write the host holder file. The guest therefore prints the
  non-default-lock-dir bypass warning; that is expected.
- Lock path defaults to `<real home>/.benchbox/test.lock`, where the real home
  comes from the user database rather than `$HOME`, so a session that rewrites
  `$HOME` still contends on the same file. `BENCHBOX_TEST_LOCK_DIR` changes only
  the directory, not the filename.
- Uses `fcntl.flock(LOCK_EX | LOCK_NB)` on POSIX, `msvcrt.locking` on Windows.
  After each acquisition the held descriptor's inode is compared with the path's
  inode; a mismatch (the file was replaced) closes the descriptor and retries.
- The holder record and `BENCHBOX_TEST_SESSION_OWNER` carry `<pid>:<create_time>`.
  A descendant of the holder skips acquisition only when the variable matches the
  holder record and that process is the current process or one of its ancestors.
  A descendant whose environment was scrubbed fails immediately with a message
  naming the scrub instead of waiting on its own ancestor.
- A process that already holds the lock re-enters it with a reference count; the
  lock is released when the outermost holder releases.
- Waiters register under `<lockdir>/test.lock.waiters/<pid>`; blocked messages
  show the number of live waiters.
- On contention, the second local run waits up to 3600 seconds with holder
  information and periodic progress messages. Set
  `BENCHBOX_TEST_LOCK_WAIT_SECONDS=0` to fail immediately; CI does this.
  Ctrl-C cancels the wait without disturbing the holder.
- The fd is held open for the whole session and released in `pytest_unconfigure`.
- `python -m benchbox.utils.host_lock run -- <cmd>` holds the lock for the
  lifetime of `<cmd>`, exports the owner variable, and forwards SIGINT, SIGTERM
  and SIGHUP to the child's process group. `python -m benchbox.utils.host_lock
  clear` (`make test-unlock`) empties the holder record of an inactive lock and
  refuses an active one.

**When to bypass**: only for intentional concurrent debug runs, and only with owner approval in the current task. Set
`BENCHBOX_SKIP_TEST_LOCK=1` - but expect noisy timing results and CPU
contention. Do not bypass in CI.

### Heavy-work admission

The lock serializes BenchBox jobs but cannot see memory held elsewhere (resident Apple `container` VMs,
JVMs, IDEs). `python -m benchbox.utils.host_lock admit [--need-gib N]` (no lock taken) and
`run --admit [--need-gib N] -- <cmd>` (after the lock is obtained, before `<cmd>`) check the host and refuse to
start heavy work when it is already stressed. A refusal from `run --admit` releases the lock and exits **76**
(75 stays "lock busy", 78 "ownership/scrubbed environment"); `admit` also exits 76 on refusal. Python callers
use `host_lock.acquire(..., admit=True)`, which raises `AdmissionRefused` after releasing the lock.

Admitted only when all measured signals pass: macOS memory pressure level is 1 (normal),
`kern.memorystatus_level` is at least the floor, psutil available memory is at least the need, and free disk on the
boot volume and the output root (`BENCHBOX_OUTPUT_DIR`, default `benchmark_runs`) is at least the floor. Swap use
and `container`/`container machine` VM reservations are reported itemized but never gate: resident VM pages are
already missing from available memory. Unmeasured signals (failed or >5 s external commands) never refuse. A
refusing reading is re-sampled every 5 s for up to 30 s before the refusal stands.

| Setting | Default | Meaning |
| --- | --- | --- |
| `BENCHBOX_HEAVY_ADMIT_MIN_GIB` | 2 | Available memory needed to admit |
| `BENCHBOX_HEAVY_MIN_LEVEL` | 35 | Minimum `kern.memorystatus_level` |
| `BENCHBOX_HEAVY_MIN_FREE_DISK_GIB` | 50 | Minimum free disk per root |
| `BENCHBOX_HEAVY_MEMORY_BUDGET_GIB` | 50% of RAM | Shared memory budget resolver (`resource_limits.heavy_memory_budget_gib`) |
| `BENCHBOX_HEAVY_ADMIT` | unset | `force` admits despite refusals, with a stderr warning |

`make heavy-status` (`python -m benchbox.utils.host_lock status`) is read-only and never takes the lock. It prints the
holder and its age, live waiters, the admission readings, VM inventory, the top 10 processes by physical footprint
and orphaned servers (ppid 1, working directory in a BenchBox worktree); each external command is cut off at 5 s and
shown as `unmeasured`. `make heavy-run CMD='...'` is `run --admit --guard -- sh -c "$CMD"`. Agent rules and known limits:
`docs/agent/heavy-work.md`.

### Heavy-work watchdog

Admission only checks the host at start. `run --guard -- <cmd>` (and `host_lock.acquire(..., guard=True)`) also starts a
small watcher **process** next to the lock holder, so a GIL-holding native call or memory pressure in the holder cannot
stall it. Every 2 s it samples the summed physical footprint of the holder and all its descendants (macOS
`proc_pid_rusage` `ri_phys_footprint`; elsewhere psutil USS, then RSS), memory pressure, free disk on the boot volume
and output root, and whether the lock file is still the one the holder locked. It exits when the holder exits.

Breach rules: tree footprint above `BENCHBOX_HEAVY_MEMORY_BUDGET_GIB` (the same budget admission uses), pressure level
critical (4), or free disk below `BENCHBOX_HEAVY_KILL_FREE_DISK_GIB`. In `kill` mode the watcher reports to stderr
(signal, measured versus limit, top 5 tree processes by footprint), SIGTERMs descendants deepest-first, waits 10 s,
SIGKILLs survivors, then SIGTERMs the holder if the tree is still over budget. Parentage is re-verified immediately
before every signal; re-parented, double-forked and sibling processes are never signalled. After a kill, `run --guard`
exits **79** (distinct from UAT's 137, 75 busy, 76 admission refusal, 78 ownership); an in-process holder that
acquired with `guard=True` exits 79 only when the watcher terminates it while the main thread is running Python code
(a holder stuck in a native call is SIGKILLed after the grace period instead). A lock file whose inode changed produces a loud
warning only. On normal release, surviving descendants are listed and left running.

VMs and containers started through the Apple `container` runtime are children of launchd, not of the holder, so
their footprint is not tree-attributed; VM memory is bounded by machine size and admission.

| Setting | Default | Meaning |
| --- | --- | --- |
| `BENCHBOX_HEAVY_WATCHDOG` | `kill` | `kill`, `warn` (report once per breach, signal nothing) or `off` (loud warning, no watcher) |
| `BENCHBOX_HEAVY_KILL_FREE_DISK_GIB` | 20, or 40% of `BENCHBOX_HEAVY_MIN_FREE_DISK_GIB` when that is set and lower | Free-disk floor per root that counts as a breach; always below the admission floor unless set explicitly |
| `BENCHBOX_HEAVY_WATCHDOG_INTERVAL_SECONDS` | 2 | Sampling interval of the watcher |
| `BENCHBOX_HEAVY_WATCHDOG_GRACE_SECONDS` | 10 | Wait between SIGTERM and SIGKILL of descendants |

### macOS-specific conftest branch (`tests/conftest.py:109-122`)

On macOS, xdist workers suppress `setproctitle` to avoid a
`launchservicesd` CPU storm:

> xdist calls setproctitle() twice per test (running/idle) via
> xdist.remote.worker_title(). At ~200 calls/second this triggers macOS
> launchservicesd to rebuild its process registry continuously,
> consuming 200%+ CPU and ~900 MB RSS - the actual root cause of
> the macOS beachball during parallel test runs.

The workaround walks the call stack to find the live xdist exec namespace
and replaces `worker_title` with a no-op. This is necessary because
`import xdist.remote` in an execnet `__channelexec__` namespace resolves
to a *different* module object than the one executing - so patching by
module import alone is a no-op.

This branch only fires when `sys.platform == "darwin"` and the process is
an xdist worker. Linux and Windows are unaffected.

## CI/CD Integration

### GitHub Actions Example
```yaml
name: Test Suite
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.11', '3.12', '3.13', '3.14']

    steps:
    - uses: actions/checkout@v3
    - name: Set up Python ${{ matrix.python-version }}
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -e .[dev]

    - name: Run fast tests
      run: |
        uv run -- python tests/utilities/unified_test_runner.py --strategy ci

    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
```

### Jenkins Pipeline Example
```groovy
pipeline {
    agent any

    stages {
        stage('Setup') {
            steps {
                sh 'python -m pip install --upgrade pip'
                sh 'pip install -e .[dev]'
            }
        }

        stage('Fast Tests') {
            steps {
                sh 'uv run -- python tests/utilities/unified_test_runner.py --strategy ci --parallel --workers 4 --output junit'
            }
            post {
                always {
                    junit 'test_results.xml'
                }
            }
        }

        stage('Integration Tests') {
            when {
                branch 'main'
            }
            steps {
                sh 'uv run -- python tests/utilities/unified_test_runner.py --mode integration --parallel --workers 2 --coverage'
            }
            post {
                always {
                    publishHTML([
                        allowMissing: false,
                        alwaysLinkToLastBuild: true,
                        keepAll: true,
                        reportDir: 'htmlcov',
                        reportFiles: 'index.html',
                        reportName: 'Coverage Report'
                    ])
                }
            }
        }
    }
}
```

## Test Configuration

### pytest.ini
The enhanced `pytest.ini` provides:
- Comprehensive marker definitions
- Parallel execution configuration
- Coverage reporting setup
- Performance optimization settings
- CI/CD friendly defaults

### conftest.py
Global test configuration including:
- Database fixtures
- Data generation helpers
- Performance monitoring setup
- Cleanup utilities

## Best Practices

### Writing Tests
1. **Use appropriate markers**: Mark tests with relevant categories and characteristics
2. **Keep tests focused**: Each test should verify one specific behavior
3. **Use fixtures**: Leverage shared fixtures for database connections and test data
4. **Handle resources**: Ensure proper cleanup of temporary files and connections
5. **Performance awareness**: Use performance markers for resource-intensive tests

### Faking External Effects and the Network Guard

Fake external effects at a boundary the code under test owns: an injectable
source, URL, runner, or function in our own module, or a real local fake
server bound to loopback. Do not fake effects by patching standard-library or
third-party internals (`urllib.request.urlopen`, `OpenerDirector.open`,
`socket`, `subprocess.run`, `time.sleep`, SDK client methods). Such a patch
binds the test to how the code happens to fetch today; a refactor of the
fetch mechanism silently bypasses the fake and the test reaches the real
network.

Unit and medium tests cannot reach non-loopback networks through Python sockets or name resolution (native-code clients that open their own sockets are outside the guard). The guard is
`tests/utilities/network_guard.py`, activated per test by the autouse fixture
in `tests/utilities/network_guard_plugin.py`. It blocks `connect`,
`connect_ex`, `sendto`, `getaddrinfo`, and `gethostbyname*` to anything but
loopback, in the test process and, through `tests/utilities/network_guard_site/sitecustomize.py`
on `PYTHONPATH`, in Python subprocesses the test spawns that inherit its
environment. A blocked call raises `NetworkAccessBlocked` with an explanatory
message, and the test also fails at teardown if the guard fired, even if the
code under test swallowed the exception (subprocess violations are collected
through a per-test log). A test that intentionally provokes a block requests
the `expect_network_block` fixture.

A test is guarded when it is under `tests/unit/` or carries the `unit` or
`medium` marker. Tests outside `tests/unit/` are exempt when they carry
`integration` or `live_integration`; a test under `tests/unit/` cannot opt out
that way. `@pytest.mark.allow_network(reason="...")` exempts a single test
whose subject is the network call itself and which cannot use a loopback
server; the reason is required and collection fails without it. Prefer moving
a network-dependent test to the integration or live tier. A
test that provokes a block on purpose uses `with expect_network_block("<target>"):`;
it excuses only violations naming that target and fails if none occurred.

These rules bind new and changed tests. Existing tests that patch stdlib or
third-party internals are inventoried and converted opportunistically; they are
not defects in an unrelated change.

### Process-Global State

Tests share one worker process. A test must not leave behind process-global
state: child-subreaper status (`become_subreaper`, `prctl(PR_SET_CHILD_SUBREAPER)`),
`waitpid(-1)` or any reaping of children it does not own, signal handlers,
the working directory, `os.environ` changes made outside `monkeypatch`, the
umask, or resource limits. Run code that changes such state in a subprocess,
or restore it before the test ends. The `attempts` reviewer command calls
`become_subreaper`; invoke it through a subprocess, not `cli.main`, from tests.

`tests/utilities/process_state_guard.py` fails any test that ends with the
worker process as a Linux child subreaper (checked with
`prctl(PR_GET_CHILD_SUBREAPER)`; skipped on other platforms) and clears the
flag so later tests are not affected.

### Test Organization
1. **Follow naming conventions**: Use descriptive test names with `test_` prefix
2. **Group related tests**: Organize tests by functionality and component
3. **Use clear assertions**: Make test failures easy to understand
4. **Document complex fixtures**: Record shared fixture contracts in this guide, not in test docstrings or comments

### Performance Testing
1. **Establish baselines**: Use the performance profiler to set baseline metrics
2. **Monitor regressions**: Regularly check for performance regressions
3. **Profile selectively**: Only profile tests when needed to avoid overhead
4. **Optimize test execution**: Use caching and parallel execution for faster feedback

## Troubleshooting

### Common Issues

#### Slow Test Execution
Profile the test execution, run only fast tests, or use parallel execution. Each `make` target is equivalent to the
`pytest` command after it.
```bash
uv run -- python tests/utilities/performance_profiler.py python -m pytest tests/unit/ -v

make test-fast
uv run -- python -m pytest -m fast

make test-parallel
uv run -- python -m pytest -n auto
```

#### Memory Issues
Run memory-intensive tests separately, and monitor memory usage with the profiler.
```bash
uv run -- python -m pytest -m "memory_intensive" --maxfail=1

uv run -- python tests/utilities/performance_profiler.py --output memory_report.md python -m pytest tests/unit/
```

#### Database Connection Issues
Run the database tests with verbose output, and test database connectivity directly.
```bash
make test-integration
uv run -- python -m pytest tests/integration/ -v -s

uv run -- python -c "import duckdb; print(duckdb.connect().execute('SELECT 1').fetchone())"
```

### Test Cache Management
The first command clears the pytest cache. The second prints the unified runner help. The third runs with `--dry-run`
to show the commands without running them.
```bash
uv run -- python -m pytest --cache-clear

uv run -- python tests/utilities/unified_test_runner.py --help

uv run -- python tests/utilities/unified_test_runner.py --dry-run
```

## Contributing

When adding new tests:

1. **Choose the right category**: Place tests in the appropriate directory
2. **Add proper markers**: Mark tests with relevant characteristics
3. **Update documentation**: Add test descriptions to this README
4. **Consider performance**: Mark resource-intensive tests appropriately
5. **Test your tests**: Ensure new tests pass in isolation and with the full suite

### Adding New Test Categories
1. Create subdirectory in appropriate category
2. Add `__init__.py` file
3. Update markers in `pytest.ini`
4. Add documentation to this README
5. Update test runner configuration if needed

## Performance Monitoring

The test suite includes comprehensive performance monitoring:

- **Execution time tracking**: Monitor test duration trends
- **Memory usage analysis**: Track memory consumption patterns
- **CPU utilization**: Monitor CPU usage during test execution
- **I/O monitoring**: Track file system operations
- **Regression detection**: Automatically detect performance regressions

Performance data is stored in `~/.benchbox/test_cache/` and can be analyzed using the performance profiler utility.

## Fixture Boundaries

The SDK reload tests in `unit/platforms/aws/test_emr_serverless_paths.py` and
`unit/platforms/gcp/test_dataproc_serverless_paths.py` check imports under
controlled `sys.modules` entries. Cleanup restores the previous entries and
reloads the adapter. Do not assert SDK availability after restoration: installed
boto3 and Google Cloud packages depend on the local environment.

`unit/tpcds/test_parameter_log.py` uses a shortened Q8 parameter-log fixture with
three ZIP entries. The upstream `query8.tpl` defines 400 ZIP values. The excerpt
checks parsing of repeated values; it does not cover all 400 values or establish
the order of a complete generated log.

## Test Utilities

The test suite includes several unified utilities for efficient testing:

- `utilities/unified_test_runner.py`: Comprehensive test runner with multiple execution strategies
- `utilities/benchmark_validator.py`: Unified benchmark validation utility
- `utilities/performance_profiler.py`: Performance monitoring and profiling
- `utilities/test_helpers.py`: Common test helper functions
- `utilities/test_runner.py`: Enhanced test runner with caching capabilities

These utilities provide a modern, efficient approach to testing and validation.

## Support

For issues with the test suite:
1. Check this README for common solutions
2. Review test output for specific error messages
3. Use the performance profiler to identify bottlenecks
4. Consult the main BenchBox documentation
5. Open an issue with detailed reproduction steps

## CPU identity test isolation

In [test_system_info.py](unit/utils/test_system_info.py), replace
`benchbox.utils.system_info._proc_cpuinfo_model` to control that fallback read.
Do not replace `builtins.open` for this purpose: psutil also reads `/proc` on
Linux, so a broad replacement can fail an unrelated psutil call while appearing
to work on macOS. Preserve separate hardware detection and fallback assertions.

## Regression fixtures and interpretation

Keep regression expectations independent of implementation constants. Names,
assertions, parameter data and recorded fixtures describe the checked behavior;
the notes below preserve constraints that matter when extending these tests.
Offline or mocked checks establish their asserted contracts, rather than live
service certification or complete SQL compliance.

### Isolation and ownership

- [Script fixtures](unit/scripts/conftest.py) make `scripts/` importable.
  Tests for the BenchBox adapter boundary load `_project/scripts` explicitly.
- [DataFrame memory fixtures](unit/platforms/dataframe/conftest.py) opt in to
  sufficient-memory injection. Keep them non-autouse so memory-policy tests
  still exercise actual capacity decisions and insufficient-memory behavior.
- [Q17 Spark tests](unit/core/tpch/test_q17_spark_execution.py) own a local Spark
  session and restore Java/PySpark environment changes with a monkeypatch
  context. Adapter fixtures detach the shared session instead of closing it;
  the module fixture stops it. LakeSail coverage here exercises its inherited
  expression client, not a Sail server.
- [CLI result tests](unit/cli/commands/test_results.py) reset the Rich console
  singleton, disable quiet mode and use a wide capture console. Preserve literal
  bracket-containing paths rather than interpreting them as Rich markup.
  [Export consistency tests](unit/cli/test_run_export_consistency.py) obtain the
  run submodule from `sys.modules` and patch its object: the package also exports
  a Click command named `run`, so attribute lookup can select the wrong target.
- [Monitoring tests](unit/monitoring/test_performance_monitoring.py) close the
  `mkstemp` descriptor before using its path. Cleanup tolerates a tracker-held
  Windows file; that tolerance is not an assertion that deletion succeeded.
- GPU [metrics](unit/experimental/gpu/test_metrics_error_capture.py) and
  [version detection](unit/experimental/gpu/test_capabilities_error_capture.py)
  inject failures without CUDA/NVML hardware. Version detection disables earlier
  branches to reach a malformed temporary version file through `CUDA_HOME`.
- [Offline adapter tests](unit/platforms/test_offline_adapter_missing_sdks.py)
  run actual inventory assertions in a fresh process with vendor SDK imports
  blocked. Keep the child assertion-enabled and its explicit optimization guard;
  remove inherited `PYTHONOPTIMIZE` before launching it. Startup and cold imports
  justify its medium tier, rather than treating it as a fast assertion-only test.
- Soundness history tests use real
  fetches and trusted checker subprocesses, which exceed the fast-test budget.
  [Release artifact tests](unit/scripts/test_release_artifact_consumer.py) retain
  their medium marker; consumer tests also import this module. Reassess measured
  budgets and CI tier wiring before changing that selection.
- [Marker tests](unit/test_marker_strategy.py) cache the discovered test modules
  to avoid repeated tree scans. Collection may enforce quarantine and budgets,
  but must not rewrite speed markers. In [SQL compatibility tests](unit/core/sql_compat),
  name benchmark parameters `bmark`: pytest-benchmark owns the `benchmark` fixture,
  and shadowing it can fail collection.

### Data and SQL regression boundaries

- [NYC Taxi](unit/core/nyctaxi/test_source_contract.py) pins its TLC file set;
  [FlightData](unit/core/flightdata/test_source_contract.py) ends month windows at
  the pinned BTS month. New upstream data must not silently change a scale
  factor's dataset. These tests are offline. A ZIP without CSV follows the
  download-failure cleanup path, not an uncaught-error path.
- TPC-DS SQL depends on data scale: [bulk query retrieval](unit/tpcds/test_get_queries_scale.py),
  [benchmark fakes](unit/core/test_tpcds_benchmark_fakes.py) and the
  [equivalence reference builder](unit/core/equivalence/test_tpcds_gate_scale.py)
  preserve matching scales. Q9 and Q44 illustrate scale-dependent templates;
  the equivalence regression also names Q46 and Q68. The fake generator checks
  forwarding; it does not establish binary generation correctness.
- [TPC-H subsets](unit/core/tpch/test_power_test_query_subset.py) accept bare and
  Q-prefixed IDs because the CLI forwards selected query IDs verbatim.
  [TPC-DS identity tests](unit/scripts/test_tpcds_platform_identity.py) compare
  repeated same-seed manifests on one platform before interpreting cross-platform
  differences; unavailable binaries retain their explicit skip conditions.
- [TPC-H manifest tests](unit/core/tpch/test_tpch_manifest_row_counts.py) exercise
  discovery of all compressed shards even when passed one shard. Reuse describes
  existing bytes, not a new generator's compression flags.
  [Compressed Data Vault tests](unit/core/datavault/test_compressed_source_buffers.py)
  retain the two-shard, 512 MB reproduction with plain input as the row-identity
  reference. This fixture targets the compressed-stream seek failure observed
  with DuckDB 1.5.5; it is not a general memory-capacity guarantee.
- [TPC-DS-OBT comparisons](unit/core/tpcds_obt/test_tpcds_obt_dataframe_queries.py)
  compare values as well as shape, including empty-set zero results for
  Q10/Q15/Q16. [SSB tuning](unit/core/tuning/test_tpc_workload_profiles.py) excludes
  `LO_ORDERKEY`: LINEORDER is denormalized and has no ORDERS join. The
  [replication prototype](unit/core/joinorder_replicated/test_replication_prototype.py)
  keeps company-type lookup identities shared across replicas.
- [Firebolt DDL](unit/core/tuning/generators/test_firebolt_ddl.py) stores the full
  `PRIMARY INDEX` clause in `distribute_by`. [ClickHouse DDL](unit/core/tuning/generators/test_clickhouse_ddl.py)
  stores a bare partition-column list but wraps it at rendering as a single
  partition expression for both single and multiple columns.
- Query-plan tests use recorded fixtures without their live services. Preserve
  Firebolt nested-parenthesis details, Synapse's RETURN root and linear chain,
  Snowflake's real Result root over stray parentless nodes, and Fabric's dedicated
  SHOWPLAN_TEXT handling that excludes the initial statement row. ClickHouse
  column expansion and multi-word names are not relational joins or table names.
  Presto/Starburst/Athena wiring uses a fake DBAPI cursor with recorded EXPLAIN JSON,
  reusing the connection to inspect the last SQL.
- Light [DSDGen](validation/test_dsdgen_integration_light.py) and
  [DSQGen](validation/test_dsqgen_integration_light.py) tests require the actual
  platform binaries. DSDGen streams a small table and checks compressed output;
  DSQGen validates base-query generation without assuming every template set
  contains variants. [Plan comparison performance](performance/test_comparison_performance.py)
  measures ten comparisons of 200-node chains against a one-second budget.

### Producers, consumers and publication

- Public-export [environment fixtures](unit/core/results/test_environment_schema_compatibility.py)
  reject dropped identifier keys anywhere in serialized output.
  [Client-link wiring](unit/platforms/base/test_client_link_wiring.py) cannot break
  a successful run when collection fails, and records unavailable when neither
  region nor overhead exists. Keep link fields nested in result payloads;
  DataFrame engines collect the locality half without a SQL overhead probe.
- [Provenance bundles](unit/core/results/test_provenance_bundle.py) omit the
  optional block for absent/empty funding and source rather than emitting an empty
  dictionary. [Timing payloads](unit/core/results/test_schema_timing_contract.py)
  retain credential redaction, explicit producer tagging and the missing-run-type
  fallback. [Result status](unit/core/results/test_status.py) distinguishes
  unexecuted validation from executed failure: unvalidated results are non-clean
  for publication without necessarily failing the CLI. Its unvalidated set is
  the non-clean set minus CLI-failure statuses.
- [Databricks cache tests](unit/platforms/test_databricks_cache_control.py) keep
  session setup outside the first measured harness query. Concrete adapters
  explicitly declare plan-capture phase eligibility; registry wrappers read the
  currently rebound registry singleton. Adapter config helpers preserve plan
  options and skip `None` values. Redshift query errors and Firebolt load errors
  retain complete nonempty driver messages, with defensive empty-message handling.
- [Livy tests](unit/platforms/azure/test_livy_mixin.py) preserve statement timing
  and counts, wait delegation and abstract header/session hooks. Azure credential
  fixtures inject the credential class and refresh five minutes before expiry.
  GCS path tests retain ConfigurationError wording, empty bucket-only prefixes
  and trailing-slash normalization shared by Dataproc adapters.
- [Explorer pipeline tests](unit/scripts/explorer_pipeline/): Explorer ranking
  rejects unofficial compliance even with an eligible trust
  label. The read-model version expectation is an independent migration pin;
  update it with the compatibility matrix deliberately, not by copying a changed
  production constant.
- Trusted mirror publication may allow partial validation while community
  submission may not; privacy and inventory still apply. Corpus workflows use
  trusted-base code, explicit permits and parity checks. Empty changed-corpus
  lists do not bypass a separately required manifest. Release tag finalization
  binds the verified merge commit and recovers a tag created before its push.
- Release-tree tests tolerate the intentional absence of development-only
  `_project` files. Code-owner ruleset tests assert that specific predicate, not
  a branch-wide approval count. The project-reference guard rejects new stale
  paths while retaining its protected historical baseline. Lock revision guards
  reject decreases while permitting unchanged or increased revisions.
- Explorer override fixtures traverse bundle and override companion publication
  together. Static receipt tests check presentation-file contracts, not browser
  rendering. Notebook content tests accept declared platform extras or explicit
  dependencies and require a core-runner reference; those textual checks do not
  prove notebook execution or installed extra contents.

## Adapter regression constraints

Platform tests include real local database checks, pure SQL builders and mocked
remote clients. Preserve that distinction when interpreting results. Recorded
plans and injected SDK responses check wiring and error handling without
establishing cloud-service availability, live performance or full SQL compliance.

### Fixture ownership and adapter boundaries

- Real SQLite, DuckDB and DataFusion connection tests exercise their local
  connection paths. Mocked cloud plan tests preserve recorded fixture bytes and
  explicit UTF-8 decoding; locale-dependent decoding can fail on Windows.
  Spark Row fakes use actual tuples so conversion terminates.
- The opt-in `chdb_probe_satisfied` fixture replaces only the `chdb` lookup and
  delegates all other `find_spec` requests. It supports local-mode construction
  without chDB, including Windows; it does not open a local ClickHouse connection
  or prove chDB is installed. Preserve its non-autouse boundary.
- PostgreSQL extension fixtures patch the extension module and the parent
  `postgresql` module because the inherited constructor checks its own driver
  reference. Fake transport responses preserve actual exception classes.
  Synapse Spark fixtures patch individual HTTP methods rather than replacing
  `requests.exceptions`; the module shares the process-wide requests object,
  so those method patches must remain scoped and restored.
- The offline adapter inventory classifies every concrete manifest coordinate,
  imports its actual class and exercises real dialect translation without vendor
  SDK connections. Keep non-SQL strict-translation refusals and the reachable
  original Snowflake CLI option hook despite the autouse option stub.
- Composition prototypes characterize existing execution paths. The core mixin
  and incumbent platform helper differ in rollback, cursor ownership, validation
  logging and digest behavior. The incumbent helper is not the rejected runtime
  package alternative described in the architecture decision.
- Credential-file tests load real temporary YAML/JSON through CredentialManager.
  Passing required-field validation alone does not establish provider validity;
  some deliberately incomplete fixtures fail a later validation step.
- Mock outputs that code mutates must be fresh per invocation: power-test rows
  carry run/iteration tags, and enhanced result builders consume actual mappings
  with keys and lengths. Avoid reusing one mutable row across measured runs.

### Plan capture, timing and recovery

- Keep capture disabled paths free of EXPLAIN calls. Non-strict capture failure
  leaves a successful query successful and records its actual cause; strict
  PlanCaptureError must escape outside the query-error catch. A real SQL failure
  remains a FAILED query. Empty or failed EXPLAIN results are absence, not an
  error string sent to the plan parser. Preserve adapter-specific empty-result
  contracts, including Databricks `None` and the shared DBAPI helper's empty string.
- DuckDB's default plan capture uses JSON without ANALYZE. Explicit ANALYZE adds
  execution/timing and one notice per run; tests assert emitted SQL directly.
  DML and row-materializing statements must not re-execute through EXPLAIN ANALYZE,
  including CTAS, materialized views and SELECT-INTO. Statement identifiers,
  string literals, generated-column AS and nested INTO are not top-level verbs.
  MotherDuck and PostgreSQL tests retain this distinction too.
- PostgreSQL stream-cursor plan capture owns a fresh cursor from its underlying
  connection and closes only that cursor. A timed-out EXPLAIN thread must not
  corrupt the caller's next-query cursor. Redshift VACUUM/ANALYZE uses an isolated
  maintenance connection so a socket timeout cannot poison the benchmark session.
- DataFrame query time excludes plan capture; capture duration is reported
  separately in milliseconds. Slow injected capture and wall-time budgets must
  not leak into execution timing. Expression capture failures retain the real
  cause on the successful row and per-run list, with one warning per run.
- The plan summary counts unique query IDs represented by result rows and the
  plans companion, not per-stream/per-variant capture attempts. A recorded zero
  timing is distinct from missing timing. Internal public-ID-plus-digest capture
  keys must still match public query filters. Failed enhanced runs restore all
  temporary plan flags; an omitted/None run option preserves adapter defaults.
- Fingerprints ignore table size, estimated cardinality and physical index
  choices, but change with logical shape. These are asserted properties of local
  fixtures, not a guarantee about every upstream EXPLAIN representation.
- Link probes retain warm-up separately from measured samples, bounded caller
  return for a hung execution, and allowlisted diagnostics rather than raw error
  text. Region discovery avoids environment proxy handlers, caches results and
  preserves the provider of observed locality; an unknown cloud must not erase
  an observed region or fabricate cross-cloud placement.

### Loading, formats and null semantics

- CSV dialect precedence is manifest, benchmark attributes, then format defaults.
  Metadata lookup is case-insensitive. Empty-string null markers are meaningful;
  a nonempty sentinel converts only that sentinel and preserves empty strings.
  DataFrame loading restores empty strings for absent/nonempty markers, but not
  when the empty marker defines them as NULL. Trailing-delimiter probing depends
  on the resolver's dialect, not a separate benchmark-name heuristic.
- Preserve metadata even when benchmark tables win the source chain. Manifest
  metadata tests construct and serialize manifests directly; they do not run the
  actual generators. Local manifest paths must not force an eager CloudPath
  import. Distinguish missing metadata as an empty mapping from `None`.
- Providers must conserve all selected manifest shards; a benchmark's single-file
  mapping must not hide them. Platform-specific selection may replace text or
  directory entries with appropriate manifest files. Plain directories, missing
  shards and stale table-format directories must not silently count as zero-row
  successful loads. Delta/Iceberg table directories load as table-format units.
- Decompression compatibility files are owned by their context manager and cleaned
  on exit. Uncompressed paths pass through without a new file when no transform
  is needed. Strip only an actual final delimiter when requested: a CSV trailing
  delimiter can represent an empty final field. Boolean normalization changes
  exact fields only. Do not infer a universal trailing-pipe rule from `.dat` alone.
- DuckDB pipe loading projects explicit schema columns, adds a dummy field only
  when the trailing delimiter exists, and retains null padding without EXCLUDE
  against a nonexistent field. Bulk shards use one array read; per-shard row
  counts are before/after deltas rather than repeatedly summed totals. Dry runs
  capture SQL and return their explicit placeholder without executing the load.
- Generic Parquet loading streams batches. A later read or insert failure rolls
  back prior batches with its savepoint so a subsequent caller commit is safe.
  Spark no-cache chunk loading counts a table before and after all appends, not
  every DataFrame chunk; count failures fail the load, and negative deltas warn
  before clamping. Spark Connect compatibility directories remain under data_dir
  for mirrored container paths; unknown compression cannot lose its suffix.
- Raw cloud URI strings retain their scheme; converting them through Path can
  collapse separators. ClickHouse S3/GCS external fixtures bypass local path
  resolution deliberately. UC Volume loading tests assert COPY INTO rather than
  temporary-view or INSERT-SELECT fallbacks, without contacting Databricks.
- Row extraction must preserve a real first_row instead of fabricating an all-null
  substitute. Fabricated padding reports uncertain row identity and warns once
  when materialized, shared by fetchone/fetchall/rows. Count-only and has_real_rows
  inspection remain silent; they must not materialize or consume that warning.
- The Presto/Trino shared helper fixtures pin explicit delimiters over extension
  heuristics, SQL null/date/string handling, nonempty source paths and cursor
  batching. Catalog selection rejects system-only choices; shared schema execution
  owns created cursors and propagates nonrecoverable failures.

### Tuning and backend-specific interpretation

- Reused-database drift is captured during connection validation. Reset run-scoped
  state before creating the connection so that capture reaches the applied ledger
  companion. Ordinary infrastructure defaults must not masquerade as applied tuning.
- DataFrame tuning records actually consumed settings in the shared AppliedTuningLedger.
  Untuned runs stay noop; tuned runtime/write-layout entries derive applied_unverified
  until corroborated. Per-query streaming is not an applied session setting.
  A consumed configured spill directory is recorded, while an unused directory or
  a default spilling policy is not falsely claimed. Folding write-layout statements
  twice must not duplicate entries or disturb construction-time settings.
- ClickHouse corroborates tuned partition and sort keys together; validating only
  a present sort key must not hide a missing partition key. Engine-mandatory baseline
  ORDER BY on untuned tables is not applied tuning. Fake catalog tests are separate
  from real local DuckDB catalog corroboration and its verified/unverified receipts.
  Snowflake key parsing accepts LINEAR(...) and bare (...) catalog forms.
- Databricks MCP liquid-clustering intent reaches the resolver through translated
  tuning_config, not constructor kwargs that from_config drops. No requested
  clustering means no implicit ZORDER. Global Hudi keys cannot leak into tables
  without their columns.
- Spark-family AQE disablement sets explicit false values rather than omitting
  default-enabled keys. Explicit spark_config entries survive benchmark setup.
  Velox option specs are authoritative; its argparse hook is a no-op. Reject
  undeclared deployment modes and avoid mistaking VeloxColumnarToRow for a bare
  ColumnarToRow fallback. Plain Parquet/ORC tuning supports partitioning without
  inventing a DDL sorting key. Real TuningColumn instances satisfy renderer validation.
- DataFusion paths normalize both shape and Path type before directory detection.
  Its column names come through schema fields rather than `.columns`; the minimal
  LazyFrameLike protocol deliberately does not accept it. PyArrow `.columns`
  contains arrays, so protocol conformance is not a guarantee of column-name shape.
  UnifiedListExpr native unwrapping and expression indices preserve DataFusion's
  zero-to-one index conversion. Iceberg merges retain null/decimal schema.
- SQL and DataFrame query skip hooks remain distinct. Removing a stale Dask Q10
  preemptive guard does not remove generic worker-death handling or establish fresh
  scale-factor execution evidence. DataFrame client-host metadata conserves the SQL
  path's key set; comparing both hierarchies prevents silent producer divergence.
- ClickHouse CLI's explicit server default and the normalizer's embedded local
  default serve different callers. Missing/empty mode options fall through; tests
  preserve compatibility aliases. Generic ClickHouse optimization remains a no-op
  for configuration fields without a ClickHouse mapping.
- JoinOrderSynthetic uses its generic row-count strategy rather than the canonical
  exact-count strategy; lookup sizes and scaled tables differ. SSB customer counts
  use 30,000 per scale factor rather than TPC-H's 150,000. Generic power tests return
  both warm-up and measurement rows, tagging each for later filtering. All-query
  failure aborts the loop; optional fail-fast also stops on any query failure.
  Execute-only shared connections use non-closing proxies. A factory crash before
  any query yields a failed sentinel instead of a false zero-query pass; actual
  failed rows must not gain another sentinel.
- SQL-builder tests conserve quoted literals, CTE aliases and identifier case while
  applying dialect rewrites. QuestDB composition applies JOIN/INTERVAL/SUBSTRING/CTE
  fixups together. StarRocks unmatched regex shapes warn without rewriting, preserves
  its comma-CSV empty-as-NULL default, and distinguishes lowercase identifier text
  from uppercase type tokens. Doris Stream Load rejects partial loads when filtering
  is disallowed. Type/DDL tests retain mandatory distribution and duplicate-key rules.
- ClickHouse Delta builder/wiring tests are server-free. Base capability registration
  does not imply the local alias; select native local reads or executable snapshots
  through the explicit registration. A remote location cannot silently fall back
  to a local snapshot that cannot execute there. Keep these checks distinct from
  the Docker-gated native execution suite, whose run status needs separate evidence.
- Throughput capability sweeps classify each SQL-capable registered adapter by its
  canonical manifest declaration and independent snapshot. New or changed capability
  requires explicit classification and the reusable stream-isolation proof harness
  before an intentional `UPDATE_THROUGHPUT_SNAPSHOT=1` refresh. Resolution returns an
  actual StreamConnectionCapability. Independent-connection adapters override
  new_stream_connection below PlatformAdapter; inherited capability plus a custom
  create_connection also requires explicit stream-session-state restoration. Direct
  declarations own their override chain and focused proof. Unsupported resolution
  remains visible in the snapshot and is rejected before runtime stream submission.

## Core regression contracts

Keep test expectations independent of implementation values. The constraints
below explain fixture choices and compatibility boundaries that names and
assertions alone do not fully describe. Mocked and static checks establish their
asserted contracts; they do not certify live platforms or benchmark compliance.

### Registry, configuration and output roots

- [Frame API checks](unit/core/equivalence/test_frame_api_check.py) parse registered
  DataFrame query modules without importing them. Synthetic negative controls
  prove that missing UnifiedLazyFrame methods are detected even when ordinary
  value-level gates do not execute the affected query.
- [Required config forwarding](unit/core/test_platform_config_required_from_config.py)
  removes a required key from the forwarding set as a negative control: helper
  omissions must be detectable even when configuration construction does not raise.
  [Presort defaults](unit/core/test_presort_registry_defaults.py) belong to benchmark
  registry entries, so adding a benchmark does not require hardcoded CLI columns.
- [Scale validation](unit/core/test_scale_factor_validation.py) preserves the
  ValueError-compatible rejection type and the declared-scale tolerance. TPC-DS
  uses its dedicated compliance classifier; accepting a scale is not an official
  compliance verdict. Public discovery uses registry surface independently of
  support tier, as checked in [surface tests](unit/core/test_registry_surface_field.py).
- [Output-root guards](unit/core/test_benchmark_output_root_guard.py) derive scan
  targets from registered classes, including adjacent generators and downloaders.
  An exception may cover a non-default fallback or probe; constructor/generator
  defaults must honor BENCHBOX_OUTPUT_DIR instead of receiving an exception.
  [Propagation tests](unit/core/test_benchmark_output_root_propagation.py) inspect
  nested generator paths at construction and after output_dir reassignment,
  including TPC-DI's derived ETL directories and wrappers forwarding through _impl.
- [Runner handler tests](unit/core/test_runner_helpers.py) assign the actual
  DatabricksPath even when its local cache equals an existing local Path: local
  equality alone does not preserve the upload target. Output-root propagation
  preserves staging wrappers and their local cache rather than allocating a new
  staging root or discarding the remote target.
- [Manifest reuse](unit/core/test_runner_manifest_reuse.py) distinguishes validated
  generated files from caller-provided external tables. Directory formats and
  file formats must match manifest entries; a same-name replacement is not reusable.
  [Transactional edge tests](unit/core/test_benchmark_edge_cases.py) use a simplified
  connection that drops WHERE clauses, so populated-but-unmanifested tables are
  not evidence of valid provenance. Real manifest matching belongs to the
  [staging provenance tests](unit/core/transactional/test_staging_provenance.py).
- [Core run service tests](unit/core/test_run_service.py) keep interaction and
  credential retry in CLI code, prohibit private cross-surface service imports and
  validate the exported names. Path-to-string expectations use the host's spelling
  instead of assuming POSIX separators. Registry kwargs consumed by constructors
  must survive loader forwarding.

### DataFrame and SQL semantics

- [DataFrame query resolution](unit/core/runner/test_dataframe_no_query_source.py)
  discovers registry objects by type rather than a fixed constant name. Missing
  query surfaces and an overly narrow user filter need different guidance; nested
  import failures must propagate rather than become a successful zero-query run.
- [Plan capture wiring](unit/core/dataframe/test_benchmark_suite_plan_capture.py)
  uses a separate untimed execution after measured iterations on lazy platforms.
  Eager platforms must not perform another materialization merely to seek a plan;
  disabled capture and capture errors retain their explicit outcomes.
- [TPC-DS column fixtures](unit/core/tpcds/test_q66_value_columns.py) vary channel
  sales/net-column choices instead of assuming one template draw. Q39's fixture
  quantities 1, 1 and 100 produce a coefficient of variation above one in both
  months, exercising the filter while checking all ten output columns. Q70 tests
  decimal-scale rounding with 0.1 and 0.2; Q12/Q20 retain LIMIT 100 while Q98's
  shared helper remains unlimited.
- [TPC-DS NULL tests](unit/core/tpcds/test_null_semantics.py) preserve NULL groups,
  NULL-last ordering and SQL set semantics across expression/pandas families.
  [Warehouse fixtures](unit/core/tpcds/test_warehouse_null_name.py) include the
  NULL warehouse name and preserve input frames. Q78 distinguishes all-NULL sums
  from partial-NULL and complete groups. Dask mixed-direction sorting and its
  available Series operations need explicit coverage.
- [SCD string comparisons](unit/core/tpcdi/test_etl_scd_processor.py) trim padded
  text for object and newer pandas string dtypes so whitespace-only changes do
  not create dimension versions. [Ingest tests](unit/core/tpcdi/test_tpcdi_ingest_dtypes.py)
  keep date-like CSV, fixed-width and JSON payloads as text; the literal date
  column is an adversarial inference case. Packaging declares the pandas floor.
- [TPC-DS streams](unit/core/tpcds/test_throughput_test_coverage.py) use the run's
  stream count and seed, not stream_id-dependent substitutes, and batch dsqgen
  generation once before per-stream translation. The disconnected single-stream
  helper in [runner tests](unit/core/tpcds/benchmark/test_tpcds_runner_streams.py)
  raises NotImplementedError; counting SQL comments cannot establish execution.
- [TPC-DI dialect tests](unit/core/tpcdi/test_tpcdi_clickhouse_overrides.py) preserve
  base SQL while checking platform-specific date, derived-relation and aggregate
  rewrites, parameter substitution and single/bulk retrieval parity. TPC-DS
  ClickHouse tests reject unexpected generator shapes rather than restoring an
  unbounded plan silently. These string/translation checks are not live-engine runs.
- [JoinOrder plan tests](unit/core/joinorder/test_dataframe_join_plan_shape.py)
  check fixed syntactic join topology and the executor's consumed plan, not just
  equal rows. This measures multi-join execution rather than optimizer reordering;
  row equality alone cannot enforce that distinction.
- [CHAR equivalence fixtures](unit/core/tpchavoc/test_equivalence_execute_transform.py)
  opt in only to trailing blank-padding tolerance on declared CHAR columns.
  Leading whitespace, tabs, newlines, VARCHAR/TEXT and changed values remain
  significant. Fake connections record actual transformed SQL without requiring
  chDB; window OVER clauses must remain inside safe-division rewrites.
- [DDL parser fixtures](unit/core/dataframe/test_schema_utils_ddl_parser.py) retain
  precision/scale parentheses, multi-word types and quoted identifiers while
  returning unquoted logical names and stripping constraints. Foreign-key loading
  orders put referenced tables first, preserve unknown input tables and append
  cyclic leftovers in input order.
- [Static SQL catalogs](unit/core/test_static_query_catalog_block_scalars.py) keep
  literal YAML block scalars, byte-preserving SQL round trips and balanced
  placeholder braces. [OBT encoding tests](unit/core/tpcds_obt/test_etl.py) use a
  real tiny DuckDB table with 500 rows and 20 mostly nullable columns; the lenient
  parquet-to-text size ratio tests encoding, not a general storage guarantee.
- [Representative query frames](unit/core/test_dataframe_query_execution.py) use
  date-typed AMPLab visitDate values and CoffeeShop's raw product name column.
  Test aliases must not accidentally replace the producer's actual schema.

### Results, comparison and publication

- [Result extension tests](unit/core/results/test_result_extension_contract.py)
  preserve plan_capture_error in compact exported queries and through reload and
  re-export. In-memory fields alone do not prove persisted compatibility.
  [Cost round trips](unit/core/results/test_cost_round_trip.py) distinguish a missing
  legacy normalized-cost block from an explicitly rejected block; malformed shapes
  must not become authoritative totals. Translation comparability disclosures and
  applied-tuning ledger/status companions retain their separate serialization paths.
- [Query boundary inventories](unit/core/results/test_query_execution_contract.py)
  classify producer literals by query_id plus duration/row signals. Presentation
  mappings are consumers; compact id/ms producers have separate guards. Dynamic
  mappings remain covered by representative runtime adapter tests rather than
  guessed from literal inventory.
- [Raw plan policy](unit/core/results/test_query_plan_raw_output_policy.py) governs
  verbatim EXPLAIN text without dropping the structured DAG or fingerprint.
  Byte caps splitting UTF-8 characters retain a valid prefix and report actual
  retained bytes. [Depth limits](unit/core/results/test_serialization_limits.py)
  emit truncation markers while fingerprinting the full in-memory plan.
- [Timing statistics](unit/core/results/test_timing_statistics.py) retain independent
  nearest-rank expectations and historical MCP/CLI percentile fixtures solely to
  describe the superseded behavior. The 22-query fixture captures a meaningful
  distribution. Current percentile arguments use a fraction, not 0-100; invalid
  arguments are rejected even when the input sequence is empty.
- [TPC-Havoc checksums](unit/core/tpchavoc/test_tpchavoc_validation_coverage.py)
  exercise mixed types and NULLs outside the bounded gate's simpler result sets.
  Type tags, separator/backslash escaping, forged type names and temporal subclasses
  prevent cross-shape collisions. Tie tolerance applies only at a genuine ambiguous
  boundary; constant columns, unique boundaries and changed non-boundary values
  remain errors. Container comparisons retain list order and structural differences.
  The gate-side int/float digest convention is a separate compatibility contract.
- [Submit classifiers](unit/core/results/test_submit_classifier_contract.py) compare
  loaded-result, path/UAT and CLI terminal states. Load failures are integrity
  errors; unvalidated classification takes precedence over translation fallback.
  Tests supply the deployment salt to isolate classification, not to certify
  downstream submission of their minimal bundles. Local anonymized export can
  remain usable without the community publication salt.
- [Provenance vocabulary tests](unit/core/results/test_provenance.py) keep offline
  ImportError fallback copies aligned with the canonical vocabulary. Unknown
  labels remain visible but unranked; source-derived labels precede other labels
  in source order. Membership alone cannot prove that ordering.
- [Integrity tests](unit/core/results/test_integrity_validator.py) use normalized
  vector-search query IDs and exclude plan/override companion files from bundle
  discovery. Compatibility skips satisfy count accounting but cannot certify a
  run in which no query executed. [Summary charts](unit/core/visualization/test_post_run_summary.py)
  keep variant query IDs separate, rank by per-query means rather than one fast
  outlier and require system_profile for the multi-column platform display.
  Rendering errors propagate to callers, which own recovery.

### Data identities and baseline maintenance

- [Logical hash goldens](unit/core/data_fetch/test_logical_hash.py) pin complete
  lookup-table contents in the tiny fixture as a build/runtime algorithm tripwire.
  Recompute them deliberately with a version change, not by copying new output.
  Integer encodings normalize across CSV/Parquet; NULL and empty string remain
  distinct. Manifest identity depends on row counts and logical hashes while
  transport-byte/archive hashes may change without changing logical content.
- [Data-fetch fixtures](unit/core/data_fetch/test_manager.py) separate caller-owned
  extraction from download. Their fake downloader may simulate extraction solely
  for the test; production extraction is not a downloader responsibility. Corrupt
  cached files raise checksum diagnostics before a download, and logical verification
  remains valid across row-order/compression rewrites.
- [Generation versioning](unit/core/test_datagen_version.py) fingerprints relevant
  specification/configuration inputs, rejects unrelated/stale stamps and does not
  attach a local dataset stamp to execute-only results lacking dataset identity.
  Saved comparisons retain generation caveats.
- [Baseline writer tests](unit/core/equivalence/test_cross_surface_baseline_update.py)
  prune only the named gate even when other gates use identical conventional keys.
  No-op writes preserve bytes; the writer's maintained header and checked-in file
  are a golden fixed point. Code-backed classified predicates do not belong in YAML.
- [Auto-detect fixtures](unit/core/equivalence/test_cross_surface_baseline_autodetect.py)
  use the actual gate, validator and YAML writer with only production DataFrame
  loading substituted. Detect reads the gate's own report; prune delegates to its
  update mode. Still-live entries and unrelated sections remain untouched. An
  unrelated unclassified divergence refuses all pruning, and resolved code-only
  entries remain attention-needed rather than falsely reported as auto-removed.

### Pricing and primitive-operation fixtures

- [Pricing provenance goldens](unit/core/cost/test_pricing_provenance.py) tie exact
  constants to named provenance tables in pricing_data.yaml. Structural pricing
  tests use table lookups instead of duplicating those constants. Update goldens
  with the cited captured source and provenance record, not current implementation
  output. The retained captures are evidence about their recorded retrieval date,
  not assertions that those prices are current.
  [Regional price goldens](unit/core/cost/test_regional_pricing.py), including
  `test_captured_single_region_values`, and the BigQuery assertion in
  [cost integration](unit/core/cost/test_integration.py) use the same captured
  `bigquery_on_demand_prices` authority: Google BigQuery pricing at
  `cloud.google.com/bigquery/pricing`, retrieved 2026-09-18. The integration
  expectation of 6.25 covers one TiB at the recorded USD 6.25 per TiB rate.
  Update these expected values from a changed vendor source and its provenance
  record, independently of calculator output.

  | Golden lookup | Provenance table and captured source |
  | --- | --- |
  | BigQuery US | bigquery_on_demand_prices; Google BigQuery pricing, retrieved 2026-09-18 |
  | Firebolt FBU | firebolt_fbu_price; Firebolt billing documentation, retrieved 2026-09-18 |
  | Redshift rg.12xlarge | redshift_node_prices; AWS Price List API, published 2026-09-11T12:45:05Z, retrieved 2026-09-18 |
  | Synapse DW100c | synapse_dedicated_dwu_prices; Azure Retail Prices API, retrieved 2026-09-18 |
  | Snowflake standard AWS US | snowflake_credit_prices; manual, retrieval unknown; provisional secondary-source verification for US editions, non-US unverified |
  | Databricks premium AWS serverless SQL | databricks_dbu_prices; manual, retrieval unknown; Azure block checked against eastus retail API capture, AWS/GCP hand-pinned |
  | Athena regional rates | athena_price_per_tb; manual, retrieval unknown; recorded AWS Athena pricing verification |
  | Synapse serverless regional rates | synapse_serverless_price_per_tb; manual, retrieval unknown; recorded Azure serverless pricing verification |

- [Billing-unit checks](unit/core/cost/test_cloud_platform_costs.py) distinguish
  BigQuery TiB (2^40 bytes) from Athena/Synapse decimal TB (10^12 bytes).
  A 2^40-byte Athena fixture is more than one decimal TB. Fabric uses CU-seconds
  divided by 3600 for CU-hours; node/DBU/FBU time fixtures retain seconds-to-hours
  conversion. Negative-duration compatibility coverage asserts the current negative
  QueryCost, despite its misleading historical test name; it does not establish
  input validation or endorse publishing such a value.
- [Cost extraction](unit/core/cost/test_platform_config_extraction.py) may derive
  a provider from a single-cloud platform identity, but must not invent an
  unobserved region. Configured sizing without observed provenance remains
  unavailable for normalized publication. Live double-nested configuration and
  loader-flattened payloads are separate supported shapes.
- [Cost consumer gates](unit/core/cost/test_gated_cost_consumers.py) keep fallback
  estimates out of persisted totals, rankings, plots, TCO and optimizer advice.
  Designed price buckets differ from catch-all fallback guesses. Reachability
  fixtures check emitted workload/tier/region keys against the captured table;
  a new emission requires a deliberate table and expectation update.
- [Tuning template parity](unit/core/tuning/test_tpc_tuning_template_parity.py)
  keeps unrendered BigQuery/Redshift layouts out of the certified set. Synthetic
  cap tests bypass rendering eligibility only to isolate BigQuery's clustering
  cap or Redshift's distribution-key limit; that bypass does not certify layouts.
  [Metadata reuse fixtures](unit/core/tuning/test_tuning_metadata.py) use a shared,
  SQL-shape-aware fake table across manager instances. Missing old-format section
  markers mean unknown drift with a warning. A real empty section-only tunings
  object differs from absent metadata; job-style clients use qualified adapter
  reads/writes without cursor or commit assumptions.
- [Write catalog fixtures](unit/core/write_primitives/test_catalog_loader.py)
  preserve decimal scale in fractional batch-value casts and dense bounded
  Snowflake row keys so cleanup deletes all generated rows. Bare NUMERIC retains
  BigQuery's separate semantics. Empirical sketch claims
  map compact documentation byte-size claims to explicit catalog validation bounds;
  add a mapping when a new verified sketch claim appears.
- [PySpark sketch fakes](unit/core/write_primitives/test_pyspark_sketch_factories.py)
  inject functions without requiring PySpark. MagicMock chaining is intentional;
  merge results use a positional-indexing stand-in that raises on the wrong column,
  rather than echoing an injected return value. Aggregate-state dispatch uses a
  configured manager stand-in without a JVM, preserves early skips and does not
  call merge after persist failure. Null platform overrides win before adapter
  sql_override injection, and FAILED transaction adapter payloads remain failures.
- [Read variant contracts](unit/core/read_primitives/test_read_primitives_variant_contracts.py)
  retain the explicit Redshift approximate-percentile parser limitation. Reassess
  that narrow allowlist when the pinned SQLGlot release recognizes the syntax;
  static parser limitations do not certify or prevent adapter execution.
- [Error recovery fixtures](unit/core/tpcdi/test_error_recovery_coverage.py) use
  distinct checkpoint identities because time-based IDs can collide within one
  second. Error-report tests inject log records and substitute statistics, so they
  do not test the nested statistics call itself. The current manager uses a
  reentrant lock; the removed comment's deadlock claim is stale. Retry tests
  retain explicit arithmetic/caps and fresh-manager state isolation.
