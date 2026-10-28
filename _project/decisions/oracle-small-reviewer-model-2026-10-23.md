# Oracle trivial and small reviewers: GPT-6 Luna instead of Claude Haiku

Date: 2026-10-23
Status: Decided.
Related: `.github/oracle-omp-config.yml`, `.github/oracle-reviewers.yml`,
`_project/scripts/oracle_reviewers/cli.py` (`_selection`),
`docs/internal/oracle-review-v2.md` (Rounds, Model selection),
`_project/decisions/oracle-reviewer-gateway-2026-10-22.md`.

## Context

The `trivial` and `small` reviewers run the `@smol` role, which mapped to
`anthropic/claude-haiku-5-5:xhigh`. Haiku often returned JSON that did not fit
the response template: new defects shaped like prior-defect entries (`id`,
`evidence`, `fix` instead of `detail`), extra keys on prior entries, extra
top-level keys, or prose instead of JSON. Each such answer is absent
(`invalid`). Because a run gave all its slots to one reviewer, two invalid
answers left the pull request pending, and every later push went back to the
same reviewer; four pull requests sat pending this way on 2026-10-23.

## Evidence

Each candidate reviewed the same 16 cases three times through the production
`cli.py review` path and the credential gateway, with model fallback off and
the served model confirmed from OMP's event stream. The cases were 12 real pull
requests (four where Haiku had failed in CI, eight with an Opus or Sonnet
verdict on the same head) and four planted changes: an off-by-one, a page size
of zero, a SQL injection, and a behavior-preserving rename. Correctness counts
only seven cases whose answer was checked by hand against the code.

| Model | Usable verdicts | Correct on checked cases | Wrongly shipped | Median time | Cost per usable verdict |
|---|---|---|---|---|---|
| Haiku, max | 20/48 | 15/21 | 0 | 525 s | $0.256 |
| Haiku, xhigh | 30/48 | 19/21 | 0 | 209 s | $0.060 |
| Sonnet 5.5, medium | 46/48 | 16/21 | 3 | 18 s | $0.069 |
| GPT-6 Luna, medium | 39/48 | 15/21 | 6 | 15 s | $0.003 |
| GPT-6 Luna, high | 46/48 | 16/21 | 5 | 31 s | $0.005 |
| GPT-6 Luna, xhigh | 47/48 | 18/21 | 3 | 87 s | $0.010 |

Cost is OMP's list-price estimate, including retries. Two earlier rounds on a
five-case subset gave Haiku xhigh 8 of 15 usable verdicts each time; Muse Spark
1.3 and Gemini Flash 3.8 at medium were 15 of 15 but missed a real defect that
Haiku and Luna xhigh found.

## Decision

- `@smol` maps to `openai-codex/gpt-6-luna:xhigh`. Its usage-limit fallback
  chain starts with `anthropic/claude-sonnet-5-5:medium`, so the small tier
  keeps a Claude model.
- `trivial` and `small` move to a new `codex` pool, so a Codex quota absence
  skips only them and the Claude reviewers above still run.
- In the same change, a run moved up the chain after a reviewer's two unusable
  answers, so an invalid answer from the small tier reached `medium` (Sonnet,
  high) and `large` (Opus) instead of ending the run. The amendment below
  replaces this rule: Jev now decides each invalid attempt's recovery.

## Rejected options

- Haiku at any effort. Its judgment was the best of the small models when its
  JSON was valid, but about two answers in five were unusable at xhigh, and
  max was worse on validity, time and cost.
- Luna at medium or high. Both marked an open Medium defect fixed with
  evidence the code contradicts, which would have shipped the change; medium
  also often reported that it could not read files the other models read.
- Sonnet at medium as the primary. Fast and reliable in form, but it cost about
  seven times Luna xhigh per usable verdict, missed one of the checked defects
  in two of three runs, and is the same model the `medium` reviewer escalates
  to.

## Consequences

A SHIP from the small tier ends the run. The `medium` and `large` reviewers see
a change only when the small tier gives no usable answer, so they are not a
backstop for a wrong SHIP. Luna xhigh shipped one checked defect in all three
of its runs: a CI workflow that skips the freshness check exactly when a pull
request edits that check. Sonnet at medium missed it too; only Haiku caught it
every time.

The owner accepted that risk on 2026-10-23 in exchange for far fewer stalled
pull requests: in the comparison, 18 of Haiku's 48 attempts were unusable,
against 1 of Luna's. Two options were rejected: having the `medium` reviewer
confirm every small-tier SHIP (one more Sonnet review per small pull request)
and sending the classifier's logic paths straight to `large` (it would not
have caught the missed defect). Changes to review machinery still start at
the `large` reviewer, in follow-ups too.

Revisit the choice if Luna xhigh's usable rate in CI falls below 95%, if a
wrong SHIP from the small tier is found after merge, or if a new small model is
tested on the same cases with better results.

## Amendment: Jev decides how an invalid attempt recovers

Date: 2026-10-28 (owner instruction 2026-10-27)
Status: Decided. Supersedes the escalation rule in the Decision section above.

A fixed rule, retry once and then move up the chain, cannot tell a weak model's
formatting slip from a failure of the review process itself: when the process is
at fault, every reviewer fails the same way and escalation spends the large,
huge and max tiers for nothing. The owner instruction was: "Use Jev to classify
the error as retry (same model), stop (process blocked), or escalate (move up to
the next tier)."

- After each invalid attempt (a reply that fails validation or the dispute
  contract) Jev answers one choice question: `retry` runs the same reviewer
  again, at most twice per reviewer, and a retry beyond that is `escalate`;
  `escalate` moves to the next reviewer; `stop` ends the run pending and posts
  nothing that passes.
- Jev sees only the redacted validation message, the reply's keys-and-types
  fingerprint, the reviewer tier and model role, the attempt number for that
  reviewer and the earlier invalid messages of the run.
- When Jev is unavailable or unkeyed, answers outside the three labels, or is
  below the 0.60 confidence bar, the run retries the same reviewer once and then
  stops. It never escalates without a Jev answer.
- The answer, its probability and confidence are recorded on the attempt
  artifact; replay reproduces the choice.
- Other absences (`incomplete`, `empty`, `error`) keep the earlier rule: one
  retry, then the chain advances.

The model choice itself (Luna for `trivial` and `small`, the `codex` pool, the
risk accepted in Consequences) is unchanged.

Evidence before shipping: Jev answered the question on the recorded cases. A
contract failure of the kind that stalled a run ("disputed finding D1 has no
concedes boolean") was `stop` on every attempt count tried (attempt 1 on the
small tier at 0.80 probability, later attempts and later tiers at 0.93 to 1.00).
Format failures of the Haiku and Luna kind (a new defect shaped like a prior
entry, extra keys on a prior entry, extra top-level keys, prose instead of JSON,
malformed JSON, truncated JSON) were `retry` at 0.83 to 0.99 on the first attempt
and `escalate` at 0.64 to 0.93 on the second.
