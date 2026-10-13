# ADR: Core Execution-Variant Settings — Execution Engine, Compute, Deployment and Gateway

## Status

Accepted.

## Date

2026-10-09

## Context

BenchBox added a Polars-only platform option named `engine`
(`default | in-memory | streaming`). The bare word already meant other things
elsewhere in the codebase:

- Firebolt uses `engine_name`, `engine_type` and `engine_size` for a billable
  **compute resource**, recorded in results as `platform_compute.engine`.
- Athena records its product name as `platform_compute.engine = "athena"`.
- The anonymizer hashes every key named `engine`, so the Polars option needed
  a special exemption to stay readable.

A review of all registered platforms, the planned platforms and the gateway
design found the same problem more widely:

- **One concept, many names.** About a dozen names exist for a compute
  resource: `warehouse`, `engine_name`, `workgroup`, `cluster_id`,
  `http_path`, `spark_pool_name`, `service_id`, `application_id`,
  `cluster_identifier`, `cluster_name` and others. Several are advertised in
  the platform guides but rejected by `--platform-option`.
- **One setting, four controls.** Polars streaming could be requested through
  the `streaming` option, the `engine` option, the tuning key
  `execution.streaming_mode` and the tuning key `execution.engine_affinity`.
  `engine_affinity: in-memory` was silently ignored.
- **One name, many concepts.** `execution_mode` means both sql/dataframe and
  lazy/eager. `mode`, `warehouse` and `backend` are overloaded in the same way.
- **Identity cannot tell variants apart.** The Explorer and the published
  corpus would merge a Polars streaming run and an in-memory run into one
  platform.
- **Planned platforms need the same concepts.** SiriusDB (GPU or CPU
  fallback), Bodo (JIT), TiDB with TiFlash and Pinot (alternative engines),
  pg_duckdb-style delegation, the Espresso and Greybeam gateways, managed
  clusters on Altinity, Aiven and Exasol, and deployment modes on Databend and
  Ballista.

The existing `deployment_modes` manifest capability and the
`--platform name:deployment` selector already solve this for deployment: the
manifest declares the values, the core validates them, and adapters map them.
This ADR applies that pattern to the other execution-variant settings.

## Decision summary

BenchBox gets one core, platform-neutral model for how and where a benchmark
executes:

- each concept has one unambiguous name;
- platforms declare what they support in the platform manifest, which stays
  the single authority for platform capabilities;
- adapters map canonical values to native settings;
- unsupported values fail closed;
- results record requested, applied and observed values, and result identity
  tells variants apart.

Decisions D1 to D13 below define the model. Each names the chosen option and
the alternatives rejected.

## D1. Taxonomy

**Chosen.** Four core concepts:

| Concept | Meaning | Examples |
|---|---|---|
| Deployment | Where the system runs and who operates it | Firebolt core/cloud, ClickHouse local/server, Velox local/remote |
| Compute resource and size | A named, billable vendor compute object inside a deployment | Snowflake warehouse, Firebolt engine, Athena or Redshift workgroup, Databricks warehouse or cluster, Synapse pool, ClickHouse Cloud service |
| Execution engine | Which execution machinery runs the query, with deployment, compute resource and endpoint held fixed | Polars auto/in-memory/streaming, pg_duckdb DuckDB or Postgres, Dask threaded or distributed, Databricks Photon |
| Gateway | An intermediary that may reroute or rewrite queries | Espresso, Greybeam |

The **observed engine**, meaning the engine that actually ran a query, is the
observed part of the execution engine. It is not a separate setting.

**Separation test.** Hold deployment, compute resource and endpoint fixed. If
the setting still changes the code path that executes the plan, it is an
execution engine. If it changes process topology, it is a deployment. If it
changes the endpoint, it is a gateway.

**Not core settings:**

- runtime version (recorded only). Athena Spark's `runtime_version` is never
  sent to `StartSession`: the session `EngineConfiguration` has no engine
  version, because the Spark-enabled workgroup sets it. BenchBox does not read
  the workgroup, so results record the value with
  `runtime_version_source: requested`;
- table format and storage engine (for example MergeTree);
- optimizer flags such as Spark adaptive query execution (tuning);
- the dtype backend.

Each core concept has at least two current or planned platforms that need it,
so each generalizes.

**Rejected alternatives:**

