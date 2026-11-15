# BenchBox Test Reference

## Targets

- Fast smoke: `make test-fast`
- Standards: `uv run -- python -m pytest -m "tpch or tpcds" --tb=short`
- One benchmark: `make test-marker TEST_MARKER=<tpch|tpcds|ssb|clickbench>` or a focused pytest path.
- DataFrame platforms: compare against DuckDB SQL at smoke scale when validating behavior.

## Rules

Report the command, benchmark, platform, scale, query subset, result, failures,
artifacts, and narrow next command. Run live, cloud, or Docker tests only with
the required approval and environment.
