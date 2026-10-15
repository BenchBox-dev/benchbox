# Comment cleanup scope policy

The scope policy fixes who owns each path before comment cleanup begins. It does
not scan comment syntax, judge whether a deletion is safe, or replace the
checker; those are separate prerequisites.

## Policy and evidence

`quality/comment-cleanup-scope.json` holds the reusable classifications this
page describes: maintained roots, file classes, prioritized ownership rules and
explicit shared overrides, external, notice, directive, and TODO/FIXME evidence
requirements, known runtime and source-text carriers, and consumer-to-producer
dependencies. It also records the accepted, narrowed, or rebutted dispositions
for R1 through R18.

Rules classify a path as `ready` or `blocked`; a blocked entry has an owner and
a specific disposition. An unclassified maintained path is reported as blocked
with no owner and fails validation; it is not an external exclusion. A
same-priority ownership collision also fails, and the local manifest lists every
competing rule and owner. When roots overlap, the most specific selector wins;
equally specific roots are invalid.

A derived rule assigns a Python test to a module owner only when every
`benchbox` module it references, by static import or by a string literal that is
entirely a dotted module name (such as an `importlib.import_module` or `patch`
target), has that one owner. The test keeps its directory rule's owner if it
references the root package facade, such as `from benchbox import Name`, which
cannot be traced to one module, or has no reference, mixed owners, an unowned
module, or unparseable source. A derived rule never overrides a higher-priority
rule or exact path, and an ownership collision stays a finding. A notice path
belongs to its notice owner unless another rule names a different owner, which
is a finding. A payload record names the one region of a file that an earlier
task may edit; the file's rule still names the later owner.

The validator also lists in the local manifest, with each path's owner, every
docstring carrier (code that reads or writes docstring text) in tracked Python
source at the base: a read of a module or object docstring; a `getdoc`,
`getsource`, `getcomments`, or `cleandoc` call; a write to `__doc__`; or a
docstring on a function under a Click or FastMCP `command`, `group`, `tool`,
`resource`, or `prompt` decorator that sets no `help` or `description`, since
the framework reads it as help text.

A module docstring passed to a command-line parser is a reader; move its text
into an explicit constant before deleting the docstring. Every runtime docstring
write must have a payload record for its file whose carrier names the written
object; validation reports any that does not. An exact path rule that a derived
rule could override is invalid.

The scan finds only references it can name. It misses assignments through
`vars`, tuple targets, class bodies, `functools.wraps`, or a variable attribute
name, and the decorator check is a name heuristic. Owners of those modules must
inventory such cases by hand before deleting a docstring.

The directive and TODO/FIXME registers are incomplete. An unregistered directive
or TODO/FIXME keeps its text until it is registered with its consumer,
necessity, and owner, or an existing tracker item. The validator prints counts
of unregistered Python comments. The base has about 380 directive comments,
and every TODO/FIXME marker is registered. A TODO/FIXME marker is a comment that
starts with the word, or has it after `#`, `;`, or two spaces, followed by a
colon, such as `# TODO: link the issue` or `# noqa: E501  TODO: later`. A
comment that only mentions the word in prose or within a sentence, such as "see
the renderer TODO", is an ordinary comment that its owner removes with the rest.
Other languages need the grammar-aware checker before they can be counted.

The policy keeps no permanent path ledger. Before dispatch, write the resolved
manifest for the immutable source commit to an ignored local path:

```console
uv run -- python scripts/check_comment_cleanup_scope.py \
  --base <40-character-commit> \
  --task-set .todo-batch/comment-cleanup-task-set.txt \
  --output .todo-batch/comment-cleanup-scope-<commit>.json
```

The output lists every classified maintained path with its owner, state, rule,
and blocking disposition, plus a digest. Freeze it before parallel work; after
integration, rerun it and allow only authorized path or dependency changes. The
policy, validator, focused test, and this page form a standalone scope-policy
change. The pre-change base proves the starting tree; after that change is
committed, validate a second immutable snapshot so those files get the same
ownership check before dispatch.

## Evidence rules

