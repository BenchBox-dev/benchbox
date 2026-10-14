# Comments and docstrings

## The rule

Do not write explanatory comments or docstrings in maintained first-party code.
When the checker flags one:

1. Delete it.
2. Make the code explain itself with simpler logic or clearer names and types.
3. If it described a public contract, add the contract to the API reference.
4. If future work needs the design reason, record it outside the source.

Do not hide removed text in an unused string, fake metadata, or a `__doc__`
assignment. Keep a help string or protocol record only if something reads it.

## Check your work

Run `make comment-policy-check` before you open a pull request. It compares
your branch with `origin/develop`, then runs the parser's own tests, which can
also fail it. To reproduce a CI result, set `BASE_REF` to the commit SHA CI
used. You need Python, `uv`, Node, and npm.

If you do not set `BASE_REF` and `origin/develop` is not an ancestor of
`HEAD`, a local run compares against their merge base. A `BASE_REF` or CI base
must be an ancestor of `HEAD`.

Other tools:

- `make comment-policy-strict` scans the whole repository using your branch's
  exceptions and the base-trusted ownership manifest, so vendored and
  externally owned paths are not reported as first-party findings. The
  manifest comes from the merge-base with `origin/develop`, which is `HEAD`
  on a clean develop checkout; strict fails when that merge-base cannot be
  derived. An explicit `--ownership-base` must be an ancestor of the
  merge-base, and is rejected when `origin/develop` is unavailable to anchor
  it. Use it to diagnose; CI does not run it.
- `make comment-policy-report` lists every violation, including old ones. It
  fails only if configuration or parsing fails.
- The pre-commit hook checks staged files, with the same result as
  `make comment-policy-check`.
- `scripts/check_comment_policy.py --path <prefix>` checks one file or
  directory.

### Prove a cleanup changed nothing else

```bash
scripts/comment_parity.py --base <rev> [--head <rev>] [--path <prefix>]
```

This shows reviewers that a change only removed comments and leading
docstrings. Without `--head`, it reads your working tree. CI does not run it.

It strips leading docstrings from both versions of each changed Python file and
compares the syntax trees. Any other difference fails.

- You may replace a body left empty by a removed docstring with `pass`.
- Adding a comment or docstring fails, even if you removed another.
- A kept comment must stay with the same code. The tool matches it by text, by
  whether it ends a code line, and by the code around it. Moving it across
  blank lines is fine; moving it to another statement fails.
- Added or deleted files, changed file modes or types, unparseable source,
  changed shebang or encoding lines, and unsupported languages fail unless you
  pass `--unverified-ok <suffix>` for that suffix.
- Directives such as `# noqa` are not in the syntax tree, so removing one
  counts as removing a comment. The main checker protects registered
  directives.

## Enforcement

Enforcement is blocking. `quality/comment-policy.json` sets
`"enforcement": "blocking"`, and the checker rejects any change back to
`advisory`. The check, the hook, and CI read this setting from the base
commit, not your branch.

CI runs `scripts/run_comment_policy.py` against the base commit and passes only
at zero violations. It fails if a changed file or completed scope gains a
violation or contains input the checker cannot analyze (a coverage error), or
if configuration or parsing fails.

The check prints up to 100 failures. It escapes control characters in paths and
text from the pull request and never starts a line with `::`, so a pull request
cannot inject CI commands.

## Exceptions

Register each comment that must stay in `quality/comment-policy.json`, as one
of three kinds:

- **Directive:** a complete registered token with no explanation, such as
  `# noqa: E501`.
- **Notice:** exact required text. Its consumer is the source that requires it.
- **Fixture:** exact parser input that a named test reads.

Each entry records the file, qualified symbol or payload, full text, consumer
(what reads it), why it is needed, the smaller alternative you considered, the
owner, and when to remove it. A suppression also needs an unexpired `expires`
date. Entries do not use line numbers. Each entry allows one occurrence unless
it has an approved positive `count`.

A first-line shebang needs no entry. An encoding declaration is allowed only in
the first two lines of a Python file, and only for a non-UTF-8 encoding.

To add an exception, first confirm the text has a real reader and need; the
checker cannot judge that. You may register the exception and the code that
uses it in the same pull request: the entries count once an actual successful
oracle verdict review covers the registry head. A merely successful
oracle-review check is not enough, since that check also passes for changes
that need no review. Without that verdict the base policy still applies and
the new entries do not count, so a change never authorizes its own exception
without that review. The local check and CI fail on the new entries until the
verdict succeeds; push, obtain the success verdict, rerun the comment-policy
check, then merge. Remove unused exceptions when you review a module. A
candidate scope notice must stay inside the byte range already pinned at the
base and must not escalate a partial pin into a whole-file exclusion.

The 55 current fixtures let named tests read exact parser input the checker
cannot parse on its own. A fixture allows no comment text. Its digest covers
the reading symbol plus the module-level bindings that symbol resolves
through, so editing unrelated code in the file keeps the entry valid, while
editing the reader or a binding it resolves through breaks the match. Entries
registered before the scoped scheme carry the whole-file syntax-tree digest
instead, and the checker still honors those during the transition. Either
syntax-tree digest ignores only empty type-parameter lists, so all Python
versions agree.

## What the checker compares

The checker identifies each comment by file, kind, symbol, and exact text, and
counts every copy. Deleting an unrelated comment does not pay for a new one.
Copying a comment, editing it, or moving it to another symbol fails. No archive
of removed text is kept.

A base comparison checks changed files and completed scopes. Report and strict
modes check everything.

