# ADR: Split the public site into its own repository

## Status

Accepted 2026-10-06.

## Context

The Astro cutover was delayed by coupling to core CI: every site change ran the
full core pipeline, which also gated site deploys. The site
needs isolated CI and fast rollouts. This ADR moves every site asset out of
`BenchBox-dev/BenchBox` (core) into `BenchBox-dev/benchbox-site` (site),
connected by one versioned contract: the site-inputs bundle that core produces
and attests. benchbox.dev stays up, keeps its URLs, and keeps deploy receipts
and rollback throughout.

## Decisions

| # | Decision | Resolution |
|---|---|---|
| D1 | Where user docs live | Superseded by [published documentation ownership](adr-site-docs-ownership.md). Original resolution: Core. Docs change with code: about 20 core tests lock code against doc text, `README.md` has 26 relative `docs/` links, and `benchbox/utils/version.py:61` reads `docs/README.md`. The site gets docs through the bundle. |
| D2 | Repo | `BenchBox-dev/benchbox-site`, public, default branch `main`, squash-merge only. The owner creates it empty. |
| D3 | Rehearsal host | `next.benchbox.dev`; the owner adds the DNS record before the batch. A project Pages subpath breaks root-absolute links. |
| D4 | Deploy control plane | Replaced, not moved: a minimal Node plane in the site repo. The Python `site_deploy` package stays in core until the site removal deletes it. Corpus and snapshot checks become core attestations. |
| D5 | Docs PRs that break the site | Detected after merge. The site refuses the bundle, keeps the last good site and opens an issue in core. Core runs no Node build. An advisory pre-merge check is a follow-up. |
| D6 | Deploy trigger | Automatic on site `main` push and on each `core-bundle` dispatch; `workflow_dispatch` for rollback and redeploy. `github-pages` has no required reviewer and a deployment branch policy of `main` only. |
| D7 | Docs revision | Trunk only. `/docs/` and `/docs/dev/` both render from the newest certified develop bundle, as production does today. Supersedes ADR D13. Release-pinned docs are a follow-up. |
| D8 | Visual regression | Advisory on site PRs. The baseline is the currently deployed artifact. No approval variables. |
| D9 | Language rule | Source only: no `.py`, `pyproject.toml` or `uv.lock` in the site repo, and no `.js`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.astro` or `package.json` in core. Third-party tools fetched at run time are allowed. Site hooks use lefthook, cspell and markdownlint-cli2. |
| D10 | `_blog/` | Moves to the site repo as `drafts/`. Each non-Markdown asset is re-evaluated: kept, deleted or ported to TS. Python never enters the site repo, including its history. |
| D11 | Sphinx retirement | At cutover. The rule that keeps the last Sphinx artifact redeployable is waived. Between cutover and the site removal, rollback is "move the domain back to core Pages"; after the removal, rollback uses site receipts. |
| D12 | Publication plane | The publication control plane retirement, including the GitHub clean-up (D18), is in this batch. |
| D13 | Runner pool | Measure first. Site receipts record queue time and merge-to-live latency; decide after a month. |
| D14 | Oracle review | Amended 2026-10-07. Soundness paths are never narrowed ahead of a deletion. C1 only *adds* `scripts/site_inputs.py` and `_project/scripts/explorer_pipeline/browser_fixtures.py` as soundness files. C2 removes the entries for the files it deletes, in the same commit, and keeps `prefix .github/workflows/`, `prefix publication/` and `prefix scripts/publication/`. Core PRs that touch soundness paths request the Codex connector; if it is unavailable, the owner posts the stand-in comment after the agent has dispositioned every blocking `oracle-review-shadow` finding on that head. Amended 2026-10-09: the self-hosted oracle replaced the Codex connector, so core PRs that touch soundness paths need the oracle's success review of the head; the stand-in applies when the oracle cannot pass it. See [`docs/internal/oracle-review-v2.md`](../oracle-review-v2.md). |
| D15 | External links | A nightly lychee job in the site repo opens or updates one issue. It never blocks a deploy. |
| D16 | Bundle compatibility | Refined by [published documentation ownership](adr-site-docs-ownership.md#bundle-compatibility-and-activation). `schema` is an integer major; removals and renames bump it, and exact member sets are checked per schema. The site supports adjacent majors, proven in CI. Core producer capability lands first without changing default emission; compatible site support is deployed second; new-schema emission activates only afterward. The original “core first” ordering does not authorize publishing a bundle unsupported by the deployed site. |
| D17 | Ruleset edit | One hosted-ruleset change only: remove the `landing` required context from ruleset 15611785, right after the site removal merges. No other ruleset change. |
| D18 | Publication clean-up | Push the archive tag, record the before-state, then delete the `publication` branch, the `publication-attestation` environment and its secrets, and `PUBLICATION_APP_ID`/`PUBLICATION_APP_PRIVATE_KEY`. Uninstall the publication App if the API allows; otherwise the owner does. |
| D19 | First production deploy | The owner approves it live in chat during the production cutover pause. Later deploys are automatic. |
| D20 | Second external review | `agy` was out of quota. The owner first deferred it to the batch, then skipped it on 2026-10-07 after a second 429 (codex substitute declined). |
| D21 | Bundle parent source | Bundle parents follow the production-deployed core SHA. Core reads the newest successful production deployment receipt from the site deployments API with the job token, verifies its digest against the status description, and fails closed when the receipt is missing, mismatched, unreadable, or its bundle expired. Rejected: well-known served URL (trust rests on TLS alone with no digest binding); push from the site (needs a new cross-repo write credential); last-built parent (drifts from what is actually serving after any failed deploy). |

## Language rule

No Python source in the site repo, no JS/TS source in core. Run-time
third-party tools are allowed. The site-inputs bundle schema uses integer
majors; the site supports adjacent majors for one release. Producer capability
lands first, then compatible site support deploys, then new emission activates.
See the [ownership decision](adr-site-docs-ownership.md) for the documentation
schema transition and the supersession of core-owned published prose.

The bundle includes each tracked TPC-DS or TPC-H EULA only when that benchmark's
query templates are selected; the site publishes it beside those query
downloads. Unrelated EULAs are not included.

## Supersessions

- `_project/decisions/single-repo-migration.md`, in part: its A3 tree split
  places `landing/`, `website/` and `deploy/` on `main`. Those paths leave core
  under this ADR; the `main`-is-released-code rule is unchanged.
- `_project/decisions/adr-astro-unified-site.md` D12, D13 and D14, where they
  conflict: D12 plugs the site into the publication deploy plane (replaced by
  the bundle plus the Node plane); D13 describes the renderer cutover (done;
  this change cuts over repositories, not renderers); D14 overlaps
  comment-cleanup items with `astro-site-*` items (those items are folded by
  the tracker reconciliation).
- `docs/internal/adr/adr-independent-publication-authorities.md`:
  superseded by this ADR for the site and deploy control-plane scope. Its
  corpus authority statements stay normative until the publication retirement
  re-homes them; that change applies the superseded marker.
  `tests/unit/docs/test_publication_architecture.py` keeps enforcing the record
  set until then.

## Consumers appendix

Core tests that consume site behavior and therefore constrain the split. An
earlier count cited 35; verification at `d076a974d` found 40 direct consumers
(plus wiring-only references, excluded). The site removal re-points or deletes
each.

Site deploy plane: `tests/unit/scripts/site_deploy/test_site_deploy_candidate.py`,
`test_site_deploy_cli_flow.py`, `test_site_deploy_cli.py`,
`test_site_deploy_gates.py`, `test_site_deploy_generation.py`,
`test_site_deploy_githubapi.py`, `test_site_deploy_mixed_version.py`,
`test_site_deploy_publish.py`, `test_site_deploy_receipt.py`,
`test_site_deploy_renderer.py`, `test_site_deploy_routes.py`,
`site_deploy_fakes.py`, `tests/unit/scripts/test_site_inventory.py`,
`test_site_parity.py`, `test_assemble_public_site.py`,
`tests/unit/workflows/test_site_deploy.py`, `test_site_parity_workflow.py`.

Site content and contracts: `tests/unit/scripts/test_blog_content_validation.py`,
`tests/unit/test_site_header_parity.py`,
`test_public_site_theme_contract.py`,
`tests/unit/landing/test_landing_quickstarts.py`,
`tests/unit/explorer/test_benchmark_labels_coverage.py`,
`test_local_result_version_parity.py`, `test_results_explorer_audit_manifest.py`,
`tests/unit/scripts/explorer_pipeline/test_duckdb_browser_contract.py`,
`test_duckdb_builder.py`, `test_duckdb_readonly_fuzz.py`,
`test_legacy_artifact_guard.py`, `test_pipeline.py`, `test_read_model_contract.py`,
`tests/unit/scripts/test_explorer_build_contract.py`,
`test_explorer_receipt_ui_contract.py`,
`test_results_explorer_corpus_migrate.py`,
`test_results_explorer_cpu_attestation_backfill.py`,
`test_results_explorer_snapshot_invariants.py`,
`test_scan_explorer_stale_theme.py`,
`tests/unit/scripts/publication/test_check_explorer_compat.py`,
`tests/unit/workflows/test_results_explorer_publication.py`,
`test_results_explorer_dependency_audit.py`, `test_website_dependency_audit.py`,
`test_public_site_visual_workflow.py`, `test_publication_preview.py`,
`test_publication_rollback.py`, `test_corpus_cutover.py`.

Cross-surface pins: `tests/unit/cli/test_logo.py`,
`tests/unit/core/test_benchmark_api_contract.py`,
`tests/unit/docs/test_api_reference_contract.py`,
`tests/unit/core/tuning/test_tuning_mode_vocabulary.py`,
`tests/uat/test_explorer_smoke.py`, `tests/uat/phases/explorer_smoke.py`.

## Soundness scope (D14)

This ADR's companion change only adds to `.github/soundness-paths.txt`:

- `file scripts/site_inputs.py`, the attestation producer the site trusts;
- `file _project/scripts/explorer_pipeline/browser_fixtures.py`, the fixture
  producer the bundle carries.

Nothing is removed or narrowed: `prefix .github/workflows/`,
`prefix publication/` and `prefix scripts/publication/` stay as they are. The
narrowing moves to C2, in the same commit as the deletions.
