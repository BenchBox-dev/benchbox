# ADR: Publish the result bundle JSON Schema, emitted from the validator

## Status

Accepted 2026-11-16. Governs `quality/result-bundle.schema.json`, its generator
`benchbox/core/results/bundle_json_schema.py`, and its delivery as the
`result-bundle.schema.json` member of the schema 6 site-inputs bundle.

## Context

Result bundles (result schema 2.4 when this was decided; 2.5 adds `execution.config_capture` and the vendor fields in `provenance`; 2.6 adds `execution.timeout`; 2.7 adds the top-level `validation.stream_checks` object and the row-evidence fields `check`, `bound`, `seed` and `streams_index`) were defined only in Python:
`SchemaV2Validator` in `benchbox/core/results/schema.py`, the version policy in
`schema_policy.py`, key orders in `schema_specs.yaml`, and the timing and row-count rules
in `timing_fields.py` and `query_execution.py`. A pipeline or agent that parses a bundle had
nothing machine-readable to validate against.

## Decision

1. The schema is emitted from the validator. It is not hand-written, and the validator does not
   read a schema. `SchemaV2Validator.REQUIRED_KEYS`, `OPTIONAL_KEYS` and the per-block `*_REQUIRED`
   tuples, the version lists and `supports_*` predicates in `schema_policy.py`, `PLAN_TIERS` and
   `VENDOR_PROVENANCE_FIELDS` in `provenance.py`, the timing and
   platform-timing constants in `timing_fields.py`, the row-count constants in
   `query_execution.py`, and `HOST_LOAD_SAMPLE_FIELD_TYPES` in `environment.py` are the single
   definition. The generator imports them; there is no second list. Where the generator restates
   a closed set of fields it asserts equality with the source constant, so a field added at the
   source without a schema decision fails generation.
2. `environment.host_load` is derived from the constants `build_host_load_block` uses.
   `HOST_LOAD_SAMPLE_FIELDS` is now `tuple(HOST_LOAD_SAMPLE_FIELD_TYPES)`, so the required field
   set and the per-field types live next to the builder.
3. `uv run -- python -m benchbox.core.results.bundle_json_schema` regenerates the file; the same
   command with `--check` (part of `make ci-lint`
   and a `guard-result-schema` step in the lint guard job of `ci.yml`) fails when the committed
   file differs from generation. Generation is deterministic.
4. The file name is `result-bundle.schema.json`, deliberately different from
   `results_schema.json`, which describes the Explorer's DuckDB read model and stays under
   `removed_legacy` in the Explorer build contract. This decision does not reverse that removal.
5. The `$id` is `https://benchbox.dev/schemas/result-bundle-2.schema.json`. The major version in
   the URL is the compatibility unit. The document carries the current result schema version in a
   top-level `result_schema_version` key (currently 2.7); `properties.result_schema_version.enum`
   lists every accepted 2.x version, so one schema validates every bundle the runtime accepts.
6. Delivery: site-inputs bundle schema 6 is schema 5 plus the top-level member
   `result-bundle.schema.json`; the site publishes it at `/schemas/result-bundle-2.schema.json`.
   Verification checks the member is byte-equal to a fresh generation.

## Fidelity limits

The schema encodes required keys at the top level and in `run`, `benchmark`, `platform` and
`summary`; the closed top-level key set; version gating (row-count validation from 2.2, round-trip
timing fields in `run`, `queries` and `environment.client_link` from 2.4, required `timing_method`,
`net_estimate_status` and per-query `request_count`/`result_bytes` rules from 2.4; `execution.config_capture` and the `provenance` plan tier, preview features, vendor permission, vendor notice and self-run fields from 2.5; `execution.timeout` from 2.6; the top-level `validation` object and the `row_count_validation` fields `check`, `bound`, `seed` and `streams_index` from 2.7); equality of
`version` and `result_schema_version` when both are present; the `row_count_validation` field set,
statuses and message length; `platform_timing` including the `valid` (requires `platform_ms`) and
`suspect`/`unavailable` (require a `reason`) rules; `run.platform_timing` required when any query
carries one; `net_estimate_status` `eligible` requiring `timing_method` 2 or later and a networked
`platform.deployment.endpoint_class`; and `environment.host_load` and `environment.client_link`.
The cross-field rules are emitted as `if`/`then` gates built from the same constants the validator
uses. The validator also enforces rules JSON Schema cannot express (instance cross-field equality,
type-coercion boundaries, and platform-keyed vendor provenance rules). The authoritative list of
those validator-only rules is `VALIDATOR_ONLY_RULES` in
`benchbox/core/results/bundle_json_schema.py`, emitted verbatim into the top-level `description`
of `quality/result-bundle.schema.json`.

