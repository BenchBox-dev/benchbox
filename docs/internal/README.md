# Internal documents

This directory is never published: `docs/publish-exclusions.txt`, which both
doc builds read, excludes it.

Put maintainer documents here: runbooks, repository and CI governance, release
and deploy procedure, evidence and measurement records, incident records, and
planning or design notes that are not for users.

New standing architecture decision records live in `docs/internal/adr/`.
Operational, campaign, and dated decision records (along with a few older
`adr-*` records such as `adr-astro-unified-site.md` kept at their original
paths) live in `_project/decisions/`. Agent governance
documents live in `docs/agent/`. User-facing pages are owned by the
`BenchBox-dev/benchbox-site` repository. Pages under `docs/operations/` are
published only when `docs/publish-allowlist.txt` lists them.
