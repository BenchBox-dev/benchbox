# Development Loop Safety Property Ledger

Test enrollment is frozen at 2026-10-03: new tests need no row, but every
workflow and script still needs one, and a row is removed only when its file
is deleted.

Safety properties keep their guards until a replacement guard proves
coverage. No workflow, script, test, or hook listed here may be deleted by
a modernization change unless its row shows the property still covered or
classifies the file pure-process.

Classification values:

- `product-safety` — guards a user-facing correctness, data integrity,
  privacy, or release guarantee. Keep or port to the replacement loop.
- `pure-process` — guards internal branch, queue, or workflow mechanics
  with no product-safety content. May retire once its replacement is green.
- `tooling` — agent, tracker, or hygiene support. Keep.

## Safety properties

| Property | Current guard | Replacement guard | Proof | Status |
| --- | --- | --- | --- | --- |
| Correctness oracle: digest arming | `TestCorrectnessGateOracle` in `tests/unit/test_standardized_test_commands.py`, `correctness-gate` in `ci.yml` | ci.yml `core` on heavy carve-outs; trunk correctness gate on develop pushes | pending | open |
| Correctness oracle: query discrimination | `TestCorrectnessGateOracle` in `tests/unit/test_standardized_test_commands.py` | ci.yml `core` on heavy carve-outs; trunk correctness gate | pending | open |
| Correctness oracle: no-skip | `TestCorrectnessGateOracle` in `tests/unit/test_standardized_test_commands.py` | ci.yml `core` on heavy carve-outs; trunk correctness gate | pending | open |
| Required local-engine cases | `scripts/required_case_evidence.py`, `make test-required-local-cases` | ci.yml `core` on heavy carve-outs and trunk required-local-cases; skipped, deselected, failing and expected-failing required cases fail the check | pending | open |
| SQL self-binding lint | `_project/scripts/detect_self_binding.py` | ci.yml `tooling` | pending | open |
| Monotonic-clock policy (wall-clock part) | `_project/scripts/timing_policy_check.py`, `_project/scripts/timing_audit.py` | ci.yml `tooling` | pending | open |
| Submission validation | `scripts/validate_submission.py`, `.github/workflows/validate-submission.yml`, `.github/workflows/validate-submission-comment.yml`, `tests/unit/workflows/test_validate_submission_changed_bundles.py`, `tests/unit/workflows/test_validate_submission_comment_security.py`, `tests/unit/scripts/test_validate_submission*.py` | ci.yml `results-data` | pending | open |
| Corpus trust boundary | `tests/unit/workflows/test_corpus_trust_boundary.py`, `.github/workflows/sync-results-data-to-published.yml` | ci.yml `results-data` | pending | open |
| Artifact privacy (no private paths in published site) | `scripts/publication/check_artifact_privacy.py` attestation in the `site-inputs.yml` bundle build, `tests/unit/scripts/test_corpus_privacy_invariant.py` | `BenchBox-dev/benchbox-site` deploy gates (`privacy`) | pending | open |
| Publication rollback and transactions | `BenchBox-dev/benchbox-site` deploy `rollback` mode, which restores the artifact of a recorded receipt, and the receipt each deploy writes | `BenchBox-dev/benchbox-site` deploy probes, which dispatch at most one rollback to the last known good receipt | pending | open |
| Corpus promotion (privacy, bijection and Explorer compatibility of the published corpus) | `privacy`, `corpus_bijection`, `explorer_compat` and `snapshot_invariants` attestations recorded by `scripts/site_inputs.py` in the `site-inputs.yml` bundle build, `tests/unit/scripts/test_site_inputs.py` | `BenchBox-dev/benchbox-site` deploy gates, which refuse a bundle whose attestations or digests do not verify | pending | open |
| Explorer snapshot and UI compatibility | `explorer_compat` and `snapshot_invariants` attestations in the `site-inputs.yml` bundle build, `tests/unit/scripts/test_results_explorer_snapshot_invariants.py` | `BenchBox-dev/benchbox-site` deploy gates (`explorer_compat`) | pending | open |
| Public-site visual acceptance | The Visual comparison workflow in `BenchBox-dev/benchbox-site` | The site repository screenshots a pull request's build and the deployed artifact and reports the differences; it is advisory and not part of that repository's `ci`. The deploy's route and digest probes prove availability and identity, not layout | pending | open |
| Binary integrity (vendored engine hashes, dbgen framing) | `_binaries/` hashes, TPC-H binary framing tests | ci.yml `core` hash checks and required macOS/Windows dbgen framing; nightly-v2.yml matrix dbgen framing on all three OS at Python 3.12 | pending | open |
| Wheel installability | `package-smoke` job, release workflow build | release workflow artifact verification | pending | open |
| Shipped bundled generator integrity | `benchbox/_binaries/SHA256MANIFEST.json`, `tests/unit/utils/test_binary_manifest.py` | trunk.yml `dist-artifact` verifies source, wheel, and sdist membership and hashes on each push to develop; installed-package verifier for release checks | pending | open |
| Dependency bounds | `scripts/check_dependency_bounds.py`, `tests/unit/scripts/test_check_dependency_bounds.py` | ci.yml `tooling` | pending | open |
| Release curation and readiness | `scripts/check_release_curation.py`, `tests/unit/scripts/test_check_release_curation.py`, `.github/workflows/validate-release-pr.yml`, `.github/workflows/release-canary.yml`, `scripts/release_flow.py`, `tests/unit/scripts/test_release_flow.py` | ci.yml always-required `ci-paths` selects release identity changes and checks them against the immutable event base; release workflow | pending | open |
| Ruleset and settings drift | `scripts/ruleset_drift_check.py`, `tests/unit/release/test_ruleset_drift_review_coverage.py`, `tests/unit/release/test_ruleset_review_enforcement.py` | Advisory nightly and manual verification; enforced release-canary and release-PR bootstrap; thread-resolution and tag-protection predicates retained | pending | open |
| Soundness-path review | `.github/soundness-paths.txt`, `_project/scripts/soundness_paths.py`, `tests/unit/scripts/test_soundness_paths.py` | Required App-bound oracle-gate on the exact head, required thread resolution and post-merge soundness digest | pending | open |
| Corpus-to-artifact bijection | `scripts/publication/check_corpus_bijection.py` with `publication/ledger-seed.json` | In-bundle `corpus_bijection` runs the former site deploy gate's form at the bundle commit, with the ledger seed; the bundle build refuses to run without the seed, so the seed stays until its dispositions move elsewhere. It proves snapshot-to-tree consistency only. Cross-commit trust chains through contiguous `validator_parity` ranges (parent = newest unexpired develop bundle that is an ancestor; the build fails without one unless a dispatch names the parent), the `certified_by` trunk run, and the bundle resolve in the `BenchBox-dev/benchbox-site` deploy; `explorer_compat` and `snapshot_invariants` run on every bundle, and `corpus_validation` runs `results-data/validate_corpus.py` over the whole corpus on every bundle, so a validator-only change is checked against existing bundles before the next deploy | pending | open |
| Durable auto-merge hold | `no-auto-merge` label and draft status, `.github/workflows/pr-hold.yml` | Exact-head arming refuses holds; the required `hold` check fails while the label is present, so an armed or raw-merged PR cannot land | pending | open |
| Workflow context validity | `tests/unit/workflows/test_workflow_expression_contexts.py` | ci.yml `tooling` | pending | open |
| Combined-tree validation before merge (retired) | Former merge-group required checks | Required PR checks, then exact-commit trunk validation; composition failures are detected after merge and handled by revert | pending | open |
| read_primitives cross-surface check | `_project/scripts/rp_scoped_check.py` | nightly T3 per-domain suites | pending | open |
| Published compatibility, query and tuning-support pages match the exported facts (retired) | `scripts/generate_compat_docs.py`, `scripts/generate_query_docs.py`, `scripts/generate_tuning_support_matrix.py` | The pages are rendered in `BenchBox-dev/benchbox-site` from the exported documentation data. Core keeps `scripts/export_docs_data.py`, whose query set, counts and SQL stability across catalog resets are asserted in `tests/unit/scripts/test_export_docs_data.py`, and the documentation-data schema checked in the site-inputs bundle; `make compat-inventory-check` keeps the DDL drift guard. The `can_reach_applied_verified` derivation stays in `scripts/docs_data_sources.py`, locked by `test_only_introspected_platforms_can_reach_applied_verified` in `tests/unit/docs/test_tuning_platform_support_matrix.py` and registered in `quality/doc-lock-tests.json` | pending | open |
| Links in published pages resolve (retired) | `scripts/check_doc_relative_links.py`, `scripts/check_doc_external_links.py` | The scanned pages now live in `BenchBox-dev/benchbox-site`, which checks relative links in its documentation build and runs a nightly crawl of the published site with fragment checking. Markdown links among the pages that stay in core have no core gate | pending | open |
| Site-owned documentation is absent from core (replaces the documentation freeze) | `scripts/check_docs_freeze.py` | `scripts/check_docs_ownership.py`, run by `make docs-validate`: every tracked document must be core-owned, and every site-owned path and its generator must be absent | pending | open |

