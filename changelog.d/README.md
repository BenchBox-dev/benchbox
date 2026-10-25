# Changelog fragments

Release notes are assembled from the JSON files in this directory. Never edit
`CHANGELOG.md` by hand: its `[Unreleased]` section must stay empty, and CI fails
a pull request that adds text there.

## When an entry is required

A pull request whose changes touch shipped paths (`.github/changelog-paths.txt`)
needs a fragment when its title is `feat`, `fix`, `perf`, `chore(deps)`, a
breaking `!` title, or is not a conventional commit title. `docs`, `test`, `ci`,
`refactor`, `chore`, `revert` pull requests and release preparation pull
requests need none. In CI, only a fragment the pull request adds satisfies the
check; the shipped-path list is read from the base branch.

## Writing an entry

```bash
make changelog-entry CHANGELOG_CATEGORY=fixed CHANGELOG_TEXT="Fixed ..."
make changelog-entry CHANGELOG_INTERNAL="Test-only refactor with no user effect"
make changelog-preview
make changelog-check
```

Without `CHANGELOG_FRAGMENT` the file is named after the branch
(`changelog.d/<branch>.json`). Running the command again appends an entry to
the same file (at most five).

```json
{
  "schema": 1,
  "entries": [
    {
      "category": "fixed",
      "breaking": false,
      "text": "Fixed the tuning receipt applied count for sorted tables.",
      "details": ["Optional sub-bullet."],
      "platforms": ["duckdb"],
      "benchmarks": ["tpch"]
    }
  ]
}
```

Categories: `before-you-upgrade`, `added`, `changed`, `fixed`, `documentation`.
`text` is one line of 20 to 2000 characters; each `details` line is at most 600
characters. Write for users, not contributors.

Breaking change: category `before-you-upgrade`, `"breaking": true`, and a
short `"lead"`. A breaking entry needs the category and the lead together.

A change with no user effect records why instead of an entry:

```json
{"schema": 1, "internal": "Test-only refactor with no user effect"}
```

## Pull request numbers

Never write a pull request number in a fragment. Release preparation derives it
from the squash commit that added the file.

## Checks

`ci-paths` runs `scripts/changelog_fragments.py check` on every pull request:
fragment validation, the empty `[Unreleased]` guard, and the required-entry rule
for the pull request title. `make changelog-check` validates fragments locally,
and `make pr-open` checks the required-entry rule against your title before
pushing.

Editing a pull request title does not re-run `ci-paths`; the `tooling (text edit)`
check re-evaluates the entry rule against the new title, so retitling a pull
request to `fix:` or `feat:` without a fragment fails that check.

Fragments are one file per pull request, so they cannot conflict, and no local
merge driver is involved.