## Compatibility promise

This text is reproduced verbatim in the schema `description` and is the source for the site's
user-facing page.

Compatibility promise for major version 2. Within major version 2 the schema changes only in ways
that keep every bundle that was valid under an earlier 2.x version valid: new optional keys, new
optional blocks, and new enum values on fields that consumers are told to treat as open. Such a
change raises the minor version (2.6 to 2.7) and is published at this same URL. A change that
removes or renames a required key, changes the type of an existing key, narrows an accepted value
set, or changes the meaning of an existing key requires major version 3, which is published at a new
URL with a 3 in its name; this URL keeps serving major version 2. Consumers of major version 2 must
ignore keys they do not recognise and must not rely on key order.

Compatible within major 2: adding optional keys or blocks; adding enum values on open fields;
raising the minor version. Requires major 3: removing or renaming a required key, changing the
type of an existing key, narrowing an accepted value set, changing the meaning of an existing key.

## Consequences

- Changing the validator without regenerating the schema fails the `--check` run.
- A deliberate schema change must bump `CURRENT_SCHEMA_VERSION` according to the promise above.
- Every bundle under `results-data/bundles` must validate; a unit test enforces it.

## Result schema 2.7: stream checks (measured-stream integrity)

The measured TPC-H and TPC-DS streams use parameters the specifications publish no answers for, so
every measured row is checked by one decision function against bounds that hold for any parameters
(`docs/agent/maintainer-bundles.md`, "Stream checks"). Schema 2.7 records what was checked.

`queries[].row_count_validation` gains four optional fields. `check` is one of `exact`, `invariant`,
`range`, `never_empty` or `structural`, and requires `bound`, a human-readable string of at most 120
characters (for example `exactly 100`, `at least 1 row`, `514 to 1469`, `at most 100`). `seed` is the
effective generator seed that produced the row's text (the dsqgen RNGSEED or the qgen seed; null is
the generator default) and `streams_index` is the dsqgen STREAMS file index (null on the
per-template and qgen paths). A `PASSED` row with check `exact` or `invariant` carries equal expected
and actual counts; a `PASSED` row with check `range` or `never_empty` carries a null expected count
and stays `PASSED`, where a stored schema 2.6 file records a range pass as `SKIPPED` with the
documented reason `within range`. A `structural` check alone never passes a row: it records a
documented `parameter sensitive:` skip, or `UNVALIDATED` where no reference exists.

A new top-level object `validation` (the status string stays in `summary.validation`) holds
`stream_checks`: `references`, one entry per reference file applied (`benchmark`, `scale_factor`,
`file`, `sha256` of the file, `data_stamp`, `query_generation_hash` and `sample_draws`), and `checks`,
the count of rows per check kind. The object is written only when a row carries a check.

References bind to the data stamp and to a query-generation identity (the dsqgen or qgen binary
digests shipped in `benchbox/_binaries/SHA256MANIFEST.json`, the template set and the below-SF-1
template overrides in `benchbox/core/tpcds/c_tools.py`). A mismatch records the documented skip
`reference data differs:` instead of a check. Stored 2.0 to 2.6 files are valid unchanged; the
fields are rejected below 2.7. Version 2.7 belongs to this change; `tpc-full-spec-result-schema` names no schema
version and takes the next minor (2.8) if it needs one.
