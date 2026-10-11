# ADR: Drop unread identifier fields from the published corpus

- Status: Accepted (implemented at the public anonymization boundary;
  retained-field handling updated 2026-08-05; residual local-path drop 2026-08-05)
- Date: 2026-08-04; field-set and residual path/host updates 2026-08-05
- Supersedes nothing. Constrains `benchbox/core/results/anonymization.py` and
  any future re-derivation of `results-data/`.

## Context

The public anonymization boundary replaces retained machine-local strings with
`<prefix>_<12 hex>` pseudonyms. Six identifier fields have no reader; the
published contract audit found the following:

**Most of the protected fields have no reader.** An audit across the publication
pipeline, the Explorer application, and the published contract found that six of
the nine pseudonymised field names are consumed by nothing:

| field | pipeline | Explorer app | contract |
|---|---|---|---|
| `machine_id` | – | – | – |
| `working_dir` | – | – | – |
| `driver_runtime_python_executable` | – | – | – |
| `database_path` | – | – | – |
| `data_path` | – | – | – |
| `engine_host` | – | – | – |
| `submission_path` | – | – | yes |
| `database_name` | – | yes | – |
| `endpoint` | yes | yes | yes |

The read model has no machine or host column. Nothing correlates results by
machine today.

## Decision

**Do not publish the six fields that have no consumer.** Remove them at the
publication boundary rather than retaining them. Also omit compact-form
**aliases** of those fields (`workdir`, `workingdirectory`, `workingroot`,
`datadir`, `datadirectory`, `pythonexecutable`). `host` / `hostname` / `server`
remain pseudonyms rather than being dropped: they are broader than `engine_host`.

For the three that do have a consumer, keep publishing pseudonyms.

After the six-field drop and alias rows, several **pure local filesystem**
keys have no Explorer or publication-pipeline consumer. Treat them like the
other unread local identifiers: **drop**.

| compact key class | policy | rationale |
|---|---|---|
| `outputdir`, `outputdirectory`, `outputpath`, `outputlocation` | **drop** | Local run output location; no public reader |
| `resultdir`, `resultpath` | **drop** | Local results location; no public reader |
| `logpath`, `logfile` | **drop** | Local log location; no public reader |
| `filepath`, `path` | **drop** | Exact key name only (compact); not `submission_path` / `*_path` retained consumers |
| `sourceroot` | **drop** | Local source tree root; no public reader |
| `credentialfile` | **drop** | Local credential path; omit entirely (stronger than redact-in-place) |
| `datadir` / `datadirectory` / `workdir` family | **drop** | Already covered by unread-field aliases |
| `sslrootcert` | **keep pseudonym** | libpq spelling; protects certificate path material |
| `s3stagingurl`, `staginglocation`, `stagingurl`, `httppath` | **keep pseudonym** | May embed account/tenant; not pure local FS |
| `host`, `hostname`, `server` | **keep pseudonym** | Broader than `engine_host`; may be remote endpoints |
| `endpoint`, `database_name`, `submission_path` | **keep pseudonym** | Retained consumers (see field-set table above) |

Corpus inventory (tip `results-data/` JSON): none of the newly dropped compact
keys appear as object keys, so **no full re-derive** is required for this
amendment. Fixed-point / privacy gates remain the regression bar.

## Alternatives considered (field-set)

**Retain unread fields.** Rejected because unread local identifiers carry
unnecessary privacy overhead for fields with no analytical consumers.

**Keep pseudonymising but stop publishing the bundles.** Not a real option; the
bundles are the product.

## Consequences

- The field-set drop and the move to a single anonymization pass share one
  re-derivation.
- `find_public_path_leaks` verifies that plaintext paths do not appear in public bytes.
- Retained `published-results` history keeps earlier revisions accessible under standard git history.
- Residual local-path keys: additional pure-FS compact keys are dropped at the
  public boundary; remote-ish path/host keys remain pseudonyms.

## What this does not change

Trust labels and provenance are untouched. Pseudonym identity was never a
provenance signal and must not become one — a submitter can choose a
pseudonym-shaped value and have it pass through by design.
