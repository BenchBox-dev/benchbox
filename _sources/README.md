# _sources/ Directory

This directory contains the source code and compilation infrastructure for TPC benchmark
data generation tools. The compiled binaries are stored in `_binaries/` for distribution.

## Directory Structure

```
_sources/
├── README.md                    # This file
├── compilation/                 # Compilation infrastructure
│   ├── docker/                  # Docker images for cross-compilation
│   │   ├── Dockerfile.linux-arm64
│   │   ├── Dockerfile.linux-x86_64
│   │   ├── Dockerfile.windows-arm64
│   │   └── Dockerfile.windows-x86_64
│   └── scripts/
│       └── compile-all-platforms.sh
├── tpc-h/                       # TPC-H source (dbgen/qgen)
│   ├── EULA.txt                 # TPC EULA - must accept to use
│   ├── PATCHES.md               # Documentation of applied patches
│   ├── stdout-support.patch     # Patch for -z stdout output
│   ├── dbgen/                   # Data generator source
│   └── ref_data/                # Reference data files
├── tpc-ds/                      # TPC-DS source (dsdgen/dsqgen)
│   ├── EULA.txt                 # TPC EULA - must accept to use
│   ├── PATCHES.md               # Documentation of applied patches
│   ├── stdout-support.patch     # Patch for FILTER stdout output
│   ├── tools/                   # Data/query generator source
│   ├── query_templates/         # SQL query templates
│   └── answer_sets/             # Reference answer sets
└── join-order-benchmark/        # JOB benchmark queries (git submodule)
```

## Obtaining TPC Source Code

The TPC source code is distributed under the TPC End User License Agreement (EULA).
By using BenchBox with TPC benchmarks, you accept the TPC EULA terms.

### TPC-H Source

1. Visit https://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp
2. Select "TPC-H" from the benchmark list
3. Download the "TPC-H Tools" package (requires free TPC account)
4. Extract to `_sources/tpc-h/`

The expected structure after extraction:
```
_sources/tpc-h/
├── EULA.txt
├── dbgen/
│   ├── bcd2.c
│   ├── bm_utils.c
│   ├── build.c
│   ├── config.h
│   ├── dists.dss
│   ├── driver.c
│   ├── dss.h
│   ├── makefile.suite
│   ├── print.c
│   ├── rnd.c
│   └── ... (other source files)
├── ref_data/
└── specification.pdf
```

### TPC-DS Source

1. Visit https://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp
2. Select "TPC-DS" from the benchmark list
3. Download the "TPC-DS Tools" package (requires free TPC account)
4. Extract to `_sources/tpc-ds/`

The expected structure after extraction:
```
_sources/tpc-ds/
├── EULA.txt
├── tools/
│   ├── address.c
│   ├── build_support.c
│   ├── config.h
│   ├── dsdgen.c
│   ├── genrand.c
│   ├── makefile
│   ├── Makefile.suite
│   ├── params.h
│   ├── porting.h
│   ├── print.c
│   ├── tpcds.dst
│   └── ... (other source files)
├── query_templates/
├── query_variants/
└── specification/
```

### TPC-DI Appendix SQL and EULA

`_sources/tpc-di/` and `benchbox/_binaries/tpc-di/` hold the same six files, byte for byte:
`EULA.txt`, `NOTICE.txt` (line 1 is the legend "THE TPC SOFTWARE IS AVAILABLE WITHOUT CHARGE FROM TPC."),
and the specification's audit (Appendix A), validation (Appendix B) and visibility (Appendix C) SQL:
`tpcdi_audit.sql`, `tpcdi_validation.sql`, `tpcdi_visibility_1.sql`, `tpcdi_visibility_2.sql`.
The SQL files are never edited. `benchbox/core/tpcdi/appendix.py` translates them at load time with the
SQLGlot fork through `translate_sql_query`, the entry point other BenchBox benchmarks use, reading them as the
`postgres` dialect and emitting the target platform's dialect (an unknown platform is refused). The one
pre-step is to remove the header's literal placeholder `FROM DUMMY TABLE`, which is not SQL (the parser reads
it as a table named `DUMMY`); that is the header table's `SQLSERVER <blank>` row, and its count is recorded.
The header placeholder `[||]` appears only inside the header comment and needs no pre-step. The labelled
duration `Date-1 day` in `tpcdi_audit.sql` is parsed as `Date - 1` with the alias `day`; this is the
translator's reading, not a rule from the header table. Each result records the source dialect, target
dialect, pre-step and notes.

**The appendix SQL is an unofficial shared copy.** The TPC site offers no separate Appendix download.
The files are the copy shared by the abroniewski/TPC-DI-MS-SQL-Benchmark repository at commit
`18eca39b128f5fcc86cbc9c1e53e30ed0e685fa7` (`Helpers/Scripts`). The shannon-barrow/databricks-tpc-di
repository at 8e0f944 holds byte-identical copies of the audit, validation and visibility_2 files; its
visibility_1 file differs from the shipped one in whitespace and line breaks only (see
`_sources/tpc-di/databricks-tpc-di/PROVENANCE.md`). The shipped files are unmodified from the abroniewski
copy; the shipped visibility_1 is that repository's version, not the Databricks one. The official archive
is being sought, and a tracked follow-up will replace this copy once it is obtained. The Tools archive is
not part of this copy.

