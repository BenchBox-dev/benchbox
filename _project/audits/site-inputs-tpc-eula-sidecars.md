---
develop_sha: cbddc0cb8dd58a10fcf509eef376bf4c9824fe09
checked_sha: 509a0f014c29b5172f72804044244ade0fceabb8
measured_at_sha: 509a0f014c29b5172f72804044244ade0fceabb8
replay_sha: d740017bcfb566122ab96b0045858ec8dbeb8cc4
replay_scope: tests/unit/scripts/test_site_inputs.py at the tracked-file guard PR head; 50 tests passed
---

# TPC EULA sidecar bundle evidence

The schema 3 bundle includes a TPC EULA only alongside a matching
query-template download and only when the EULA is tracked. A sidecar
cannot expand the tracked file allowlist by itself.

## Selection controls

The positive and untracked controls are exercised by:

`tests/unit/scripts/test_site_inputs.py::test_referenced_downloads_include_matching_tpc_eula_only_when_tracked`

- TPC-DS `_sources/tpc-ds/query_templates/query1.tpl` to `_sources/tpc-ds/EULA.txt`.
- TPC-H `benchbox/_binaries/tpc-h/templates/queries/1.sql` to `_sources/tpc-h/EULA.txt`.

Tracked EULAs are included with their templates; untracked EULAs are excluded.
`test_referenced_downloads_exclude_tpc_eula_for_non_template_files` rejects
TPC-DS `templates.lst` and the TPC-H query-template `README` as sidecar triggers.

## Bundle and consumer path

At core commit `509a0f014c29b5172f72804044244ade0fceabb8`, local bundle
verification reported schema 3, 11 members, 4 pass attestations, and 1 skip.
Both EULAs in the downloads member matched their source bytes. This artifact
was built locally and is not CI-certified.

The producer-to-consumer path is:

- `referenced_downloads` selects each tracked sidecar.
- `build_downloads` copies it into the bundle's `downloads` member.
- `website/src/lib/legacy-assets.ts::publishLegacyFiles` publishes it as
  `TPC_EULA.txt` beside the query download.

The local consumer smoke used an uncommitted site worktree and a local bundle;
it is not production or certification evidence. Rebuild against the certified
`develop` artifact after the core PR merges.

## Replay

At replay commit `d740017bcfb566122ab96b0045858ec8dbeb8cc4`,
`make pr-preflight` ran all 50 tests in
`tests/unit/scripts/test_site_inputs.py` and passed, including the
tracked/untracked and non-template controls above.