## Known KEEP traps

These were misclassified as process in earlier plan revisions and must stay.
Each is `product-safety` or `tooling`, never `pure-process`:

- `_project/scripts/detect_self_binding.py`
- `_project/scripts/rp_scoped_check.py`
- `_project/scripts/timing_audit.py` and the wall-clock policy in
  `_project/scripts/timing_policy_check.py`
- `tests/unit/workflows/test_workflow_expression_contexts.py`
- `scripts/ruleset_drift_check.py`
- `scripts/validate_submission.py`

## File classification

### `.github/workflows/`

| File | Classification | Property or reason |
| --- | --- | --- |
| `ci-text-edit-refresh.yml` | product-safety | When a full CI run completes on a pull request head, re-runs the failed jobs of the newest `CI (text edit)` run on that head so a title or body edit does not hold runners while it waits |
| `comment-policy-nightly.yml` | tooling | Nightly and on-demand whole-tree comment-policy scan of develop; the backstop for files a pull request leaves unchanged |
| `corpus-drift-check.yml` | product-safety | Corpus drift detection |
| `corpus-event-bridge.yml` | product-safety | Corpus event integrity |
| `corpus-reconciler.yml` | product-safety | Corpus reconciliation |
| `cross-platform-validation.yml` | product-safety | Cross-platform correctness |
| `cross-surface-baseline-autodetect.yml` | tooling | Baseline maintenance support |
| `docker-integration.yml` | product-safety | Container integration coverage |
| `extension-smoke.yml` | product-safety | Extension smoke coverage |
| `gitignore-lint.yml` | tooling | Hygiene; standalone, not part of the ci.yml units |
| `lint.yml` | tooling | Lint gate |
| `mirror-ci-images.yml` | product-safety | Seeds digest-pinned images in GHCR only when absent; scheduled CI checks mirrors by digest without routine Docker Hub reads |
| `nightly.yml` | product-safety | Scheduled validation; nightly T3 replaces most domains, including the DuckDB and CedarDB throughput UAT; Windows legs and workflow liveness are non-blocking; ruleset drift runs as advisory |
| `nightly-v2.yml` | product-safety | Nightly T3: platform matrix, docker engines, cross-browser, extension, install, DuckDB and CedarDB throughput UAT with the `nightly/throughput-uat` status, drift |
| `oracle-gate.yml` | product-safety | Required gate from trusted code: result-affecting changes need a success review of the head from the `benchbox-oracle` App, whose Bot identity only the owner controls, or a stand-in approval comment from an attester account posted after the oracle's latest review; that account is also the one local automation uses, so the stand-in records who vouched, not that a human read it. Posted as `oracle-gate` for the head from develop code on trusted triggers; sole writer in-tree, with the required check scoped to the oracle App in the ruleset |
| `oracle-review.yml` | product-safety | Review-event notification, not a check: a no-op job on review submitted, edited and dismissed whose completion makes `oracle-gate` re-evaluate |
| `oracle-verdict.yml` | product-safety | Self-hosted soundness review: reviewers chosen by tier and availability in one review job, each holding only the gateway client token, and a review and non-required `oracle-verdict` status posted by the owner's App, which the required `oracle-gate` check reads |
| `perf-smoke.yml` | product-safety | Performance smoke |
| `pr-hold.yml` | product-safety | Required `hold` check: fails while the `no-auto-merge` label is present, on every label change |
| `pricing-data-drift-check.yml` | product-safety | Pricing data integrity |
| `release.yml` | product-safety | Release publishing |
| `release-v2.yml` | product-safety | Tag-triggered publication of the admitted trunk artifacts: annotated tag, latest-run admission, install matrix, artifact-bound UAT, publish-time tag re-check, attestation |
| `release-canary.yml` | product-safety | Release canary protection |
| `seed-corpus.yml` | product-safety | Corpus seeding |
| `site-inputs.yml` | product-safety | Versioned site-inputs bundle build, attestation, artifact upload and site dispatch |
| `soundness-merge-digest.yml` | product-safety | Post-merge soundness review digest |
| `submission-validator-drift-check.yml` | product-safety | Submission validator sync |
| `sync-results-data-to-published.yml` | product-safety | Corpus trust boundary sync |
| `test.yml` | product-safety | Test tiers; replaced by ci.yml units |
| `todo-state-validate.yml` | tooling | Tracker state validation |
| `trunk-coverage.yml` | product-safety | Scheduled (six a day) and dispatched instrumented four-shard medium tier, v2 coverage map combine, published-map self-fetch, and a map-staleness issue on its own daily cron; never consulted by arming |
| `trunk.yml` | product-safety | Develop-push fast lane, uninstrumented four-shard medium tier, correctness gate, required-local-cases, whole-tree comment-policy scan and verified release artifact; retains pending runs with queue:max |
| `trunk-red-disarm.yml` | product-safety | Completed develop Trunk run disables auto-merge on armed PRs the arm gate would refuse |
| `upload-answers.yml` | product-safety | Answer file publication |
| `trunk-duration-trend.yml` | tooling | Daily comparison of Trunk job p50 durations with the prior week; opens or closes one issue assigned to the repository owner; read-only actions access plus issue writes |
| `fast-duration-baseline-refresh.yml` | tooling | Weekly regeneration of the committed fast-tier duration baseline from successful merged-PR fast-junit artifacts; opens one pull request with contents and pull-requests write only, bounded logged API calls, and a medium-shard drift report |
| `validate-release-pr.yml` | product-safety | Release PR base validation |
| `validate-submission.yml` | product-safety | Submission validation |
| `validate-submission-comment.yml` | product-safety | Submission validation comment |
| `windows-fast.yml` | product-safety | Dispatch-only Windows fast unit suite, the same command as the nightly `t3:windows` domain, preceded by the installed-wheel bundled SQLGlot runtime smoke |
| `tpcds-official-qualification.yml` | product-safety | Weekly SF 1 TPC-DS qualification; fails on DataFrame-to-SQL divergence or an unclassified printed-answer difference |
| `tpcds-platform-identity.yml` | product-safety | Bundled TPC-DS generators agree across platforms (data checksums and dsqgen parameters) |
| `tpch-dbgen-intel-macos.yml` | product-safety | Bundled darwin-x86_64 TPC-H dbgen is executed on an Intel macOS runner and emits the canonical supplier and customer rows |

