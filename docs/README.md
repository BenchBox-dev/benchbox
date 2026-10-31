<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox Documentation

```{tags} beginner
```

BenchBox helps you run industry benchmarks across analytical databases. The documentation is organized to get you productive quickly and keep deeper references tidy.

## Start Here

- [Getting started in 5 minutes](https://benchbox.dev/docs/usage/getting-started.html), install, run your first benchmark, and inspect the output.
- [CLI quick reference](https://benchbox.dev/docs/usage/cli-quick-start.html), command overview and the most useful flags.
- [Choose a platform](https://benchbox.dev/docs/platforms/platform-selection-guide.html), requirements and trade-offs for each adapter.
- [Installation & environment setup](https://benchbox.dev/docs/usage/installation.html), uv, pip, extras, and dependency checks.

## Versioning & Releases

BenchBox adopts Semantic Versioning (`MAJOR.MINOR.PATCH`) to communicate compatibility expectations:

- Increase **MAJOR** for breaking CLI, configuration, or API changes.
- Increase **MINOR** for backward-compatible feature additions.
- Increase **PATCH** for bug fixes or documentation-only updates.

Current release: `v0.5.0rc2`. Use `benchbox --version` for a human-readable summary or `benchbox --version-json` when you need structured diagnostics in automation. Both commands confirm that the package, `pyproject.toml`, and documentation markers stay aligned. Platform runs now capture the requested versus resolved driver package versions, so comparisons across DuckDB or connector releases remain reproducible.

## Usage Guides

- [Usage overview](https://benchbox.dev/docs/usage/), end-to-end workflows and tips.
- [Configuration handbook](https://benchbox.dev/docs/usage/configuration.html), CLI flags, config files, and validation.
- [Examples](https://benchbox.dev/docs/usage/examples.html), curated snippets for automation.
- [API reference](https://benchbox.dev/docs/reference/api-reference.html), programmatic access points.

## Benchmark Library

- [Benchmark catalog](https://benchbox.dev/docs/benchmarks/), supported suites and feature matrix.
- [Platform comparison](https://benchbox.dev/docs/platforms/comparison-matrix.html), latency, scale, and capability notes.
- [Data generation and compression](https://benchbox.dev/docs/usage/data-generation.html), storage guidance and tuning.

## Advanced Topics

- [Power-run and throughput testing](https://benchbox.dev/docs/advanced/power-run-concurrent-queries.html).
- [Optimizer and validation tooling](https://benchbox.dev/docs/advanced/optimizer-tests.html).
- [Performance monitoring and regression alerts](https://benchbox.dev/docs/advanced/performance.html).
- [Custom benchmark authoring](https://benchbox.dev/docs/advanced/custom-benchmarks.html).
- Heavy-work guard (`docs/operations/heavy-work-guard.md`): opt in the CLI to the host lock, admission checks and watchdog.

## Design & Contribution

- [Architecture & design notes](https://benchbox.dev/docs/design/).
- [Development workflow](https://benchbox.dev/docs/development/development.html) and [platform onboarding guide](https://benchbox.dev/docs/development/adding-new-platforms.html).

- Check the root `CONTRIBUTING.md` for contribution expectations and coding standards.

## Need Help?

- [Frequently Asked Questions (FAQ)](https://benchbox.dev/docs/usage/faq.html) answers common questions about BenchBox.
- [Troubleshooting guide](https://benchbox.dev/docs/usage/troubleshooting.html) covers common errors and remediation steps.
- Open issues or discussions on GitHub if you get stuck.
