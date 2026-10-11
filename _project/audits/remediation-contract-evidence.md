---
develop_sha: 2eb03f3e67ec8f7f1347738f29696fa39ed5f526
measured_at_sha: dd20aed2d53a87a4c34ae74c73a187cfa9bf3c5e
checked_sha: dd20aed2d53a87a4c34ae74c73a187cfa9bf3c5e
replay_sha: 8405f5e3c76ecb679e46079c737083d36494ba57
replay_scope: PR 2888 mirror, parity, throughput contract tests and full fast suite at replay tree 8405f5e3c76ecb679e46079c737083d36494ba57
---

# Remediation contract evidence

This record re-derives the accepted remediation claims from their source
instances. The exact test nodes and control outcomes below are the evidence;
the counts are only a replay summary.

## SCD2 validation

The source of truth is the SCD2 operation set in
`benchbox/core/write_primitives/catalog/operations.yaml`. Every operation has
an intended case and a rejected case in
`tests/integration/test_write_primitives_duckdb.py::TestWritePrimitivesSCD2DuckDB`:

- `merge_scd_type2_basic`: `test_scd2_basic_executes_validates_and_cleans_up`
  succeeds; `test_basic_wrong_insert_count_fails_cardinality_bound` rejects an
  under-inserted write.
- `merge_scd_type2_no_change`: `test_scd2_no_change_is_idempotent` succeeds;
  `test_no_change_noop_against_missing_keys_fails_validation` rejects a no-op
  with deleted unchanged keys. `test_no_change_companion_check_is_load_bearing`
  is the vacuity control.
- `merge_scd_type2_new_keys_only`:
  `test_scd2_new_keys_only_inserts_without_closing` succeeds;
  `test_failing_validation_reports_validation_failed_not_success` rejects a
  duplicate current version. `test_new_keys_only_no_rows_closed_scoped_to_new_keys`
  is the cross-operation scope control.

The isolated N2 control deletes unchanged keys 21-40 and applies the real
`merge_scd_type2_no_change` write SQL. The three old offending queries return
zero rows, while `every_unchanged_key_has_current_version_matching_hash`
returns 20 rows. The old query set therefore passes vacuously; the positive
companion rejects the missing persistence state. The SCD2 selection replay
passed 13 tests.

Producer-to-persistence-to-consumer trace: the staging tables and operation
write SQL produce the dimension rows; `scd2_ops_dim_customer` is the
persistence; validation queries and `OperationResult` consume the rows. The
successful, rejected, repeatability, and companion-control nodes exercise that
write-to-validation seam for all three operations.

## Cohort ranking and read-model persistence

The source of truth is `_build_benchmark_summaries` in
`_project/scripts/explorer_pipeline/pipeline.py`, with persisted consumers in
`results`, `benchmark_rankings`, `cohort_metadata`, and
`result_detail_metrics`:

- `test_mismatched_query_sets_are_not_ranked` rejects a cohort whose three
  members have query sets of 197, 206, and 220 IDs; the exclusion is persisted
  as `mismatched_query_set` and every member is ineligible.
- `test_non_rankable_query_gap_does_not_exclude_rankable_peers` rejects the
  incomplete member with `missing_primary_metric` while two complete peers
  remain eligible.
- `test_partial_query_set_does_not_poison_complete_majority` rejects the
  partial member with `mismatched_query_set` while the complete majority stays
  eligible.
- `test_partial_query_set_exclusion_reaches_every_read_model_consumer` runs
  the producer through the real pipeline and verifies the partial member's
  exclusion and ineligibility in all four persisted consumers; the two
  complete peers remain eligible in `benchmark_rankings`.

Producer-to-persistence-to-consumer trace: result bundles produce
`ManifestEntry` and `DetailResult`; `DuckDBSnapshotBuilder` persists the
cohort and result rows; the explorer's result, ranking, cohort-metadata, and
detail views consume those rows. The end-to-end partial fixture is the seam
control for the prior failure mode where a partial input could leave the
downstream read model incomplete or poison peer rankings.

## Publication receipt and journal reconciliation

The authorized receipt producers are the legacy publication workflow and the
publication-transaction workflow. Their durable attestation is stored in the
publication journal; the deploy-time reconciliation consumer compares the
signed receipt identity with the candidate's recorded parent before a Pages
write. The cross-writer contract is exercised by
`tests/unit/workflows/test_publication_rollback.py::test_deploy_revalidates_signed_receipt_identity_across_both_live_receipt_writers`.