- **Completed scopes:** after you clean a file or directory, add its path to
  `completed`. Every violation there then fails, including old ones. You cannot
  remove a completed scope.
- **External exclusions:** each names its upstream owner and a provenance file.
  A pull request cannot widen one or overlap it with a completed scope. New
  files under an exclusion are still checked in a base comparison.
- Generated first-party code and `_sources/compilation` scripts are in scope.
  TPC templates and catalog-owned skill mirrors are not; they have their own
  provenance.

The old 90% docstring-coverage gate is retired, because a percentage cannot
tell a useful contract from filler. Before you remove a docstring that
documents an API contract, move the contract to a reference page. Existing
module docstrings are transition debt, not an example.

## How CI protects the check

The `comment-policy` job runs on every pull request and feeds the required
tooling result. Its exit status decides the result; the log is for information
only. The job stops a pull request from weakening the check
that judges it:

- **Fixed base.** The base SHA comes from the GitHub event and cannot be
  overridden. A pull request compares against the first parent of the merge
  commit CI checks out: the target branch tip. Later changes to the target are
  not charged to it.
- **Trusted code.** The launcher, checker, adapters, and hash-pinned parser
  dependency lists come from the base. CI uses the pull request's checker only
  when the base contains rollout commit
  `ed5c263c513ba65499f4918d3a7de607f280c65b` but has no trusted checker files,
  launcher, or policy registry. On any other base, a missing checker fails.
- **Isolation.** The checker ignores project configuration, import and
  installer overrides, and the pull request's `node_modules` and Python
  environments. CI installs Python, uv, and Node before checkout, so no pull
  request file can run first or change the interpreter.
- **Safe output.** The checker escapes untrusted text. Native JavaScript tests
  run inside a `stop-commands` block with a random token.
- **Pinned notices.** A separate drift step runs
  `scripts/check_comment_cleanup_scope.py` from the pull request. It can fail
  the job but does not replace or change the trusted parser.

`.github/soundness-paths.txt` protects the workflow, checker, manifest, and
tests. Changes to them need an independent soundness review.

## What the checker reads

The checker uses parsers, because a text search would confuse strings with
comments and Ruff cannot enforce this rule across languages.

- **Python:** AST and tokenizer, including standalone strings and runtime
  `__doc__` assignments.
- **JavaScript and TypeScript:** the isolated TypeScript parser.
- **SQL:** strings that hold SQL and arguments to SQL execution calls.
- **Other formats:** Pygments lexers. Embedded code gets its language's check.
- **MDX:** top-level ESM imports and exports (including TypeScript `type`,
  `interface`, `enum`, `namespace`, `declare`, and `abstract`), and JSX block
  comments outside Markdown code spans and fences.
- **Piped text:** heredocs, and literal `echo` or `printf` text piped into a
  supported interpreter, through plain `cat` stages and static `sudo`, `env`,
  or `nohup` wrappers, nested or not. Static SSH pipelines into a supported
  remote interpreter count too.
- **SQL clients:** text piped to `psql`, `duckdb`, or `sqlite3` when the
  command reads standard input. A query or file on the command line, or
  `sqlite3 -no-stdin`, is not piped input.
- **Runners:** a static shell command passed as one argument to `ssh`, `watch`,
  or `xargs`.
- **License pins:** required CI pins `LICENSE` and `COPYRIGHT.md`. If you
  change either, update its pin in the same pull request. External notices are
  checked against pins from the base.

Programs match by file name, not path. Redirecting output keeps piped input in
scope. Anything the checker cannot analyze is a coverage error, never a pass:
for example redirected standard input, ambiguous `watch` or `xargs` input,
dynamic or unsupported wrapper options, and unsupported `printf` formats or
`echo -e` escapes.

### Limits

The checker only checks syntax. Reviewers must judge whether a string has a
reader and whether code is simple. Dynamic SQL, notebook magics, custom
template languages, and unusual shell or Make constructs need review and may
need new adapter code. The adapters are `scripts/comment_syntax.py`,
`scripts/comment_payloads.py`, and `scripts/comment_execution.py`. When you
extend one, add a regression fixture to
`tests/unit/scripts/test_comment_policy.py`.

### Known gaps

The checker does not see comments in:

- TOML values, and YAML or JSON keys other than `run`, `command`, `entrypoint`,
  `entry`, `script`, SQL keys, and `package.json` scripts.
- Text written by a Python `open()` call whose path cannot be resolved; it is
  not treated as HTML output.
- Call sites of a JavaScript wrapper that only forwards to a reviewed SQL
  wrapper.
- An unreadable shell chunk with no comment marker for the interpreter it
  names, including programs built from variables at run time.
- The `-c` or `-e` argument of programs listed in `DATA_FLAG_PROGRAMS` in
  `scripts/comment_execution.py`, which treat it as data. Any other program
  given such a flag is reported.
- Commands built at run time and passed to `ssh`, `watch`, `xargs`, or similar
  runners.
- Text piped to interpreters the checker does not model, such as `perl` or
  `ruby`.
- HTML comments and MyST `%` lines in Markdown prose. In maintained docs these
  are copyright headers, generator start and end markers, `<!-- content-ok -->`
  markers read by `scripts/blog_content_validation.py`, and two `%` lines in
  `docs/blog/index.md` that keep a heading ID stable.
- Python strings that never reach recognized SQL, shell, or HTML output.
  Generated output keeps such comments where the product needs them:
  maintenance SQL headers, dry-run DDL previews, stream-file headers, the Trino
  tuning note, and the AWS Glue job script.
- JSX `{// ...}` line comments in MDX prose outside Markdown code fences.
