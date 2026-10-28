# Phase 2 Qualitative Request Log

Append-only log of Phase 2 user requests that map to Phase 3 promotion
metrics #4-#6 in
[`_project/analysis/phase-3-promotion-metrics.md`](../analysis/phase-3-promotion-metrics.md).

## Format

Append entries under the relevant section. Each entry is a bullet
block with bold field labels.

Required fields per entry:

- **Date** (YYYY-MM-DD) — also used as the entry-count anchor
- **Source** (issue #, PR #, Discord thread, email, in-conversation)
- **Requester** (handle or "anonymous"; do not log private contact info)
- **Summary** (one sentence)

Section-specific fields:

- **Org-Spaces** entries also need an **Organization** line — distinct
  org names are what the threshold counts (one org with 5 employees
  asking is one signal, not five).

How each section is counted:

| Section            | Count rule                               |
|--------------------|------------------------------------------|
| Private/Unlisted   | distinct **Requester** values, lowercased |
| Blocked-Maintainer | total entries (one per **Date** line)    |
| Org-Spaces         | distinct **Organization** values, lowercased |

The Blocked-Maintainer section is counted by entry rather than by
requester because one heavy contributor with three blocked submissions
is a stronger signal than one submission, and the strategy doc
specifies submissions, not people.

## Private/Unlisted

Requests for results that are not publicly browsable (private to the
submitter, unlisted but linkable, or scoped to an organization).

_(none yet)_

## Blocked-Maintainer

Submissions blocked by constraints only a maintainer can resolve
(missing platform adapter, NDA-restricted hardware, schema-v2 fields
that need approval, etc.). This is *not* a place for "user got confused
by docs" — that goes into a different kind of follow-up.

_(none yet)_

## Org-Spaces

Requests for organization or team accounts: shared submission
namespace, group-level trust labels, multi-user attribution.

Each entry must include an **Organization** field (this is what the
metric counts; distinct organizations, not distinct requesters).

_(none yet)_
