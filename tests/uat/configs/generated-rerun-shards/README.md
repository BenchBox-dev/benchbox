# Generated rerun shards (frozen historical)

These YAML files are **generated rerun shards** — operational scratch emitted by
the 2026-05-05 tuned follow-up UAT sweep (one aggregate config plus one per
platform). They are class 3 ("generated rerun shard") in the config lifecycle
documented in https://benchbox.dev/docs/operations/uat-framework.html.

They are **not editable templates** and must not be cloned as starting points:

- They pin a specific platform/scale/resume slice from a single past sweep.
- They are retained here as evidence of that sweep, frozen as-is.
- To start a new sweep, clone a top-level `# TEMPLATE` config in
  `tests/uat/configs/` instead.

Do not add new generated shards to the top-level `tests/uat/configs/` directory;
emit them here (or to an ignored scratch path) so they cannot masquerade as
reusable templates. Ephemeral per-run resume state is `resume.json` under the
run's log dir — not a config file and not stored here.

## Retention

Shards expire 180 days after the sweep date in their filename stem
(`scripts/check_rerun_shard_retention.py`, enforced in `make ci-lint`).
When this check fails, archive the expired shards with the printed
`mkdir -p` + `git mv` command into `_project/_archive/` and leave this
directory empty until the next sweep emits shards. An empty directory is a
valid state; the corpus guard tolerates it.
