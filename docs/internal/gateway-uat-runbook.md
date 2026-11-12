<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Gateway live UAT runbook

Write-only procedure for a live check of gateway-routed Snowflake and
Databricks runs. Nothing in the repository executes it and no CI job runs it:
it needs vendor gateway credentials and spends real warehouse credits, so only
the owner starts it. The mock-gateway tests in
`tests/unit/platforms/test_snowflake_gateway.py` and
`tests/unit/platforms/test_databricks_gateway.py` are the automated coverage.

## Gates

| Gate | What it decides | Status |
| --- | --- | --- |
| G1 | Mock-gateway connection behavior: handshake routing, query-tag preservation and cache receipt parsing against a loopback-only fake gateway | Evaluated by the unit suite on every run |
| G2 | The advertised-options, platform-manifest and compatibility-docs checks pass, no platform key is added, and the only manifest change is the `gateways` declarations | Evaluated by `make platform-manifest-check`, `make compat-inventory-check` and `tests/unit/platforms/test_advertised_platform_options.py` |
| G3 | Live UAT of this runbook against a real gateway | Not executed. Recorded as pending the owner's credentials and credit budget |

G3 stays pending until the owner runs this procedure and records the evidence
below in a dated note. A passing unit suite never closes G3.

## Preconditions

- A Snowflake account with a dedicated, empty UAT database and a warehouse
  capped at X-Small with a resource monitor that suspends at 5 credits.
- Key-pair or password login for a UAT user whose role can only use that
  warehouse and database. A Databricks workspace needs a personal access token
  for a SQL warehouse capped at 2X-Small.
- A gateway endpoint the owner controls or has a vendor trial for. Record its
  host in the owner's notes only; do not put it in a commit, pull request or
  result bundle that is published.
- A clean worktree from `develop` and a green `make pr-preflight`.

## Caps

| Cap | Value |
| --- | --- |
| Scale factor | 0.01 (`--scale 0.01`), the repository minimum |
| Benchmark | TPC-H, power phase only (`--phases power`) |
| Queries | At most 22, one iteration, no throughput phase |
| Warehouse | X-Small (Snowflake), 2X-Small (Databricks) |
| Credit ceiling | 5 Snowflake credits and 2 Databricks DBUs, enforced by the resource monitor or warehouse limit, not by BenchBox |
| Wall clock | 60 minutes; abort when exceeded |
| Greybeam | Query phases only. Load natively first and wait for replica quiescence as in the Snowflake platform page; never pass `--allow-greybeam-inline-load` |

## Procedure

1. Load the TPC-H 0.01 data natively, with no gateway option, and run the
   power phase once as the native baseline.
2. Wait for the quiescence checks in the Snowflake gateway page (row-count
   parity on three consecutive checks).
3. Run the same power phase with `--gateway <name>` and the gateway options
   for the endpoint. Add `--platform-option gateway_ca_bundle=...` only for a
   private-CA endpoint, with `REQUESTS_CA_BUNDLE` set to the same file before
   BenchBox starts.
4. Run the same command a second time to observe warm-run behavior.
5. For Databricks, repeat steps 1 and 3 with `--gateway espresso` or
   `--gateway custom` and an explicit `server_hostname` and `access_token`.
6. Run `benchbox compare` between the native and the gateway bundle and
   confirm it refuses the comparison as an incomparable variant.

## Evidence checklist

Record a dated note with every item below. Attach nothing that contains a
host name, token, account identifier or user name.

- [ ] Native baseline and gateway bundles exist for each platform exercised.
- [ ] `platform.gateway` is `{name, routed: true}` in each gateway bundle and
      absent in each native bundle.
- [ ] `tpc_compliant` is `false` in each gateway bundle.
- [ ] Every `queries[].execution_engine` in a gateway bundle is `unknown`, or
      a name the manifest declares for that gateway.
- [ ] `platform.compute.cache_control` carries `gateway_cache: unverified` and
      `warm_run_metrics: cache_unverified`; for Snowflake it also shows
      `snowflake_result_cache: disabled`.
- [ ] `normalized_cost.cost_status` is `not_comparable_gateway` with a null
      `normalized_cost_usd`, and the bundle has no published cost total.
- [ ] `metadata_degraded` is recorded when the gateway returns synthetic or
      truncated rows for the warehouse or region probes, and no probe raised.
- [ ] The Snowflake query tag carries the `_gw-<8 hex>` run suffix and the
      vendor's log join on that suffix finds every query.
- [ ] `benchbox compare` refused the native-versus-gateway pair.
- [ ] Credits consumed, read from the vendor's billing view, stayed under the
      caps above.
- [ ] The warm run's numbers are labeled cache-unverified and are not quoted
      as cold-run results anywhere.

## Abort and cleanup

Stop at the first failed item that could cost credits. Suspend the warehouse,
drop the UAT database, revoke the UAT token, and delete the local result
bundles that name the gateway endpoint. Record what stopped the run in the
dated note; do not retry without the owner.
