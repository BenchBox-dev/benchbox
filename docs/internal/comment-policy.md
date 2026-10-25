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

Run `make comment-policy-check` before you open a pull request. It scans the
files that differ from `origin/develop`, or the whole repository after a global
change (see below), and compares your policy with `origin/develop`, then runs the
parser's own tests, which can also fail it. To reproduce a CI result, set
`BASE_REF` to the commit SHA CI
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
- `make comment-policy-dead-exceptions` lists every registered exception with no
  current finding at its path and digest, or whose path no longer exists, with
  the commit and PR that registered it. It reuses the checker's scan, never
  edits the policy, exits 0 unless configuration or parsing fails, and CI and
  the commit hook do not run it.
- The pre-commit hook checks staged files, with the same result as
  `make comment-policy-check` for the files you changed. It scans only the
  staged files that differ from the base, and scans the whole repository when
  the commit changes `quality/comment-policy.json`, the cleanup scope policy,
  the launcher itself, any file in the base launcher's `TRUSTED_FILES` (also
  when it cannot read that list), or removes an exception. An expired
  exception fails every run, including the hook.
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

CI runs `scripts/run_comment_policy.py` against the base commit. It passes only
at zero violations in every file that differs from the base, or anywhere in the
repository after a global change. It fails if any scanned file gains a
violation or contains input the checker cannot analyze (a coverage error), or
if configuration or parsing fails. A scheduled workflow,
`.github/workflows/comment-policy-nightly.yml`, scans the whole of `develop`
every night (and on demand) with `scripts/run_comment_policy.py --full`. When
any file, changed or not, has a violation, the run fails and opens (or comments
on) an issue titled "Comment policy nightly scan failed on develop"; a clean
run closes that issue.

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
uses it in the same pull request: the entries count only once the head carries
an oracle-gate success status posted by the benchbox-oracle App and a success
oracle verdict review. Neither alone is enough: a gate status also passes for
changes that need no review. Without both, the base policy still applies and
the new entries do not count, so a change never authorizes its own exception
without that review. CI and `make comment-policy-check` fail on the new entries until the
verdict succeeds; push, obtain the success verdict, rerun the comment-policy
check, then merge. The commit hook alone also accepts a new `fixture` entry
without that verdict when all of these hold: the entry's path is a file the staged commit changes
against the base, its digest equals the digest the checker computes from the
staged content of that file, and the entry has no `count` above one, so it
cannot excuse a second finding or another path. The hook passes
`--accept-same-commit-fixtures` for this; the option is off by default, the
checker refuses it outside a staged comparison with a base or when `GITHUB_ACTIONS`
is set, and the CI job never passes it. CI behavior is unchanged: it still
ignores every new entry until the verdict covers the registry head, so a commit
that registers a fixture for a file it changes to hide a new finding passes the
hook and fails CI. Do not register a digest in an earlier pull request to get
around the hook; register the entry in the pull request that changes the code.
Remove unused exceptions when you review a module. A
candidate scope notice must stay inside the byte range already pinned at the
base, must keep the bytes of that range identical to the base, and must not
escalate a partial pin into a whole-file exclusion. The verdict counts only
when the scanned checkout is the reviewed head, or a merge commit of the base
and the reviewed head with no other content; a stale event or a modified
checkout gets no review cover.

A fixture for an input the checker cannot read statically (an unresolved
executable or HTML source) binds to a digest of its consumer and the code in
the same module that can change that input. The digest covers the top-level
function or class that holds the call, plus every other top-level statement
that, repeatedly:

- runs at import time and has an effect: a module-level or class-level call,
  delete, or attribute or subscript store, or a call in a decorator, default
  argument, or base class, other than a call to a known pure function (a
  builtin, `pytest`, `unittest.mock.patch`, `dataclasses`, `functools`) and
  other than assigning a constant or a path built only from constants. A
  definition included only for such a header contributes its header, not its
  body;
- defines or rebinds a name that covered code uses;
- passes module-level data that covered code uses to a call, stores it, or
  reads one of its attributes or items into a value, or calls a method on
  it, unless the value is a constant;
- calls or instantiates a covered function or class that mutates module-level
  data, a global, an imported object, or the process environment, or that
  has mutable class attributes;
- passes a covered function, class, or imported name as a value, assigns
  into it, or calls a method on it;
- sets an attribute named like a covered module-level name, or reaches the
  same object through another import of it;
- subclasses a covered class;
- references the consumer, or any definition enclosing the call, when the payload can come from
  the caller: a parameter, `self` or `cls` state, a constructor, or a dataclass field. Callers are
  followed transitively, through every definition they contain. The digest falls back to the
  whole module when a followed statement uses `getattr`, `setattr` or `delattr` with a
  non-literal name;
- takes a pytest fixture that covered code takes, when the fixture has module
  scope or wider or mutates module-level state.

