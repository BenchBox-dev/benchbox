# Cloud builder sessions

This contract applies to an agent that implements one BenchBox change in a
cloud session, such as Claude Code on the web or Codex Cloud. The cloud session
builds and verifies the change, pushes one branch, and opens its pull request.
A local closer owns the tracker claim, independent review, and arming through
`docs/agent/remote-closeout.md` for eligible PRs. Ineligible PRs use the linked
worktree close-out below.

Nothing here depends on one vendor's hooks, skills, or subagents. Cloud clones
do not contain the gitignored `.agents/skills` mirror, so this file and
`AGENTS.md` are the builder's complete instructions. Name this file in every
cloud builder task prompt.

## Environment setup (owner)

Configure each cloud environment once:

| Setting | Value |
|---|---|
| `BENCHBOX_EPHEMERAL_CLONE` | `1` |
| `BENCHBOX_GIT_NAME` | The human commit author's name |
| `BENCHBOX_GIT_EMAIL` | The human commit author's email |
| Setup script | Install `uv` if the image lacks it, put a `node` that satisfies `^22.22.2`, `^24.15.0`, or `>=26` first on `PATH`, then run `make cloud-bootstrap` |
| Network | GitHub and the Python package index; add Docker registries for container checks |
| Repository access | `BenchBox-dev/BenchBox` with push access |

Set these as environment variables, not secrets, so they stay visible to
every command and Git hook in the session.

The `node` range comes from a dependency of the markdownlint pre-commit hook;
an older `node` makes that hook fail to install. `make cloud-bootstrap` refuses
one before it changes anything, then builds the hook environments so they use
the `node` it checked. Make the `node` change persist beyond the setup script,
for example by linking the newer `node`, `npm`, and `npx` into
`/usr/local/bin`, because some platforms do not keep the setup script's
environment.

Start each session from a clone of the GitHub repository, with the
repository attached and push access granted: in Claude Code on the web, select
the repository when creating the session; in Codex Cloud, use an environment
for the repository. A session uploaded from a local checkout may have no
`origin` remote, and a session without push access gets
`403 ... not in this session's authorized repository set` on push. Both cases
cost the whole session, so the builder checks them first.

## Builder procedure

1. **First-minute checks.** Before any other work, confirm that the session
   can publish and that the setup script ran:

   ```bash
   git remote get-url origin
   git fetch origin develop
   git push --dry-run --no-verify origin HEAD:refs/heads/<branch from the task prompt>
   cat "$(git rev-parse --absolute-git-dir)/benchbox-cloud-bootstrap"
   ```

   `--no-verify` skips the pre-push hooks here only, so the dry run tests
   access and not the change. Stop and report the exact error if `origin` is
   missing or is not the GitHub repository, if the fetch fails, or if the
   dry-run push is refused. If the
   marker file is missing, run `make cloud-bootstrap`; stop if it refuses.
   Never commit as an agent or vendor identity.

2. **Scope.** Work only on the item, acceptance criteria, and path scope in the
   task prompt. If the change needs paths outside that scope, stop and report
   the blocker. Do not write tracker state; the local session owns the claim.

3. **Branch.** Use the `chore/`, `fix/`, `feat/`, or `docs/` branch named in
   the task prompt, created from `origin/develop`. If the platform refuses that
   name, use the branch it created and report the refusal. Push no other branch.

4. **Implement** the narrowest coherent change under `AGENTS.md`.

5. **Verify.** Run the checks in the background and read the log, because some
   platforms stop long foreground commands:

   ```bash
   make cloud-verify LEVEL=tests > /tmp/cloud-verify.log 2>&1
   ```

   `LEVEL=static` runs the comment policy, duplicate-code delta, and type
   checks. `LEVEL=tests` adds `pr-preflight`, the fast lane, and the
   medium tests selected for the changed paths. Fix every failure, then rerun on
   the final tree. A rerun after any new commit replaces the earlier result.

6. **Observe behavior.** When the change has a runnable entry point, drive it
   once through that entry point: a CLI dry run, a local DuckDB run at a small
   scale factor, or `make uat-cell` for an adapter change when Docker is
   available. Report the command and what it printed. This is observed behavior,
   not UAT or production certification. Never run live cloud platforms or
   `live_integration`, `slow`, or `stress` tests without explicit approval.

7. **Bug fixes.** Show that a new regression test fails without the fix: revert
   the fix, run the test, confirm the failure, then restore the fix.

8. **Self-review.** Review the final diff adversarially. Use a fresh-context
   subagent when the platform has one; otherwise do a separate pass that
   starts from the diff and the acceptance criteria, not from the
   implementation notes. Try to refute "this diff meets the acceptance criteria
   and is safe to merge" through acceptance coverage, edge cases and
   regressions, repository rules (`AGENTS.md`, comment policy, BenchBox
   invariants), and test adequacy. Each finding gives its location, its
   reasoning, a reproduction when one applies, and its confidence. Fix Critical
   and Required findings and rerun step 5. Report the rest.

9. **Publish.** Stage explicit paths and write durable commit messages.
   Push the session branch, then run `make pr-open` with a PR body describing
   the change and verification. Do not set `READY=1`. Report the PR URL and
   exact head SHA. Never arm or merge a pull request, resolve review threads,
   change labels or draft state, or post an approval: those belong to the
   closer and the owner. After reporting, push again only when the closer
   hands back a review finding or CI failure, after disabling auto-merge.

## Builder report

Return this report to the closer and record it in the item's tracker context
when tracker access is available:

- PR URL, branch and exact head SHA;
- the `cloud-verify` summary table for that head;
- behavior commands and observed output, or why none applied;
- the regression-test proof, for bug fixes;
- self-review findings, each with its disposition;
- anything out of scope, unverified, or blocked;
- setup and verification timings from the bootstrap marker and the summary.

The report is the builder's claim, not proof. Readiness comes from the
independent review and exact-head hosted CI.

## Close-out

For an eligible PR, the closer follows `docs/agent/remote-closeout.md` without
creating a worktree. Bind the builder's reported PR and head, obtain an
independent review record, then arm through `make pr-arm-remote`. The closer
withdraws auto-merge before asking the builder for any revision.

For an ineligible PR, use a linked worktree. `make worktree-create` creates only
new branches, so check out the pushed branch directly and pin the human identity:

```bash
git fetch origin <branch>
git worktree add --track -b <branch> ../BenchBox.wt-<slug> origin/<branch>
sh scripts/set_worktree_identity.sh ../BenchBox.wt-<slug>
cd ../BenchBox.wt-<slug>
make agent-write-preflight
```

Keep the builder's branch even if the platform assigned a nonstandard name:
the existing PR already points to it.

The builder has already opened the PR. Run the independent review and
`make pr-arm` from the close-out worktree. If CI fails after arming, start the
revision transaction with `make pr-landing-start` and
`make pr-landing-withdraw` before handing a finding or CI failure back to the
builder. Fetch its revised branch into the close-out worktree before
`make pr-landing-ready`.
