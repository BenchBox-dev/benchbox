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

## Gate the dependent work

A sentence in an ADR or an item description leaves a dependent item `ready`,
so the next agent to pick ready work builds it. Make the tracker hold it:

1. `create_item` for the decision. The title starts with `Decide:`; the
   description holds the options and the evidence; the acceptance is the
   owner's recorded answer.
2. `update_item` on each item that depends on it, adding the decision item to
   `needs`. `needs` is replaced wholesale, so pass the full list.
3. When the owner answers, `take` the decision item and `finish` it. Quote the
   answer word for word in `closing_note`, with its date and source, as
   `handoffs.md` requires for an owner decision. The dependent items become
   ready again.

Do this in the turn the decision is found, before starting any item that
depends on it.
