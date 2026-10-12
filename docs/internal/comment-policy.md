# Comments and docstrings

Maintained first-party code has no explanatory comments or docstrings. When the
check fails, delete the text and clarify the code: simplify it, or improve names
and types. A useful public contract goes in the canonical API reference. Keep
design rationale outside source only when future work needs the reason. Do not
move removed prose into inert strings, fake metadata or runtime `__doc__`
assignments. A help string or protocol record needs an actual reader.

## Commands

- `make comment-policy-check` compares against `origin/develop`, then runs the
  native parser regressions. While enforcement is advisory it lists new
  violations without failing; once enforcement is blocking it rejects them. Set `BASE_REF` to an immutable
  commit SHA to reproduce a CI comparison.
- `make comment-policy-strict` checks the whole inventory. It applies the
  candidate's own exceptions, so it is a diagnostic, not a gate; CI runs the
  comparison mode, which honours only exceptions already on the base.
- `make comment-policy-report` lists remaining violations without rejecting
  legacy debt. Configuration and parser setup failures still fail.
- The pre-commit hook checks staged content, with the same advisory or blocking
  result as the check target.
- `scripts/check_comment_policy.py --path` checks one file or directory prefix.
- `scripts/comment_parity.py --base <revision> [--head <revision>] [--path <prefix>]`
  shows that a change only removed comments and leading docstrings. For each
  changed Python file it compares the syntax trees after leading docstrings are
  removed, so any other change is drift. A body left empty by a removed docstring
  may become `pass`. Comments or docstrings that the base did not have also
  count as drift, so a removal cannot pay for an addition. A retained comment
  must keep its place: it is matched by its text, whether it trails code, and
  the code tokens around it, so moving it to another statement is drift (moving
  it across blank lines only is not). Added or deleted files, a changed file
  mode or type, unparseable source, a changed shebang or encoding declaration,
  and files in a language it cannot compare fail unless `--unverified-ok <suffix>`
  names that suffix. A directive such as `# noqa` is not syntax-tree content, so
  removing one counts as a removed comment here; the checker's comparison of
  registered directives protects those. It is an evidence tool, not a CI gate,
  and without `--head` it reads the working tree.

The check target and hook need Python, `uv`, Node and npm. Without `BASE_REF`,
if `origin/develop` is not an ancestor of `HEAD`, a local run uses their merge
base; an explicit or CI base must be an ancestor. Native tests run only after
the source check passes, so they cannot replace the checker, but they can still
fail the command.

## Enforcement

`quality/comment-policy.json` sets `enforcement` to `advisory` or `blocking`. A
comparison against a base (the check target, the hook and CI) reads the mode
from the base policy. While it is `advisory`, the check prints new violations,
up to 100 of them, and in CI marks each as a warning on the changed line, but
exits successfully, so a pull request is never blocked for a comment. Findings
about input the checker could not analyze are reported the same way and do not
fail either, and the summary line counts them, so the gaps are visible before
the switch to blocking. Parser and configuration failures, which stop the check
before it has findings, still fail in both modes. The enforced gate is the CI
comparison (`scripts/run_comment_policy.py`) at 0 violations against the base commit.
Strict mode (`make comment-policy-strict`) is a diagnostic tool that scans the whole
inventory applying the candidate's own exceptions, rather than the baseline intersection,
and is not an enforced CI gate. `comment-policy-report` provides raw inventory counts. Paths and
comment text in the output come from the pull request, so control characters are
escaped and a line is never left starting with `::`.

Moving from `advisory` to `blocking` is a one-line change to the policy. It is
checked against the base policy, so that change is not blocked by itself, and
the next pull request is. The pull request that flips the mode is therefore
exempt by construction: its findings are reported but cannot fail it, so check
it with `make comment-policy-strict` before merging. A policy cannot be moved back from `blocking` to
`advisory`. Flip it after the open pull requests have merged or been cleaned,
so that no one meets the new rule on a branch that was started before it
existed.

## Exceptions

`quality/comment-policy.json` lists exceptions. Each names the file, qualified
symbol or payload, complete text, actual consumer, necessity, smaller
alternative considered, owner and removal condition; suppressions need an
unexpired `expires` date. Line numbers are not identities. An entry permits one
occurrence unless an approved positive `count` says otherwise. Three kinds:

- **Directive:** a whole registered token, such as `# noqa: E501`, with no
  explanatory text.
- **Notice:** exact required text, with its governing source as consumer.
- **Fixture:** exact parser input that a named test consumes.

A first-line shebang needs no entry; an encoding cookie is accepted only in the
first two lines for a non-UTF-8 Python encoding. The check target, hook and CI
use the base policy, so add an exception in an earlier change than the source
it permits. Review its real need and reader first; syntax checks cannot. Remove
unused exceptions during module review. The one current fixture lets a test run
Makefile-derived conditions to prove a broken gate still runs the guard. It
grants no comment text, and changing its consumer code invalidates it (the AST
digest ignores only empty type-parameter lists, so Python versions agree).

## Scopes and comparison

The checker compares a multiset of exact file, kind, symbol and text
identities. Deleting an unrelated comment does not pay for a new one; adding a
copy, changing text or moving prose to another symbol fails. No archive of
removed prose is kept. Base comparisons inspect changed files and completed
scopes; report and strict modes inspect everything.

