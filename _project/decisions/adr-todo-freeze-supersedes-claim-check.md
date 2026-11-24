# ADR: maintenance freeze supersedes claim-check for todo-db cutover
- Status: Historical (completed one-off todo-db 0.3 cutover decision).
- Date: 2026-08-08
- Decides: cutover-record-corrections-and-quiescence-probe-v2 w1
- Migration item migrate-hosted-tracker-db-to-todo-db-0-3-schema has 5 dependents; must-preserve is create-time-only and cannot be recreated without losing edges. Therefore the operative precondition is updated in place rather than re-creating the item.
- Old: never restore --replace while another actor holds a live claim
- New: never restore --replace while a leased maintenance freeze is not held; claim-check alone MUST NOT gate the destructive step. Corroborated by stable stats.events fingerprint across two reads.
- Evidence: claims cover only claim/start/done/complete; create/defer/promote/block/config take no claim — two items were created mid-window by an unclaimed actor (falsifies claim-check).
