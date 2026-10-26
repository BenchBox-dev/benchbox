# Oracle verdict: one review job instead of a chain

Date: 2026-10-25
Status: Decided. Supersedes the per-job credential-isolation rationale recorded
for commit `0e5123270` in
`_project/decisions/oracle-review-v2-shadow-2026-10-05.md`.
Related: `.github/workflows/oracle-verdict.yml`,
`_project/scripts/oracle_reviewers/cli.py` (`attempts`, `scan-disk`,
`redact-check`), `_project/scripts/oracle_reviewers/runner.py`,
`_project/scripts/oracle_reviewers/processes.py`,
`_project/scripts/oracle_reviewers/credentials.py`,
`_project/decisions/oracle-reviewer-gateway-2026-10-22.md`,
`docs/internal/oracle-review-v2.md` (Job structure and controls).

## Context

`oracle-verdict.yml` ran each review as a chain of jobs: `plan`, `select-1`,
`attempt-1-omp`, then a `select-N` and `attempt-N-omp` pair for every retry up
to `max_attempts` (5), then `post`. Every job waits for a runner in the
organization's 20-job pool. Two reviews measured on 2026-10-25 spent about two
hours and fifteen minutes queueing for about four minutes of work, and each
took more than two hours from run creation to a posted verdict. A review
needed five admissions on its first decision and up to eleven with five
attempts.

The chain was built so that each reviewer CLI held only its own provider key
and only the last job held the App key. The gateway decision of 2026-10-22
removed provider keys from runners: every attempt now runs the same harness
with the same one credential, the gateway client token. Separate attempt jobs
therefore keep identical credentials apart. Only two credentials remain
distinct: the TypeSafe key, used by classification, and the App private key,
used to post.

## Decision

The workflow has one review job, `review`, beside `sweep`. It checks out the
trusted `develop` copy, plans, checks out the pull request head as read-only
data, classifies, scans the disk, installs and verifies OMP, joins the tailnet
once, runs the whole attempt loop as one command, finalizes, redacts, mints the
App token, guards and posts, uploads the attempt and retry-state artifacts and
dispatches `oracle-gate`. Queue admissions per verdict fall to one, plus the
unchanged `oracle-gate` admission. No required status context names an
`oracle-verdict` job (the `develop` ruleset requires `core`, `explorer`,
`results-data`, `docs`, `tooling`, `hold` and `oracle-gate`), so removing the
chain's jobs changes no required check.

### Threat model

