# JoinOrder Canonical Data License Notes

## Dataset Provenance

BenchBox's canonical `joinorder` dataset is derived from the Harvard Dataverse
`imdb_pg11` archive, DOI `10.7910/DVN/2QYZBT`. The source represents the May
2013 IMDb plain-text list-files snapshot parsed with IMDbPY into the 21-table
relational schema used by the Join Order Benchmark paper, then restored into
PostgreSQL and converted by BenchBox to Parquet for reproducible benchmark use.

The Harvard Dataverse record declares the deposit as `CC0 1.0` and lists
`imdb_pg11` as unrestricted. IMDb's current dataset terms, however, describe the
underlying IMDb data as personal / non-commercial and restrict republication or
repurposing. The JOB paper also describes the IMDb source data as non-commercial.

## IMDb Attribution

Information courtesy of IMDb (https://www.imdb.com). Used with permission.

## Current Redistribution Posture

BenchBox does not treat the current BenchBox-hosted Parquet release asset as
clearly permitted for broad commercial redistribution. The project-owner
decision accepts the remaining redistribution risk for this 2013 academic
benchmark dataset and keeps the verified BenchBox-hosted Parquet archive as the
default data path because direct Dataverse restore/conversion would make first
use substantially heavier.

Use this dataset only under the IMDb and Dataverse terms. Do not treat
BenchBox's converted archive as BenchBox-cleared for broad commercial
redistribution or republication.

## Strict Redistribution Environments

The selected hardening path is documentation of a BYO-data workflow while the
accepted-risk hosted archive remains the default fast path. Users who cannot
consume BenchBox's re-hosted archive can provide the canonical 21 Parquet files
themselves under the normal datagen directory:

```text
benchmark_runs/datagen/joinorder_sf1/
```

or under `datagen/joinorder_sf1/` beneath `BENCHBOX_OUTPUT_DIR`. BenchBox
verifies the pre-populated Parquet files against `data_manifest.toml` before
running and does not download the hosted archive when every table file is
already present with the expected sha256.

Maintainers who need to rebuild those Parquet files from Dataverse should use
the `build_joinorder_data.py` maintainer script in the BenchBox source repository. That script downloads the
`imdb_pg11` file from DOI `10.7910/DVN/2QYZBT`, verifies the Dataverse MD5 and
records the pg_dump SHA256, restores the source into PostgreSQL, converts the 21
tables to Parquet, and runs the logical-content and manifest checks. This is an
explicit maintainer path, not the default first-run user experience.

## Scope

This data is provided for research, database systems evaluation, and query
optimizer benchmarking. It is not intended for consumer IMDb replacement
services, republication as a general-purpose movie database, or commercial
redistribution outside benchmark reproducibility.

## Redistribution Disclaimer

BenchBox redistributes a derivative form of this dataset as a convenience for
benchmark reproducibility. Optional follow-up work may seek explicit permission
or add an advanced direct-Dataverse/BYO data path, but the default user
experience remains the verified BenchBox-hosted Parquet archive. BenchBox makes
no claim of ownership over the underlying IMDb data and will honor takedown
requests.

## Takedown Contact

Send takedown or licensing concerns to `joeharris76@gmail.com`.