An ignored local task-set file fixes this run's live task IDs. The validator
rejects any owner outside that set without querying the tracker at runtime. A
notice records the immutable blob SHA-256, retained byte range, retained-byte
SHA-256, source identity, governing requirement, owner, and blocking
disposition. Do not replace a notice with a blanket SPDX label.

The validator checks each notice against the blob at the comparison base, so a
notice entry must stay true on every later base. Record a notice in this policy
only for a rarely changed file whose exact bytes need protection, such as a
licence, EULA, or vendor patch record. Register notice comments in maintained
source individually in `quality/comment-policy.json` with their exact text,
consumer, and necessity, so a source edit cannot invalidate trusted ownership
for later pull requests.

An external entry excludes a path or directory prefix that another party owns.
It records the selector, provenance, governing owner decision, owner, and
disposition, and its selector must match a tracked path. It marks `excluded`
only paths that no ownership rule, notice, or derived rule claims. The TPC
vendor trees, including the BenchBox patches inside them, are excluded whole;
the retained-notice rule still protects the exact bytes of their licence,
notice, and patch records.

A format class marks files `comment-free` when a verifier finds no comment
syntax. The checks are deliberately conservative: a failing or ambiguous file
stays unowned.

- `strict-json`: the file, or each line of a JSON-lines file, parses as JSON
  with no duplicate keys, no `NaN` or `Infinity`, and no `comment`, `_comment`,
  `__comment`, `$comment`, or `//` key.
- `png-signature`: the file starts with the PNG signature. It does not check the
  image or its metadata chunks.
- `markdown-prose`: no code fence (also inside quotes and lists), indented line,
  `<pre>`, `<code>`, `<script>`, or `<style>` tag, HTML, MDX, MyST, Liquid, or
  Jinja comment marker, link reference definition (any label, since its title
  can hold hidden text), or commented front matter. Footnote definitions and
  inline links are allowed.
- `sql-without-comment-markers`: no `--`, `/*`, or `#` anywhere in the file.
- `empty-file`: zero bytes, such as a `.gitkeep` placeholder.

A class applies only to unclaimed paths under its selectors with a listed
extension; a file with no suffix, such as `.gitkeep`, matches by its whole name.
The final-enforcement task owns comment-free files and confirms none gained
comments.

A class with `"state": "blocked"` instead hands a file that no verifier can
clear to a named owner with a specific disposition. It must use
`markdown-needs-review` (the Markdown file fails `markdown-prose`) or
`any-content` (every file with a listed extension under the selectors). A
comment-free class must use a verifier that proves cleanliness, and a blocked
class must not, so a blocked class can never mark a file clean. List clean
classes first, because a class claims only paths earlier classes left unowned.
Before any edit, a blocked class's owners decide by provenance whether each file
is maintained, frozen evidence, or an external source.

A directive records its exact token, occurrence count in the file, actual
consumer, necessity, smaller alternative considered, owner, and removal trigger.
A TODO or FIXME records its path, token, owner, blocking disposition, and an
existing tracker reference or approved destination. The validator requires the
token in the file at the base and a matching directive count, and reports a
directive or obligation owner that differs from its path's owner. Deletion alone
does not satisfy the obligation.

Record known runtime and source-text carriers only with source proof. New or
unresolved carriers stay blocked until their owner establishes reader, producer,
and behavior evidence. This policy does not claim parser, syntax, safe-deletion,
strict/report, raw-payload parity, or algorithmic assurance.

## Dispatch boundaries

Migrate a consumer before deleting its producer. An edge with a blocked consumer
blocks the producer.

The committed edges cover two kinds of reader, each traced to its source. Three
production readers (`benchbox/core/query_catalog.py`, `benchbox/core/dryrun.py`,
and `benchbox/mcp/tools/benchmark.py`) return `inspect.getsource` of the
registered DataFrame implementations, so each of the 38 files that define them
(resolved from the live registry) is a producer for all three. Three tests
assert that specific docstring text is present. A test that only asserts absence
or a code token is not an edge, since deleting a comment cannot break it. The
list is not exhaustive: Click and FastMCP help read a docstring in the same
file, which an edge cannot express, and owners must still inventory readers
reached through dynamic names.

