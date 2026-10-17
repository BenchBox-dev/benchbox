<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Development Guide

```{tags} contributor, guide
```

This guide provides information for developers who want to contribute to BenchBox or understand its internal architecture.

## Getting Started with Development

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) **0.8 or newer** (recommended for environment
  management). The committed `uv.lock` uses lockfile `revision = 3`; an older uv
  silently rewrites it to revision 2 as a side effect of any `uv add`/`uv lock`
  run. A pre-commit guard (also runnable via `make uv-lock-revision-check`)
  rejects such a downgrade — if it fires, upgrade uv (`uv self update`) and regenerate the lock.
- `git`

### Setting up the Development Environment

1. **Clone the repository:**

    ```bash
    git clone https://github.com/BenchBox-dev/benchbox.git
    cd BenchBox
    ```

2. **Create a virtual environment:**

    ```bash
    uv venv
    source .venv/bin/activate
    ```

3. **Install dependencies:**

    ```bash
    uv pip install -e .[dev]
    ```

    The `[dev]` extra installs testing, linting, and typing tools.

### Running Tests

BenchBox uses `pytest` for testing. Run tests using either `make` commands or direct `pytest`:

```bash
make test
uv run -- python -m pytest -m fast

make test-all
uv run -- python -m pytest

make test-unit
uv run -- python -m pytest -m unit

make test-integration
uv run -- python -m pytest -m "integration and not live_integration"

make coverage-fast
make coverage-all
make coverage-opt-in-all
uv run -- python -m pytest --cov=benchbox --cov-report=term-missing
```

Each `uv run` line is the direct equivalent of the `make` target above it. In order, the blocks run:

- Fast tests for quick feedback (`make test`).
- The full test suite (`make test-all`).
- Unit tests only (`make test-unit`).
- Integration tests (`make test-integration`).
- Coverage: `make coverage-fast` covers fast tests only, for quick feedback. `make coverage-all` is routine coverage that excludes stress, resource-heavy and live tests. `make coverage-opt-in-all` covers the full tree, including opt-in stress, resource-heavy and live tests, and needs services and credentials. The final `uv run` command is the direct equivalent for coverage.

Linting and formatting run through Ruff:

```bash
make format
uv run ruff format .

make lint
uv run ruff check .
```

Each `uv run` command is the direct equivalent of the `make` target above it.

Type checking is available via:

```bash
make typecheck
uv run ty check
```

`uv run ty check` is the direct equivalent of `make typecheck`.

## Contributing

We welcome contributions! Please see the `CONTRIBUTING.md` file in the root of the project for details on our development process, coding standards, and how to submit a pull request.

## Validation APIs

BenchBox exposes a CLI-independent validation workflow through
`benchbox.core.validation.ValidationService`. The service orchestrates preflight,
manifest, database, and platform capability checks and returns structured
`ValidationResult` objects that mirror the data surfaced by the CLI.
Programmatic consumers can run comprehensive validation with:

```python
from benchbox.core.validation import ValidationService

service = ValidationService()
results = service.run_comprehensive(
    benchmark_type="tpcds",
    scale_factor=1.0,
    output_dir=output_path,
    manifest_path=manifest_path,
    connection=db_connection,
    platform_adapter=adapter,
)
summary = service.summarize(results)
```

This enables benchmarks, adapters, and external automation to reuse the same
validation logic without importing CLI modules.

The CLI mirrors these capabilities via the `benchbox run` command. Use
`--enable-preflight-validation`, `--enable-postgen-manifest-validation`, and
`--enable-postload-validation` to toggle each stage while the core lifecycle
runner captures the resulting validation metadata.

## Import Patterns

Benchmarks use a lightweight lazy-loading system so the core package can start
quickly and only load optional dependencies when needed. See
`docs/development/import-patterns.md` for guidance on adding new benchmarks to
the registry, writing tests for lazy imports, and troubleshooting missing
dependency errors.
