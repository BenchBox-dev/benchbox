# ADR: Publish the result bundle JSON Schema, emitted from the validator

## Status

Accepted 2026-11-16. Governs `quality/result-bundle.schema.json`, its generator
`benchbox/core/results/bundle_json_schema.py`, and its delivery as the
`result-bundle.schema.json` member of the schema 6 site-inputs bundle.

## Context

Result bundles (result schema 2.4 when this was decided; 2.5 adds `execution.config_capture` and the vendor fields in `provenance`) were defined only in Python:
`SchemaV2Validator` in `benchbox/core/results/schema.py`, the version policy in
`schema_policy.py`, key orders in `schema_specs.yaml`, and the timing and row-count rules
in `timing_fields.py` and `query_execution.py`. A pipeline or agent that parses a bundle had
nothing machine-readable to validate against.

## Decision

1. The schema is emitted from the validator. It is not hand-written, and the validator does not
   read a schema. `SchemaV2Validator.REQUIRED_KEYS`, `OPTIONAL_KEYS` and the per-block `*_REQUIRED`
   tuples, the version lists and `supports_*` predicates in `schema_policy.py`, the timing and
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
   top-level `result_schema_version` key (currently 2.5); `properties.result_schema_version.enum`
   lists every accepted 2.x version, so one schema validates every bundle the runtime accepts.
6. Delivery: site-inputs bundle schema 6 is schema 5 plus the top-level member
   `result-bundle.schema.json`; the site publishes it at `/schemas/result-bundle-2.schema.json`.
   Verification checks the member is byte-equal to a fresh generation.

## Fidelity limits

The schema encodes required keys at the top level and in `run`, `benchmark`, `platform` and
`summary`; the closed top-level key set; version gating (row-count validation from 2.2, round-trip
timing fields in `run`, `queries` and `environment.client_link` from 2.4, required `timing_method`,
`net_estimate_status` and per-query `request_count`/`result_bytes` rules from 2.4; `execution.config_capture` and the `provenance` plan tier, preview features, vendor permission, vendor notice and self-run fields from 2.5); equality of
`version` and `result_schema_version` when both are present; the `row_count_validation` field set,
statuses and message length; `platform_timing` including the `valid` (requires `platform_ms`) and
`suspect`/`unavailable` (require a `reason`) rules; `run.platform_timing` required when any query
carries one; `net_estimate_status` `eligible` requiring `timing_method` 2 or later and a networked
`platform.deployment.endpoint_class`; and `environment.host_load` and `environment.client_link`.
The cross-field rules are emitted as `if`/`then` gates built from the same constants the validator
uses. The validator also enforces rules JSON Schema cannot express, because they compare two values
in the instance or normalise a value before comparing. That residue is stated in the schema
`description` and reproduced here:

Checks the BenchBox validator (SchemaV2Validator) enforces that JSON Schema cannot express and that
this schema therefore omits: row_count_validation.actual must equal the query's rows, and a PASSED
row_count_validation with an integer expected must have equal expected and actual (both compare two
values in the instance); the validator matches row_count_validation.status case-insensitively while
this schema lists only the normalised upper-case form; JSON Schema cannot tell 2 from 2.0, while the
validator rejects a float for run.timing_method (including the net-estimate check), request_count,
result_bytes and platform_timing.requests[].attempt; row_count_validation.expected and actual are
the reverse, because the validator also coerces integral floats and numeric strings while this
schema requires JSON integers.

## Compatibility promise

This text is reproduced verbatim in the schema `description` and is the source for the site's
user-facing page.

Compatibility promise for major version 2. Within major version 2 the schema changes only in ways
that keep every bundle that was valid under an earlier 2.x version valid: new optional keys, new
optional blocks, and new enum values on fields that consumers are told to treat as open. Such a
change raises the minor version (2.5 to 2.6) and is published at this same URL. A change that
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
