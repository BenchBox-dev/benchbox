# Theseus extraction: source and consumer inventory

Date: 2026-11-02
Source revision: `b64f28fc73001bcd9273e80c2ff51a5e43a6b8ca`.
Contract: [Theseus oracle extraction](theseus-oracle-extraction-2026-11-02.md).

## Count and evidence scope

At the named source revision, `_project/scripts/oracle_reviewers/` contains **45 Python
files** and **3 prompt templates**: `templates/deep.md`, `templates/gate.md` and
`templates/standard.md`. `templates/README.md` is documentation, not a fourth template.
`scripts/remote_review_prompt.txt` is an additional consumer prompt, outside that count.
These are scoped bundle counts, not repository-wide Python or documentation counts.

Reproduce against the pinned revision, not a later working tree:

```bash
git ls-tree -r --name-only b64f28fc73001bcd9273e80c2ff51a5e43a6b8ca \
  -- _project/scripts/oracle_reviewers
```

Count `.py` entries in that output; count only the three named prompt files, excluding
the template README. All citations below refer to this source revision. Braced paths
name a bounded set of files, not an invitation to migrate everything under a directory.
Proving checks are required later, not reported as exercised by this inventory.

## Ownership and deliverables

**Theseus** owns generic engine/gate/local-driver/cache/matcher behavior, reusable workflow
implementation and OMP maintenance. **BenchBox** owns policy and manifests, read-index
scanning, PR lifecycle, trigger wrappers/schedules, environments/permissions, App source
binding and operating host/provider accounts. A row with both owners is a boundary:
extract the generic behavior, not the consumer's authority. Matcher packaging remains
an owner decision; no second drifting implementation is permitted.

The descriptive deliverables below are acceptance groupings, not claims of completion:

- **Generic engine and protocol extraction**: trusted package, assets and wire contracts.
- **Consumer cutover and guard reconciliation**: every BenchBox caller and drift guard.
- **OMP maintenance and gateway compatibility**: pin/candidate/watch machinery and separate
  owner-operated live gateway proof.
- **Offline package and workflow parity**: frozen-byte installation, workflow and consumer
  scenarios without live secrets.

