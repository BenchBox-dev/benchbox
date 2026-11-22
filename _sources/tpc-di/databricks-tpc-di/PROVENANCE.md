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

## Paths BenchBox never takes

- `src/tools/**`: the project's generator code, its copy of the TPC-DI tools and tools zip (`DIGen.jar`, `DIGen_Build.zip`, `pdgf.jar`, plugins, libraries, dictionaries, configuration), its static files and its static audit snapshots.
- DIGen output and generated reference files.
- Its copy or rewrite of the appendix SQL (`src/incremental_batches/audit_validation/**`), including `Appendix_v1.1.0/`. The audit, validation and visibility SQL comes from the TPC and is never edited in the repository.
- `src/single_batch/**`, `src/databricks_app/**`, `tests/**`, `docs/**`.

## Guard

`FORBIDDEN_HASHES.json` records the SHA-256 of every non-empty file under the project's `src/tools/**` at the commit above, grouped as generator and tools, audit snapshots and DIGen output, and of the four files of `Appendix_v1.1.0/`. `tests/unit/core/tpcdi/test_provenance_guard.py` fails if a file from the first three groups is in the tree outside `benchbox/_binaries/tpc-di/tools/`. The `Appendix_v1.1.0/` hashes are for comparison with the shipped appendix SQL and are not forbidden.
