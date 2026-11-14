<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Run safety: billable platforms, driver installs and --force

## Billable-platform acknowledgement

`benchbox run`, `benchbox run-official` and `benchbox compare` refuse a non-interactive run of a billable platform
(registry category `cloud`, or a managed deployment) with exit 6 unless `--acknowledge-billable-platform` or
`BENCHBOX_ACKNOWLEDGE_BILLABLE_PLATFORM=1` is set. The refusal happens before any adapter, credential read or
connection. A terminal session is not prompted. The acknowledgement and its source are recorded in the result bundle.
The flag is a statement of intent, not an access control.

Paths outside the guard, by decision:

| Path | Why it is not guarded |
|---|---|
| `run --dry-run` | It previews configuration. It reads `~/.benchbox/credentials.yaml`, installs no package and makes no Databricks network call. |
| `setup --validate-only`, `setup --diagnose` | Connecting is their purpose. A Snowflake check can resume a suspended warehouse. |
| Remote `start_benchmark` | Gated only by the operator allow-list; the acknowledgement is not plumbed through it. |
| `shell` with an `md:` MotherDuck path | The user asked for that connection. |

Local MCP `run_benchmark` is guarded.

## Driver auto-install

`--platform-option driver_version=X` selects an exact driver version. The value must be a valid package version
(for example `1.2.3`); anything else is refused before it reaches an installer.

When the installed driver differs from the requested version and no isolated runtime matches, BenchBox can install
the requested version with `uv pip install` or `pip install`. This happens only when `driver_auto_install=true` is
passed as a platform option or `BENCHBOX_DRIVER_AUTO_INSTALL=1` is set. BenchBox prints the exact
`package==version` and the target interpreter to stderr before it installs.

In a non-interactive session (stdin or stdout is not a terminal, or `BENCHBOX_NON_INTERACTIVE` is set) the platform
option alone is refused with exit 6; set `BENCHBOX_DRIVER_AUTO_INSTALL=1` to allow the install. A dry run never
installs a package: a missing or mismatched driver is reported and the preview continues without it.

`benchbox platforms install` prints installation instructions only; it never installs.

## What --force deletes

| Value | Local data | Target database |
|---|---|---|
| `datagen` | Regenerated, overwriting existing files | Deleted before the run (same as `--platform-option force_recreate=true`) |
| `upload` | Untouched | Untouched; data is re-uploaded to remote storage even if a valid copy exists |
| `all` or `datagen,upload` | As `datagen` | As `datagen`, plus the re-upload |

Deleting the target database means: a local database file or directory is deleted; on a remote platform the database
is dropped, except Snowflake (drops this benchmark's tables and tuning metadata, keeps the database) and Databricks
(truncates every table in the schema). Platforms that cannot drop a database refuse the run instead.