The allowed publication seam replay covers both successful and rejected
paths:

- `test_recovery_required_reconciles_forward_to_durable` accepts a successful
  provider observation and commits the durable transaction.
- `test_recovery_reconciliation_requires_succeed_status`,
  `test_recovery_reconciliation_rejects_mismatched_deployment_id`,
  `test_recovery_reconciliation_rejects_missing_deployment_id`,
  `test_recovery_reconciliation_requires_controller_workflow_sha`, and
  `test_recovery_reconciliation_rejects_pre_send_failure` reject mismatched,
  incomplete, stale, or pre-send evidence.
- `test_cas_conflict_detection` rejects a writer using an old journal parent;
  `test_ambiguous_push_failure_reconciles_remote_success` and
  `test_timeout_resolution` distinguish an unknown push outcome from a
  confirmed remote journal head; `test_corrupt_journal_fails_closed` rejects
  missing or invalid persistence state.

## Prescribed replay command

At `checked_sha` (`dd20aed2d53a87a4c34ae74c73a187cfa9bf3c5e`), the prescribed
replay was:

```bash
uv run -- python -m pytest \
  tests/unit/scripts/explorer_pipeline/test_pipeline.py \
  tests/unit/scripts/publication/test_transaction.py \
  tests/unit/scripts/publication/test_journal.py -q
```

The clean detached-worktree result was `111 passed`. The command and pinned
SHA define the replay; the aggregate count is summary evidence only.

The prescribed pipeline and publication seam replay passed 111 tests after
the end-to-end cohort consumer control was added. Re-run the cited nodes after
any source or consumer change; a passing aggregate count is not acceptance of
the collective claim.

## PR #2122 review follow-ups (Tier 3 CLI batch)

Three Codex inline findings on PR #2122 (all unresolved threads at
remediation time), each reproduced before fixing. Producer chain for the
P1 class: `LogicalOperator.to_dict` writes depth-truncation markers,
`.plans.json` persists them, `QueryPlanDAG.from_dict` rehydrates the
companion, and `compare_query_plans` consumes the rehydrated DAGs.

- P1 truncated plans indistinguishable after reload: the pre-fix probe
  (`/tmp/p1_probe.py`, depth-3 chains differing only at the leaf,
  serialized with `max_depth=1`) reloaded STALE/STALE and compared at
  1.0 with zero mismatches.
  `tests/unit/core/query_plans/test_query_plans_comparison.py::TestTruncationCaveat::test_truncated_pair_with_differing_full_fingerprints_is_flagged`
  succeeds (0.867 overall, one `structure_mismatch` carrying the
  truncation reason); the same node failed pre-fix via stash, alongside
  `test_non_truncated_difference_has_no_caveat` failing pre-fix only on
  the new attribute and passing on behavior both before and after.
  `test_truncated_identical_pair_compares_clean` rejects the
  false-positive direction (identically truncated identical plans stay
  at 1.0 with no caveat).
- Truncation preservation at the reload seam:
  `tests/unit/core/results/test_query_plan_models.py::TestTruncationPreservation::test_from_dict_preserves_shallowest_cut`
  succeeds (cut recorded as 2, integrity STALE);
  `test_from_dict_full_depth_has_no_truncation` succeeds (no cut,
  VERIFIED);
  `test_find_truncation_depth_ignores_non_markers` rejects
  non-marker/boolean payloads.
- P2 lazy benchmark import for completion: pre-fix fresh-process probe
  returned `[]` for nyctaxi/joinorder/vector_search while tpch_skew
  completed incidentally.
  `tests/unit/cli/test_benchmark_hooks.py::TestBenchmarkOptionShellCompletion::test_completion_imports_lazy_benchmark`
  succeeds (registry + module entry wiped, completion re-imports and
  still offers `skew_preset=`);
  `test_completion_unknown_benchmark_is_silent` rejects unknown ids
  with no completions; the fresh-process `BashComplete` probe for
  `--benchmark nyctaxi --benchmark-option tax...` now yields
  `taxi_types=`.
- P2 stale run reference: `docs/reference/cli/run.md` stated plain
  `run` has no `--streams` flag in two passages; both now document the
  canonical `--streams` spelling with `--concurrency` as alias, and a
  docs-wide grep finds no remaining `has no --streams` claim.

## Execution-engine provenance through result export

