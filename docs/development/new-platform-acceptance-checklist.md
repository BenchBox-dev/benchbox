# New Platform Acceptance Checklist

```{tags} contributor, guide, platform
```

Use this checklist before opening a platform-support PR. It is deliberately concrete: platform breadth is only useful when the registry, docs, optional dependencies, compatibility rules, and tests move together.

## First Decision

| Question | Required answer |
| --- | --- |
| Is this SQL, DataFrame, or dual-mode? | Pick one primary path; dual-mode still needs separate SQL and DataFrame checks. |
| What is the `support_status`? | Use exactly one of `stable`, `beta`, `experimental`, `repo_only`, `deprecated`, `document_only`. |
| Is it runnable from a wheel? | If no, do not add it as a normal runtime platform. Use `repo_only` or `document_only`. |
| What dependency group enables it? | Add or reuse an optional extra; do not make base import require a platform SDK. |
| What is the blast radius? | List adapter files, registry metadata, docs, tests, SQL compatibility, and UAT/live scope. |

(new-platform-checklist-sql)=

## SQL Platform Checklist

- Add the adapter under `benchbox/platforms/` or an existing platform family package.
- Keep platform SDK imports lazy or guarded so `import benchbox` remains cheap.
- Add the platform's canonical entry to `benchbox/core/platform_manifest.py`, including one explicit `support_status`.
  The registry's `_OPTIONAL_ADAPTERS` and `_build_platform_metadata()` paths are live pass-throughs and should not
  be treated as independent registration authorities.
- Fill `capabilities`: `supports_sql`, `supports_dataframe`, `default_mode`, `platform_family`, `inherits_from`, `cost_class`, and deployment modes when relevant.
- Declare every deployment mode in the manifest with its class (`local`, `self-hosted`, or `managed`) and exactly one platform default; the `--platform name:mode` selector is the only canonical spelling.
- Name no canonical option `engine`, `mode`, `backend`, `runtime`, `executor`, or `warehouse`; list any reserved-word alias in the core deprecated-alias table with its removal release.
- Record the factory-resolved deployment in result metadata as `platform.deployment.selected` with `selected_class`, and cover each mode with an adapter test.
- Declare compute when the platform bills a named compute object: add a manifest `capabilities.compute` block with
  `resource_kind` (`warehouse`, `engine`, `workgroup`, `cluster`, `pool`, `application` or `service`),
  `native_resource_keys` and `native_size_keys`. The core registers `compute_resource` and `compute_size`
  with the native keys as deprecated aliases automatically; never register a canonical key named `engine`,
  `mode`, `backend`, `runtime`, `executor` or `warehouse`. The adapter reads the canonical names first with
  native fallback, and results carry the canonical kind and size alongside legacy fields.
- Declare gateways when the platform supports query routing through an intermediary: add a manifest
  `capabilities.gateways` block with `{name: {routes_to: {engine_name: {class}}, requires_host: bool}}`.
  The core registers the `--gateway` CLI flag and typed connection options (`gateway_host`, `gateway_port`
  as an integer port, `gateway_protocol`, `gateway_ca_bundle`, and the booleans `gateway_ocsp_fail_open` and
  `allow_insecure_gateway`) with the other platform options. Every non-native gateway must require a host;
  `native` is accepted and recorded as no gateway. Until the adapter routes queries through the gateway, any
  other `--gateway` value fails before work starts with "gateway routing is not implemented for <platform>
  yet". A routed result records `platform.gateway` with `name` and `routed: true`, sets the top-level
  `tpc_compliant: false`, restricts per-query `execution_engine` to `routes_to` targets plus `unknown`, and
  observes `mixed` when queries differ. `benchbox compare` and MCP `compare_results` refuse runs that differ
  in deployment, requested engine or gateway (the CLI accepts `--allow-heterogeneous-comparison`), and
  regression baselines use only runs of the same variant.
