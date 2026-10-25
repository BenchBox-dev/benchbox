# Packaged Tuning Templates

This directory ships copies of the `<benchmark>_tuned.yaml` templates that
`benchbox/cli/tuning_resolver.py` auto-discovers for `--tuning tuned`. They
exist so that auto-discovery still resolves a template when `benchbox` is
installed as a package (e.g. `pip install benchbox`) and run outside a
checkout of this repository, where `examples/tunings/` does not exist.

**`examples/tunings/<platform>/<benchmark>_tuned.yaml` remains the
dev-time source of truth.** These files are plain copies, not symlinks
(symlinks do not survive sdist/wheel packaging reliably across platforms).
`tests/unit/cli/test_tuning_resolution.py::TestPackagedTemplatesParity`
asserts byte-for-byte parity between each file here and its
`examples/tunings/` counterpart, so any edit to a source template that
forgets to re-sync the packaged copy fails CI.

To re-sync after editing a template in `examples/tunings/`:

```bash
cp examples/tunings/<platform>/<benchmark>_tuned.yaml \
   benchbox/core/tuning/templates/<platform>/<benchmark>_tuned.yaml
```

Only the platforms/benchmarks with real auto-discovery naming
(`duckdb/`, `databricks/`, `snowflake/`, `clickhouse/`) are packaged here.
`clickhouse/` holds `tpch`, `tpcds` and `ssb`, and serves every ClickHouse
platform (`clickhouse-local`, `clickhouse-server`, `clickhouse-cloud` and the
`chdb` alias). DataFrame profiles (`examples/tunings/dataframe/`) are resolved only
from a checkout (`--tuning tuned` finds `<platform>_optimized.yaml` for
`polars`, `pandas` and `cudf` there; see `examples/tunings/README.md`) and are
not packaged.

This is the **last** discovery tier - it is only consulted after the
`BENCHBOX_TUNING_PATH` environment variable and the cwd-relative
`examples/tunings/` / `<platform>/<benchmark>_tuned.yaml` paths have all
been checked and none of them exist. See
<https://benchbox.dev/docs/reference/cli/tuning.html#how-tuning-is-resolved> for the full
precedence order.
