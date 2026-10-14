# Pull Request

## Description

Brief description of the changes in this PR (the *what* and *why*).

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation
- [ ] Refactor / chore

## Testing

How did you verify the change? Include the commands you ran (e.g.
`make pr-preflight`, targeted `pytest`, manual smoke).

## Public Contract Check

- [ ] I updated `docs/reference/public-contracts.md`, or this PR does not change
      public, beta-public, deprecated, generated, experimental, or repo-only
      contract surfaces.
- [ ] I checked result schema fields, MCP tool parameters, platform support
      status, wrapper facades, adapter subclassing hooks, and generated docs for
      contract-map impact.
- [ ] Any platform/benchmark count claim in docs is generated/checked from
      registry metadata, or explicitly marked editorial/non-authoritative.

## Documentation

- [ ] I updated the relevant user-facing docs.
- [ ] I added regression notes for behavior changes.
- [ ] I confirmed API contract wording remains accurate.
- [ ] This PR does not change `results-explorer/`, `website/`, `landing/`,
      `docs/blog/`, `_blog/` or `_project/design/site-inventory/`, or its
      description says the change must be replayed into
      `BenchBox-dev/benchbox-site` to reach benchbox.dev (see
      `CONTRIBUTING.md`).

## Code Quality

- [ ] I ran formatting and lint/type checks.
- [ ] I reviewed impacted modules for backwards compatibility and migration impact.
- [ ] I added/updated tests for behavior changes and error paths.
- [ ] I confirmed public contracts and release checklists are still valid.

## Artifact Hygiene

- [ ] I did not commit raw screenshots, browser reports, generated logs,
      benchmark outputs, or temporary binary artifacts.
- [ ] Any committed binary/raw evidence file has durable repo value and is
      justified here with its consumer and size:

## Notes

Anything you want reviewers to focus on (risks, follow-ups, deferred work).