The review of `7eff3646dfa3e9dea10a6be0e3bee32982759a0d` identified two
accepted defects: current Polars engine requests did not affect newly derived
variant identity, and config-only requests disappeared during load/re-export.
The repairs use existing producer evidence without changing published bundles
or assigning new engine suffixes to legacy identities.

The behavioral instances in
`tests/unit/core/results/test_execution_variant_schema.py` are:

- `test_current_polars_producer_request_defines_variant[default]`,
  `[in-memory]`, and `[streaming]`: the actual adapter writes
  `configuration.engine_requested`; the result exporter consumes it to persist
  distinct requested-engine variants. The default control has no engine suffix,
  and no case invents an adapter receipt.
- `test_config_only_engine_request_survives_load_export[default]`,
  `[in-memory]`, and `[streaming]`: `config.execution_engine` is loaded into
  run configuration, persisted by JSON export, and consumed by a second load.
  All requests and unknown applied/observed values survive; no platform receipt
  is synthesized, and the source bundle remains unchanged.
- `test_legacy_engine_request_keeps_existing_identity[None]` and
  `[polars-df]`: legacy explicit streaming evidence remains readable, but both
  missing and already-recorded legacy identity stay `polars-df`, not
  `polars-df+streaming`; source bundles remain unchanged.

These instances and the existing complete-receipt round trip passed together
with CLI export and Explorer transformer tests: 230 passed. Replay with
`uv run -- python -m pytest tests/unit/core/results/test_execution_variant_schema.py tests/unit/cli/test_cli_output.py tests/unit/test_results_exporter.py tests/unit/scripts/explorer_pipeline/test_transformer.py -q`.

## PR #2888 review remediations

Code source commit: `45b2751364e00e8d71d1c9ed907bb841b944ce76`.
This is a replay of the contract tests and full fast suite; it does not replace
the original measurements bound by this file's `checked_sha`.

The accepted Oracle findings were on PR #2888 head
`cccec335475cc35bc2f2c1c828989e47d009af5e`: mutable tag mirrors could diverge
from upstream content, ClickHouse/Trino consumer overrides could drift from
Compose, and CedarDB mirror failure could suppress DuckDB throughput evidence.
The fixes below are in source commit `45b2751364e00e8d71d1c9ed907bb841b944ce76`.

### Digest-pinned ClickHouse and Trino mirrors

Enumerated instances: ClickHouse
`clickhouse/clickhouse-server:25.8@sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77`
and Trino
`trinodb/trino:480@sha256:1565e8cac299a32dd9177a4da2d748da4ceb9f1560a9c409d1d18fd72ea5253e`.

The Compose defaults produce the source tag-plus-digest references.
`mirror-ci-images.yml` extracts and requires the digest, inspects GHCR by
repository digest, and persists copied content under a digest-derived tag using
`skopeo copy --all --preserve-digests`; it then re-inspects the digest and hashes
the raw manifest. `nightly-v2.yml` and `docker-integration.yml` consume GHCR
references carrying those same digests; parity is independently checked below.

The ClickHouse/Trino positive nodes in `tests/unit/workflows/test_nightly_t3_workflow.py` passed:

- `test_mirror_reuses_an_existing_digest_without_reading_docker_hub[clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_reuses_an_existing_digest_without_reading_docker_hub[trino-trinodb/trino:480]`
- `test_mirror_copies_only_a_missing_digest_and_checks_the_result[clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_copies_only_a_missing_digest_and_checks_the_result[trino-trinodb/trino:480]`

Rejected controls passed for each source instance:

- `test_mirror_refuses_an_unpinned_compose_source[clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_refuses_an_unpinned_compose_source[trino-trinodb/trino:480]`
- `test_mirror_fails_if_existing_digest_reference_returns_other_content[clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_fails_if_existing_digest_reference_returns_other_content[trino-trinodb/trino:480]`
- `test_mirror_checks_the_digest_after_copy[clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_checks_the_digest_after_copy[trino-trinodb/trino:480]`

The non-missing-error matrix rejected auth denial, timeout, and HTTP 503 without
a copy for each image:

