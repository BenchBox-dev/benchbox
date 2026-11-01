# Maintainer bundles and the pseudonym salt

How a maintainer's local run becomes a corpus bundle, and why the pseudonym
salt is never an owner blocker. The user-facing description is the "Pseudonym
salt" section of `results-data/README.md`.

## The salt is automatic

Resolution order, first hit wins:

1. a value passed in code;
2. the `BENCHBOX_MACHINE_ID_SALT` environment variable (an override);
3. the private file `~/.benchbox/submission_salt`;
4. otherwise a random value is generated and written to that file, but only by
   `benchbox submit`, `benchbox export` and `benchbox publish run`.

Never ask the owner for the salt and never report its absence as a blocker.
Check live state instead, without printing the value:

```bash
test -s ~/.benchbox/submission_salt && echo present || echo missing
```

Never `cat`, `echo`, log, commit or paste the file or the variable. A test or
script that needs to compare pseudonyms recomputes them inside the script and
prints only match or no match.

## Tools that never generate a salt

`scripts/stage_seed_corpus_bundle.py` and
`_project/scripts/results_explorer_corpus_migrate.py` use the variable, then an
existing file, and otherwise refuse naming the file path. The salt feeds the
permanent `result_id` (a digest of the staged bytes), so a silently generated
salt would fork corpus identity. They print a stderr notice when they use the
file. A refusal means neither the variable nor the file exists for the `HOME`
the command ran under: check `HOME`, then run `benchbox export` once or place
the maintainer value in the file, rather than working around the guard.

The CI seed-corpus lane passes the `BENCHBOX_MACHINE_ID_SALT` secret. A runner
has no salt file, so a missing secret fails the lane.

## Staging a local run

```bash
make corpus-stage-local RESULT=~/Developer/benchmark_runs/results/<run>.json OUT=<staging dir>
uv run -- python scripts/validate_submission.py --require-manifest <staging dir>/<run>.json
```

`make corpus-stage-local` wraps
`scripts/stage_seed_corpus_bundle.py --internal-manifest`. It writes the
anonymized bundle and an internal manifest into `OUT`. Keep `OUT` outside the
repository. Copying staged files into `results-data/bundles/` needs the owner's
approval. Published bundle JSON is never modified.

## Guard refusals

A guard refusal is never worked around: no bypass flag, no edited guard, no
override of a check. Before relaying a refusal as a blocker, verify it against
live repository and machine state (does the file exist, what is `HOME`, what
does the command print now), not against a summary, recap or earlier message.
