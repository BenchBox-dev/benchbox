# TPC binary release

This is the maintainer procedure for the TPC-H and TPC-DS binaries shipped in
the BenchBox package.

## Updating bundled binaries

BenchBox ships pre-compiled binaries inside the Python package at
`benchbox/_binaries/tpc-{h,ds}/{platform}/`.  These are the binaries used
at runtime (priority-1 path in `ensure_tpc_binaries()`).

Whenever the patched sources in `_sources/tpc-ds/tools/` or
`_sources/tpc-h/dbgen/` change, the bundled binaries must be rebuilt and
committed.  **Always use the automation** - manual copies are error-prone
(this is how `dsqgen` was once shipped unpatched while `dsdgen` was patched).

### Rebuild and deploy for the current platform (no Docker required)

```bash
make compile-tpcds-binaries
```

This calls `compile-all-platforms.sh --native`, which:

1. Compiles `dsdgen`, `dsqgen`, `tpcds.dst`, `tpcds.idx` from the patched
   sources in `_sources/tpc-ds/tools/`
2. Writes build artefacts to `_binaries/tpc-ds/darwin-arm64/` (untracked)
3. **Deploys the four files to `benchbox/_binaries/tpc-ds/darwin-arm64/`
   and regenerates `checksums.md5`** (this is the tracked, shipped location)

After running the command, regenerate the bundled-tree hash manifest and
commit the changed files in `benchbox/_binaries/`:

```bash
uv run -- python scripts/bundled_binary_manifest.py --write
git add benchbox/_binaries/SHA256MANIFEST.json benchbox/_binaries/tpc-ds/darwin-arm64/
git commit -m "fix(tpcds): rebuild darwin-arm64 binaries from patched sources"
```

### Rebuild for all platforms (requires a container engine)

```bash
cd _sources/compilation/scripts
./compile-all-platforms.sh
```

The Linux/Windows builds run in a container. The script auto-detects a
Docker-compatible engine, preferring `container` (Apple Containerization on
macOS, which runs amd64 images under Rosetta) then `docker`, `podman` and
`mocker`; override with `BENCHBOX_CONTAINER_ENGINE=<engine>`. The macOS
`arm64`/`x86_64` binaries build natively (no engine needed); the `x86_64`
cross build compiles its `mkheader` and `distcomp` host tools for the build
host, so it does not need Rosetta on macOS.

The TPC-DS C sources are not tracked (TPC EULA). To rebuild, download the
TPC-DS 4.0.0 tools from tpc.org and apply the patches as described in
`_sources/tpc-ds/REBUILD.md`.

**Supported platforms:**

| Platform | Architecture | Method |
|----------|--------------|--------|
| darwin | arm64 | Native (on Apple Silicon) |
| darwin | x86_64 | Cross-compilation |
| linux | x86_64 | Container (mocker/docker/podman) |
| linux | arm64 | Container (mocker/docker/podman) |
| windows | x86_64 | Container + MinGW |
| windows | arm64 | Container + MinGW |

> **Framing convention:** all `dbgen` builds compile with `-DEOL_HANDLING`
> (no trailing field separator), matching TPC-DS `-terminate n`. Keep this
> consistent across every build path — see `_sources/tpc-h/PATCHES.md`.
> `tests/unit/core/tpch/test_tpch_dbgen_framing.py` enforces it.

> **Note:** The full Docker build currently only deploys to `_binaries/`
> (untracked).  After running it, manually copy the updated binaries into
> `benchbox/_binaries/{platform}/` and regenerate `checksums.md5` there
> before committing.

## Bundled binary checks

Two hosted workflows run the bundled binaries on hosted runners.

- `tpch-dbgen-intel-macos.yml` runs the bundled `darwin-x86_64` `dbgen` on an
  Intel macOS runner at scale factor 0.01 and compares the supplier and customer
  output hashes with the canonical values in
  `tests/unit/core/tpch/test_tpch_dbgen_framing_binaries.py`.
- `tpcds-platform-identity.yml` generates TPC-DS data and `dsqgen` parameters at
  scale factor 0.01 on every bundled platform (linux-x86_64, linux-arm64,
  darwin-arm64, darwin-x86_64, windows-x86_64, windows-arm64) and compares the
  manifests. The Linux and macOS cells must agree. The Windows cells are
  advisory. The windows-arm64 pair are x86-64 binaries running under emulation.

Owner decision (2026-10-04): the Windows cells (x86_64 and arm64) stay advisory.