### `tests/unit/workflows/`

| File | Classification | Property or reason |
| --- | --- | --- |
| `test_corpus_event_bridge.py` | product-safety | Corpus event integrity |
| `test_corpus_trust_boundary.py` | product-safety | Corpus trust boundary |
| `test_docker_integration_workflow.py` | product-safety | Container integration |
| `test_nightly_t3_workflow.py` | product-safety | Nightly T3 domain coverage |
| `test_nightly_legacy_test_job.py` | product-safety | Legacy nightly matrix timeout bound and Windows legs |
| `test_oracle_gate_workflow.py` | product-safety | Trusted triggers, develop-only checkout, single per-PR concurrency group, App-only posting and no other workflow posting the gate context |
| `test_oracle_review_workflow.py` | product-safety | The notification-only shape of the oracle-review workflow: review triggers, empty permissions, one `review-event` job, no checkout, secrets or tools |
| `test_oracle_verdict_workflow.py` | product-safety | Oracle review triggers, guards, the single review job's step order and secret scoping, and App-only posting |
| `test_t2_partition_workflow.py` | product-safety | Complete medium selection, correctness gate conservation, and binary framing placement |
| `test_comment_policy_nightly_workflow.py` | tooling | Nightly comment-policy workflow triggers, develop checkout, read-only permissions, whole-tree launcher call and shared action pins |
| `test_trunk_coverage_workflow.py` | product-safety | Coverage workflow triggers, instrumented shards, map combine with self-fetch, schedule-only staleness issue |
| `test_trunk_workflow.py` | product-safety | Develop-push triggers, uninstrumented medium tier, whole-tree comment-policy scan, retained pending runs and read-only test permissions |
| `test_trunk_red_disarm_workflow.py` | product-safety | Red-trunk disarm trigger, develop-push filter, exact permissions, workflow token and full-history checkout |
| `test_published_results_base_ci.py` | product-safety | Published corpus CI |
| `test_release_canary_shard_evidence.py` | product-safety | Release canary evidence |
| `test_release_uat_charter_guard.py` | product-safety | Release charter guard |
| `test_seed_corpus_pr_base.py` | product-safety | Corpus seeding |
| `test_site_inputs_workflow.py` | product-safety | Site-inputs parent resolution by SHA |
| `test_stacked_pr_base_guard.py` | pure-process | Stacked-PR base mechanics |
| `test_validate_submission_changed_bundles.py` | product-safety | Submission validation |
| `test_validate_submission_comment_security.py` | product-safety | Submission comment security |
| `test_validate_submission_corpus_allowlist.py` | product-safety | Submission corpus allowlist |
| `test_validate_submission_fail_open.py` | product-safety | Submission fail-open behavior |
| `test_validate_submission_override_approval.py` | product-safety | Submission override approval |
| `test_validate_submission_trusted_checkout.py` | product-safety | Submission trusted checkout |
| `test_validate_submission_vendor_gate.py` | product-safety | Submission vendor gate |
| `test_validate_submission_workflow_guard_mirror.py` | product-safety | Submission workflow guard mirror |
| `test_workflow_context_availability.py` | product-safety | Workflow context availability (KEEP trap) |
| `test_windows_fast_workflow.py` | product-safety | Windows fast workflow shares the nightly command, pins and read-only permissions |
| `test_workflow_expression_contexts.py` | product-safety | Workflow context validity (KEEP trap) |