| File | SHA-256 |
|------|---------|
| `tpcdi_audit.sql` | `32eb0f55a08b8cf5a906d4aff563539d42c873c72d6dc662798218b2bc3107fc` |
| `tpcdi_validation.sql` | `9ee9ef8144c7f53bb6fb0d4ac489dda2379e7aa1b8e24d7a12df1539b7f86c02` |
| `tpcdi_visibility_1.sql` | `209454549598d8147a2a0eb9ced8bd86eb3548f32fc90c1765975b0076e8bde7` |
| `tpcdi_visibility_2.sql` | `a22645d5c9d2095d8d77549db3e7ecbc8d5d5417e8f0973fbd92150347ea894a` |

`EULA.txt` is the TPC's published End User License Agreement v2.2.0,
`https://www.tpc.org/TPC_Documents_Current_Versions/txt/EULA_v2.2.0.txt`, SHA-256
`017bd9dbc4c332ac433fc0cc018f3691b330f8aca82a05a20274512de423576e`. Its wording equals
`_sources/tpc-h/EULA.txt` (clause 9 included); the two differ only in line wrapping, quotation marks and
numbering whitespace.

The official `TPC-DI_Tools_v1.1.0.zip` (SHA-256 `d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b`)
contains no TPC EULA and no appendix SQL; it is not committed.

## Applying BenchBox Patches

BenchBox includes patches that add features and fix compilation issues:

### TPC-H Patches

The `stdout-support.patch` adds a `-z` flag for streaming data to stdout:

```bash
cd _sources/tpc-h
patch -p1 < stdout-support.patch
```

**Changes applied:**
- Adds `-z` flag to stream generated data to stdout (enables piping to compression)
- Fixes format specifier bug in money printing (`%ld` → `%d`)

See `_sources/tpc-h/PATCHES.md` for detailed documentation.

### TPC-DS Patches

The `stdout-support.patch` fixes the broken `-FILTER Y` flag and adds Linux compatibility:

```bash
cd _sources/tpc-ds
patch -p1 < stdout-support.patch
```

**Changes applied:**
- Fixes `-FILTER Y` flag for stdout output (was broken in upstream)
- Adds `MAXINT` definition for Linux builds
- Adds `-fcommon` flag for GCC 10+ compatibility

See `_sources/tpc-ds/PATCHES.md` for detailed documentation.

### Verifying Patches

After applying patches, verify they were applied correctly:

```bash
grep -n "zstdout" _sources/tpc-h/dbgen/dss.h

grep -n '"FILTER"' _sources/tpc-ds/tools/params.h
```

The first command checks the TPC-H `-z` flag and should print `EXTERN int  zstdout;`. The second checks the TPC-DS FILTER fix and should show `"FILTER"` without a leading underscore.

## Compiling Binaries

### Prerequisites

**macOS (native compilation):**
- Xcode Command Line Tools: `xcode-select --install`
- For cross-compilation: `brew install make` (optional)

**Linux/Windows (Docker-based):**
- Docker: https://docs.docker.com/get-docker/
- Docker Buildx for multi-platform builds

### Quick Compilation (macOS ARM64 only)

For quick local development on Apple Silicon:

```bash
cd _sources/tpc-h/dbgen
make -f makefile.suite CC=clang MACHINE=LINUX DATABASE=ORACLE WORKLOAD=TPCH

cd _sources/tpc-ds/tools
make CC=clang CFLAGS="-O2 -DMACOS -DMAXINT=INT_MAX -fcommon"
```

The TPC-H build produces `dbgen` and `qgen`. The TPC-DS build requires the patches to be applied first and produces `dsdgen`, `dsqgen` and `distcomp`.

### Full Multi-Platform Compilation

The `compile-all-platforms.sh` script builds binaries for all supported platforms:

```bash
cd _sources/compilation/scripts
./compile-all-platforms.sh
```

**Supported platforms:**
| Platform | Architecture | Method |
|----------|--------------|--------|
| darwin | arm64 | Native (on Apple Silicon) |
| darwin | x86_64 | Cross-compilation |
| linux | x86_64 | Docker |
| linux | arm64 | Docker |
| windows | x86_64 | Docker + MinGW |
| windows | arm64 | Docker + MinGW |

**Output location:** `_binaries/tpc-{h,ds}/{platform}-{arch}/`

### Docker Images

Build Docker images for cross-compilation:

```bash
docker build -f _sources/compilation/docker/Dockerfile.linux-x86_64 \
    -t benchbox/tpc-linux-x86_64 _sources/compilation/docker/

docker build -f _sources/compilation/docker/Dockerfile.linux-arm64 \
    -t benchbox/tpc-linux-arm64 _sources/compilation/docker/
```

The first command builds the Linux x86_64 image and the second builds the Linux ARM64 image.

## Binary Verification

After compilation, verify binaries work correctly:

