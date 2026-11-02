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
| Network | GitHub and the Python package index; add Docker registries for container checks. For the [oracle gateway](#oracle-gateway-access), also the OMP download host named in the `omp:` section of `.github/oracle-reviewers.yml`, and the Tailscale control plane |
| `TS_AUTHKEY` | Optional. A Tailscale auth key for `tag:oracle-cloud` nodes (reusable, ephemeral, pre-approved, tagged) |
| `ORACLE_GATEWAY_URL` | Optional. The gateway's tailnet URL |
| `ORACLE_CLOUD_GATEWAY_TOKEN` | Optional. The token of the `oracle-cloud` gateway client |
| Repository access | `BenchBox-dev/BenchBox` with push access |

Set the `BENCHBOX_*` rows as environment variables, not secrets, so they stay
visible to every command and Git hook in the session. `TS_AUTHKEY`,
`ORACLE_GATEWAY_URL` and `ORACLE_CLOUD_GATEWAY_TOKEN` are credentials: never
print, log, commit or paste their values anywhere.

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

## Oracle gateway access

When `TS_AUTHKEY`, `ORACLE_GATEWAY_URL` and `ORACLE_CLOUD_GATEWAY_TOKEN` are
all set, `make cloud-bootstrap` runs `scripts/cloud_gateway.sh`, which:

1. downloads the OMP build that CI reviewers use, checks its SHA-256 and
   installs it into the versioned cache `~/.cache/benchbox/omp/<version>/<platform>/omp`.
   The version, URL and checksum per platform are read from the `omp:` section
   of `.github/oracle-reviewers.yml` on `origin/develop` (fetched first, and the
   install stops if that fetch fails), not from the branch. The installed path
   is recorded in `omp-binary` inside the gateway state directory
   (`<git dir>/benchbox-cloud-gateway/`) and survives daemon cleanup, including a
   failed bring-up; it is removed only by `ACTION=stop`, or when the install did
   not succeed. A checksum mismatch, or a platform with
   no pinned build, refuses the install. The download goes straight to the host in the pin, so
   the environment network allowlist needs that host;
2. starts `tailscaled` in userspace-networking mode with in-memory state, so
   nothing touches disk and no TUN device or root is needed. The tailnet DNS
   and routes are unavailable outside it, so the join goes through the
   platform's HTTPS proxy and the gateway is reached only through the local
   outbound HTTP proxy the daemon listens on, `127.0.0.1:1055` (override with
   `BENCHBOX_GATEWAY_PROXY_PORT`);
3. joins the tailnet with `TS_AUTHKEY` as an ephemeral `tag:oracle-cloud`
   node, which the tailnet policy allows to reach only the gateway host on
   port 4000;
4. checks `/healthz` and an authenticated `/v1/models` call through that
   proxy, printing only the status codes.

Tailscale (`tailscale` and `tailscaled`) must already be installed in the
environment image or setup script. The marker file records `gateway=reachable`
with `gateway_proxy=...`, or `gateway=unavailable` with the reason, and
`omp=installed` or `omp=not-installed`.

The cloud environment also needs `TYPESAFE_API_KEY`, which `make pr-open`'s
local review uses to classify the diff. The gateway client token, the Tailscale
key and `TYPESAFE_API_KEY` stay in the session environment and may be readable
by the review process; that residual is accepted because the `oracle-cloud`
client has its own quota and can be revoked alone, the Tailscale key is
ephemeral and tagged so it reaches only the gateway, and the TypeSafe key
classifies only. No other sensitive value, such as a GitHub token, may be
readable there.

The daemon socket lives in a per-checkout directory under `/tmp` (or a short
`TMPDIR`). The script refuses to start the daemon when that path is a symbolic
link or is owned by another user, so another local user cannot pre-create it
to capture the join.

Without the three variables, or when Tailscale is missing or the join or check
fails, bootstrap still succeeds, stops any daemon it started, and records the
gateway as unavailable. When the diff needs a review, `make pr-open` then
refuses before any push (exit 2), because its local review cannot run: fix the
missing variable or Tailscale install and run `make cloud-gateway`, or report
the blocker. A diff the policy classifies as needing no review proceeds without
the gateway. A builder never skips a required review.

`make pr-open` does the proxy wiring itself. It reads `gateway_proxy=` from the
status file (accepting only a loopback `http://127.0.0.1:<port>` value), reaches
the gateway through it for the health check, and gives the reviewer
`HTTPS_PROXY`, `HTTP_PROXY`, `PI_PROXY` and `NO_PROXY` (loopback only) for that
one run. `omp` ignores the first two for its gateway call and honors `PI_PROXY`.
The `oracle-local` client and CI never receive proxy variables. It installs
or reuses the pinned `omp` in the shared cache (`~/.cache/benchbox/omp`, or
`BENCHBOX_OMP_CACHE`), the same build `make cloud-gateway` verified; the
`omp-binary` path file is only for manual runs.

Use the gateway per command, never as a session-wide proxy, because the proxy
reaches only the tailnet and would break GitHub and package access:

```bash
HTTPS_PROXY=http://127.0.0.1:1055 "$(cat "$(git rev-parse --git-dir)/benchbox-cloud-gateway/omp-binary")" ...
```

`make cloud-gateway` repeats the setup idempotently: a live daemon and a
verified install are reused, never duplicated. Run it when a later session step
finds the proxy gone. `make cloud-gateway ACTION=stop` stops the daemon and
`ACTION=status` prints the recorded state.

Codex Cloud is not yet enabled for the gateway: the same variables are set on
its environments, but the path has not been tested there and the owner has
deferred it. Only Claude Code on the web is verified.

## Batch work

A cloud builder is never a batch member. Batch members and the integration
branch live in local linked worktrees under one leader, who is the only tracker
writer (`docs/agent/batch-feature-delivery.md`); a cloud session cannot be
handed a member branch, a landing, or the final PR. A cloud session builds one
change on its own branch and PR as below. Items of a batch that are not yet
landed are not dispatched to a cloud session.

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

3. **Branch.** Use the Conventional Commit type branch (`feat/`, `fix/`, `docs/`, `test/`, `chore/`, ...) named in
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

   `make pr-open` first runs the same review driver as CI on the committed
   `HEAD`, from a trusted export of `origin/develop` (see
   `docs/internal/oracle-review-v2.md`, Local pre-PR review), and refuses before
   any push on a DO NOT SHIP or a Critical or High defect, including one carried
   from an earlier review of the PR, a missing `TYPESAFE_API_KEY` in the cloud
   environment, an unreachable gateway, a missing `oracle-cloud` client
   (`ORACLE_GATEWAY_URL` and `ORACLE_CLOUD_GATEWAY_TOKEN`), a secret value found
   on disk, an `origin/develop` that cannot be fetched, or an `omp` that is not
   the CI-pinned version. A Medium-only SHIP WITH FIXES proceeds. Fix a finding
   and recommit. A finding that is a false positive is disputed with
   `REBUTTALS_FILE=<json>` (finding id to a rebuttal with a line that starts `Dispute:` and cites file:line
   counter-evidence), which the reviewer that raised it re-judges; never adjudicate it yourself, because only
   the owner may, and only when the owner names the head, the finding and the decision. A review that
   cannot run cannot be disputed, so report that blocker instead. It pushes
   exactly the commit it reviewed. Never print the token, the key or the gateway
   URL.

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