- *One "engine" concept covering both compute and execution machinery*, as
  Firebolt's vocabulary does. A billable object and an execution code path
  vary independently: a Databricks warehouse can run with or without Photon.
  Merging them would make identity unable to tell those runs apart.
- *A separate "observed engine" setting.* Users cannot set what they observe.
  A second field would let the requested and observed values drift without
  ever being compared.
- *Making runtime version, table format, optimizer flags or dtype backend
  core.* They are already recorded or tuned elsewhere. Adding them would grow
  the core list without a shared meaning across platforms.

## D2. Names

**Chosen settings:**

- `execution_engine`;
- `compute_resource` and `compute_size`;
- `gateway`, with the connection options `gateway_host`, `gateway_port`,
  `gateway_protocol`, `gateway_ca_bundle`, `gateway_ocsp_fail_open` and
  `allow_insecure_gateway`;
- the existing `--platform name:mode` deployment selector.

**Chosen result fields:**

- `config.execution_engine`, the requested value;
- `platform.execution_engine` with `requested`, `applied`, `applied_class`,
  `applied_native`, `resolution`, `observed` and `observed_source`;
- `queries[].execution_engine`, the observed engine per query;
- `platform.compute` with `resource`, `resource_kind` and `size`;
- `platform.gateway` with `name` and `routed`;
- `platform.deployment` with `selected` and `selected_class`;
- `platform.variant`, which holds the `variant_id` string (D8).

Native compute keys such as `warehouse`, `engine_name`, `workgroup` and
`cluster_id` become aliases of `compute_resource` on the platforms that
declare compute.

**Why `execution_engine`.** Polars (`engine=`), pandas (`engine=`), Modin
(`MODIN_ENGINE`) and Databricks (`runtime_engine: STANDARD | PHOTON`) all use
"engine" for this concept, so the qualified name keeps the familiar noun. The
gateway design already records the per-query engine as `execution_engine`, so
one name covers requested and observed values.

**Rejected alternatives:**

- *Bare `engine`.* Firebolt's engine is compute, Athena's is a product name,
  and the anonymizer hashes every `engine` key. One word would carry three
  meanings.
- *`execution_mode`.* It already means sql/dataframe and lazy/eager.
- *`backend` or `runtime`.* Both already name other things (dtype backends,
  runtime versions).
- *`warehouse`, `cluster` or `engine_name` for compute.* Each is one vendor's
  term. `cluster` is wrong for serverless workgroups, and Fabric uses
  `warehouse` for a database.
- *`proxy_*` names for gateway connection options.* They collide with HTTP
  forward-proxy settings.
- *A new deployment option.* The selector already works and is validated
  against the manifest. A second spelling would duplicate it.

## D3. Execution-engine values

**Chosen.**

- **Classes** form a closed core list: `adaptive`, `in-memory`, `streaming`,
  `gpu`, `native-vectorized`, `standard`, `distributed`, `jit`, `delegated`.
  Adding a class takes a core change.
- **`default`** is a reserved sentinel meaning "pass nothing; the platform or
  version decides". Platforms cannot declare it.
- **Manifest capability:**
  `execution_engines: {name: {class, display_name, description, dependencies, selectable}}`.
  `selectable: false` marks an engine that can be observed but not requested.
- **Value names** use the platform's own documented term when it has one,
  otherwise the class name. The same name maps to the same class on every
  platform, and a parity test enforces it.
- **Declarations:**

  | Platform | Values (class) |
  |---|---|
  | polars-df | `auto` (adaptive), `in-memory` (in-memory), `streaming` (streaming) |
  | pg-duckdb | `duckdb` (delegated, the current default), `postgres` (standard) |
  | dask-df | `threaded` (standard), `distributed` (distributed) |
  | databricks (SQL) | `photon` (native-vectorized), `standard` (standard); both `selectable: false` |
  | velox | `velox` (native-vectorized), `standard` (standard); both `selectable: false` |
  | gateway route targets | `snowflake` (standard), `duckdb` (delegated), `databricks` (standard) |

  Every other platform declares none and accepts only `default`. Polars does
  not declare `gpu` until a GPU dependency gate exists.
- **Adapter contract:** one hook on the base platform adapter, for SQL and
  DataFrame adapters alike:
  `resolve_execution_engine(requested) -> ExecutionEngineReceipt`. The base
  implementation accepts only `default` and returns
  `resolution=platform_default` and `observed=not_captured`.
