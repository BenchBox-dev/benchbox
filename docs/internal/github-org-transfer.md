# Transfer `joeharris76/BenchBox` to `BenchBox-dev`

Operator runbook for the ownership cutover authorized by
`_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md`.
This file is the transfer restore checklist. Live develop/release/tag
semantics stay in `repo-admin-settings.md`.

Do not enable merge queue. Do not add `develop-squash-only` bypass actors.
GitHub Pages domain verification is exclusive. do not click Verify on
org `BenchBox-dev` for `benchbox.dev` before G4; keep
`_github-pages-challenge-joeharris76` until after G4 and a live 200.
`www.benchbox.dev` is `CNAME joeharris76.github.io` until after G4.

## Never recreate the old owner/name

After transfer, GitHub redirects `https://github.com/joeharris76/BenchBox`
and its git/release URLs. Creating a new repository at that owner/name
permanently deletes those redirects. Published wheels hardcode
`_DEFAULT_ANSWERS_BASE_URL` under the old owner. JoinOrder
`data_manifest.toml` `url` is inside `manifest_hash` and must not be
edited as part of transfer.

## G0 — Org hardening (operator)

1. `gh auth refresh -s admin:org` (current `repo`/`read:org` is not enough
   for org Actions/PAT policy).
2. Confirm `gh api orgs/BenchBox-dev` and membership role `admin`.
3. Confirm the org has **no** repository named `BenchBox`.
4. Set org profile (description, website `https://benchbox.dev`).
5. Enable required 2FA for the organization.
6. Leave `default_repository_permission=read`. Prefer
   `members_can_create_repositories=false` for a one-repo org.
7. Do **not** restrict org `allowed_actions` and do **not** enable org
   SHA pinning. Live source repo is `enabled=true`,
   `allowed_actions=all`, `sha_pinning_required=false`.
8. **Do not click Verify** for `benchbox.dev` on org `BenchBox-dev`
   while the repo is still user-owned. GitHub Pages domain verification
   is **exclusive**: a domain verified on a user account can only be
   published by that user’s repos; verifying it on another account
   immediately releases other Pages bindings, and an already-verified
   domain refuses a second verification. Keep
   `_github-pages-challenge-joeharris76`. Optional and safe: add TXT
   `_github-pages-challenge-BenchBox-dev.benchbox.dev` without Verify
   (different hostname from the user challenge). Do not change apex
   A/AAAA (they already point at GitHub Pages IPs). Do not retarget
   `www.benchbox.dev` (today `CNAME joeharris76.github.io`) before G4.
9. Org PAT / fine-grained token policy must allow
   `RULESET_DRIFT_TOKEN` and `TODO_EXPORT_PR_TOKEN` against
   `BenchBox-dev/BenchBox`, or replace those secrets with
   org-approved tokens **before** G4.

## G2 — PyPI trusted publishers (operator)

On the existing PyPI project `benchbox`, add a GitHub publisher for
`BenchBox-dev/BenchBox`, workflow `release.yml`, environment `pypi`.
Repeat on TestPyPI for environment `test-pypi`. Keep the
`joeharris76/BenchBox` publishers during overlap. OIDC is checked at
publish time; the repo does not have to exist yet for an existing
project.

## G3 — Freeze and snapshot (operator)

1. No `release-cut` / `v*` tag in flight; no `incident:develop-red`.
2. Accept that open develop PRs move with the repo.
3. Save **full JSON** for every ruleset and environment, including
   `github-pages` `deployment-branch-policies` (live: branch `release`
   only) and develop `require_extra_approval_for_unattributed_changes`.
4. Snapshot Pages: `gh api repos/joeharris76/BenchBox/pages` (`cname`,
   `protected_domain_state=verified`, HTTPS approved) and
   `curl -fsSI https://benchbox.dev/` plus `https://www.benchbox.dev/`
   (HTTP 200). Record `dig` of apex A/AAAA and `www` CNAME.
5. Written GO in this decision record or a dated operator note. G3 GO
   does **not** require org Pages verification.

## G4 — Transfer (operator)

Transfer to owner `BenchBox-dev`, keep name `BenchBox`. Confirm:

```bash
gh api repos/BenchBox-dev/BenchBox --jq '{full_name,owner:.owner.login,type:.owner.type,default_branch}'
```

Expected: `BenchBox-dev/BenchBox`, `Organization`, `develop`.

## G5 — Rebuild (operator)

Order:

1. Workflow permissions: `default_workflow_permissions=read`,
   `can_approve_pull_request_reviews=true`.
2. `actions/permissions`: `enabled=true`, `allowed_actions=all`,
   `sha_pinning_required=false`.
3. Recreate four named rulesets from the G3 JSON:
   `develop-squash-only`, `release-only`,
   `v-release-branches-minimal`, `v-tag-restricted`.
   Develop: no bypass_actors. Tag: bypass `User:57046` always.
   Capture **new IDs** for the pin-update PR.
4. Rebuild environments `github-pages` (deployment-branch `release`
   only), `pypi` (required reviewer `joeharris76` / 57046,
   `can_admins_bypass: true`, `prevent_self_review: false`),
   `test-pypi` (empty protection).
5. Confirm secret **names** exist, then prove
   `RULESET_DRIFT_TOKEN` and `TODO_EXPORT_PR_TOKEN` **authenticate**
   against `BenchBox-dev/BenchBox` (name presence is not enough).
6. Pages serving-only: `curl -fsSI https://benchbox.dev/` and
   `https://www.benchbox.dev/` HTTP 200.
   `gh api repos/BenchBox-dev/BenchBox/pages` still has
   `cname=benchbox.dev`. This is **not** publish-path proof.
   Production Pages is deployed from `BenchBox-dev/benchbox-site`;
   `docs.yml` does not deploy.
7. **After G4 and those 200s**, org-verify `benchbox.dev` (now click
   Verify). If GitHub says the domain is already verified by the user,
   remove user-account Pages verification **only after** the repo is in
   the org, then Verify on `BenchBox-dev`. Then retarget
   `www.benchbox.dev` CNAME from `joeharris76.github.io` to
   `BenchBox-dev.github.io` if www is not 200 or as the planned DNS
   step. Keep the user challenge TXT until org `protected_domain_state`
   is `verified`.

## G6 — Pin-update (after G5)

Retarget public and load-bearing owner strings. Do not touch
`SOUNDNESS_PREFIXES` soundness paths, JoinOrder
`data_manifest.toml`, or `_DEFAULT_ANSWERS_BASE_URL`. Edit
`repo-admin-settings.md` parse blocks **in place** (owner strings and
new ruleset IDs only). See the tracker item
`github-org-transfer-03-pin-update`.

## G7 / G7b

G7: old git URL redirects; answers-v1 and JoinOrder archives still
fetch via the old owner URL; scheduled workflows registered (none
`disabled_inactivity`); Codecov badge regenerated; `ruleset-drift`
green; org Pages `protected_domain_state=verified` for `benchbox.dev`;
apex and www HTTP 200; `www` CNAME `BenchBox-dev.github.io` (or still
GitHub Pages with 200).

G7b: first post-transfer `push` to `release` proves Pages publish.
Serving-only success at G5 does not close G7b.

> Historical note: this transfer predates independent publication and the site split. Production
> Pages is now deployed from `BenchBox-dev/benchbox-site`; see
> `publication-deployer-soak-and-retirement.md`.
