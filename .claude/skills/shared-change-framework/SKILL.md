---
name: change-framework
description: "Unified source-code selection and change-execution workflow: reuse ladder, vertical slicing, post-edit verification, named branches, commits, and authorized-write PRs."
---

# Change Framework

## 1. Source-code selection

After required research and before editing source code, choose the first option
that fully satisfies the authorized requirement, repository policy, and safety
constraints:

1. No change, if the required outcome already exists.
2. An existing helper or established pattern.
3. A standard-library or native platform feature.
4. A declared, direct dependency supported by the project.
5. The smallest clear new implementation.

Do not trade correctness, compatibility, validation, security, accessibility,
or explicit requirements for an earlier option. When a simplification has a
material or non-obvious limit, document the limit and its replacement trigger.

## 2. Slicing discipline

Use for multi-file work, features, refactors, or changes likely to exceed about
100 lines before testing.

- Touch only task-required code. Report adjacent issues as "noticed but not
  touching."
- Prefer vertical slices. Use contract-first slices for parallel components and
  risk-first slices for uncertainty.
- Each slice must implement, test, and verify one logical behavior. Commit each
  verified slice separately.
- Keep the project buildable and each increment independently revertible.
- New incomplete code stays disabled by default.

## 3. Post-edit verification ladder

Run before returning, staging, or committing changes.

### Checks

1. Read edited regions with five surrounding lines. Check indentation, nesting,
   stale imports, and orphaned lines.
2. Run project lint if available.
3. Run project typecheck if available.
4. Run targeted tests, then fast/default suite for meaningful code changes.

### Rules

- Report why any verification step is unavailable.
- Fix failures before committing or report the blocker.
- Report command, result, and residual risk.
- Run the narrowest proving check first. Use fast, full, or preflight checks as
  final gates. Save long output to a log and report the summary.

### Delegated gate runs

A low-effort subagent may run deterministic gates, including full tests,
preflight, CI status, push, PR opening or follow-up, and other long
run-and-report commands.

- The main agent sets the command, working directory, log path, maximum runtime,
  and stop condition.
- The main agent retains failure analysis, fixes, scope decisions, retries, and
  final reporting.
- The subagent runs only the assigned command and reports its status, log tail,
  PR URL, and check state. It must not edit, change scope or commands, retry
  without instruction, resolve review threads, or make policy decisions.

Delegation changes only who waits. Run the same gate inline when delegation or
reasoning-effort control is unavailable.

## 4. Branch, commit, and PR workflow

Use for every authorized action that changes repository content. Review-only
capture remains local under `shared-review-protocol/SKILL.md`.

### Inputs

| Parameter | Meaning |
|---|---|
| `file_scope` | Exact file discovery rule |
| `prefix` | Conventional type: `feat`, `fix`, `refactor`, `docs`, `test`, `chore` |
| `verify_cmd` | Required pre-commit verification |

### Steps

1. Inspect the current branch, upstream, worktree status, worktree list, and
   recent log before selecting a branch.
2. Create or reuse a clearly named non-default branch for this task before
   editing. Reuse it only when its name matches the scope. If edits began on
   the default branch, switch before further edits.
3. Discover files from `file_scope`. If none exist, report "No files to commit."
   Inspect `git status --porcelain {files}` and `git diff {files}`.
4. Run Section 3. Fix failures or stop without committing.
5. Stage and commit the explicit files in one shell command.
6. Unless a direct user instruction requires local-only work, or the exception
   below for a delegated batch member applies, confirm the
   remote host, owner, and repository match an authorized repository or an
   authorized fork of it, then push the branch. Use `-u origin {branch}` when it
   has no upstream. Stop on an unknown remote, a push to a default or protected
   branch, or a history rewrite. A PR that targets the default branch is normal
   and is not a stop.
7. Create the branch's draft PR, or update the existing PR for that branch, as
   part of the same repository-write authorization unless the user explicitly
   requires local-only work, another publication mode, or the exception below
   applies.

**Exception: delegated batch member.** A worker delegated one member of an
active integration-branch batch (the `todo` skill's prepared delivery) ends at
step 5: a committed, checked change in its own member worktree, handed to the
batch leader as a landing request. It does not push for publication and does
not create or update a PR; the leader lands the member and publishes the one
final PR. This narrows steps 6-7 only for that delegation; the steps above 6,
the Section 3 gate rules (including the delegated gate-runner rules), and every
other rule here still apply to the member, except that its terminal state is the
landing request, not the repository's terminal state or a pushed branch and PR;
the leader follows steps 6-7 for
the final branch.

### Rules

- **[COMMIT-IDENTITY-001] Resolve and validate human identity.** Before the
  first commit, inspect the effective `user.name` and `user.email` and their
  config origins. Repository-local values override global values and apply to
  linked worktrees; do not assume they are intentional.
- Reject agent or service authors, including Claude, Codex, Gemini, ChatGPT, and
  vendor noreply addresses, unless the current task names that exact identity.
  Otherwise use the user's effective human author identity. A signing service
  may remain the committer behind a human author.
- Do not use `--author`, `GIT_AUTHOR_*`, or `GIT_COMMITTER_*` to bypass stale
  config. An authorized task-local override applies only to that task.
- Add an agent or service `Co-Authored-By` trailer only when the current task
  requests that exact trailer. Stale requests, tool conventions, and claims of
  agent contribution do not grant permission.
- After committing, verify the resulting author and committer with
  `git show -s --format=fuller HEAD` when identity was explicitly overridden or
  identity is part of the acceptance criteria.
- Never `git add -A`.
- Commit only authorized/session-modified files.
- Use Conventional Commits.
- Do not commit if verification fails or scope is ambiguous.
- Branch creation and commits are required close-out steps for completed
  repository write actions, not separate permissions. The authorizing user
  message, or a later one, may explicitly require local-only work or another
  publication mode.
- If repository policy uses trunk-based or direct-default-branch development,
  keep the user-authorized terminal state and use a task branch and draft PR.
  Repository policy never authorizes a direct default- or protected-branch
  write; only a direct user instruction in the current task does. Report the
  policy conflict, and report the deviation from the default task-branch
  workflow when the user authorizes that target.
- A task branch is cheap isolation; it does not alter another user's branch or
  linked worktree.
- If a required push or PR is mechanically unavailable, keep the verified
  commit and report the blocker; do not describe the workflow as complete.
- The workflow ends at the terminal state the repository's agent instructions
  name, or at a pushed branch and its created or updated draft PR when they
  name none. The authority boundary that ends it there, and the actions it does
  not authorize (deployment, activation, production publication, writes to an
  unnamed repository), is owned by `shared-review-protocol/SKILL.md` §1; do not
  restate it here. Repository policy may choose how an already-authorized step
  is performed, such as commit style, required checks, or PR template. Report
  the state reached per surface; never describe local-only work as applied or
  shipped.

## 5. Capability removal and replacement [CHANGE-REMOVAL-001]

When an item removes or disables a user-reachable capability, name the item
that replaces it. The removal item must not reach a terminal state until that
replacement is merged. If the capability is intentionally not replaced, record
that as an explicit decision on the removal item with the affected callers
named.
