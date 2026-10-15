#!/bin/sh
set -eu

legacy='_project/handoffs/2026-08-01-credential-egress-closeout.md
_project/handoffs/2026-08-29-tuning-unblock-handoff.md
_project/handoffs/2026-09-01-independent-publication-a2-handoff.md
_project/handoffs/2026-10-09-ci-devloop-batch-handoff.md
_project/handoffs/astro-unified-site-handoff-20261003.md
_project/handoffs/catalog-verified-comment-sweep-2026-05-06.md
_project/handoffs/ci-validation-evidence-2026-04-27.md
_project/handoffs/external-dry-run-retrospective-2026-04-28.md
_project/handoffs/external-dry-run-retrospective-2026-04-29.md
_project/handoffs/phase-3-review-baseline-2026-04-27.md
_project/handoffs/results-explorer-uat-corpus-integration-20260503.md
_project/handoffs/results-explorer-uat-retrospective-20260502.md
_project/handoffs/results-explorer-uat-tuned-followup-20260505.md
_project/handoffs/results-explorer-uat-tuned-followup-resume-20260505.md
_project/handoffs/skill-sync-lock-audit-2026-05-06.md
_project/handoffs/uat-methodology-w2-proposals.md
_project/handoffs/uat-methodology-w3-stress-test.md'

tracked=$(git ls-files \
  | grep -iE '(^|/)(_project/handoffs|\.claude/handoffs|\.todo-batch)/|handoff[^/]*\.md$' \
  | grep -vE '^\.claude/skills/|^docs/agent/handoffs\.md$' \
  | grep -vxF "$legacy" || true)
if [ -n "$tracked" ]; then
  echo "ERROR: agent handoff files are git-tracked:" >&2
  echo "$tracked" >&2
  echo "Move them to ~/.benchbox/handoffs/ and untrack them with 'git rm --cached <path>'." >&2
  exit 1
fi