- Declare execution engines when the platform offers more than one way to execute a query: add a manifest
  `capabilities.execution_engines` block with `{name: {class, display_name, description, dependencies, selectable}}`.
  The class comes from the closed core list (`adaptive`, `in-memory`, `streaming`, `gpu`, `native-vectorized`,
  `standard`, `distributed`, `jit`, `delegated`). Name each value with the platform's own documented term when one
  exists, otherwise the class name; the same name must map to the same class on every platform (the vocabulary
  parity test enforces this). `default` is a reserved sentinel meaning "pass nothing" and is never declared.
  Platforms without engines omit the block and accept only `default` through the inherited
  `resolve_execution_engine` hook; override the hook only to map native settings into a receipt, and fail closed
  on unsupported values. Never name a canonical option `engine`, `mode`, `backend`, `runtime`, `executor` or
  `warehouse`; a reserved-word spelling survives only as a deprecated alias registered in the core
  `DEPRECATED_OPTION_ALIASES` table with its removal release.
- Add `driver_package` and `installation_command`; dependency extras belong in `pyproject.toml`.
- Add DDL/query compatibility rules or an explicit exemption in the SQL compatibility inventory when the adapter rewrites SQL or DDL.
- Add unit tests for adapter construction, SQL rendering/loading behavior, registry metadata, optional import failure behavior, and any deployment-mode routing.
- Add a platform guide under `docs/platforms/` and update the comparison matrix only through registry-derived counts.
- Define smoke/UAT scope: local Docker, cloud live, or explicit no-live justification.

## DataFrame Platform Checklist

- Decide whether the platform belongs to the expression family, pandas family, Spark family, or a new family.
- Add adapter/context code under `benchbox/platforms/dataframe/` unless an existing family file already owns the behavior.
- Add the platform's canonical entry to `benchbox/core/platform_manifest.py`, including one explicit `support_status`.
  DataFrame-only platforms must have `supports_sql=False`. The registry's `_OPTIONAL_ADAPTERS` and
  `_build_platform_metadata()` paths are live pass-throughs and should not be treated as independent
  registration authorities.
- Add the `-df` CLI path or confirm the existing `adapter_factory` mapping routes to the new adapter.
- Declare execution engines when the platform offers more than one way to execute a query, following the same
  manifest `capabilities.execution_engines` block, closed class list, value-naming rule and reserved-word ban as
  SQL platforms above. The worked example is `polars-df`, which declares `auto` (adaptive), `in-memory` and
  `streaming` under the platform's own documented names. DataFrame adapters inherit `resolve_execution_engine`
  from the adapter base and map the resolved engine into their collect or compute call; unsupported values fail
  closed.
- Add native DataFrame query implementations or document unsupported benchmarks with benchmark-gate rules.
- Add lazy availability checks and dependency extras; GPU or native-stack dependencies must not load during base import.
- Add unit tests for family behavior, adapter lifecycle, query execution primitives, and registry capability alignment.
- Add docs under `docs/platforms/` using the `-df` CLI name and compatibility caveats.
- Validate against DuckDB SQL at SF=0.01 where the benchmark has parity expectations.

## Documentation and Drift Checks

- README and platform matrix exact counts must match `PlatformRegistry.get_platform_count_summary()`.
- The public docs should describe support status separately from local dependency availability.
- New docs should link to setup, credentials, deployment modes, and benchmark limitations.
- If a platform is experimental or deprecated, the docs must say so on the first screen.

## Expected Extension Cost

The minimum durable file count for a new platform is more than one adapter file.

Target for a straightforward SQL platform is **7-10 files**:

- adapter or family module,
- registry metadata and optional adapter entry,
- optional dependency extra,
- SQL compatibility rule or explicit exemption,
- unit tests,
- platform docs,
- count/drift or acceptance checklist update when a claim changes.

Target for a straightforward DataFrame platform is **8-12 files** because native query implementations and family tests usually add another surface.

## Scaffold Helper

Print the expected file plan with manifest and adapter stubs before implementation:

```bash
uv run -- python scripts/new_platform_scaffold.py --name myengine --kind sql
uv run -- python scripts/new_platform_scaffold.py --name myengine --kind dataframe
```

A maintainer scaffold script prints the expected file plan for a new SQL or
DataFrame platform, a manifest entry for `_PLATFORM_MANIFEST_JSON`, and an
adapter stub. The manifest entry declares no execution engines and passes the
same manifest validator the package uses at import; the script exits with an
error if the key or an alias collides with an existing entry. The
adapter stub includes a `resolve_execution_engine` hook that returns the
platform-default receipt. `--output-dir DIR` also writes the entry as
`manifest-entry.json` and the stub under the file name the plan uses. The
helper is an architecture health aid, not a framework that owns platform
implementation.
