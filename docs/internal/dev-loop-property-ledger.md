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

Risk classes for the safety properties below:

- `HIGH-RISK` — a failure lets an unsafe change merge or ships a bad artifact: merge safety and required contexts,
  release integrity (including the published corpus and its privacy), commit identity and attribution, the
  soundness hold, or the reviewer gate. Each row needs evidence of both the pass and the refuse behavior, named as
  `test file::test`, a workflow run, or a canary pull request, or it names a blocker.
- `PROCESS-ONLY` — a failure costs a rerun or degrades a report or page and cannot, alone, merge an unreviewed or
  unsound change or publish a bad release. Each row carries a one-line waiver reason.

Status values: `proven` (pass and refuse evidence named), `blocked: <name>` (a named blocker below), `waived`.

Evidence is bound to develop commit `db5cff9be33e61c9c329e90828199d0fd126a29e`: test names exist in that tree,
and the Trunk run 40599245390 and site-inputs run 40602249886 are runs at that commit. Test evidence proves the
behavior of the checked-in code, not the live ruleset. A `release-v2.yml` run is evidence for the tagged commit it
names. Tests marked "site" are in `BenchBox-dev/benchbox-site` at commit
`92c910f3d6a3446e0b5c36778924042fe0ac00ab`; its CI run 42278553403 ran every `deploy/test/*.test.ts` file in the
`npm run test:deploy` step (113 passed, 0 failed, 3 skipped, none of them cited here).

Release-canary freshness evidence added 2026-11-08:

The former `release-canary-freshness` blocker is closed by the merged #4040
implementation, the retained publish-free probe, and a real authenticated freshness
check against source `43cb9728a19523c1aeae18c9cfbc695522682cab`, as recorded in
the release-readiness row. The probe uses a synthetic canary; the separate CLI
check reads the real run and its retained summary artifact. Neither publishes.
The freshness decision is time-dependent and must pass again at every release.
The real CLI also refused the historical baseline
`64f2f1f603083fc3f6b9c3b13d12ce73f97e6fd3` with exit 1 and a named
`non-ancestor` reason: none of its newest 30 completed canaries tested an
ancestor of that target. The API and retained artifacts were not substituted.

The required CI units are five: `core`, `explorer`, `results-data`, `docs` and `tooling` (`UNITS` in
`scripts/ci_units.py`). The `hold` check, the App-bound `review-gate` status and the other controls are separate
required contexts, not units. The earlier six-unit count included `landing`, which no `ci.yml` unit defines.

Owner decisions of 2026-10-27:

- The per-test 0.5 s duration requirement (dlv2-13) is superseded by the aggregate fast-duration gate:
  `scripts/build_fast_duration_baseline.py`, `fast-duration-baseline-refresh.yml`, the `verify-fast-duration`
  verdict and `tests/fixtures/fast_tier_baseline`. Evidence is PR #3625 and the "Fast-test duration policy" section
  of `docs/internal/nightly-t3.md`. `tests/fixtures/test_durations.json` and the `duration_exempt` marker are removed.
