# Branch rename runbook: `main` → `release` (Decision B)

Operational steps for the GitHub-side half of Decision B (see
`_project/decisions/branch-rename-main-to-release-2026-07-09.md`). The in-repo
rename lands via PR; these admin steps are maintainer-only (the CI
`GITHUB_TOKEN` has no `administration` scope).

## Preconditions

- The in-repo rename PR is reviewed (it touches `release.yml`, a soundness
  path, so it needs the `oracle-gate` check to pass) and ready to merge.
- **No release in flight** — no open `Release vX.Y.Z` PR, and not mid
  `release-cut`/`release-finalize`:
  ```bash
  gh pr list --state open --json title --jq '.[] | select(.title | startswith("Release v"))'
  ```
  Expect empty.

## Ordering (one sitting, no real gap)

Merge the in-repo PR and do the branch rename together. The release branch only
receives pushes via `release-finalize` (maintainer-controlled), and direct
pushes are ruleset-blocked, so the brief window where workflows reference
`release` before the branch exists is not a real coverage gap. Do **not** rename
the branch before the PR merges, or `release-cut` would open PRs against a
`release` branch that doesn't exist.

1. **Merge the in-repo rename PR** into `develop`.
2. **Rename the branch** (GitHub redirects old `main` URLs and retargets open
   PRs):
   ```bash
   gh api -X POST repos/BenchBox-dev/BenchBox/branches/main/rename -f new_name=release
   ```
3. **Recreate the ruleset** as `release-only` on `refs/heads/release`, preserving
   the current `main-release-only` properties. Inspect the old one first:
   ```bash
   gh api repos/BenchBox-dev/BenchBox/rulesets --jq '.[] | {id, name, target}'
   gh api repos/BenchBox-dev/BenchBox/rulesets/<old-id>
   ```

   The first command lists the rulesets. The second captures the rules of the old one.
   Recreate with: target `refs/heads/release`; required checks `validate-base`,
   `release-required-result`; `strict_required_status_checks_policy: false`;
   `required_linear_history: true`; `non_fast_forward: true`; deletion blocked;
   no bypass actors. Then update the ruleset-id reference in
   `repo-admin-settings.md`.

## Verify

```bash
gh api repos/BenchBox-dev/BenchBox/branches/release --jq .name
gh api repos/BenchBox-dev/BenchBox/branches/main --jq .name

gh api repos/BenchBox-dev/BenchBox/rulesets --jq '.[] | {name, target}'

uv run -- python scripts/ruleset_drift_check.py --token "$RULESET_DRIFT_TOKEN" --require-bypass-actor-visibility
```

Expected results:

- The `release` branch query returns `release`, so the branch is renamed.
- The `main` branch query returns 404; the redirect covers old URLs.
- The ruleset listing shows a release-only ruleset that targets `refs/heads/release`.
- The drift check parses this document and matches live state, once the PR is on `develop`.

Then confirm the next real `release-cut` opens its PR against `release`,
`validate-release-pr` passes for the `vX.Y.Z` head, and `release-finalize`
fast-forwards `release` and tags it.

## Rollback

Rename back with the branches/rename API, recreate the ruleset on
`refs/heads/main`, and revert the in-repo PR. The release branch carries no
unique history (its HEAD is always a tagged release commit), so rollback loses
nothing.

## Not affected (do not touch)

- PyPI trusted publishing (repo + `release.yml` filename + `pypi` environment).
- The `pypi` / `test-pypi` / `github-pages` environments and the `release.yml`
  filename.
- The `validate-base` required-check context name (only the workflow file was
  renamed).
