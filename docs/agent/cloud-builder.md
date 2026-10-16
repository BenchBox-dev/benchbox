# Cloud builder sessions

This contract applies to an agent that implements one BenchBox change in a
cloud session, such as Claude Code on the web or Codex Cloud. The cloud session
builds and verifies the change, then pushes one branch. A local session owns
the tracker claim, the independent review, and close-out through the existing
`make pr-open` and `make pr-arm` path.

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
| Setup script | Install `uv` if the image lacks it, then run `make cloud-bootstrap` |
| Network | GitHub and the Python package index; add Docker registries for container checks |

Set these as environment variables, not secrets, so they stay visible to
every command and Git hook in the session.

## Builder procedure

1. **Bootstrap.** Confirm the setup script ran:

   ```bash
   cat "$(git rev-parse --absolute-git-dir)/benchbox-cloud-bootstrap"
   ```

   If the file is missing, run `make cloud-bootstrap`. Stop if it refuses.
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

9. **Commit and push.** Stage explicit paths and write durable commit messages.
   Push the session branch. Do not open a pull request or poll CI.

## Hand-off

End the session with this report:

- branch and head SHA;
- the `cloud-verify` summary table for that head;
- behavior commands and observed output, or why none applied;
- the regression-test proof, for bug fixes;
- self-review findings, each with its disposition;
- anything out of scope, unverified, or blocked;
- setup and verification timings from the bootstrap marker and the summary.

The report is the builder's claim, not proof. Readiness comes from the
independent review and exact-head hosted CI.

## Local close-out

`make worktree-create` creates only new branches, so check out the pushed
branch directly and pin the human identity:

```bash
git fetch origin <branch>
git worktree add --track -b <branch> ../BenchBox.wt-<slug> origin/<branch>
sh scripts/set_worktree_identity.sh ../BenchBox.wt-<slug>
cd ../BenchBox.wt-<slug>
make agent-write-preflight
```

If the builder could not use a standard branch name, create a standard branch
with `make worktree-create` instead, run `git fetch origin <builder-branch>`
in it, and cherry-pick the builder's commits with
`git cherry-pick "$(git merge-base HEAD FETCH_HEAD)..FETCH_HEAD"`.
Cherry-picking keeps the human author.

Then run `make pr-open`, the independent review, and `make pr-arm`. If CI fails
after arming, start the revision transaction with `make pr-landing-start` and
`make pr-landing-withdraw` before anyone edits again. A builder may then push the
fix to the same branch; fetch it into the close-out worktree before
`make pr-landing-ready`.