- **Fail closed at three layers:** CLI and MCP validation against the
  manifest; core validation when the adapter is built; and the adapter's probe
  of the installed library, which raises a typed error when the engine is
  unavailable.

**Rejected alternatives:**

- *Free-form strings per platform.* Nothing would make `streaming` mean the
  same thing on two platforms, so cross-platform comparison would be guesswork.
- *One global value list with no per-platform names.* Vendor terms that users
  know, such as `photon` or `duckdb`, would disappear behind class names.
- *Requiring every platform to name its default.* Defaults change between
  versions (the Polars default did), and many platforms have no name for
  theirs. A reserved sentinel avoids both problems.
- *Native mapping in the manifest.* The manifest is declarative data. Mapping
  to native arguments needs code and capability probes, so it stays in the
  adapter.

## D4. Recording requested, applied and observed values

**Chosen.**

- `applied` resolves `default` by introspecting the installed library, never
  from a version string. For Polars it is the default of the `engine`
  parameter of `LazyFrame.collect` (`auto` on current releases), or
  `in-memory` when the parameter does not exist.
- `applied_native` records the literal native arguments and environment. For
  Polars that is the `collect` keyword arguments and `POLARS_ENGINE_AFFINITY`
  from `pl.Config.state()`, because that variable changes what `auto` means.
- `resolution` is one of `explicit`, `tuning_profile`, `legacy_option`,
  `version_default` or `platform_default`.
- Run-level `observed` is a declared name, `mixed` or `not_captured`.
  Query-level `observed` is a declared name or `unknown`.
- The two differ on purpose. `not_captured` means no capture mechanism exists
  for the platform. `unknown` means a mechanism exists but produced no receipt
  for that query.
- `observed_source` is one of `explain`, `vendor_log`, `session_var` or
  `none`.

**Rejected alternatives:**

- *A version table that maps library versions to default engines.* It breaks
  on every new release and on backports, and BenchBox detects capabilities by
  signature or probe, never by version string.
- *A single `unknown` value for every missing observation.* Readers could not
  tell a platform with no instrumentation from a query whose receipt was lost.
- *Recording only the requested value, or only the applied value.* Requested
  alone loses the evidence of what ran. Applied alone depends on the installed
  version, which makes it unsuitable for identity (D8).

## D5. Precedence and conflicts

**Chosen.**

- **Precedence:**
  1. an explicit setting: CLI `--execution-engine`, the MCP
     `execution_engine` parameter, or config `execution_engine`;
  2. a tuning file: `execution.engine_affinity`, or
     `execution.streaming_mode: true` meaning `streaming`;
  3. a legacy option: Polars `streaming=true`;
  4. `default`.
- **Conflicts fail.** If two non-default sources supply different values, the
  run fails before any work starts, with an error naming both sources. If they
  agree, the highest-precedence source sets `resolution`.
- **The Polars `engine` platform option is removed without an alias.** It is in
  no release: the latest release, 0.4.2, does not contain it, so no saved
  configuration can hold it.
- **The Polars `streaming` option, `--polars-streaming` and the MCP Polars
  `streaming` key become deprecated aliases** of `execution_engine=streaming`.
  They shipped in releases, so they keep working through the deprecation
  window (D13). `streaming=false` does nothing, because it never selected an
  engine.
- **Tuning keys** `execution.engine_affinity` and `execution.streaming_mode`
  become the tuning-file spelling of the requested engine, with
  `resolution=tuning_profile`. This makes `engine_affinity: in-memory` take
  effect.
- **Smart defaults stop choosing an engine.** The automatic DataFrame tuning
  defaults no longer pick a Polars engine from host memory, and the shipped
  example tuning files no longer set `engine_affinity` or `streaming_mode`.
  Tuning files that users write may still set them. `--tuning auto` and
  `--tuning tuned` on Polars therefore no longer switch to the streaming
  engine on hosts with less than 16 GB of memory; use
  `--execution-engine streaming` instead.

**Rejected alternatives:**

- *The explicit value wins with a warning.* A warning is easy to miss in a
  long run, and the result would record one engine while the user also asked
  for another. Failing costs nothing because no work has started.
- *A deprecated alias for the Polars `engine` option.* An alias for a name that
  never shipped would add a deprecation window with nothing to protect.
