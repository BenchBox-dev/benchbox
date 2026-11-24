# shannon-barrow/databricks-tpc-di

## Source

- Repository: https://github.com/shannon-barrow/databricks-tpc-di
- Commit: 8e0f944 (full SHA 8e0f944612b2919ba0a3f47e70969faa8cc38c68, committed 2026-11-16)
- Author: Shannon Barrow
- Authorship: of the 877 commits reachable from that commit, 730 carry a databricks.com author address (696 `shannon.barrow+data@databricks.com`, 34 `shannon.barrow@databricks.com`). The others are Shannon Barrow's GitHub identities (143 commits) and two contributors (`dbsys21`, 3 commits; one other address, 1 commit).
- Licence file: the project has none.

## Licence treatment

The owner's instruction of 2026-11-21, verbatim:

> "You will consider the license to be MIT for the purposes of this effort and we will include the usual TPC caveats and limitations"

`LICENSE` beside this file is the standard MIT text with the owner's copyright line, "Copyright (c) 2023-2026 Shannon Barrow and the databricks-tpc-di contributors". It is the notice BenchBox keeps for text derived from the project. It is not a licence granted by the project. The wider decision record is `_project/decisions/tpcdi-databricks-base-provenance-2026-11-21.md`.

Site and documentation sentence: "BenchBox's TPC-DI ETL is based on Shannon Barrow's databricks-tpc-di (MIT)."

## Paths BenchBox takes

- `src/incremental_batches/**`, except its audit and validation rewrites in `src/incremental_batches/audit_validation/**`.
- Every derived SQL file is committed only under `benchbox/core/tpcdi/etl/`, listed in `benchbox/core/tpcdi/etl/PROVENANCE.json`, with all comments removed.

## Names only

- `src/incremental_batches/dw_init.sql` is read for staging table and column names only. `benchbox/core/tpcdi/schema_specs.yaml` takes the names of its stage tables (`FinWire`, `WatchIncremental`, `DailyMarketIncremental`, `CashTransactionIncremental`, `HoldingIncremental`, `TradeIncremental`, `AccountIncremental`, `CustomerIncremental`, `ProspectIncremental`) and of `BatchDate`, with their column names. Column types are the specification's exact types, not the project's; no statement, comment, constraint, partitioning or table property is taken. The file is not committed and is not listed in `benchbox/core/tpcdi/etl/PROVENANCE.json`.

## Paths BenchBox never takes

- `src/tools/**`: the project's generator code, its copy of the TPC-DI tools and tools zip (`DIGen.jar`, `DIGen_Build.zip`, `pdgf.jar`, plugins, libraries, dictionaries, configuration), its static files and its static audit snapshots.
- DIGen output and generated reference files.
- Its copy or rewrite of the appendix SQL (`src/incremental_batches/audit_validation/**`), including `Appendix_v1.1.0/`. The audit, validation and visibility SQL comes from the TPC and is never edited in the repository.
- `src/single_batch/**`, `src/databricks_app/**`, and the project's own `tests` and `docs` directories.

## Guard

`FORBIDDEN_HASHES.json` records the SHA-256 of every non-empty file under the project's `src/tools/**` at the commit above, grouped as generator and tools, audit snapshots and DIGen output, and of the four files of `Appendix_v1.1.0/`. `tests/unit/core/tpcdi/test_provenance_guard.py` fails if a file from the first three groups is in the tree outside `benchbox/_binaries/tpc-di/tools/`. The `Appendix_v1.1.0/` hashes are for comparison with the shipped appendix SQL and are not forbidden.

## Appendix SQL comparison

The project's copy of each appendix file next to the shipped file (`benchbox/_binaries/tpc-di/`, an unofficial shared copy; see `_sources/README.md`). BenchBox takes nothing from the project's copy; the comparison only records whether they are identical.

| File | Project's copy (SHA-256) | Shipped (SHA-256) | Identical |
|------|--------------------------|-------------------|-----------|
| `tpcdi_audit.sql` | `32eb0f55a08b8cf5a906d4aff563539d42c873c72d6dc662798218b2bc3107fc` | `32eb0f55a08b8cf5a906d4aff563539d42c873c72d6dc662798218b2bc3107fc` | yes |
| `tpcdi_validation.sql` | `9ee9ef8144c7f53bb6fb0d4ac489dda2379e7aa1b8e24d7a12df1539b7f86c02` | `9ee9ef8144c7f53bb6fb0d4ac489dda2379e7aa1b8e24d7a12df1539b7f86c02` | yes |
| `tpcdi_visibility_1.sql` | `a95eb53868fa42cf234ecf6899fa3ce3a87839a66f21945f87a9ff0d94e58526` | `209454549598d8147a2a0eb9ced8bd86eb3548f32fc90c1765975b0076e8bde7` | no (5164 and 5028 bytes), whitespace only |
| `tpcdi_visibility_2.sql` | `a22645d5c9d2095d8d77549db3e7ecbc8d5d5417e8f0973fbd92150347ea894a` | `a22645d5c9d2095d8d77549db3e7ecbc8d5d5417e8f0973fbd92150347ea894a` | yes |

The `tpcdi_visibility_1.sql` difference, read from the project's copy and the shipped file (nothing is taken from the project's copy): lines 32 to 93 of the shipped file, the row-count branches of the derived table (`DimAccount` through `FactWatches`, including the joined counts), are reformatted in the project's copy. The `union` keyword moves from the start of each branch to the end of the previous line or to its own line, column padding is added, and line breaks and tabs inside the branches change. Every other line, including the header comment, the `insert into DImessages` head and the closing `y on 1=1`, is identical. Outside block comments, the lowercased token counts of the two files are equal, so the statements are the same. The shipped copy is the version found in the abroniewski repository; the project's copy is a reformatted variant of it.