The reviewer is an untrusted model inside a trusted harness with read-only
tools. It can read what the runner user can read while it runs, and can leak
it only through the review text it publishes. It cannot write, execute or
persist processes. Step timing alone is not a memory-isolation boundary:
Runner.Worker may receive the App key and the actions-write job token in the
initial job message. Immediately before the loop a fail-closed Linux
prerequisite requires Yama `ptrace_scope` 1, 2 or 3, a single non-root UID, and
no effective, permitted, inheritable or ambient `CAP_SYS_PTRACE`.
The [kernel's Yama documentation](https://www.kernel.org/doc/html/latest/admin-guide/LSM/Yama.html)
defines mode 1 as descendant-only tracing unless the target explicitly grants
access. The Worker is an ancestor, not a descendant. This relies on the
trusted runner not granting `PR_SET_PTRACER` access, and the read-only harness
not executing sudo, changing UID or acquiring capabilities. It does not claim
to withstand arbitrary code execution with the hosted user's sudo access.
Same-user parent environments are still readable; the loop must not hold the
TypeSafe key, App key, job token or Tailscale values there. A permanent Linux
CI test checks the real prerequisite and that a sibling cannot open another
process's memory. No sentinel secrets or standalone canary are introduced.

The per-job credential isolation of `0e5123270` no longer applies: its premise
was that several reviewer CLIs each held a different provider key and that
only a separate machine could keep the keys apart. With one harness and one
scoped gateway token, a job boundary between attempts protects nothing that
the controls below do not.

### Controls

These controls are permanent checks, enforced by tests and by commands that run
in every review:

- C2 total exit: after each reviewer exits, the harness process group and
  every process the loop has adopted (as a Linux child subreaper) must be
  gone. Survivors are killed and the run fails closed.
- C3 no secret in a script: no `run:` block names `secrets.*` or
  `github.token`.
- C4 disk scan: the exact TypeSafe key and job token, and the job token's
  basic-auth header form, must not appear under
  `$HOME`, `$RUNNER_TEMP`, either checkout, `/tmp`, `/var/tmp`, `/dev/shm`
  or `${TMPDIR:-/tmp}`, before the reviewer and after the loop. These are
  declared scratch roots, not all readable files. Nested roots are deduplicated
  and symlinks are not followed. Values come from the scanning step's environment.
- C5 redaction completeness: after the loop, a step holding every secret value
  the job held redacts the attempt and result files and fails if a value
  remains in clear or control-split form. The loop step cannot hold those
  values, because a same-user child can read its parent's environment.
- C6 fresh `HOME` per attempt, deleted before the next attempt, and a reset
  checkout.
- C7 token scope: permissions are exactly `actions: write`, `contents: read`,
  `pull-requests: read` and `id-token: write`; `github.token` is absent from
  the loop step; both checkouts use `persist-credentials: false`.
- C8 timeout budget: `timeout-minutes` is `max_attempts` times the largest
  reviewer timeout plus a setup allowance, under 360; the harness timeout fires
  first.
- C9 cancellation: the top-level concurrency expression and
  `cancel-in-progress` are unchanged; a cancelled run posts nothing.
- C10 the App token is minted last: the only step that references
  `ORACLE_APP_PRIVATE_KEY` runs after the loop, the second disk scan and C5.

The union of the permissions the old jobs held means every trusted step in the
job now holds posting and dispatch authority. The model never receives the job
token, so the blast radius grows only for bugs in trusted code.

### Redaction decisions

- `ORACLE_GATEWAY_URL` is sensitive. Together with `TS_OAUTH_CLIENT_ID`,
  `TS_AUDIENCE` and `ACTIONS_ID_TOKEN_REQUEST_TOKEN` it joins the names in
  `credentials.py`, so every sanitising step removes its value.
- The OIDC request token is accepted within the reviewer's read reach, as the
  gateway token already is. This relies on GitHub's documented rule that the
  token is valid only while the job runs, and its value is redacted. It was not
  probed.

### The dropped canary

A sentinel canary on a hosted runner (check C1 of the independent review that
preceded this change) was dropped by owner decision. C2 to C10 stay as
permanent checks instead of a one-time experiment.

### Fallback

If any of C2 to C10 cannot be made to pass on GitHub-hosted runners, in CI or
in the first five live runs, the shape becomes option D: the same `review`
job without the posting steps, plus a separate `post` job that holds the App
key and consumes the uploaded artifacts. The failed check and the reason go
into this record. Only a failure of D as well stops the change for a new
decision.

## Consequences

- Admissions fall from five (first decision) or up to eleven to one.
- A re-run repeats the whole review, and a push cancels the whole run,
  including attempts already finished.
- The run shows one job row; attempt timing and absence reasons live in the
  step summary and the attempt artifacts, which keep their names
  (`oracle-verdict-attempt-N`, `oracle-verdict-state-<number>`) and their
  seven-day retention.
- `ORACLE_APP_ID` alone decides whether the App steps run. A set ID with a
  missing private key now fails the mint step instead of being skipped.
- A failed OMP download, tailnet join, pull request head checkout or
  classification no longer ends the review: the loop records the affected
  attempts as absent or pending, and the run still posts. A failed disk scan,
  a failed loop (including a reviewer that leaves processes behind) or a failed
  redaction check fails the job and posts nothing.
- The old per-job `plan` and classification artifacts are no longer uploaded;
  nothing read them outside the run.
