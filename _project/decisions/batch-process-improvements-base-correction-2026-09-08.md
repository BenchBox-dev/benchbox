# Batch pr-batch-process-improvements: recorded base

Date: 2026-09-08
Status: Historical record of one batch run under the earlier shared-worktree
protocol, which the integration-branch loop replaced
(`docs/internal/adr/adr-batch-integration-loop.md`).

## Record

- The batch was `pr-batch-process-improvements`, with base `origin/develop`
  `b221e852dba0ec2aa4bce79f15100fb909ff5599`. That was the branch creation base;
  the develop tip had advanced since, and the recorded base stood until a
  deliberate rebase, which re-records it.
- The integrator was a single agent session. Member commits and integration
  commits shared authorship because preparation was serial under one integrator.

## Correction

The first `start` recorded the then-current develop tip instead of the branch
base. The record was removed and written again against the frozen base SHA. The
tool refuses to overwrite a recorded base, so an operator correction is
documented here and never silent.