### `tests/unit/scripts/` (complete)

| File | Classification |
| --- | --- |
| `test_agent_instruction_audit.py` | tooling |
| `test_audit_sha_check.py` | product-safety |
| `test_batch_integration.py` | product-safety |
| `test_blind_spot_tools.py` | product-safety |
| `test_branch_prune_merged.py` | pure-process |
| `test_browser_fixtures_port.py` | product-safety | Fixture-query port byte-identity |
| `test_browser_gate_aggregate.py` | product-safety |
| `test_build_joinorder_data.py` | product-safety |
| `test_check_api_contract_symbols.py` | product-safety |
| `test_check_complexity.py` | tooling |
| `test_check_dependency_bounds.py` | product-safety |
| `test_check_duplicate_code.py` | tooling |
| `test_check_makefile_inventory.py` | tooling |
| `test_check_project_references.py` | product-safety |
| `test_check_release_curation.py` | product-safety |
| `test_check_rerun_shard_retention.py` | tooling |
| `test_check_sqlglot_repro_retirement.py` | tooling |
| `test_check_submission_validator_sync.py` | product-safety |
| `test_check_uv_lock_revision.py` | tooling |
| `test_changelog_fragments.py` | tooling | Changelog fragment validation, rendering, required-entry decision and conflict-free merge property |
| `test_check_windows_antipatterns.py` | tooling |
| `test_comment_policy.py` | tooling |
| `test_comment_policy_strict_ownership.py` | tooling |
| `test_ci_lint_environment_boundary.py` | tooling |
| `test_comment_cleanup_scope.py` | tooling |
| `test_comment_parity.py` | tooling |
| `test_compile_all_platforms.py` | product-safety |
| `test_corpus_cohort_depth.py` | product-safety |
| `test_corpus_drift_canary.py` | product-safety |
| `test_corpus_privacy_invariant.py` | product-safety |
| `test_corpus_pseudonym_recovery.py` | product-safety |
| `test_dev_loop_pr_metrics.py` | pure-process |
| `test_ci_unit_result.py` | tooling |
| `test_ci_units.py` | tooling |
| `test_duckdb_datasketches_smoke.py` | product-safety |
| `test_duckdb_version_matrix.py` | product-safety |
| `test_explorer_build_contract.py` | product-safety |
| `test_fast_lane_ceiling_check.py` | pure-process |
| `test_generate_changelog_entry.py` | product-safety |
| `test_heavy_tier_needed.py` | product-safety |
| `test_inert_paths.py` | product-safety | Inert-path manifest predicates, machine-read reader index and guard |
| `test_generate_corpus_inventory.py` | product-safety |
| `test_guard_messages.py` | tooling |
| `test_inline_applied_ledger_bounds.py` | product-safety |
| `test_local_validation.py` | pure-process |
| `test_migrate_clickhouse_labels.py` | product-safety |
| `test_mirror_partial_validation_policy.py` | pure-process |
| `test_oracle_gate.py` | product-safety | Gate decision parity with the shared checker decision, status mapping and App posting |
| `test_oracle_review_check.py` | product-safety | Oracle review or stand-in approval decision for soundness-path changes |
| `oracle_reviewers/test_absence.py` | product-safety |
| `oracle_reviewers/test_attempts.py` | product-safety |
| `oracle_reviewers/test_brief.py` | product-safety |
| `oracle_reviewers/test_classifier.py` | product-safety |
| `oracle_reviewers/test_cli_plan.py` | product-safety |
| `oracle_reviewers/test_commands.py` | product-safety |
| `oracle_reviewers/test_github.py` | product-safety |
| `oracle_reviewers/test_policy.py` | product-safety |
| `oracle_reviewers/test_retry.py` | product-safety |
| `oracle_reviewers/test_runner.py` | product-safety |
| `oracle_reviewers/test_selection.py` | product-safety |
| `oracle_reviewers/test_verdict.py` | product-safety |
| `test_path_filter_decision.py` | tooling |
| `test_preflight_targets.py` | pure-process |
| `test_phase2_metrics.py` | pure-process |
| `test_perf_smoke_baseline.py` | pure-process |
| `test_post_merge_signature.py` | pure-process |
| `test_pr_arm.py` | pure-process |
| `test_pr_ready_make.py` | pure-process |
| `test_pr_landing.py` | pure-process |
| `test_trunk_revert.py` | pure-process |
| `test_trunk_red_disarm.py` | pure-process |
| `test_pytest_shard_evidence.py` | product-safety | Real serial and distributed test selection and execution conservation |
| `test_required_case_evidence.py` | product-safety | A required local-engine case that skips, is deselected, fails or is expected to fail fails the required-case check |
| `test_pr_refresh_certification.py` | pure-process |
| `test_pr_refresh_replay.py` | pure-process |
| `test_pr_review_followups.py` | pure-process |
| `test_reference_usage_audit.py` | tooling |
| `test_regenerate_correctness_gate_digests.py` | product-safety | TPC-H gate digests regenerate from qgen default parameters and record a null seed |
| `test_release_admitted_dist.py` | product-safety | Admitted directory matches its admission receipt, the tag and the commit before publication; only the verified wheel and sdist are staged |
| `test_release_artifact_consumer.py` | product-safety | Producer receipt, provenance selection and archive admission fail closed |
| `test_release_artifact_execution.py` | product-safety | Real tag objects, isolated verifier boundary, credential and Git configuration isolation, bounded download and no-replace publication |
| `test_release_cut_start.py` | product-safety |
| `test_release_finalize.py` | product-safety |
| `test_release_flow.py` | product-safety |
| `test_results_explorer_corpus_migrate.py` | product-safety |
| `test_results_explorer_cpu_attestation_backfill.py` | product-safety |
| `test_results_explorer_snapshot_invariants.py` | product-safety |
| `test_comment_policy_changed_files.py` | tooling | Changed-file selection, global-change triggers and unconditional policy checks in base comparisons |
| `test_scheduled_workflow_liveness.py` | tooling |
| `test_shrink_rollup.py` | pure-process |
| `test_site_inputs.py` | product-safety | Site-inputs bundle manifest, digests and verify |
| `test_site_inputs_parent.py` | product-safety | Site-inputs parent resolution by SHA |
| `test_skill_sync_ci_policy.py` | tooling |
| `test_soundness_merge_digest.py` | product-safety | Post-merge soundness review digest |
| `test_sqlglot_generator.py` | product-safety |
| `test_sqlglot_generator_known_failures.py` | product-safety |
| `test_sqlite_extract_repro.py` | product-safety |
| `test_submission_workflow_waiver.py` | product-safety |
| `test_timing_policy_check.py` | product-safety |
| `test_todo_review_own_edit_freshness.py` | tooling |
| `test_todo_state_contract_check.py` | tooling |
| `test_todo_state_workflow.py` | tooling |
| `test_update_version.py` | product-safety |
| `test_validate_submission.py` | product-safety |
| `test_validate_submission_corpus_allowlist.py` | product-safety |
| `test_validate_submission_corpus_paths.py` | product-safety |
| `test_validate_submission_resource_heavy.py` | product-safety |
| `test_validate_submission_slim_fallback.py` | product-safety |
| `test_verify_mcp_conformance.py` | tooling |
| `test_worktree_audit.py` | tooling |
| `explorer_pipeline/test_cpu_hardware_identity.py` | product-safety |
| `explorer_pipeline/test_duckdb_browser_contract.py` | product-safety |
| `explorer_pipeline/test_duckdb_builder.py` | product-safety |
| `explorer_pipeline/test_duckdb_readonly_fuzz.py` | product-safety |
| `explorer_pipeline/test_duplicate_result_id.py` | product-safety |
| `explorer_pipeline/test_legacy_artifact_guard.py` | pure-process |
| `explorer_pipeline/test_measurement_basis.py` | product-safety |
| `explorer_pipeline/test_meta_leaderboard.py` | product-safety |
| `explorer_pipeline/test_normalized_cost_contract.py` | product-safety |
| `explorer_pipeline/test_override_display.py` | product-safety |
| `explorer_pipeline/test_pipeline.py` | pure-process |
| `explorer_pipeline/test_privacy_rejection.py` | product-safety |
| `explorer_pipeline/test_ranking.py` | product-safety |
| `explorer_pipeline/test_read_model_contract.py` | product-safety |
| `explorer_pipeline/test_result_id_contract.py` | product-safety |
| `explorer_pipeline/test_transformer.py` | pure-process |
| `explorer_pipeline/test_visible_metrics_registry.py` | product-safety |
| `publication/test_artifacts.py` | product-safety |
| `publication/test_check_corpus_bijection.py` | product-safety |
| `publication/test_check_explorer_compat.py` | product-safety |
| `publication/test_ledger_seed.py` | product-safety |
| `publication/test_override_companion_exclusion.py` | product-safety |
| `publication/test_reconciler.py` | product-safety |
| `test_tpcds_divergence_report.py` | tooling |
| `test_tpcds_platform_identity.py` | product-safety |

