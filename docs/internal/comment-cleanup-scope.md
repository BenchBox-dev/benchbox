# Comment cleanup scope ledger

`quality/comment-cleanup-scope.json` is an enforcement input, not a work plan.
The comment cleanup it once scheduled is finished, and the comment policy is
blocking (see [comment-policy.md](comment-policy.md)). The file now does two
jobs:

- **Ownership manifest.** It tells the comment-policy checker which tracked
  paths belong to another party, such as the TPC vendor trees, so the checker
  skips them instead of reporting them as first-party comments.
- **Notice pins.** It holds SHA-256 pins for licence, EULA, notice, and patch
  record files whose exact bytes must not change unnoticed.

Every ownership rule says `"state": "blocked"`, and every owner is a
`comment-cleanup-*` name. These are labels left from the cleanup, not open
tasks. The checker accepts only `ready` or `blocked`, so leave the states as
they are rather than inventing a finished state.

## What reads the ledger

`scripts/check_comment_cleanup_scope.py` holds all the ledger logic. Three
things call it.

1. **The comment-policy checker.** `scripts/check_comment_policy.py` imports
   `immutable_external_ownership` and runs it in `strict` mode, with or
   without a comparison base. That covers `make comment-policy-check`, the pre-commit hook,
   `make comment-policy-strict`, and the CI step "Enforce comment and
   docstring policy" in `.github/workflows/ci.yml`. It reads the ledger from a
   trusted commit, not from the working tree:
   - A run with a comparison base (local check, hook, and CI) uses that base. In
     CI that is the pull request's base SHA.
   - A run without one uses the merge-base of `HEAD` and `origin/develop`, or an
     explicit `--ownership-base` that is an ancestor of it.
2. **The pinned-notice drift check.** `--check-drift` runs in
   `make comment-policy-check`, `make check-comment-scope`, and the CI step
   "Enforce pinned notice drift". CI skips the step unless `LICENSE`,
   `COPYRIGHT.md`, or the ledger changed. When it runs, it uses the base's copy
   of the scope checker, never the pull request's copy.
3. **Full validation.** Running the script with `--base` and `--task-set`
   checks every section of the ledger and can write a resolved manifest. No CI
   job or `make` target runs it. Paths added since the cleanup have no
   ownership rule, so it reports `SCOPE001` findings for them; those findings
   do not affect any gate.

`.github/soundness-paths.txt` lists the ledger, the scope checker, and
`tests/unit/scripts/test_comment_cleanup_scope.py`. Any change to them needs
oracle review.

## What the ownership manifest enforces

The checker resolves each tracked path under `maintained_roots` to one owner:

1. The highest-priority matching entry in `ownership_rules` wins. When roots
   overlap, the most specific root applies; equally specific roots are a
   configuration error.
2. `derived_rules` can move a Python test to the owner of the `benchbox`
   modules it references, but never over a higher-priority rule.
3. A path pinned in `notices` takes the notice's owner.
4. `external_entries` mark a path `excluded` only if nothing above claimed it.

The comment-policy checker skips every `excluded` path. Two other sources can
also exclude a path, and both depend on the ownership result:

- An entry in the `external` list of `quality/comment-policy.json` excludes a
  path that no ownership rule claims, or that the `external-ownership-mirrors`
  rule claims (`tools/` and `.claude/skills/`).
- A vendored wheel listed there is excluded as an immutable dependency only
  while its bytes, its `pyproject.toml` source binding, and its `uv.lock` entry
  all match the base.

Because exclusion applies only to unclaimed paths, `ownership_rules` decide
what stays first-party. Removing or narrowing a rule can silently widen the
exclusions, so do not delete rules to tidy the file.

The checker stops with a configuration error, and the comment-policy check
fails, when:

- two rules of the same priority claim one path with different owners;
- a notice owner conflicts with the rule that claims its path;
- an external selector matches no tracked path, or two external prefixes
  overlap;
- a rule state is not `ready` or `blocked`, or an owner does not start with
  `comment-cleanup-`;
