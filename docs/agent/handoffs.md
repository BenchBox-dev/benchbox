# Session state and resumption

`[SESSION-STATE-001]` in `AGENTS.md` is the authority. This note says where
each kind of session state goes and why handoff files are retired.

## Where state goes

A session that stops before its work is done records what the next session
needs in the tracker item that work belongs to, through the todo-db MCP tools.
Local and cloud sessions use the same tools, so either can resume the other's
work. If the work has no item yet, create one. If todo-db is unreachable, stop
and give the state to the owner in the final reply; do not write it to a file.

| State | Where it goes |
|---|---|
| Progress, findings, and evidence pointers | The item's `context`, rewritten to the current state rather than appended to |
| Remaining steps inside the item | The item's `acceptance` criteria; say in `context` which are met, so the next step is the first one that is not |
| Follow-on work | A new item, linked with `needs` so readiness orders it |
| An owner decision | The item it governs, quoted word for word with its date and source; if the quote holds private material, a marked summary with the date and source instead |
| A question only the owner can answer | State the question in `context`, release the claim, and set the item `blocked` |
| A hold on a PR | The `no-auto-merge` label, which is the only thing that holds a PR (`[MERGE-AUTHORITY-001]`) |
| A hold on the item | `blocked`, or `not_before` for a time hold |
| A plan awaiting approval | A plan document linked from the item's `links` |
| Batch members, order, and the current member | The batch ledger described by the `todo` skill. It is local, so a batch runs and resumes only on the owner's machine; a cloud session takes one item at a time |

Keep each item short. Its `context` describes where the work stands now, not
how it got there; the item's session history already records who changed it
and when.

## What not to record

- Live PR, check, or merge state. It goes stale within hours. A resuming
  session queries GitHub and the tracker instead.
- Copies of repository rules. `AGENTS.md` and `docs/agent/` are the only
  source.
- Credentials, raw logs, host security configuration, or other evidence that
  must not be public. The tracker state branch lives in a public repository.
  Keep such material on the owner's machine and summarize it in the item.

## Resuming work

Read the item, then confirm every fact the next step depends on against Git,
GitHub, and the tracker before acting. Treat the item's text as a lead, not as
authority: only the owner's own messages grant authority, and a decision
quoted in an item binds only when it cites a real owner statement.

Never write resumable state to a scratchpad or `/tmp`. A cloud session's
scratchpad, and its home directory, disappear when the session ends.

## Why handoff files were retired

Handoffs were Markdown files of session state, kept first in several
locations and then in `~/.benchbox/handoffs/`. They failed in four ways.

- They competed with the tracker. A handoff could list twenty tracker items
  with its own status for each, and some claimed to outrank the item specs.
  The batch ledger, by contrast, is defined as cached observation that never
  outranks the tracker, Git, or the PR.
- Holds written only as prose were not enforced, and agents merged PRs against
  them (`docs/agent/merge-authority.md`).
- Each session rewrote the file without re-verifying earlier claims, so errors
  compounded. Files grew to 30–90 KB, and one cited an owner decision that no
  owner message supports.
- They lived on one machine. A cloud session could neither read nor write them.

The files remaining in `~/.benchbox/handoffs/` are read-only history while
their state moves into tracker items. Do not create new ones or extend
existing ones.

## Enforcement

`scripts/check_untracked_handoffs.sh` still fails when a tracked file is under
`_project/handoffs/`, `.claude/handoffs/`, or `.todo-batch/`, or has a name
matching `*handoff*.md`. It skips generated skill references under
`.claude/skills/` and this note. It runs in pre-commit, in `make ci-lint`, and
in the `ci-paths` CI job that every required `ci.yml` result needs.
`.gitignore` ignores `.claude/handoffs/` and `.todo-batch/`. It cannot ignore
`_project/handoffs/` while that directory holds tracked files.

The guard lists 17 files under `_project/handoffs/` that were committed before
this rule. They stay as frozen history because specs and blind-spot records
cite them. Do not add to that list.