### `tests/unit/release/`

| File | Classification | Property or reason |
| --- | --- | --- |
| `test_changelog_tag_guard.py` | product-safety | Release tag integrity |
| `test_ruleset_drift_review_coverage.py` | product-safety | Ruleset drift |
| `test_ruleset_review_enforcement.py` | product-safety | Ruleset review enforcement |

### `tests/unit/test_release_*`

| File | Classification | Property or reason |
| --- | --- | --- |
| `test_release_canary_incident.py` | product-safety | Release canary |
| `test_release_canary_sharding.py` | product-safety | Release canary sharding |
| `test_release_infrastructure.py` | product-safety | Release infrastructure |
| `test_release_readiness.py` | product-safety | Release readiness |

### `scripts/`

| File | Classification | Property or reason |
| --- | --- | --- |
| `validate_submission.py` | product-safety | Submission validation (KEEP trap) |
| `ruleset_drift_check.py` | product-safety | Ruleset drift (KEEP trap) |
| `check_dependency_bounds.py` | product-safety | Dependency bounds |
| `check_release_curation.py` | product-safety | Release curation |
| `changelog_fragments.py` | tooling | Changelog fragment validation, rendering, required-entry decision and Unreleased guard |
| `check_sqlglot_upstream_freshness.py` | product-safety | Bundled SQLGlot freshness release gate |
| `check_sqlglot_advisories.py` | product-safety | Bundled SQLGlot published-advisory release gate |
| `generate_landing_quickstarts.py` | product-safety | Landing quickstart generation |
| `site_inputs.py` | product-safety | Site-inputs bundle build and verify |
| `site_inputs_parent.py` | product-safety | Site-inputs parent resolution by SHA |
| `check_duplicate_code.py` | tooling | Hygiene |
| `check_windows_antipatterns.py` | tooling | Hygiene |
| `path_filter_decision.py` | tooling | Path classifier shared with ci.yml units |
| `pytest_shard_evidence.py` | product-safety | Exact assigned, collected, and executed medium test evidence |
| `pr_landing.py` | pure-process | PR-loop mechanics |
| `trunk_revert.py` | pure-process | Revert of a merged PR and the red-trunk gate on `make pr-open` |
| `trunk_red_disarm.py` | pure-process | Disables auto-merge on armed PRs the arm gate would refuse when a develop Trunk run is red |
| `pr_refresh_certification.py` | pure-process | Refresh mechanics |
| `pr_refresh_replay.py` | pure-process | Refresh mechanics |
| `post_merge_signature.py` | pure-process | Post-merge mechanics |
| `local_validation.py` | pure-process | Shared test-lock mechanics |
| `phase2_metrics.py` | pure-process | Legacy metrics mechanics |
| `trunk_duration_trend.py` | tooling | Trunk job p50 comparison, run-timestamp windows, API-call budget and the single trend issue |
| `bundled_binary_manifest.py` | product-safety | Deterministic hashes of the shipped generator tree |
| `verify_distribution_binaries.py` | product-safety | Distribution membership, archive safety, and source-bound generator hashes |
| Remaining scripts (ledger-catch-all: scripts/) | product-safety | Benchmark, corpus, and validation product code; reclassify individually before any deletion |
| `tpcds_divergence_report.py` | product-safety | Cause labels for DataFrame-versus-SQL divergences (report only, no verdict) |
| `tpcds_platform_identity.py` | product-safety | Cross-platform agreement of the bundled TPC-DS generators |