Names resolve through enclosing function scopes, so a parameter or local that
shadows a module name does not link statements. Pytest entry points that run
implicitly (autouse fixtures, `pytestmark`, `setup_module` and the other
setup and teardown hooks, `pytest_*` hooks) are always covered, and
`monkeypatch` calls are not, because pytest reverts them.

Editing an unrelated function, class, or constant leaves the digest and the
registered entry matching, so you do not register a future digest early.
Editing the consumer, its decorators, or anything it reads changes the
digest, and the new digest is a new entry, which needs the review cover
described above. The digest falls back to the whole module when the module
has a star import; when it reaches the module namespace (`globals`, `locals`,
`vars`, `eval`, `exec`, `__import__`, `import_module`, `builtins`, `modules`,
`__dict__`, `__globals__`, frame access); when a fixture is registered in a
way the checker cannot read (a `name=` that is not a literal, `fixture(...)`
outside a decorator, `getfixturevalue` with a non-literal name); or when the
call is not inside a top-level function or class.

The digest does not track state that lives outside the covered statements:
`conftest.py`, the code of imported modules, and process-wide state such as
the working directory or environment that an unrelated test body changes
without `monkeypatch`. The whole-module digest caught an edit to such a test
body because it hashed every test; the scoped digest does not, so a review
of a change to the consumer or to import-time code has to cover process
state. Its syntax-tree hashing ignores only empty type-parameter lists, so
all Python versions agree. When the checker changes how a digest is computed,
re-hash every registered fixture in the same pull request; that change is a
new entry and takes the same review path.

## What the checker compares

The checker identifies each comment by file, kind, symbol, and exact text, and
counts every copy. Deleting an unrelated comment does not pay for a new one.
Copying a comment, editing it, or moving it to another symbol fails. No archive
of removed text is kept.

A base comparison applies the strict rules to the files that differ from the
base, and to every file in the repository after a global change: a change to
the policy, the cleanup scope policy, the launcher, any file in the base
launcher's `TRUSTED_FILES` (also when that list is unreadable), or a removed
exception or exclusion. A file that holds an exception is also scanned when the
exception's consumer file changes. The policy-level checks never depend on
which files changed: the ratchet, review gating for new exceptions, new files
under an exclusion, consumer and provenance validation, and expired-exception
findings. It reads the base only for the policy, the trusted checker files, the
ownership manifest, and the list of files that existed at the base. It never
scans the base's sources. Every violation in a scanned file fails, including old
ones. The nightly scan covers the files a pull request leaves unchanged,
including files that come into scope when the base's scope policy changes
which paths it excludes; those are first scanned by the nightly run, not by a
pull request.

- **External exclusions:** each names its upstream owner and a provenance file.
  A pull request cannot widen one. New files under an exclusion are still
  checked in a base comparison.
- **Policy ratchet:** a base comparison (`--base` without `--bootstrap`, as
  `make comment-policy-check`, the hook, and CI run it) compares your policy
  with the base policy. It fails if enforcement moves from blocking to
  advisory or an external exclusion is added or changed. Report and strict
  runs without `--base`, and bootstrap runs, skip it.
- **Scope:** every first-party path is in scope. The policy has no list of
  cleaned scopes, and a `completed` key fails schema validation.
- **Wheel bumps:** the one allowed external change replaces a pinned `.whl`
  entry with a wheel of the same distribution in the same directory, with the
  same `owner` and `provenance`; the old entry must go. The checker then
  verifies the new wheel: `provenance` is the owning `pyproject.toml`, which
  lists the owner as a dependency and binds it in `[tool.uv.sources]` to the
  new wheel's relative path. The `uv.lock` beside it has one package of that
  name, with the same source, the wheel's version, and exactly that file name
  and SHA-256 hash. The wheel's `METADATA` name and version must match. With
  `--staged` it reads these files from the index. To bump a wheel, add the new
  file and delete the old one, point `[tool.uv.sources]` at it, run `uv lock`
  in that project, change only `path` in the policy entry, and commit them
  together.
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
- **Pinned notices.** When a pull request changes `LICENSE`, `COPYRIGHT.md`,
  or `quality/comment-cleanup-scope.json`, a separate drift step runs the base
  commit's copy of `scripts/check_comment_cleanup_scope.py` against the pull
  request's files. A missing base copy fails the step. The step can fail the
  job but does not replace or change the trusted parser.

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
  are copyright headers and generator start and end markers.
- Python strings that never reach recognized SQL, shell, or HTML output.
  Generated output keeps such comments where the product needs them:
  maintenance SQL headers, dry-run DDL previews, stream-file headers, the Trino
  tuning note, and the AWS Glue job script.
- JSX `{// ...}` line comments in MDX prose outside Markdown code fences.