- a notice's `blob_sha256` or `retained_sha256` does not match its file at the
  base;
- a `directives` entry's token or count does not match its file at the base, or
  the working-tree ledger lists a directive whose count differs from the
  working-tree file.

For a notice under an external selector, the working-tree file must keep the
pinned bytes, and a whole-file pin must match the whole file. A pull request
may change these pins only in a run with a comparison base, and only after a success
verdict from `benchbox-oracle` covers its head. Even then a changed pin must
name a path already pinned at the base, stay inside the base range, keep those
bytes identical to the base, and not turn a partial pin into a whole-file pin.

## What the candidate ledger must satisfy

The checker validates the pull request's own ledger, read from the committed
tree, or from the index under `--staged`, for structure. The check fails when:

- a `notices` entry names a path that is not tracked at the head, or its byte
  range, `retained_sha256`, or (for a pin covering the whole file) `blob_sha256`
  does not match the head bytes;
- an `external_entries` selector or a `payloads` path matches no path tracked
  at the head;
- a notice, directive, or obligation has a missing or malformed field or an
  owner that does not start with `comment-cleanup-`.

This validation grants no exemption. Exclusions and authorization still come
from the ledger at the base, so a candidate ledger never exempts source in its
own pull request.

CI and the commit hook run the checker copied from the base commit. The
validation therefore protects pull requests whose base already contains it, not
the pull request that introduces it.

## What the drift check enforces (`SCOPE008`)

The drift check covers the two first-party pins, `LICENSE` and `COPYRIGHT.md`.
It reports a `SCOPE008` finding when:

- a pin present at the base is removed;
- a pinned file is not tracked at the base or is missing from the working tree;
- the file's `blob_sha256` or `retained_sha256`, or its `byte_start` and
  `byte_end` range, no longer match the working-tree bytes.

It also reports any notice entry that is not an object or has an invalid path.
Locally, the base is the merge-base of `HEAD` and `origin/develop` unless
`BASE_REF` gives a full commit SHA.

## How to change the ledger

Edit the JSON by hand. The comment-policy checker reads ownership from the
base, so an ownership change takes effect only after it merges.

- **Edit `LICENSE` or `COPYRIGHT.md`.** In the same pull request, update that
  file's notice: `blob_sha256`, `retained_sha256`, and `byte_end` if the length
  changed. For a whole-file pin, both digests are the file's SHA-256 and
  `byte_end` is its size in bytes (`shasum -a 256 LICENSE` and `wc -c LICENSE`).
- **Add an externally owned tree.** Add an `external_entries` item with a
  `selector` (`{"prefix": "dir/"}` or `{"path": "file"}`), `owner`,
  `provenance`, `governing_requirement`, and `blocking_disposition`. Check that
  no ownership rule claims the paths, or the entry has no effect.
- **Change a registered directive.** The `directives` list is empty. Add an
  entry only for a `# noqa` or similar comment that this ledger must pin to a
  count in a named file; update its `count` in the same pull request when the
  number changes, and remove the entry when the last comment goes.
- **Register a comment in maintained source.** Use
  `quality/comment-policy.json`, not this ledger.

Then run:

```console
make comment-policy-check
make comment-policy-strict
uv run -- python -m pytest tests/unit/scripts/test_comment_cleanup_scope.py
```

## Sections kept as a record

`format_classes`, `payloads`, `consumer_edges`, and `review_dispositions` (R1
to R18) record decisions from the cleanup. Only full validation reads them, and
no gate depends on them. `obligations` is empty; an entry there would be
checked like a directive. Leave every section in place: the loader rejects a
ledger with a missing section.

To inspect the resolved ownership of every path, write a task-set file listing
the ledger's `comment-cleanup-*` owners, one per line, under an ignored path
such as `.todo-batch/`, then run:

```console
uv run -- python scripts/check_comment_cleanup_scope.py \
  --base <40-character-commit> \
  --task-set .todo-batch/comment-cleanup-owners.txt \
  --output .todo-batch/comment-cleanup-scope-<commit>.json
```