### `_project/scripts/`

| File | Classification | Property or reason |
| --- | --- | --- |
| `detect_self_binding.py` | product-safety | SQL self-binding lint (KEEP trap) |
| `rp_scoped_check.py` | product-safety | read_primitives cross-surface check (KEEP trap) |
| `timing_audit.py` | product-safety | Timing audit (KEEP trap) |
| `timing_policy_check.py` | product-safety | Monotonic-clock policy (KEEP trap) |
| `fast_lane_ceiling_check.py` | pure-process | Fast-lane marker and path guards; retires with the fast lane |
| `soundness_paths.py` | product-safety | Soundness path manifest |
| `oracle_review_check.py` | product-safety | Oracle review or stand-in approval check for soundness-path changes; the attester account is the one local automation also uses |
| `oracle_reviewers/cli.py` | product-safety | Shadow review entry point; its package holds the classifier, reviewer selection, absence classification and verdict validation |
| `ruleset_review_enforcement.py` | product-safety | Ruleset review enforcement |
| `soundness_drain_report.py` | pure-process | Drain digest mechanics |
| `soundness_merge_digest.py` | product-safety | Post-merge soundness review digest |
| `reference_usage_audit.py` | tooling | Reference hygiene |
| `agent_instruction_audit.py` | tooling | Agent instruction lockstep |
| `worktree_audit.py` | tooling | Worktree hygiene |
| `todo_state_contract_check.py` | tooling | Tracker state contract |
| `check_uv_lock_revision.py` | tooling | Lockfile hygiene |
| `pr_review_followups.py` | pure-process | PR-loop mechanics |
| `preflight_targets.py` | pure-process | Changed-file lint and test selection for the local preflight |
| `dev_loop_pr_metrics.py` | pure-process | Program baseline metrics mechanics |
| Remaining project scripts (ledger-catch-all: _project/scripts/) | product-safety | Sweep, corpus, and validation product code; reclassify individually before any deletion |
| `_project/scripts/explorer_pipeline/browser_fixtures.py` | product-safety | Browser fixture queries for the site-inputs bundle |