- The browser check is ported to the site repository (`dlv2-site-browser-check`) and the Explorer-token lint is
  retired. The `t3:browser` check (Explorer Firefox and WebKit smoke) is now
  `.github/workflows/nightly-browser.yml` in `BenchBox-dev/benchbox-site` (benchbox-site#73), which runs daily at
  05:43 UTC and reports failures to a site-local issue labelled `t3:browser`; its dispatch run 40609640737 passed on
  both browsers. Core `nightly-v2.yml` has no browser job.
- GizmoSQL stays in the release rehearsal candidate.
- The `release` branch and its ruleset were deleted by the owner on 2026-11-07, after the first publication on the new
  release path; no agent deleted them.
- The merge-queue and auto-revert branch namespaces were purged on 2026-10-27: 105 remote branches were deleted
  (6 `gh-readonly-queue`, 8 `auto-revert`, 46 closed-PR `auto/results-mirror`, 45 other closed or merged pull
  request branches), with their tips archived in the tracker. The `release`, `develop`, `published-results` and
  `todo-state` branches, open-pull-request branches and the `no-auto-merge` and `incident:release-canary-red`
  labels were not touched.

## Safety properties

| Property | Risk | Current guard | Replacement guard | Proof | Status |
| --- | --- | --- | --- | --- | --- |
| Correctness oracle: digest arming | HIGH-RISK | `TestCorrectnessGateOracle` in `tests/unit/test_standardized_test_commands.py`, `correctness-gate` in `ci.yml` | ci.yml `core` on heavy carve-outs; trunk correctness gate on develop pushes | pass: `tests/unit/test_standardized_test_commands.py::TestCorrectnessGateOracle::test_strict_mode_passes_when_all_configured_queries_evaluate`, Trunk run 40599245390 jobs `correctness-gate (a)` and `(b)` success at `db5cff9be`; refuse: `TestCorrectnessGateOracle::test_strict_mode_fails_on_value_digest_mismatch`, `TestCorrectnessGateOracle::test_strict_mode_fails_when_value_digest_missing` | proven |
| Correctness oracle: query discrimination | HIGH-RISK | `TestCorrectnessGateOracle` in `tests/unit/test_standardized_test_commands.py` | ci.yml `core` on heavy carve-outs; trunk correctness gate | pass: `TestCorrectnessGateOracle::test_gate_query_set_is_answer_backed_and_discriminating`, `TestCorrectnessGateOracle::test_gate_excludes_dbgen_unstable_threshold_queries`; refuse: `TestCorrectnessGateOracle::test_strict_mode_fails_on_benchmark_drift`, `TestCorrectnessGateOracle::test_strict_mode_fails_on_scale_drift` | proven |
| Correctness oracle: no-skip | HIGH-RISK | `TestCorrectnessGateOracle` in `tests/unit/test_standardized_test_commands.py` | ci.yml `core` on heavy carve-outs; trunk correctness gate | pass: Trunk run 40599245390 `correctness-gate (a)` and `(b)` success at `db5cff9be`; refuse: `TestCorrectnessGateOracle::test_gate_target_enforces_no_skip` | proven |
| Required local-engine cases | HIGH-RISK | `scripts/required_case_evidence.py`, `make test-required-local-cases` | ci.yml `core` on heavy carve-outs and trunk required-local-cases; skipped, deselected, failing and expected-failing required cases fail the check | pass: `tests/unit/scripts/test_required_case_evidence.py::test_a_run_that_passes_every_required_case_is_accepted`, Trunk run 40599245390 job `required-local-cases` success at `db5cff9be`; refuse: `test_a_required_case_that_skips_fails_or_is_expected_to_fail_is_rejected`, `test_a_deselected_required_case_is_rejected`, `test_a_missing_required_case_is_rejected` | proven |
| SQL self-binding lint | HIGH-RISK | `_project/scripts/detect_self_binding.py` | ci.yml `tooling` | pass: `tests/unit/test_self_binding_detector.py::test_all_current_tpchavoc_variants_are_clean`, `test_fully_qualified_correlation_is_not_flagged`; refuse: `test_detector_flags_reverted_pr756_fix`, `test_dml_correlated_self_bind_is_flagged` | proven |
| Monotonic-clock policy (wall-clock part) | HIGH-RISK | `_project/scripts/timing_policy_check.py`, `_project/scripts/timing_audit.py` | ci.yml `tooling` | pass: `tests/unit/scripts/test_timing_policy_check.py::test_monotonic_clock_use_is_not_a_violation`, `test_allowlisted_wall_clock_duration_passes_strict`; refuse: `test_strict_fails_on_a_non_allowlisted_wall_clock_duration` | proven |
| Submission validation | HIGH-RISK | `scripts/validate_submission.py`, `.github/workflows/validate-submission.yml`, `.github/workflows/validate-submission-comment.yml`, `tests/unit/workflows/test_validate_submission_changed_bundles.py`, `tests/unit/workflows/test_validate_submission_comment_security.py`, `tests/unit/scripts/test_validate_submission*.py` | ci.yml `results-data` | pass: `tests/unit/scripts/test_validate_submission.py::test_valid_funding_ok`, `test_checked_in_corpus_satisfies_public_integrity_policy`; refuse: `test_invalid_source_rejected`, `test_self_asserted_vendor_outside_vendor_subtree_rejected`, `test_public_private_path_is_rejected_by_cli_boundary`; workflow: `tests/unit/workflows/test_validate_submission_fail_open.py::test_failing_validator_fails_the_step`, `test_passing_validator_still_passes_the_step`, `tests/unit/workflows/test_validate_submission_trusted_checkout.py::test_checkout_uses_trusted_base_sha` | proven |
| Corpus trust boundary | HIGH-RISK | `tests/unit/workflows/test_corpus_trust_boundary.py`, `.github/workflows/sync-results-data-to-published.yml` | ci.yml `results-data` | pass: `tests/unit/workflows/test_corpus_trust_boundary.py::test_corpus_trust_boundary`; refuse: `tests/unit/workflows/test_validate_submission_trusted_checkout.py::test_trigger_is_pull_request_target_not_pull_request`, `test_validator_dispatch_is_trusted_base_with_merge_payload`, `tests/unit/workflows/test_validate_submission_comment_security.py::test_comment_target_pr_is_not_read_from_the_untrusted_artifact` | proven |
| Artifact privacy (no private paths in published site) | HIGH-RISK | `scripts/publication/check_artifact_privacy.py` attestation in the `site-inputs.yml` bundle build, `tests/unit/scripts/test_corpus_privacy_invariant.py` | `BenchBox-dev/benchbox-site` deploy gates (`privacy`) | pass: `tests/unit/scripts/test_corpus_privacy_invariant.py::test_no_corpus_file_exposes_a_private_path`, `test_anonymized_bundle_passes_the_export_flag`, site-inputs run 40602249886 success at `db5cff9be`; site `deploy/test/gates.test.ts::privacy: secrets are found and documented placeholders pass`, `privacy gate scans the artifact's text files`; refuse: `test_detector_still_flags_a_planted_leak`, `test_detector_still_flags_a_leak_encoded_as_an_object_key`, `test_unanonymized_bundle_fails_the_export_flag`; site `deploy/test/rollback.test.ts::a rollback still refuses an artifact carrying a secret` | proven |
| Publication rollback and transactions | HIGH-RISK | `BenchBox-dev/benchbox-site` deploy `rollback` mode, which restores the artifact of a recorded receipt, and the receipt each deploy writes | `BenchBox-dev/benchbox-site` deploy probes, which dispatch at most one rollback to the last known good receipt | pass: site `deploy/test/rollback.test.ts::passing probes never roll back`, `a failed deploy rolls back to the last known good receipt`; site `deploy/test/receipt.test.ts::receipt bytes are canonical and their sha256 is stable`, `only receipts with green gates and probes are last known good`; refuse: site `deploy/test/rollback.test.ts::a failing rollback never triggers another rollback`, `a rollback target must be a recorded, last known good receipt of that run`, `with no green receipt yet, a failed deploy opens an issue instead of rolling back`; site `deploy/test/receipt.test.ts::invalid receipts are rejected` | proven |
| Corpus promotion (privacy, bijection and Explorer compatibility of the published corpus) | HIGH-RISK | `privacy`, `corpus_bijection`, `explorer_compat` and `snapshot_invariants` attestations recorded by `scripts/site_inputs.py` in the `site-inputs.yml` bundle build, `tests/unit/scripts/test_site_inputs.py` | `BenchBox-dev/benchbox-site` deploy gates, which refuse a bundle whose attestations or digests do not verify | pass: `tests/unit/scripts/test_site_inputs.py::test_verify_accepts_consistent_bundle`, site-inputs run 40602249886 success at `db5cff9be`; site `deploy/test/resolve.test.ts::an ahead or identical bundle is fresh and no deployed receipt skips the check`; refuse: `test_verify_rejects_failed_attestation`, `test_verify_rejects_tampered_member`, `test_verify_rejects_missing_attestation`, `test_verify_rejects_snapshot_digest_mismatch`; site `deploy/test/resolve.test.ts::an unverifiable bundle is refused`, `a diverged bundle is refused with its own failure class` | proven |
| Explorer snapshot and UI compatibility | PROCESS-ONLY | `explorer_compat` and `snapshot_invariants` attestations in the `site-inputs.yml` bundle build, `tests/unit/scripts/test_results_explorer_snapshot_invariants.py` | `BenchBox-dev/benchbox-site` deploy gates (`explorer_compat`) | waiver: guards Explorer display compatibility; a break degrades a page, not merge safety, release integrity, identity or soundness, and the attestation stays in the bundle build | waived |
| Public-site visual acceptance | PROCESS-ONLY | The Visual comparison workflow in `BenchBox-dev/benchbox-site` | The site repository screenshots a pull request's build and the deployed artifact and reports the differences; it is advisory and not part of that repository's `ci`. The deploy's route and digest probes prove availability and identity, not layout | waiver: advisory by design and not part of any required check | waived |
| Binary integrity (vendored engine hashes, dbgen framing) | HIGH-RISK | `_binaries/` hashes, TPC-H binary framing tests | ci.yml `core` hash checks and required macOS/Windows dbgen framing; nightly-v2.yml matrix dbgen framing on all three OS at Python 3.12 | pass: `tests/unit/utils/test_binary_manifest.py::test_shipped_manifest_covers_the_real_package`, `tests/unit/core/tpch/test_tpch_dbgen_framing_binaries.py::test_all_runnable_bundled_binaries_agree`, Trunk run 40599245390 job `dist-artifact` success at `db5cff9be`; refuse: `test_binary_manifest.py::test_tree_rejects_membership_and_byte_changes`, `test_tpch_dbgen_framing_binaries.py::test_host_binary_has_no_trailing_delimiter` | proven |
| Wheel installability | HIGH-RISK | `package-smoke` job, release workflow build | release workflow artifact verification | pass: `tests/unit/workflows/test_release_v2_workflow.py::test_install_matrix_installs_the_staged_wheel_not_the_checkout`, `test_workflow_covers_the_twelve_cell_install_matrix`; `release-v2.yml` run 42039780363 (attempt 2, `v0.5.0rc5` at `df8a31fd3`): `admit`, all twelve install cells, the release UAT gate and `publish to test-pypi` success; run 42133357291 (`v0.5.0` at `dfe585e09`): `admit`, all twelve install cells and `publish to pypi` success; refuse: `test_publish_needs_every_gate_job`, `tests/unit/scripts/test_release_admitted_dist.py::test_refuses_altered_distribution_bytes`; `release-v2.yml` run 40711857367 (dispatch with tag `v0.5.0-rc.1`) failed `admit` at `Validate tag shape` and skipped the install matrix, release UAT and publish | proven |
| Shipped bundled generator integrity | HIGH-RISK | `benchbox/_binaries/SHA256MANIFEST.json`, `tests/unit/utils/test_binary_manifest.py` | trunk.yml `dist-artifact` verifies source, wheel, and sdist membership and hashes on each push to develop; installed-package verifier for release checks | pass: `tests/unit/utils/test_binary_manifest.py::test_shipped_manifest_covers_the_real_package`, Trunk run 40599245390 job `dist-artifact` success at `db5cff9be`; refuse: `test_tree_rejects_membership_and_byte_changes`, `test_distribution_uses_trusted_source_manifest`, `test_archive_rejects_alias_overwrites_and_nonregular_members` | proven |
| Dependency bounds | PROCESS-ONLY | `scripts/check_dependency_bounds.py`, `tests/unit/scripts/test_check_dependency_bounds.py` | ci.yml `tooling` | waiver: reports caps approaching their ceiling; a miss delays a dependency bump and cannot merge unsound code or publish a bad release | waived |
| Release readiness | HIGH-RISK | `.github/workflows/release-canary.yml`, `scripts/release_flow.py`, `scripts/release_canary_freshness.py`, `scripts/release_reuse_uat_evidence.py`, `tests/unit/scripts/test_release_flow.py` | ci.yml always-required `ci-paths` selects release identity changes and checks them against the immutable event base; `release-v2.yml` admission, install matrix, release UAT gate and publish-time tag re-check. `admit` refuses every tag unless the newest completed `release-canary.yml` run that tested an ancestor of the tag commit is green and started at most 48 hours earlier (`scripts/release_canary_freshness.py`), on the evidence-reuse path and the full-UAT path alike. `uat-reuse` reuses the newest qualifying trunk UAT run, or a final tag's release-candidate run, and `release-uat` re-verifies the reused evidence against the admitted digest; the full UAT stages run when nothing qualifies. A maintenance-line tag cannot pass the canary check yet, because the canary tests `develop` only | pass: `release-v2.yml` run 42039780363 admitted, installed, gated and published `v0.5.0rc5` to TestPyPI, and run 42133357291 did the same for `v0.5.0` on PyPI, with hashes equal to the admitted `SHA256SUMS` and attestations verified; `tests/unit/scripts/test_release_canary_freshness.py::test_a_green_canary_on_an_ancestor_within_48_hours_passes`; `tests/unit/scripts/test_release_reuse_uat_evidence.py::test_decide_reuses_the_newest_qualifying_trunk_run`; refuse: `tests/unit/scripts/test_release_flow.py`, `tests/unit/workflows/test_release_v2_workflow.py::test_workflow_never_falls_back_to_an_older_run`, invalid-tag dispatch run 41949365404 refused in admit; `tests/unit/scripts/test_release_canary_freshness.py::test_a_red_newest_canary_on_an_ancestor_refuses_even_when_an_older_run_was_green`, `::test_a_canary_exactly_48_hours_old_passes_and_one_minute_older_is_stale`, `::test_a_canary_that_tested_a_non_ancestor_commit_does_not_count`, `::test_no_canary_run_at_all_is_missing`; `tests/unit/scripts/test_release_reuse_uat_evidence.py::test_decide_names_the_reason_each_trunk_run_is_disqualified_and_runs_the_full_uat` (14 reasons) and `::test_verify_gate_rejects_every_retry_record_except_a_passed_second_attempt`; wiring: `tests/unit/workflows/test_release_v2_workflow.py::test_admit_refuses_a_release_without_a_fresh_green_canary_before_admitting_artifacts`, `tests/unit/workflows/test_release_v2_workflow_reuse_step.py::test_the_decide_step_command_runs_under_the_real_uv_and_reaches_the_decision`; live: merged implementation #4040; publish-free reuse probe run 42487650248 at `03b0711a4c8ac2ac61afad5c7311ba84ab10b7e9` passed its synthetic canary step and publish dry run (real tag admission and publishing skipped); the real read-only freshness CLI at `43cb9728a19523c1aeae18c9cfbc695522682cab` passed against authenticated API and artifact data for canary run 42491171323, which tested that commit and started 2026-11-08T14:43:05Z, 0.9 h before the check. These close the freshness blocker, not the separate UAT scheduling and guarded-path reuse acceptance items | proven |
| Ruleset and settings drift | HIGH-RISK | `scripts/ruleset_drift_check.py`, `tests/unit/release/test_ruleset_drift_review_coverage.py`, `tests/unit/release/test_ruleset_review_enforcement.py` | Advisory nightly and manual verification; enforced release-canary and release-v2 admission; thread-resolution and tag-protection predicates retained | pass: `tests/unit/release/test_ruleset_drift_review_coverage.py::test_develop_drift_passes_when_the_app_bound_gate_is_required_with_or_without_the_legacy_gate`, `tests/unit/release/test_ruleset_review_enforcement.py::test_enforced_ruleset_passes`; refuse: `test_the_legacy_gate_alone_is_a_blocking_drift`, `test_missing_gate_contexts_are_blocking_despite_stale_expected_contexts`, `test_unenforced_thread_resolution_fails`, `test_review_gate_without_the_review_app_binding_fails`, `test_missing_review_gate_fails` | proven |
| Soundness-path review | HIGH-RISK | `.github/soundness-paths.txt`, `_project/scripts/soundness_paths.py` | Required App-bound review-gate on the exact head, required thread resolution and post-merge soundness digest | pass: `tests/unit/scripts/test_soundness_manifest.py::test_soundness_predicate_matches_review_required_paths` (the live `.github/soundness-paths.txt` through the vendored matcher), `test_soundness_predicate_ignores_fast_default_paths`, `tests/unit/scripts/test_theseus_lock.py::test_the_vendored_matcher_is_the_copy_the_lock_pins`, `tests/unit/scripts/test_soundness_merge_digest.py::test_a_commit_is_judged_by_the_manifest_of_its_first_parent`; refuse: `tests/unit/workflows/test_oracle_gate_workflow.py::test_only_the_theseus_caller_jobs_can_post_the_gate_context`, `test_soundness_merge_digest.py::test_a_commit_that_weakens_the_predicate_cannot_hide_an_earlier_match`, `test_a_commit_that_removes_a_rule_and_edits_the_path_it_covered_is_still_flagged`, `test_theseus_lock.py::test_each_caller_workflow_is_pinned_to_the_locked_commit_in_both_places`; engine evidence lives in Theseus (`joeharris76/theseus`): `tests/unit/test_soundness_paths.py::test_soundness_predicate_matches_review_required_paths`, `tests/unit/test_review_check.py`, `tests/unit/test_gate.py`, `tests/unit/test_matcher_isolated.py` | proven here for the vendored matcher, caller pins and digest; the gate and review decisions are proven in Theseus |
| Corpus-to-artifact bijection | HIGH-RISK | `scripts/publication/check_corpus_bijection.py` with `publication/ledger-seed.json` | In-bundle `corpus_bijection` runs the former site deploy gate's form at the bundle commit, with the ledger seed; the bundle build refuses to run without the seed, so the seed stays until its dispositions move elsewhere. It proves snapshot-to-tree consistency only. Cross-commit trust chains through contiguous `validator_parity` ranges (parent = newest unexpired develop bundle that is an ancestor; the build fails without one unless a dispatch names the parent), the `certified_by` trunk run, and the bundle resolve in the `BenchBox-dev/benchbox-site` deploy; `explorer_compat` and `snapshot_invariants` run on every bundle, and `corpus_validation` runs `results-data/validate_corpus.py` over the whole corpus on every bundle, so a validator-only change is checked against existing bundles before the next deploy | pass: `tests/unit/scripts/publication/test_check_corpus_bijection.py::test_check_bijection_exact_pass`, site-inputs run 40602249886 success at `db5cff9be`; site `deploy/test/resolve.test.ts::an ahead or identical bundle is fresh and no deployed receipt skips the check`; refuse: `test_check_bijection_reports_unaccounted_skip`, `test_main_expect_source_rejects_moved_ref`; site `deploy/test/resolve.test.ts::a changed corpus needs validator parity from the deployed commit`, `a diverged bundle is refused with its own failure class` | proven |
| Durable auto-merge hold | HIGH-RISK | `no-auto-merge` label and draft status, `.github/workflows/pr-hold.yml` | Exact-head arming refuses holds; the required `hold` check fails while the label is present, so an armed or raw-merged PR cannot land | pass: `tests/unit/workflows/test_pr_hold_workflow.py::test_hold_step_fails_only_while_the_label_is_present[false-0]`; refuse: `test_hold_step_fails_only_while_the_label_is_present[true-1]`, `tests/unit/scripts/test_pr_arm.py::test_a_hold_label_wins_even_when_everything_else_is_fine`; context: `test_pr_hold_workflow.py::test_hold_job_reports_under_the_required_context_name`, `test_runbook_requires_the_hold_context` | proven |
| Workflow context validity | HIGH-RISK | `tests/unit/workflows/test_workflow_expression_contexts.py` | ci.yml `tooling` | pass: `tests/unit/workflows/test_workflow_expression_contexts.py::test_expressions_name_known_contexts`, `test_job_level_env_uses_only_available_contexts`; refuse: `test_detector_flags_the_known_failure_shapes` | proven |
| Combined-tree validation before merge (retired) | HIGH-RISK | Former merge-group required checks | Required PR checks, then exact-commit trunk validation; composition failures are detected after merge and handled by revert | pass: `tests/unit/scripts/test_ci_unit_result.py::test_untouched_unit_passes_when_all_jobs_skipped`, Trunk run 40599245390 success at `db5cff9be`; refuse: `test_ci_unit_result.py::test_required_job_skipped_is_a_failure`, `test_ci_units.py::test_unowned_path_fails_closed_to_core`, `tests/unit/scripts/test_trunk_red_disarm.py::test_red_streak_selects_armed_ordinary_pr`, `test_trunk_revert.py::test_refuses_missing_run_evidence_before_mutating`; end to end: #3679 passed every required PR check alone, then Trunk run 40796288417 at `4ee345105` failed `medium-test (shard 1)` in `tests/integration/review/test_golden_outputs.py`; `make trunk-revert` opened #3707, which reverted only #3679, and Trunk run 40808628795 at `5efcbc10d` passed with the later merges kept. The replacement detects a composition failure after merge; nothing refuses one before merge. Later composition failures (Trunk runs 41166847029 and 41273143829) were repaired forward rather than reverted | proven for the after-merge replacement; the pre-merge refusal is retired |
| read_primitives cross-surface check | PROCESS-ONLY | `_project/scripts/rp_scoped_check.py` | nightly T3 per-domain suites | waiver: nightly cross-surface consistency check on one benchmark; failure surfaces in nightly, outside merge and release paths | waived |
| Published compatibility, query and tuning-support pages match the exported facts (retired) | PROCESS-ONLY | `scripts/generate_compat_docs.py`, `scripts/generate_query_docs.py`, `scripts/generate_tuning_support_matrix.py` | The pages are rendered in `BenchBox-dev/benchbox-site` from the exported documentation data. Core keeps `scripts/export_docs_data.py`, whose query set, counts and SQL stability across catalog resets are asserted in `tests/unit/scripts/test_export_docs_data.py`, and the documentation-data schema checked in the site-inputs bundle; `make compat-inventory-check` keeps the DDL drift guard. The `can_reach_applied_verified` derivation stays in `scripts/docs_data_sources.py`, locked by `test_only_introspected_platforms_can_reach_applied_verified` in `tests/unit/docs/test_tuning_platform_support_matrix.py` and registered in `quality/doc-lock-tests.json` | waiver: generated documentation pages retired from core; correctness of the underlying facts stays covered by the exported-data tests | waived |
| Links in published pages resolve (retired) | PROCESS-ONLY | `scripts/check_doc_relative_links.py`, `scripts/check_doc_external_links.py` | The scanned pages now live in `BenchBox-dev/benchbox-site`, which checks relative links in its documentation build and runs a nightly crawl of the published site with fragment checking. `scripts/check_doc_anchors.py`, run by `make docs-validate` and the `docs` unit, gates relative links and fragments among the Markdown documents retained in core: a relative link must name a tracked file or directory, and a fragment into tracked Markdown must match a heading slug or explicit anchor. It does not inspect sources under `_project/decisions/`, `_project/specs/`, `_project/audits/` (archival records that describe the tree when they were written) or `_project/todo-db-export/` (generated tracker export); the list is `EXCLUDED_SOURCE_ROOTS` in the script, with a reason per entry. Core performs no external link checking: the retained documents are maintainer-only, an external check needs network, and the 76 external links in `docs/` and `README.md` carry no fragments | waiver: link rot in documents costs a rerun or a degraded page and cannot alone merge an unsound change; the check stays enforced by `make docs-validate` and is covered by `tests/unit/scripts/test_check_doc_anchors.py` | waived |
| Site-owned documentation is absent from core (replaces the documentation freeze) | PROCESS-ONLY | `scripts/check_docs_freeze.py` | `scripts/check_docs_ownership.py`, run by `make docs-validate`: every tracked document must be core-owned, and every site-owned path and its generator must be absent | waiver: documentation ownership layout hygiene, enforced by `make docs-validate` | waived |
| Commit identity and attribution | HIGH-RISK | `scripts/check_agent_trailers.sh`, `_project/scripts/agent_instruction_audit.py`, hooks `agent-git-identity` and `agent-attribution-trailers` | Same hooks and `make agent-identity-check`; unchanged | pass: `tests/unit/test_check_agent_trailers.py::test_accepts_human_attribution`, `tests/unit/scripts/test_agent_instruction_audit.py::test_repository_candidate_passes`; refuse: `test_check_agent_trailers.py::test_rejects_agent_attribution`, `test_agent_instruction_audit.py::test_imposed_identity_fails` | proven |

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

The repository maintainer is accountable for every workflow listed here.
Release execution belongs to the release manager; corpus operations belong
to the corpus maintainer; review-App credentials and OMP pin evidence belong
to the repository owner. Nightly failures go first to the failing-area owner,
with maintainer and release-manager escalation as described in
`nightly-t3.md`. These are operational roles, not CODEOWNERS assignments.

| File | Classification | Property or reason |
| --- | --- | --- |
| `batch-loop-e2e.yml` | tooling | Runs the disposable-repository batch landing loop end to end on pull requests that touch the loop's scripts or fixture inputs, nightly and on demand; read-only permissions, no credentials, and `tests/unit/workflows/test_batch_loop_e2e_workflow.py` keeps the trigger paths covering every file the fixture copies |
| `ci-text-edit-refresh.yml` | product-safety | When a full CI run completes on a pull request head, re-runs the failed jobs of the newest `CI (text edit)` run on that head so a title or body edit does not hold runners while it waits |
| `ci.yml` | product-safety | Pull-request path selection, fast and medium tests, correctness and required-case evidence, lint and required five-unit aggregation |
| `comment-policy-nightly.yml` | tooling | Nightly and on-demand whole-tree comment-policy scan of develop; the backstop for files a pull request leaves unchanged |
| `corpus-drift-check.yml` | product-safety | Corpus drift detection |
| `corpus-event-bridge.yml` | product-safety | Corpus event integrity |
| `corpus-reconciler.yml` | product-safety | Corpus reconciliation |
| `cross-surface-baseline-autodetect.yml` | tooling | Baseline maintenance support |
| `docker-integration.yml` | product-safety | Container integration coverage |
| `extension-smoke.yml` | product-safety | Extension smoke coverage |
| `gitignore-lint.yml` | tooling | Hygiene; standalone, not part of the ci.yml units |
| `green-unmerged-sweep.yml` | tooling | Daily sweep that upserts the green-but-unmerged PR digest issue; never enables auto-merge |
| `mirror-ci-images.yml` | product-safety | Seeds digest-pinned images in GHCR only when absent; scheduled CI checks mirrors by digest without routine Docker Hub reads |
| `nightly-v2.yml` | product-safety | Nightly T3: platform matrix, docker engines, extension, install, DuckDB and CedarDB throughput UAT with the throughput commit status published through the `t3:throughput` domain, and drift with selection failures reported through `t3:drift`; `t3:sql-execution-gate-obt`, `t3:parity`, `t3:integration` and `t3:mirror-postgres17` are reported domains. It replaced the retired Nightly Validation and Cross-Platform Validation workflows. The browser check is owned by the site repository and has no domain here (`docs/internal/nightly-t3.md` lists every domain) |
| `omp-pin-bump.yml` | tooling | Scheduled omp pin bump: a read-only job builds and proves the candidate, and a separate job holding the write token opens the bump pull request, which `review-gate` holds until the owner posts the Mac evidence line |
| `omp-pin-watch.yml` | tooling | Scheduled comparison of the first reviews after an omp pin bump with the previous week, with a read-only job that keeps the attempt ledger and a separate job holding the write token that opens a revert pull request, which `review-gate` holds until the owner posts the Mac evidence line for the restored pin |
| `review-gate.yml` | product-safety | Required gate from trusted code: result-affecting changes need a success review of the head from the `benchbox-oracle` App, whose Bot identity only the owner controls, or a stand-in approval comment from an attester account posted after the reviewer's latest review; that account is also the one local automation uses, so the stand-in records who vouched, not that a human read it. Posted as `review-gate` for the head from develop code with the App token on stand-in comments, head and base changes and the scheduled sweep, and one of two in-tree writers (the other is the verdict publish step); every head change gets a pending `review-gate` before evaluation so a push back to an earlier green head cannot keep a stale green. Open review threads are enforced by the ruleset's required thread resolution, not by this gate; the required check is scoped to the reviewer App in the ruleset |
| `review-request.yml` | product-safety | Review-event notification, not a check: a no-op job on review edited and dismissed events only, whose completion makes `review-gate` re-evaluate that head |
| `review-verdict.yml` | product-safety | Self-hosted soundness review: reviewers chosen by tier and availability in one review job, each holding only the gateway client token, and a review and non-required `review-verdict` status posted by the owner's App, followed in the same publish step by the required `review-gate` status for the exact head, evaluated from the trusted develop checkout with the same decision the gate workflow uses and posted with the App token |
| `pr-hold.yml` | product-safety | Required `hold` check: fails while the `no-auto-merge` label is present in the PR's live labels, on every label change and on every re-run |
| `pricing-data-drift-check.yml` | product-safety | Pricing data integrity |
| `release-v2.yml` | product-safety | Tag-triggered publication of the admitted trunk artifacts: annotated tag, latest-run admission, install matrix, reuse of qualifying trunk or release-candidate UAT evidence re-verified against the admitted digest (full UAT only as the fallback), a fresh green release canary on an ancestor of the tag for every tag, publish-time tag re-check, attestation; a publish-free probe mode runs the same job graph without a tag |
| `release-v2-probe.yml` | product-safety | Pull-request check that dispatches both `release-v2.yml` probe variants (reuse and fallback) from the branch when a release workflow input changed, and fails unless both succeed |
| `release-canary.yml` | product-safety | Release canary protection |
| `seed-corpus.yml` | product-safety | Corpus seeding |
| `site-inputs.yml` | product-safety | Versioned site-inputs bundle build, attestation, artifact upload and site dispatch |
| `soundness-merge-digest.yml` | product-safety | Post-merge soundness review digest |
| `sqlglot-delivery.yml` | product-safety | Daily SQLGlot delivery consumer: a read-only job plans and prepares the adoption from the fork's verified delivery, and a separate job holding the App token publishes one automation pull request and arms it through the normal arming checks; gated by a kill switch and a dry-run default |
| `submission-validator-drift-check.yml` | product-safety | Submission validator sync |
| `sync-results-data-to-published.yml` | product-safety | Corpus trust boundary sync |
| `todo-state-validate.yml` | tooling | Tracker state validation |
| `trunk-coverage.yml` | product-safety | Scheduled (six a day) and dispatched instrumented four-shard medium tier, v2 coverage map combine, published-map self-fetch, and a map-staleness issue on its own daily cron; never consulted by arming |
| `trunk.yml` | product-safety | Develop-push fast lane, uninstrumented four-shard medium tier, correctness gate, required-local-cases, whole-tree comment-policy scan and verified release artifact; every develop push has its own run, which a later push never cancels or replaces |
| `trunk-uat.yml` | product-safety | Nightly (02:00 UTC) and dispatched release-gate UAT matrix: the release-v2 shard jobs run against the trunk dist of the newest releasable develop commit; scheduled runs skip when no guarded path changed since the last green executed run, defer while a release-v2 run is in progress, and fail when zero cells executed; 90-day shard evidence, gate summary and per-cell history bound to the source commit and dist digest |
| `trunk-red-disarm.yml` | product-safety | Completed develop Trunk run disables auto-merge on armed PRs the arm gate would refuse |
| `upload-answers.yml` | product-safety | Answer file publication |
| `trunk-duration-trend.yml` | tooling | Daily comparison of Trunk job p50 durations with the prior week; opens or closes one issue assigned to the repository owner; read-only actions access plus issue writes |
| `fast-duration-baseline-refresh.yml` | tooling | Weekly regeneration of the committed fast-tier duration baseline from successful merged-PR fast-junit artifacts; opens one pull request with contents and pull-requests write only, bounded logged API calls, and a medium-shard drift report |
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
| `test_omp_pin_bump_workflow.py` | tooling | The omp pin bump workflow splits a read-only build job from the write-token pull request job and checks out no credentials |
| `test_omp_pin_watch_workflow.py` | tooling | The omp pin watch workflow splits a read-only evaluation job from the write-token revert job, checks out no credentials and opens a revert only for an anomalous state and never twice for one version |
| `test_review_gate_workflow.py` | product-safety | Trusted triggers, develop-only checkout, single per-PR concurrency group, App-only posting and no other workflow posting the gate context |
| `test_review_workflow.py` | product-safety | The notification-only shape of the reviewer-review workflow: edited and dismissed review triggers, empty permissions, one `review-event` job, no checkout, secrets or tools |
| `test_review_verdict_workflow.py` | product-safety | Review triggers, guards, the single review job's step order and secret scoping, and App-only posting |
| `test_sqlglot_delivery_workflow.py` | product-safety | The delivery workflow's read-only credential-free preparation job, App token confined to the publish job, kill switch and dry-run gating, repository and branch guard, and pinned actions |
| `test_t2_partition_workflow.py` | product-safety | Complete medium selection, correctness gate conservation, and binary framing placement |
| `test_comment_policy_nightly_workflow.py` | tooling | Nightly comment-policy workflow triggers, develop checkout, read-only permissions, whole-tree launcher call and shared action pins |
| `test_trunk_coverage_workflow.py` | product-safety | Coverage workflow triggers, instrumented shards, map combine with self-fetch, schedule-only staleness issue |
| `test_trunk_workflow.py` | product-safety | Develop-push triggers, uninstrumented medium tier, whole-tree comment-policy scan, shared develop-push concurrency group with per-commit groups for other events, and read-only test permissions |
| `test_trunk_red_disarm_workflow.py` | product-safety | Red-trunk disarm trigger, develop-push filter, exact permissions, workflow token and full-history checkout |
| `test_published_results_base_ci.py` | product-safety | Published corpus CI |
| `test_release_canary_shard_evidence.py` | product-safety | Release canary evidence |
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
| `test_browser_fixtures_port.py` | product-safety | Fixture-query port byte-identity; the browser check moved to the site repository by the owner decision of 2026-10-27, so the row goes when the file is deleted |
| `test_build_joinorder_data.py` | product-safety |
| `test_check_api_contract_symbols.py` | product-safety |
| `test_check_complexity.py` | tooling |
| `test_check_dependency_bounds.py` | product-safety |
| `test_check_duplicate_code.py` | tooling |
| `test_check_make_targets.py` | tooling |
| `test_check_project_references.py` | product-safety |
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
| `test_host_lock.py` | pure-process |
| `test_migrate_clickhouse_labels.py` | product-safety |
| `test_mirror_partial_validation_policy.py` | pure-process |
| `test_path_filter_decision.py` | tooling |
| `test_preflight_targets.py` | pure-process |
| `test_perf_smoke_baseline.py` | pure-process |
| `test_pr_arm.py` | pure-process |
| `test_pr_ready_make.py` | pure-process |
| `test_pr_landing.py` | pure-process |
| `test_trunk_revert.py` | pure-process |
| `test_trunk_red_disarm.py` | pure-process |
| `test_pytest_shard_evidence.py` | product-safety | Real serial and distributed test selection and execution conservation |
| `test_required_case_evidence.py` | product-safety | A required local-engine case that skips, is deselected, fails or is expected to fail fails the required-case check |
| `test_pr_refresh_certification.py` | pure-process |
| `test_pr_review_followups.py` | pure-process |
| `test_reference_usage_audit.py` | tooling |
| `test_regenerate_correctness_gate_digests.py` | product-safety | TPC-H gate digests regenerate from qgen default parameters and record a null seed |
| `test_release_admitted_dist.py` | product-safety | Admitted directory matches its admission receipt, the tag and the commit before publication; only the verified wheel and sdist are staged |
| `test_release_artifact_consumer.py` | product-safety | Producer receipt, provenance selection and archive admission fail closed |
| `test_release_artifact_execution.py` | product-safety | Real tag objects, isolated verifier boundary, credential and Git configuration isolation, bounded download and no-replace publication |
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
| `test_sqlglot_delivery.py` | product-safety | Delivery planning, trust-root refusals, suppression from the receipt alone, one-active-pull-request reconciliation, ownership proof by recorded commit and body-edit history, and live re-validation before arming |
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

### `scripts/`

| File | Classification | Property or reason |
| --- | --- | --- |
| `validate_submission.py` | product-safety | Submission validation (KEEP trap) |
| `ruleset_drift_check.py` | product-safety | Ruleset drift (KEEP trap) |
| `check_dependency_bounds.py` | product-safety | Dependency bounds |
| `changelog_fragments.py` | tooling | Changelog fragment validation, rendering, required-entry decision and Unreleased guard |
| `check_sqlglot_upstream_freshness.py` | product-safety | Bundled SQLGlot freshness release gate |
| `check_sqlglot_advisories.py` | product-safety | Bundled SQLGlot published-advisory release gate |
| `sqlglot_delivery.py` | product-safety | Consumes the fork's verified runtime deliveries: plans against the reviewed trust root, prepares the adoption commit, publishes one automation-owned pull request and arms it only through the existing arming rules |
| `generate_landing_quickstarts.py` | product-safety | Landing quickstart generation |
| `site_inputs.py` | product-safety | Site-inputs bundle build and verify |
| `site_inputs_parent.py` | product-safety | Site-inputs parent resolution by SHA |
| `check_duplicate_code.py` | tooling | Hygiene |
| `check_make_targets.py` | tooling | Make targets named in the documentation exist; reads GNU Make's rule database for the Make interface tests |
| `check_windows_antipatterns.py` | tooling | Hygiene |
| `path_filter_decision.py` | tooling | Path classifier shared with ci.yml units |
| `pytest_shard_evidence.py` | product-safety | Exact assigned, collected, and executed medium test evidence |
| `pr_landing.py` | pure-process | PR-loop mechanics |
| `trunk_revert.py` | pure-process | Revert of a merged PR and the red-trunk gate on `make pr-open` |
| `trunk_red_disarm.py` | pure-process | Disables auto-merge on armed PRs the arm gate would refuse when a develop Trunk run is red |
| `trunk_uat.py` | product-safety | Trunk UAT plan, admission, run record and per-cell history; fail-closed zero-cell detector |
| `release_canary_freshness.py` | product-safety | Release canary freshness: the newest canary run on an ancestor of the tag commit must be green and at most 48 hours old |
| `pr_refresh_certification.py` | pure-process | Refresh mechanics |
| `benchbox/utils/host_lock.py` | pure-process | Shared test-lock mechanics |
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
| `ruleset_review_enforcement.py` | product-safety | Ruleset review enforcement |
| `soundness_drain_report.py` | pure-process | Drain digest mechanics |
| `soundness_merge_digest.py` | product-safety | Post-merge soundness review digest |
| `reference_usage_audit.py` | tooling | Reference hygiene |
| `agent_instruction_audit.py` | tooling | Agent instruction lockstep |
| `worktree_audit.py` | tooling | Worktree hygiene |
| `todo_state_contract_check.py` | tooling | Tracker state contract |
| `check_uv_lock_revision.py` | tooling | Lockfile hygiene |
| `pr_review_followups.py` | pure-process | PR-loop mechanics |
| `preflight_targets.py` | pure-process | Changed-file lint and test selection for the local preflight, including the ledger coverage test for every ledger-governed path |
| `dev_loop_pr_metrics.py` | pure-process | Program baseline metrics mechanics |
| Remaining project scripts (ledger-catch-all: _project/scripts/) | product-safety | Sweep, corpus, and validation product code; reclassify individually before any deletion |
| `_project/scripts/explorer_pipeline/browser_fixtures.py` | product-safety | Browser fixture queries for the site-inputs bundle |

### `.pre-commit-config.yaml` hooks

| Hook | Classification | Property or reason |
| --- | --- | --- |
| `corpus-inventory` | product-safety | Corpus inventory integrity |
| `uv-lock-revision-guard` | tooling | Lockfile hygiene |
| `timing-policy-check` | product-safety | Monotonic-clock policy |
| `comment-policy` | tooling | Comments, docstrings, parser coverage and whole-tree enforcement |
| `windows-antipatterns` | tooling | Windows antipattern ratchet (WA004-WA007): `/dev/null`, POSIX-only imports, `env=` without `SYSTEMROOT`, git `text=True` without `encoding=` |
| `audit-raw` | tooling | Dependency inventory drift against `dependency-audit-raw.md` |
| `blind-spot-validate` | product-safety | Blind-spot coverage |
| `agent-write-preflight` | tooling | Worktree safety |
| `untracked-handoffs` | tooling | Handoffs stay out of the repository |
| `agent-git-identity` | tooling | Identity pinning |
| `agent-attribution-trailers` | tooling | Attribution hygiene |
| `ruff-check`, `ruff-format` | tooling | Lint and format |
| `codespell` | tooling | Spelling |
| Standard hooks (`check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `check-added-large-files`, `check-merge-conflict`, `detect-private-key`, `markdownlint`) | tooling | Standard hygiene |