```bash
_binaries/tpc-h/darwin-arm64/dbgen -s 0.01 -f
ls -la *.tbl

_binaries/tpc-h/darwin-arm64/dbgen -z -s 0.01 -T c | head -5

_binaries/tpc-ds/darwin-arm64/dsdgen -SCALE 1 -TABLE ship_mode -DIR /tmp
cat /tmp/ship_mode.dat | head -5

_binaries/tpc-ds/darwin-arm64/dsdgen -SCALE 1 -TABLE ship_mode -FILTER Y | head -5
```

The four checks run in order: TPC-H small dataset, TPC-H stdout mode (`-z`), TPC-DS small dataset, and TPC-DS stdout mode (`-FILTER Y`). The first should create `customer.tbl`, `orders.tbl` and the other TPC-H tables. The TPC-DS checks use `-SCALE 1` because the bundled binaries require a scale factor of at least 1.

## Important Constraints

### TPC-DS Scale Factor

**Bundled dsdgen binaries require scale factor >= 1.** Fractional scale factors (e.g., 0.01)
cause segmentation faults in the bundled (unpatched) dsdgen binaries. This is a known upstream
limitation rooted in `dist.c:dist_weight` / `scaling.c:LogScale` (see `_sources/tpcds-subscale-probe.md`
for the Gate Zero analysis).

A patched dsdgen that fixes subscale generation ships with BenchBox (see the
`patch-and-redistribute-tpcds-dsdgen-subscale-support` TODO for history). SF<1 runs are
allowed by default for development use.

**Unofficial subscale runs:** SF<1 works out of the box with the bundled patched binaries.
Results are tagged `compliance_class: unofficial_subscale`; official TPC metrics are suppressed.
These results cannot be submitted (`benchbox submit` refuses them) and require
`--label unofficial-research` to publish. See `benchbox/core/tpcds/compliance.py`.

### File Sizes

Generated data sizes at various scale factors:

| Benchmark | SF 0.01 | SF 1 | SF 10 | SF 100 |
|-----------|---------|------|-------|--------|
| TPC-H | ~10 MB | ~1 GB | ~10 GB | ~100 GB |
| TPC-DS | N/A | ~1 GB | ~10 GB | ~100 GB |

### Thread Safety

Neither dbgen nor dsdgen are thread-safe for parallel generation of the same table.
Use the `-C` (chunks) and `-S` (step) flags for parallel generation:

```bash
for i in 1 2 3 4; do
    dbgen -s 10 -C 4 -S $i -T c &
done
wait
cat customer.tbl.* > customer.tbl
```

This generates the customer table in 4 parallel chunks and then concatenates them.

## Git Tracking Strategy

The `_sources/` directory is structured for selective git tracking:

**Tracked in git (included in releases):**
- `_sources/compilation/` - Docker configs and build scripts
- `_sources/tpc-*/PATCHES.md` - Patch documentation
- `_sources/tpc-*/stdout-support.patch` - Patch files
- `_sources/tpc-ds/query_templates/` - SQL templates

**Tracked but can be refreshed from upstream:**
- `_sources/tpc-h/dbgen/` - TPC-H source code
- `_sources/tpc-ds/tools/` - TPC-DS source code
- `_sources/tpc-*/EULA.txt` - License files

**NOT tracked (gitignored):**
- `_sources/tpc-*/tools/*.o` - Object files
- `_sources/tpc-*/tools/tpcds.idx*` - Generated index files
- `_sources/join-order-benchmark/` - Separate git repo

## Updating TPC Source

When a new TPC specification is released:

1. Download new source from TPC website
2. Extract over existing directory (preserving patches)
3. Re-apply patches: `patch -p1 < stdout-support.patch`
4. Verify patches applied: Check `PATCHES.md` verification commands
5. Recompile: `./compile-all-platforms.sh`
6. Run tests: `uv run pytest tests/integration/test_tpc*.py`
7. Update version references in documentation

## Troubleshooting

### "MAXINT undefined" error (TPC-DS)
Ensure patches are applied: `patch -p1 < stdout-support.patch`

### "multiple definition" errors (TPC-DS, GCC 10+)
Ensure `-fcommon` flag is in CFLAGS (handled by patch)

### Segfault with small scale factors (TPC-DS)
Stock dsdgen crashes at SF<1. BenchBox ships patched binaries that handle subscale generation
(see `patch-and-redistribute-tpcds-dsdgen-subscale-support` TODO for history); SF<1 runs are
allowed by default and tagged `compliance_class: unofficial_subscale`.

### "FILTER unknown" error (TPC-DS)
Patch not applied correctly. Re-apply: `patch -p1 < stdout-support.patch`

### Missing `dists.dss` or `tpcds.dst`
These distribution files must be in the same directory as the binary, or specified
via the `-b` flag (TPC-H) or environment (TPC-DS).

## Related Documentation

- `_binaries/README.md` - Pre-compiled binary distribution
- `AGENTS.md` - Project architecture and conventions
- `benchbox/core/tpch/` - TPC-H benchmark implementation
- `benchbox/core/tpcds/` - TPC-DS benchmark implementation
