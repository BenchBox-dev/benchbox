<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox Documentation

```{tags} beginner
```

BenchBox helps you run industry benchmarks across analytical databases. The documentation is organized to get you productive quickly and keep deeper references tidy.

## Start Here

- [Getting started in 5 minutes](usage/getting-started.md), install, run your first benchmark, and inspect the output.
- [CLI quick reference](usage/cli-quick-start.md), command overview and the most useful flags.
- [Choose a platform](platforms/platform-selection-guide.md), requirements and trade-offs for each adapter.
- [Installation & environment setup](usage/installation.md), uv, pip, extras, and dependency checks.

## Versioning & Releases

BenchBox adopts Semantic Versioning (`MAJOR.MINOR.PATCH`) to communicate compatibility expectations:

- Increase **MAJOR** for breaking CLI, configuration, or API changes.
- Increase **MINOR** for backward-compatible feature additions.
- Increase **PATCH** for bug fixes or documentation-only updates.

Current release: `v0.5.0rc1`. Use `benchbox --version` for a human-readable summary or `benchbox --version-json` when you need structured diagnostics in automation. Both commands confirm that the package, `pyproject.toml`, and documentation markers stay aligned. Platform runs now capture the requested versus resolved driver package versions, so comparisons across DuckDB or connector releases remain reproducible.

## Usage Guides

- [Usage overview](usage/index.md), end-to-end workflows and tips.
- [Configuration handbook](usage/configuration.md), CLI flags, config files, and validation.
- [Examples](usage/examples.md), curated snippets for automation.
- [API reference](reference/api-reference.md), programmatic access points.

## Benchmark Library

- [Benchmark catalog](benchmarks/index.md), supported suites and feature matrix.
- [Platform comparison](platforms/comparison-matrix.md), latency, scale, and capability notes.
- [Data generation and compression](usage/data-generation.md), storage guidance and tuning.

## Advanced Topics

- [Power-run and throughput testing](advanced/power-run-concurrent-queries.md).
- [Optimizer and validation tooling](advanced/optimizer-tests.md).
- [Performance monitoring and regression alerts](advanced/performance.md).
- [Custom benchmark authoring](advanced/custom-benchmarks.md).

## Design & Contribution

- [Architecture & design notes](design/index.md).
- [Development workflow](development/development.md) and [platform onboarding guide](development/adding-new-platforms.md).
- Check the root `CONTRIBUTING.md` for contribution expectations and coding standards.

## Need Help?

- [Frequently Asked Questions (FAQ)](usage/faq.md) answers common questions about BenchBox.
- [Troubleshooting guide](usage/troubleshooting.md) covers common errors and remediation steps.
- Open issues or discussions on GitHub if you get stuck.
