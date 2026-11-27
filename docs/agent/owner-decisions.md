# Owner decisions

Procedure behind `[OWNER-DECISION-001]` in `AGENTS.md`.

An owner decision is a choice only the owner can make: one an ADR, plan, or
tracker item reserves for the owner, or one whose options change published
output, comparability, scope, or spend.

## Ask

1. Ask through the harness's blocking question tool (`ask`, `ask_question`,
   `AskUserQuestion`), one question per decision.
2. Give the options, the tradeoff, your recommendation, and every recorded
   decision, plan gate, or ADR line an option would change.
3. Treat only a reply to that question as the answer. After any other message
   ("proceed", "continue", "resume"), ask again and work only on what does not
   depend on the decision. Your recommendation stays a recommendation.

In a harness with no blocking tool, end the turn with the question as its last
paragraph.

## Hold the governed work

A sentence in an ADR or an item description leaves a governed item `ready`,
so the next agent to pick ready work builds it. Make the tracker hold it, with
the states `handoffs.md` already defines:

1. On each item the decision governs, state the question in `context`,
   release any claim, and set the item `blocked`.
2. When the owner answers, record the answer on each governed item, quoted
   word for word with its date and source, and move the item back to `open`.

Do step 1 in the turn the decision is found, before starting any item it
governs.
