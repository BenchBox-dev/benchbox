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
- In the same change, a run moves up the chain after a reviewer's two
  unusable answers, so an invalid answer from the small tier reaches `medium`
  (Sonnet, high) and `large` (Opus) instead of ending the run.

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

Luna xhigh missed one checked defect in all three runs: a CI workflow that
skips the freshness check exactly when a pull request edits that check. The
small tier will miss some subtle cross-file defects that Haiku caught; the
`medium` and `large` reviewers remain the backstop. Revisit the choice if Luna
xhigh's usable rate in CI falls below 95%, or if a new small model is tested
on the same cases with better results.