### `.pre-commit-config.yaml` hooks

| Hook | Classification | Property or reason |
| --- | --- | --- |
| `corpus-inventory` | product-safety | Corpus inventory integrity |
| `uv-lock-revision-guard` | tooling | Lockfile hygiene |
| `uat-loc-table` | tooling | UAT table hygiene |
| `timing-policy-check` | product-safety | Monotonic-clock policy |
| `comment-policy` | tooling | Comments, docstrings, parser coverage and whole-tree enforcement |
| `windows-antipatterns` | tooling | Windows antipattern ratchet (WA004-WA007): `/dev/null`, POSIX-only imports, `env=` without `SYSTEMROOT`, git `text=True` without `encoding=` |
| `audit-raw` | tooling | Dependency inventory drift against `dependency-audit-raw.md` |
| `timing-policy-fast-lane` | pure-process | Fast-lane mechanics |
| `pr-preflight` | pure-process | PR-loop mechanics |
| `blind-spot-validate` | product-safety | Blind-spot coverage |
| `explorer-tokens` | product-safety | Explorer token integrity |
| `duplicate-code-warn` | tooling | Hygiene |
| `agent-instructions-check` | tooling | Agent instruction lockstep |
| `agent-write-preflight` | tooling | Worktree safety |
| `untracked-handoffs` | tooling | Handoffs stay out of the repository |
| `agent-git-identity` | tooling | Identity pinning |
| `agent-attribution-trailers` | tooling | Attribution hygiene |
| `ruff-check`, `ruff-format` | tooling | Lint and format |
| `codespell` | tooling | Spelling |
| Standard hooks (`check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `check-added-large-files`, `check-merge-conflict`, `detect-private-key`, `markdownlint`) | tooling | Standard hygiene |
