# PR review evidence contract

A review that closes on a collective premise ("each X has property P",
"all instances fixed", "no downstream impact") must re-derive the premise
instance by instance from the source of truth. Reading the author's claim
text inherits the author's framing and checks the aggregate; the SCD2 N2
miss (three review passes accepted "each op backstops existence", false for
`merge_scd_type2_no_change`) is the standing counterexample.

## Rules

1. **Mechanical enumeration over rereading.** List every member first with one
   query or grep per member (one pytest node per op, one SQL check per table,
   one consumer per producer). The enumeration itself is review evidence.
2. **Successful and rejected case per instance.** For each member, show the
   intended behavior passing and a negative control failing: delete the
   guarded rows, tamper the digest, advance the ref, replay the incident
   fixture. A member with only a passing case is unverified.
3. **Producer-to-persistence-to-consumer trace.** For each accepted defect
   class, name the producer that writes the evidence, the persistence that
   holds it, and every downstream consumer that reads it; exercise the seam
   with the smallest integration fixture covering it.
4. **Exact identities, not counts.** Record test node IDs, query IDs, SHAs,
   and control outcomes. A high test count, a static wording check, or zero
   open threads is not an integrated outcome.
5. **Vacuous-success guard.** Any check that passes on an empty or deleted
   input must be paired with a positive companion that fails on that input;
   the pairing is proven by removing the guarded content in an isolated
   fixture and observing old-pass plus companion-fail.

## Records

Per-remediation records live in `_project/audits/remediation-contract-evidence.md`:
enumerated instances, node IDs, control outcomes, and the seam trace.

## Hosted review recovery

Checked against Theseus 0.2.12 (`origin/main` at `a9acebe`, six commits after
tag `v0.2.11`): `report.py` (`REPOST_NOTE`), `cli.py` (`_dispute_replies`),
`status_text.py` (`STOPPED`) and `docs/consumer-policy.md` ("Disputing a
finding"). Recheck these when the pinned Theseus version changes.

1. **`/review` on an unchanged head only reposts.** The reviewer does not
   re-judge a head whose patch it already reviewed; it reposts the previous
   verdict as "unchanged (no reviewer ran)". A new judgement needs a pushed
   change, or a dispute followed by `/review`.
2. **Clearing a carried finding.** Reply on the finding's own review thread
   with a line that starts `Dispute: D<n> - <reason>` (`D<n>` is the id printed
   on the thread, `<reason>` cites file:line counter-evidence), then comment
   `/review`. The thread reply counts only while the thread is unresolved,
   unedited and the replier is an owner, member or collaborator; a resolved
   thread is ignored, so never resolve the thread first. A top-level pull
   request comment `Dispute: D<n> - <reason>` also works when its author is
   listed in `consumer.attesters` (`.github/review.yml`), the prefix is spelled
   exactly, the comment is unedited, posted after the review that listed the
   finding, and the finding is still open; any other top-level comment is not
   a dispute. Two dispute re-reviews of one patch that leave a finding open
   escalate it; then only an attester's
   `Review adjudication: WITHDRAW|MAINTAIN D<n> - <reason>` settles it.
3. **"Reviewer process is blocked".** A review reporting "review stopped, a
   reviewer process is blocked" does not retry by itself. Comment `/review` to
   restart it.