- *Mapping `streaming=false` to `in-memory`.* That would change the behavior of
  existing configurations that set it only to be explicit.
- *Letting smart defaults keep choosing an engine.* The choice depends on host
  memory. Because identity follows the requested value (D8), the same command
  would produce different variants on different hosts, and a run could switch
  engines without the user asking.
- *Letting smart defaults choose but leaving the choice out of identity.* Runs
  that used different engines would then merge under one identity, which is
  the problem this ADR exists to fix.

## D6. Result schema 2.3

**Chosen.** Schema 2.3 is additive.

- Bundles at schema 2.2 or earlier read as `requested=default`, with applied
  and observed unknown.
- `platform_options.streaming=true` maps to `requested=streaming` with
  `resolution=legacy_option`.
- `platform.config.engine_requested`, recorded by the Polars version-matrix
  bundles, maps to that value with `resolution=explicit`, except `default`,
  which maps to `resolution=version_default`.
- Athena's old `platform_compute.engine` field reads as `product`.
- Existing published bytes do not change. Legacy mapping happens when bundles
  are read.

**Rejected alternatives:**

- *A breaking schema 3.0 that renames existing fields.* It would require
  re-deriving published bundles and changing published result IDs, which
  [ADR: `public_result_id` permanence attaches at publication](adr-public-result-id-permanence.md)
  forbids.
- *Rewriting legacy bundles to add the new fields.* Same objection: published
  bytes and IDs would move.

## D7. Anonymization

**Chosen**, consistent with
[ADR: Drop unread identifier fields from the published corpus](adr-published-identifier-field-set.md):

- `compute_resource` is **dropped** at publication. Nothing in the
  publication pipeline or the Explorer reads a warehouse, engine or cluster
  name; only `warehouse_size` is read.
- `resource_kind` and `size` stay readable.
- Existing handling of legacy keys (`warehouse`, `enginename` and others) is
  unchanged, so no published byte moves.
- `gateway_host` is published as a pseudonym, like other endpoints.
- `execution_engine` and `gateway` values stay readable. They come from closed,
  declared lists and identify product behavior, not people or accounts.
- Athena's literal `engine` field is renamed `product`.
- The Polars `engine` exemption is deleted when the option is removed.

**Rejected alternatives:**

- *Retaining `compute_resource`.* The field has no reader. The field-set ADR
  drops unread identifiers.
- *Publishing `compute_resource` in plain text.* Resource names can identify
  an account or organization.
- *Changing legacy keys under the new name.* Published bytes and result IDs
  would change.

## D8. Identity

**Chosen.**

- `variant_id = <platform_id>[~<deployment>][+<engine>][@<gateway>]`, for
  example `polars-df+streaming` or `snowflake@greybeam`.
- The deployment appears only when it is not the manifest default, the engine
  only when the requested value is not `default`, and the gateway only when
  the run was routed.
- The **requested** value defines identity. Every existing bundle therefore
  keeps `variant_id == platform_id`, and no published ID changes.
- The Explorer derives display labels, such as "Polars (streaming)" or
  "Snowflake via Greybeam", from the variant.
- Gateway runs get `ranking_exclusion_reason="gateway_routed"` in native
  cohorts.
- The separators `~`, `+` and `@` cannot appear in a platform ID, so the
  string parses without ambiguity.
- The result writer records `platform.variant` with the manifest key as its
  base (`polars-df+streaming`). Published identity, in the Explorer and the
  corpus depth gate, uses the legacy base instead: the platform ID derived
  from the display name (`polars+streaming`, `apache-doris`). New runs then
  join the rows of earlier bundles of the same platform. Both consumers build
  the suffix with the core `append_variant_suffix` function and read the
  requested engine through the schema 2.3 legacy reader. Neither reads the
  stored `platform.variant` base.

**Rejected alternatives:**

- *Applied value defines identity.* The applied engine depends on the
  installed version (Polars `default` resolves to `auto` on current releases
  and to `in-memory` on older ones). Legacy bundles do not record it, so
  existing identities would change.
- *Observed value defines identity.* It is often `not_captured` or `mixed`.
- *Separate columns with no single identifier.* The corpus and the Explorer
  need one key to group and join runs.
- *Always printing every part, including defaults.* Every existing published
  identity would change.