Scope validation covers only ownership, evidence, and dependency data. The
checker owns deletion comparison, syntax and parser coverage, directive grammar,
and strict/report enforcement. Shared tooling owns public command and CI wiring.

Under this policy, do not change TPC-DS stream ordering, RNG, timing,
missing-count behavior, query algorithms, or payload identity, and do not
rewrite historical results. Keep useful nonempty query references, and record
unsupported assurance as a blocker instead of adding inert replacement prose.

## Delivery on one integration branch

All remaining cleanup stays on one isolated integration branch until the whole
scope is complete and validated, then ships in one final pull request to
`develop`. That pull request also carries foundation corrections, soundness
paths, payload changes, the exception register, and blocking enforcement. Local
chunks ease integration and review; they are not separate delivery units.

### Tracker lifecycle

The repository owner exempted this work from registered-batch delivery, so it
uses the ordinary serial tracker lifecycle without a schema migration or
registered-batch cutover. Before work, record an immutable starting commit, an
ordered task set, and frozen ownership. Only the integrator writes tracked files
on the shared branch. Validated local work may order dependent implementation,
but a task finishes only after the final pull request merges. Preserve claim
ownership; release an owned claim with notes naming the work and evidence.

### Ownership and parallel work

Refresh live source, tracker requirements, and open pull requests. Record
conflicts and their owners, and reconcile them before final delivery instead of
excluding required files.

Use a linked worktree and `make agent-write-preflight`. Parallel workers inspect
bounded paths and write only ignored proposals or review reports; they do not
change Git configuration, tracker state, or the shared test lock. The integrator
applies proposals in consumer-before-producer order and runs shared tests one at
a time.

### Checks for each local chunk

Keep evidence for intentional behavior changes separate from pure deletion. Each
chunk requires:

- deletion parity against an exact recorded base and path set;
- strict comment-policy checks for its owned paths, with retained text
  registered;
- ownership checks and evidence that every affected reader is migrated;
- applicable Ruff, formatting, and compilation checks;
- bounded module and consumer tests under the shared test lock.

A mixed branch cannot pass as deletion-only; review its reader, checker, policy,
and payload changes separately. Preserve raw SQL and output identity unless an
intentional change has the required hash, token, order, parameter, and hint
evidence, plus applicable correctness, plan, and timing evidence.

### Whole-scope acceptance and publication

Finish and measure the pilot before broad deletion; its authoring and review
cost sets local chunk sizes and review budgets. Repair a failing chunk; do not
drop required scope to pass.

Before publication, the whole scope needs zero unapproved prose and zero
unresolved executable coverage gaps. Reconcile retained notices and directives,
generators, contracts, and decision records. Validate report, strict, staged,
transition, and workflow behavior. Complete final enforcement and register
evidence before setting the candidate policy to blocking, and include strict CI
wiring in the same pull request. Advisory-base acceptance and post-merge
blocking are separate checks.

Assess open pull requests for enforcement impact, and give the repository owner
the impact list and any notification text. An authorized operator sends
notifications before activation; tracker text alone does not authorize messages.
With isolated tests of the actual workflow path, prove that an injected
prohibited comment is rejected and an unchanged clean candidate accepted. After
merge, verify the fresh trusted base and collect hosted enforcement evidence
from the next legitimate pull request. Do not create an extra probe pull
request.

Reconcile with current `develop` before final readiness, rebasing when needed,
and refresh affected evidence after any base or head change. List every changed
path with Git; capped hosted file lists cannot prove complete review coverage.

Review the exact final head, resolve required and all Critical and High
findings, get the required independent Soundness review, and run canonical
preflight once content settles. The final pull request's durable description
covers resulting behavior, preserved contracts, intentional changes, and
validation; task and comparison receipts stay in local evidence or the tracker.

Follow the active landing policy and exact-head hosted checks. Soundness changes
need the `oracle-review` check passing on the current head and every review
thread resolved before arming; do not bypass that check. After the actual merge,
verify the merged tree and trusted blocking enforcement, then finish tasks in
dependency order. Local checks and pilot results do not certify UAT, deployment,
or production use.