| Consumer and source citation | Dependency | Owner and migration action / later deliverable | Proving check |
|---|---|---|---|
| Reviewer bundle; `scripts/oracle_local_review.py:19–40,47–120` enumerates trusted exports; `.github/oracle-reviewers.yml:1–147` supplies policy | Engine, templates, config, trusted base | Theseus extracts engine/assets; BenchBox retains policy. **Generic engine and protocol extraction** | `tests/unit/scripts/oracle_reviewers/`; `tests/integration/oracle/` |
| `_project/scripts/oracle_gate.py:1–207`; `.github/workflows/oracle-gate.yml:74–85,200–211` | Check evaluator, trusted sparse checkout | Theseus owns generic gate; BenchBox owns trusted trigger and App binding. **Generic engine and protocol extraction** | `tests/unit/scripts/test_oracle_gate.py`; `tests/unit/workflows/test_oracle_gate_workflow.py` |
| `_project/scripts/oracle_review_check.py:1–519,260–425` | Receipt parsing, PR data, matcher precedence, OMP owner evidence | Theseus extracts evaluator contract; BenchBox supplies policy/identity and PR facts. **Generic engine and protocol extraction** | `test_oracle_review_check.py`, `test_oracle_review_check_decisions.py`, `test_oracle_review_check_omp_bump.py` under `tests/unit/scripts/` |
| `scripts/oracle_local_review.py:19–40,47–120,158–195` | Trusted-develop export, local driver/cache | Theseus owns generic driver/cache; BenchBox wrapper selects trusted base/lock. Replace source exports with verified installation. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/oracle_reviewers/test_local_review.py`; `tests/integration/oracle/test_local_review_scenario.py` |
| `scripts/pr_arm.py:15–18,222–247,335–348` | `pr_landing`, bump evidence, gate | BenchBox retains PR arming; call Theseus contract instead of source imports. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_pr_arm.py` |
| `scripts/pr_arm_omp_bump.py:1–74` | `omp_bump_gate`, pin/head-bound owner evidence | BenchBox retains arming adapter; Theseus owns generic evidence validator. **OMP maintenance and gateway compatibility** | `tests/unit/scripts/test_oracle_review_check_omp_bump.py`; `tests/unit/workflows/test_omp_pin_bump_workflow.py` |
| `scripts/pr_landing.py:24–27,1267–1270` | Check suites, soundness matcher, bump gate | BenchBox retains lifecycle; migrate imports and compatible-base refusal. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_pr_landing.py`; `tests/integration/oracle/test_pr_landing_soundness_scenario.py` |
| `scripts/remote_closeout.py:18–21,529–534` | Batch/arm/landing composition | BenchBox retains remote landing authority; use locked evaluator. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_remote_closeout.py`; `docs/agent/remote-closeout.md:182–186` operator contract |
| `scripts/remote_review.py:23–25,382–386,1013–1015`; `scripts/remote_review_prompt.txt` consumed at `:1013–1015` | Remote closeout/landing and prompt asset | BenchBox retains remote PR lifecycle/prompt policy; generic local reviewer belongs to Theseus. **Consumer cutover and guard reconciliation** | Remote/local oracle integration and landing scenarios |
| `_project/scripts/soundness_paths.py:1–138`; `_project/scripts/inert_paths.py:1–150,370–472`; `_project/scripts/oracle_review_check.py:260–425` | `.github/{soundness-paths,inert-paths}.txt`, precedence, machine-read docs | Theseus owns matcher; BenchBox retains manifests/read-index scan. Resolve matcher arrangement before cutover. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/{test_soundness_paths.py,test_inert_paths.py}`; reviewer `test_classifier.py`, `test_selection.py` |
| `_project/scripts/soundness_merge_digest.py:1–110,1310–1460`; `.github/workflows/soundness-merge-digest.yml:1–50` | Checker/protocol, merge-safety digest, schedule | BenchBox retains digest and schedule/permissions; consume stable Theseus protocol. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/{test_soundness_merge_digest.py,test_soundness_merge_digest_dispatch.py}` |
| `_project/scripts/soundness_drain_report.py`, invoked by `make/worktree-maintenance.mk:12–18`; `docs/internal/soundness-drain.md:124–133` | `required_lane.py`, soundness matcher; manual/local reporting | BenchBox retains pure-process drain report; update imports. **Consumer cutover and guard reconciliation**. No active drain workflow: daily workflow retired; `docs/internal/pr-triage.md:60–67` is stale | `tests/unit/scripts/test_soundness_drain_report.py`; `uv run -- python _project/scripts/soundness_drain_report.py --self-test` |
| `_project/scripts/preflight_targets.py:21–65,300–380` | Oracle scenario/test mapping | BenchBox retains selector; remap moved source and installed-consumer tests. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/{test_preflight_targets.py,test_pr_preflight_base.py}` |
| `scripts/coverage_test_selection.py:1–45,420–470,780–850,1280–1370` | Preflight mapper and inert-doc readers | BenchBox retains coverage selection; update installed-code coverage/test associations. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_coverage_test_selection.py` |
| `scripts/heavy_tier_needed.py:1–80,120–258`; `.github/workflows/ci.yml:220–340,1040–1098,1700–1768` | Base/PR matcher comparison, selection policy, sparse paths | BenchBox retains CI policy and fail-closed delta selection. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/{test_heavy_tier_needed.py,test_path_filter_decision.py}` |
| `scripts/changelog_fragments.py:1–55,530–643`; `Makefile:1046–1056,1295` | Soundness predicates, change/release governance | BenchBox retains changelog rules; call the approved matcher. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_changelog_fragments.py`; `make changelog-check` |
| `scripts/check_comment_cleanup_scope.py:1–160`; `.github/workflows/ci.yml:25–75`; `Makefile:524–529` | Trusted-base cleanup scope and comment checker | BenchBox retains cleanup policy and trusted-base execution; replace moved-code dependencies. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/{test_comment_cleanup_scope.py,test_comment_policy.py}`; `tests/system/test_ci_lint_parity.py`; `make check-comment-scope` |
| `.github/oracle-reviewers.yml:1–147`; gateway policy consumed by `oracle-reviewer-gateway-2026-10-22.md:36–43` | `.github/oracle-omp-config.yml`, model roles/retry/pin policy | BenchBox owns policy YAML; Theseus accepts validated consumer config. No model-policy redesign. **OMP maintenance and gateway compatibility** | Reviewer policy/selection tests, workflow config tests |
| OMP maintenance bundle consumed by `scripts/pr_arm_omp_bump.py:1–74`: `_project/scripts/oracle_reviewers/{omp_bump.py,omp_bump_gate.py,omp_candidate.py,omp_pin.py,omp_watch.py}` | Candidate compatibility, pin, owner attestation, artifact watch; `.github/workflows/omp-pin-{bump,watch}.yml` | Theseus owns generic maintenance; BenchBox owns trigger wrappers/schedules and owner attestations. **OMP maintenance and gateway compatibility** | Reviewer `test_omp_bump.py`, `test_omp_candidate.py`, `test_omp_pin.py`, `test_omp_watch.py`; workflow `test_omp_pin_{bump,watch}_workflow.py` |
| Gateway host contract `oracle-reviewer-gateway-2026-10-22.md:24–43,100–114`; `_project/scripts/oracle_gateway_host.sh`, `scripts/cloud_gateway.sh` | OMP `auth-gateway`/`auth-broker`, tailnet, host and provider accounts | BenchBox owner retains running host/credentials/accounts; Theseus only generic compatibility machinery. **OMP maintenance and gateway compatibility** | `tests/unit/scripts/oracle_reviewers/test_gateway_host.py`; `docs/internal/oracle-gateway-host.md`; separately authorized live canary |
| Wire parsing contracts `oracle-review-protocol-2026-10-08.md:39–59,77–79`; reviewer `{runner.py,verdict.py,evidence.py,protocol.py,candidate_checks.py}` | OMP streams, review markers, tool-read evidence, candidate gateway responses | Theseus ports producers/parsers and captures together, preserving artifact/finding identities. **Generic engine and protocol extraction** | Reviewer `test_runner.py`, `test_verdict.py`, `test_evidence.py`, `test_protocol.py`, `test_response_ingress.py`, `test_omp_candidate.py` |
| `.github/workflows/oracle-verdict.yml:53–61,114–126,313–323`; `.github/workflows/oracle-gate.yml:8–17,74–85,200–211`; `.github/path-filters.yml:1–83` | Trusted source paths, archive/sparse/filter lists and job wiring | Theseus owns reusable execution; BenchBox retains triggers/permissions/environments. Reconcile lists with exact assets/lock. **Offline package and workflow parity** | `tests/unit/workflows/{test_oracle_gate_workflow.py,test_oracle_verdict_workflow.py}`; integration oracle scenarios |
| Retired-check notifier contract `oracle-review-check-retired-2026-10-24.md:50–62`; `.github/workflows/oracle-review.yml` | Edited/dismissed review event forwarding | BenchBox retains trigger wrapper; do not remove without replacement or revive required `oracle-review`. **Consumer cutover and guard reconciliation** | Gate/notifier workflow tests and current-head review-event scenario |
| `Makefile:524–529,970–986,1046–1056,1295`; `make/worktree-maintenance.mk:12–18`; `.github/workflows/ci.yml:730–733` | Dev entry points and curated release trees | BenchBox retains Make/release policy; replace all source invocations, preserve exclusion from runtime artifacts. **Consumer cutover and guard reconciliation** | `scripts/check_release_curation.py`; `tests/unit/scripts/test_check_release_curation.py`; `tests/unit/test_release_infrastructure.py` |

## Cross-cutting cutover checklist

The inventory is a source baseline, not a declaration that extraction is ready. The
consumer cutover must reconcile `quality/comment-policy.json` symbols/digests,
`quality/docs-reference-inventory.json`, `_project/inert-doc-readers.json`,
`tests/unit/ledger_cutover_files.txt`, Windows antipattern exclusions, mutation targets,
copied inputs/test paths/runner, archive/sparse/filter paths and both CI matcher copies.
Require comment-policy, duplicate-check-delta, inventory freshness, scenario selection,
consumer scenarios and publication gates. Keep historical exports inert rather than
leaving a second runnable implementation. The additional source citations below identify
those less direct readers.

| Additional consumer and source citation | Dependency | Owner and migration action / later deliverable | Proving check |
|---|---|---|---|
| `.github/workflows/ci.yml:203–209,2081–2087`; classification at `:187–192,404–407` | **Both** event-base copies of `scripts/changelog_fragments.py`, `_project/scripts/soundness_paths.py` and `.github/soundness-paths.txt` | BenchBox retains trusted event-base semantics; replace both copies with the owner-approved matcher arrangement, never one independently evolving copy. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/{test_path_filter_decision.py,test_heavy_tier_needed.py,test_changelog_fragments.py}` |
| `scripts/check_windows_antipatterns.py:66–71,686–688`; `.github/workflows/ci.yml:646–649` | Explicit exclusions for reviewer `omp_pin.py`, `processes.py`, `runner.py` and baseline `quality/windows-antipatterns-baseline.json` | BenchBox owns Windows guard; update obsolete exclusions without broadening them. Theseus owns platform claims for extracted helpers. **Consumer cutover and guard reconciliation** | `make windows-antipatterns-check`; checker tests and changed-path scenarios |
| `scripts/coverage_test_selection.py:678–706`; `.github/workflows/ci.yml:296–320,1346–1383` | POSIX-only reviewer test selection and direct-PR-change exception, distinct from antipattern exclusions | BenchBox retains Windows scenario policy; preserve the direct-change exception. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_coverage_test_selection.py:1854–1869,1915–1939,1977–1985` |
| `pyproject.toml:395–419`; `_project/scripts/oracle_mutation.py:18–48`; `Makefile:659–662` | Mutation targets, `tests_dir`, copied `_project/scripts/`, `.github/`, `scripts/`, tests/config/xdist safety input and staged symlinks | Theseus owns generic engine mutation targets; BenchBox keeps consumer gate/digest targets. Split configuration, copied inputs and runner coherently rather than mutating nonexistent source paths. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_oracle_mutation_stage.py:1–38`; `make oracle-mutation-test` |
| `tests/integration/oracle/test_curated_capture_fixtures.py:17–20,76–90,118–129`; `tests/unit/scripts/oracle_reviewers/test_omp_candidate.py:438–442` | `tests/fixtures/oracle_reviewers/provenance.json`, captured streams and `tests/fixtures/oracle_reviewers/omp_candidate/` | Theseus ports generic captures with provenance/redaction; BenchBox retains consumer scenarios. Frozen captures alone cannot satisfy candidate contract rows or live proof. **Offline package and workflow parity** | Curated-capture integration test, candidate contract tests and secret/private-host scan |
| `quality/comment-policy.json:32854–32864,32867–32870`; `scripts/check_comment_cleanup_scope.py:80–108,218–263,713–728` | `local.py:withhold_secrets`, local wrapper `launch`, payload digests, exception consumer/owner/removal evidence | Each repository owns its policy exceptions; migrate symbol identities and recompute applicable digests, not ownership by path replacement alone. **Consumer cutover and guard reconciliation** | `make comment-policy-check`; `tests/unit/scripts/test_comment_cleanup_scope.py:643–688,1091–1112` |
| `scripts/doc_reference_inventory.py:15–23,250–304`; `quality/docs-reference-rules.json:198–222`; `quality/docs-reference-inventory.json:501–519,630–689` | Generated docs-reference inventory, historical-record/archive classification, ownership and lock-test inputs | BenchBox updates active references and freshness inventory, retaining explicit historical exceptions. The script advertises a remediation Make target not located in the current Makefile; confirm the actual regeneration entry point instead of assuming that command works. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_doc_reference_inventory.py:15–20,45–123`; script `--check` freshness comparison |
| `_project/scripts/inert_paths.py:15–25,390–420,455–472` | `_project/inert-doc-readers.json` and `.github/inert-paths.txt`; read-index scanning | BenchBox retains producer/index, read-only default and explicit `--regenerate-readers` write gate; Theseus consumes configured matching inputs. **Consumer cutover and guard reconciliation** | `tests/unit/scripts/test_inert_paths.py`; regenerated-reader freshness guard |
| `tests/unit/ledger_cutover_files.txt:21–32,81,86–88,115,147–149`; `docs/internal/dev-loop-property-ledger.md:90–92,255–265,378–410` | Reviewer/gate/workflow test lists, inert ledger readers and property classifications | BenchBox reconciles test lists and ledger classifications; preserve inert archival evidence, no active imports from exports. **Consumer cutover and guard reconciliation** | Ledger cutover/list freshness tests, mapped oracle/selector scenarios and `duplicate-check-delta` |

The **Offline package and workflow parity** and **Consumer cutover and guard
reconciliation** deliverables must also prove publication gates against the frozen
named artifact bytes. No consumer may satisfy an absent installed implementation by
using a historical archive, a sparse-checkout residue or a stale cache entry.