- *Publishing the manifest-key base.* A default run would form its own row
  (`polars-df` beside `polars`), and moving legacy rows to the manifest key
  would change every published identity whose key differs from its display
  name.

## D9. Gateway

**Chosen.**

- **Manifest capability:**
  `gateways: {name: {routes_to: {engine_name: {class}}, requires_host}}`.
- **Declarations:**
  - Snowflake: `espresso` (routes to `snowflake`), `greybeam` (routes to
    `snowflake` and `duckdb`) and `custom` (requires a host; routes to
    `snowflake`).
  - Databricks: `espresso` (routes to `databricks`) and `custom` (requires a
    host; routes to `databricks`).
- `native` is accepted and means no gateway; the `platform.gateway` block is
  omitted.
- A routed run always records `tpc_compliant=false`. There is no override.
- The core owns `variants_comparable(a, b)` over deployment, requested engine
  and gateway. The compare command, the regression policy and analytics
  baseline selection all call it.
- These gateway rules also hold:
  - connection option names use `gateway_*`, never `proxy_*`;
  - insecure TLS and plain HTTP to a non-loopback host each need an
    environment variable and log a warning;
  - Databricks gateway use is personal-access-token only;
  - native and gateway metrics are never blended.

**Rejected alternatives:**

- *Gateway as Snowflake and Databricks platform options only.* Baseline
  guards, compliance flags, cost suppression and identity are cross-cutting.
  Implementing them per adapter would repeat them.
- *Gateway as a separate platform*, such as `greybeam-snowflake`. The queries
  still run on Snowflake. A separate platform ID would split one warehouse's
  results and hide the native-versus-gateway comparison.
- *Gateway as an execution engine.* A gateway changes the endpoint and may
  send each query to a different engine. The `routes_to` declaration records
  those engines instead.
- *Comparability checks in each surface.* That repeats the duplication that
  [ADR: One Engine, Scoped Surfaces](adr-one-engine-scoped-surfaces.md)
  removed.

## D10. Surfaces

**Chosen**, following
[ADR: One Engine, Scoped Surfaces](adr-one-engine-scoped-surfaces.md):

- One core validator serves both CLI and MCP.
- CLI: `--execution-engine`, an advanced option next to `--mode`, and
  `--gateway`, an advanced option added with the gateway capability.
- MCP: an `execution_engine` parameter on `run_benchmark` and
  `start_benchmark`, with one core contract entry of security class
  `execution`.
- Not MCP parameters, listed as rejected alternatives in the MCP contract:
  `compute_resource`, `compute_size`, `gateway` and the gateway connection
  options.
- The deprecated MCP keys (Polars `streaming`, ClickHouse `deployment_mode`)
  stay allowlisted and resolve through the core during the deprecation window.

**Rejected alternatives:**

- *Exposing compute and gateway settings through MCP.* They let a request name
  a billable cloud resource or an endpoint, CA bundle or insecure TLS mode.
  They are `security-scoped` in the omission ledger.
- *Removing the deprecated MCP keys at once.* MCP clients would break before
  the deprecation window ends.
- *Separate validation in the CLI and MCP layers.* Two validators would drift.

## D11. Reserved words and the grandfather list

**Chosen.** No canonical option key may be `engine`, `mode`, `backend`,
`runtime`, `executor` or `warehouse`. Each of these words already means
different things on different platforms.

Grandfathered keys, generated from the live option registries (the platform
option specs, the CLI platform defaults including spec aliases, and the MCP
allowlist) with every manifest adapter module imported, because some adapter
packages register their option specs on import:

| Platform | Key | Where registered | Removed by |
|---|---|---|---|
| polars | `engine` | option spec and MCP allowlist | The Polars adoption change removes it with no alias. |
| databend | `warehouse` | option spec | The compute unification change makes it an alias of `compute_resource`. |
| fabric_dw | `warehouse` | option spec | The compute unification change makes the existing `database` option canonical, because in Fabric Data Warehouse the warehouse is the target database, not compute. `warehouse` becomes a deprecated alias of `database`. |
| clickhouse | `mode` | alias of `deployment_mode` | The deployment selection change makes `deployment_mode`, and so `mode`, an alias of the deployment selector. |
| influxdb | `mode` | option spec, registered when the InfluxDB adapter package loads | The deployment selection change maps `core` and `cloud` to manifest deployment modes and makes `mode` an alias of the deployment selector. |

