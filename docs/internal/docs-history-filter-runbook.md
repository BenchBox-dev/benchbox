# Filtered documentation history runbook

Procedure for producing the documentation history that is archived on the site
repository as `history/docs-through-<core sha>`. The archive branch is never
merged; the import pull request squashes the snapshot. This runbook covers
building and verifying the filtered history in a throwaway clone. Publishing the
archive branch is a separate, deliberate step done only for the frozen input
commit.

## What the filter keeps and drops

- Keeps history of the paths that `quality/docs-ownership.json` marks
  `site-owned` at the input commit, plus the earlier names those paths had
  (rename lineage taken from `git log -M`).
- Drops everything else at every revision: `core-only` and `core-generated`
  paths, `docs/benchmarks/queries/`, `docs/blog/`, `docs/CNAME`, and any
  `*.py`, `*.pyi`, `*.pem`, `pyproject.toml` or `uv.lock` path.
- Keeps original authors, committers, dates, commit messages and
  `Co-Authored-By` trailers. Commit hashes cited in messages are left as
  written (`--preserve-commit-hashes`).
- Removes `Claude-Session:` trailer lines and `https://claude.ai/code/session_*`
  URLs from commit messages. Nothing else in a message changes.

A path's earlier name is kept for the whole history, so a name that was later
reused by an unrelated file keeps that file's earlier revisions too. The tip
check below proves only the final tree; review the path list
(`keep-paths.txt`) when the input changes.

## Run

```bash
git clone --no-local --no-hardlinks <core remote> /tmp/docs-history-source
uv venv /tmp/docs-history-tools && uv pip install --python /tmp/docs-history-tools/bin/python git-filter-repo
PATH=/tmp/docs-history-tools/bin:$PATH uv run -- python scripts/docs_history_filter.py rehearse \
  --source /tmp/docs-history-source \
  --sha <input sha> \
  --work /tmp/docs-history-work \
  --privacy-ts <site checkout>/deploy/gates/privacy.ts \
  --triage quality/docs-history-gitleaks-triage.json
```

Check free disk space first and stop below 30 GB. `--work` must not exist.
`gitleaks` and `git-filter-repo` must be on `PATH`; `--skip-gitleaks` exists
only for the unit test.

The command builds a clone under `<work>/repo`, filters branch
`docs-history-rehearsal`, deletes every other ref and writes
`<work>/receipt.json`. It exits non-zero on any failed check or open finding. It
never pushes and never creates `history/docs-through-*` refs.

## Checks the command enforces

1. The tip tree has exactly the `site-owned` paths of the input commit, with
   identical blob ids.
2. No path in any filtered revision or any reachable object path is prohibited.
3. No commit message contains a session trailer or session URL, and no blob does.
4. Every filtered commit has the same author, committer and dates as its
   source commit, the same `Co-Authored-By` lines, and a message equal to the
   source message with only the session lines removed.
5. Rename discovery disables Git's rename limit. For each kept tip path,
   `git log --follow` must see the same names and the same commit count in the
   source and in the filtered history, so a rename lost to git's rename limit
   fails the run instead of silently shortening history.
6. Every reachable blob passes the privacy rules. The rules are read from the
   site's `deploy/gates/privacy.ts` (patterns, AWS example values, connection
   string form and placeholder passwords), so the scan cannot drift from the
   deploy gate and adds no exceptions of its own.
7. `gitleaks git` over the filtered history reports nothing outside the triage
   file.

## Gitleaks triage

A gitleaks finding is accepted only when its fingerprint is in
`quality/docs-history-gitleaks-triage.json`. A fingerprint is the SHA-256 of the
rule id, path and the matching line text, so it is independent of commit ids and
survives a re-filter. Add an entry only after reading the exact line and
confirming it is documentation placeholder text (for example a sequential fake
token or `password="password"`). A real credential is never triaged: stop,
report the path and revision without printing the value, and rotate it.

## Receipt

`receipt.json` records the input SHA, the SHA-256 of `quality/docs-ownership.json`
at that SHA, the SHA-256 of `privacy.ts`, tool versions (git, git-filter-repo,
gitleaks, Python), the path list digest, the rehearsal tip and tree ids,
commit counts, and the scan results. Re-run for the frozen input commit and keep
that receipt with the archive publication.