- Add a cleaned file or directory prefix to `completed`. Every violation there
  then fails, including inherited ones. Completed scopes cannot be removed.
- External exclusions name upstream owners and provenance files. A candidate
  policy cannot expand them or overlap them with completed scopes, and new
  files under them are not exempt in a base comparison.
- Generated first-party code and `_sources/compilation` scripts are included;
  TPC templates and catalog-owned skill mirrors have separate provenance.
- The 90% docstring-coverage gate is retired. It required public docstrings for
  the `docs` check to pass, and a percentage cannot tell a useful contract from
  filler. Useful API contracts move to reference pages before their source
  docstrings are removed. Existing module docstrings are transition debt, not
  an endorsement.

## CI trust model

The `comment-policy` job runs on every pull request and merge group and feeds
the required tooling result. While enforcement is advisory its only failures
are parser and configuration failures. Its base SHA comes from the platform event and
cannot be overridden. The launcher, checker, adapters and hash-pinned parser
dependency specifications come from that immutable base. For a pull request,
the comparison is against the first parent of the merge commit CI checks out,
which is the target branch tip, so changes that reached the target after the
event are not charged to the pull request. A merge group keeps the event base. The checker runs
isolated from project configuration, import and installer overrides, candidate
`node_modules` and candidate Python environments. The candidate's own checker is used only to bootstrap a
base that contains the rollout commit `ed5c263c513ba65499f4918d3a7de607f280c65b`
and holds no trusted checker files, launcher or policy registry; on any other
base, a missing checker fails. The job log is informational and the exit status is what decides the result. The
checker escapes untrusted text it prints, and the native JavaScript tests run
inside a `stop-commands` fence with a random token. Every tool the trusted
launcher uses (Python, uv and Node) is set up before the pull request is checked
out, so no candidate file or configuration, such as a `.yarnrc` that names a
script, can run before the checker or change the interpreter it uses. The pull
request's own code still runs in the job through those native tests, and its
text can reach the log through other actions, so a log can be made to read
differently from what the checker found. It cannot change the exit status. `.github/soundness-paths.txt` protects these files, and changes to this wiring need the
repository's independent soundness review.

## Coverage and limits

Python is read with its AST and tokenizer, including standalone strings and
runtime docstring assignments; JavaScript and TypeScript with the isolated
TypeScript parser; SQL-valued strings and execution-sink arguments as SQL; other
formats with Pygments lexers; embedded code with its own language check.
Unknown input is a coverage error, never a pass, and under blocking enforcement
or in strict mode changed files and completed scopes always reject it. While
enforcement is advisory a coverage error is reported and counted but does not
fail the comparison. Parsers are used because text search would
confuse strings with comments, and Ruff has no cross-language ban.

This syntax rule cannot prove a string has a reader or that code is simple.
Dynamic SQL, notebook magics, custom template languages and unusual shell or
Make constructs need review and adapter work. Details are in
`scripts/comment_syntax.py`, `comment_payloads.py` and `comment_execution.py`;
add a regression fixture to `tests/unit/scripts/test_comment_policy.py` when
extending an adapter.

Known gaps, each a place where a comment can pass unreported:

- TOML values and YAML or JSON keys other than the recognized command keys
  (`run`, `command`, `entrypoint`, `entry`, `script`, SQL keys, and
  `package.json` scripts) are not extracted as code.
- A Python `open()` whose path cannot be resolved is not treated as an HTML
  sink, so text written through it is not scanned.
- A JavaScript wrapper that only passes its argument to a reviewed SQL wrapper
  is accepted without checking its call sites.
- A shell chunk that the parser cannot read is skipped when it holds no
  comment marker for the interpreter it names; a program assembled from
  variables at run time is not inspected in that case.
- Unknown programs that take `-c` or `-e` as data are listed in
  `DATA_FLAG_PROGRAMS` in `scripts/comment_execution.py`; any other program
  given such a flag is reported.
- Commands passed to a runner such as `ssh` or `watch` as one string are not
  split, so an interpreter inside that string is not followed.
- HTML comments and MyST `%` lines in Markdown prose are not scanned. In the
  maintained docs they are copyright headers, generator start and end markers,
  `<!-- content-ok -->` markers read by `scripts/blog_content_validation.py`,
  and two `%` lines in `docs/blog/index.md` that keep a heading id stable.
- Comments inside Python string values are not scanned unless the string
  reaches a recognized SQL, shell or HTML sink. Generated SQL and scripts keep
  such comments where they are part of the product's output, for example
  maintenance SQL headers, dry-run DDL previews, stream-file headers, the Trino
  tuning note and the AWS Glue job script.
- A program piped to an unmodeled interpreter on stdin (such as `printf '...' | perl`
  or `echo '...' | ruby`) is not scanned.
- A whole-file pinned notice in `quality/comment-cleanup-scope.json` (such as
  `_sources/tpc-h/PATCHES.md`) is verified against its recorded digest, but
  changes to the file are not guarded to require an accompanying pin update on branches.
- MDX (`.mdx`) files map to the Markdown scanner, so MDX imports and JSX `{/* */}`
  comments outside Markdown code fences are not inspected.