Related native keys that are not on the list:

- Snowflake and Snowpark Connect store `warehouse` in credential profiles, not
  as a registered option. The compute unification change resolves it as an
  alias of `compute_resource`.
- Velox `deployment` is not a reserved word. The deployment selection change
  makes it an alias of the deployment selector.
- LakeSail `sail_mode` is a command-line argument only and is not a
  registered option. The deployment selection change maps it to manifest
  deployment modes.

After those changes no canonical key is a reserved word. The aliases that
remain are listed in the deprecated alias table (D12) and are removed by the
alias removal change (D13).

**Rejected alternatives:**

- *No rule.* Each new platform would reintroduce a bare word with its own
  meaning.
- *Banning only `engine`.* `mode` and `warehouse` are already overloaded the
  same way.
- *Renaming every grandfathered key at once with no alias.* Released
  configurations that use them would break.

## D12. Aliases

**Chosen.**

- Canonical option names must never be reserved words.
- An alias may be a reserved word only if it is listed in a core
  `DEPRECATED_OPTION_ALIASES` table with its canonical target and removal
  release.
- The parity test checks canonical names strictly and checks aliases against
  that table.
- Each deprecated alias warns once per run.

**Rejected alternatives:**

- *Treating aliases like canonical keys for the reserved-word rule.* Every
  existing alias would fail at once, which forces the immediate rename that
  D11 rejects.
- *Leaving aliases unchecked.* Reserved words could return as permanent
  aliases.

## D13. Deprecation and migration

**Chosen.**

- Aliases introduced by these changes stay through the next minor release
  after the one that ships the canonical names: canonical names ship in 0.5.0
  and the aliases are removed in 0.6.0, by a separate alias removal change.
- Every change records a CHANGELOG `[Unreleased]` entry. User-visible behavior
  changes also go under "Before you upgrade".
- Saved YAML configurations and credential profiles that hold native keys,
  such as `warehouse`, `deployment_mode`, `streaming`, `engine_name` and
  `workgroup`, resolve through the same alias table, with tests.

**Rejected alternatives:**

- *Removing native keys in the same release that adds canonical names.* Saved
  configurations and MCP clients would break with no warning period.
- *Keeping aliases indefinitely.* Two names would exist for each setting
  forever, and the parity test could never converge on canonical names only.
- *A one-time tool that rewrites user configuration files.* BenchBox does not
  own those files. Resolving aliases when a file is read is cheaper and needs
  no user action.

## Consequences

### Positive

- Each concept has one name across CLI, MCP, configuration and results.
- Engine, deployment and gateway variants get distinct identities, so the
  Explorer and the corpus no longer merge runs that differ.
- Polars has one engine control, and the ignored `in-memory` tuning value
  takes effect.
- New platforms declare engines, compute and gateways in the manifest instead
  of inventing option names.
- No published result ID or published byte changes.

### Negative and costs

- Users of released native keys see deprecation warnings until 0.6.0 and must
  move to the canonical names.
- `--tuning auto` and `--tuning tuned` on Polars no longer pick the streaming
  engine on small hosts.
- Adapters gain a receipt hook, and the parity test adds a check that new
  platforms must pass.

### Enforcement

- A vocabulary parity test checks that manifest classes are a subset of the
  core classes, that one value name maps to one class everywhere, that no
  canonical key is a reserved word, that reserved-word aliases appear in the
  deprecated alias table, that the anonymizer keeps vocabulary values readable
  and drops `compute_resource`, and that documented engine tables match the
  manifest.
- `make platform-manifest-check` validates the new manifest capabilities with
  the existing ones.

## Implementation

This ADR is a decision record and changes no runtime code. The work it
requires lands as separate changes:

- the framework and manifest change: core classes, the manifest capability
  and the adapter hook;
- the result schema change: schema 2.3 and the legacy mapping;
- the Polars adoption change: the CLI and MCP surfaces, the Polars receipt,
  removal of the `engine` option, and the smart-default change;
- engine declarations for pg-duckdb, Dask, Databricks and Velox;
- the deployment selection change: recording the selection and folding
  duplicate deployment keys into aliases;
- the compute unification change: `compute_resource` and `compute_size` with
  native aliases;
- the gateway capability change;
- the Explorer identity change: `variant_id`, labels and ranking exclusion;
- the alias removal change, in 0.6.0.