- `test_mirror_fails_closed_on_non_missing_registry_errors[unauthorized: access denied-clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_fails_closed_on_non_missing_registry_errors[unauthorized: access denied-trino-trinodb/trino:480]`
- `test_mirror_fails_closed_on_non_missing_registry_errors[Get https://ghcr.io/v2/: dial tcp: i/o timeout-clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_fails_closed_on_non_missing_registry_errors[Get https://ghcr.io/v2/: dial tcp: i/o timeout-trino-trinodb/trino:480]`
- `test_mirror_fails_closed_on_non_missing_registry_errors[unexpected status code 503 Service Unavailable-clickhouse-clickhouse/clickhouse-server:25.8]`
- `test_mirror_fails_closed_on_non_missing_registry_errors[unexpected status code 503 Service Unavailable-trino-trinodb/trino:480]`

The existing CedarDB digest control passed:
`test_mirror_keeps_the_cedardb_digest_reference`.

These nodes execute the workflow shell step with isolated fake registry tools.
They prove digest handling and fail-closed branches, not live GHCR publication
or a live Docker pull; those remain post-merge checks.

### ClickHouse and Trino consumer parity

The two enumerated Compose-to-consumer nodes in `tests/unit/workflows/test_docker_integration_workflow.py` passed:

- `test_ci_mirror_refs_match_compose_default_digests[clickhouse-docker/clickhouse/docker-compose.yml-CLICKHOUSE_IMAGE]`
- `test_ci_mirror_refs_match_compose_default_digests[trino-docker/trino/docker-compose.yml-TRINO_IMAGE]`

The source is each Compose image default; the persisted consumer contract is
the GHCR image override in each of `nightly-v2.yml` and
`docker-integration.yml`; the downstream jobs use those overrides for their
service image. Four isolated negative controls mutated ClickHouse and Trino
independently in each consumer workflow; each mutation was rejected (4/4).

### Independent DuckDB and CedarDB throughput evidence

In `nightly.yml`, DuckDB `throughput-uat` produces its job result and artifacts
independently; CedarDB `throughput-uat-cedardb` alone needs `mirror-cedardb` and
exports `cedardb_sweep_outcome`. Both jobs upload logs on failure.
`throughput-uat-signal` waits for both job results and publishes the
`nightly/throughput-uat` commit status; only `success:success` maps to success,
cancellation maps to error, and all other result pairs map to failure. The
DuckDB baseline artifact is uploaded only after a successful DuckDB sweep.

Passing positive nodes in `tests/unit/workflows/test_nightly_throughput_uat_contract.py`:
`test_failure_signal_waits_for_both_independent_jobs`,
`test_only_both_successful_jobs_publish_success[success-success-success]`,
`test_baseline_record_is_uploaded_only_after_a_successful_duckdb_sweep`, and
`test_status_description_reports_cedardb_sweep_outcome_without_a_quarantine_label`.

Both separate failure-artifact instances passed:

- `test_each_throughput_job_uploads_its_logs_on_failure[throughput-uat-Upload throughput UAT logs and results-~/Developer/benchmark_runs/logs/uat_throughput_duckdb_*]`
- `test_each_throughput_job_uploads_its_logs_on_failure[throughput-uat-cedardb-Upload CedarDB throughput UAT logs and results-~/Developer/benchmark_runs/logs/uat_throughput_cedardb_*]`

Rejected status controls passed:
`test_only_both_successful_jobs_publish_success[success-skipped-failure]`,
`test_only_both_successful_jobs_publish_success[success-failure-failure]`,
and `test_only_both_successful_jobs_publish_success[failure-success-failure]`;
cancellation is rejected as success by
`test_only_both_successful_jobs_publish_success[cancelled-success-error]`.
The signal test runs the actual workflow shell against a fake `gh` boundary
and checks the emitted commit-status fields.

A real nightly dispatch on merged `develop` is still required to verify actual
GitHub job outputs, artifacts, and status contexts.

### Verification at the rebased replay tree

Focused workflow tests passed 105:

```bash
uv run -- python -m pytest tests/unit/workflows/test_nightly_t3_workflow.py tests/unit/workflows/test_docker_integration_workflow.py tests/unit/workflows/test_nightly_throughput_uat_contract.py -q
```

The full configured verifier passed: 37,273 tests passed, 26 skipped, 857
warnings, and 4 subtests passed; `ty check` reported 669 diagnostics and exited 0.

```bash
uv run -- ruff check . && uv run ty check && uv run -- python -m pytest -m fast -q
```
Changed-test Ruff check and format check, codespell 2.4.3, and
`make comment-policy-check` passed. The 4/4 consumer drift controls also
passed their expected rejection outcome. Docker CLI is unavailable in this
environment; no real Compose CLI config or live registry inspection was
performed.
