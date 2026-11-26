# TPC-DI tools: sourcing and conditions

`TPC-DI_Tools_v1.1.0.zip` is the official TPC-DI tools archive (DIGen 1.1.0, the PDGF data generator, its
dictionaries and configuration), exactly as the Transaction Processing Performance Council distributes it.

| Item | Value |
| --- | --- |
| Archive | `TPC-DI_Tools_v1.1.0.zip` |
| Size | 6,676,639 bytes |
| SHA-256 | `d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b` |
| Download location | tpc.org, TPC-DI downloads (the page sits behind a registration form); downloaded by the BenchBox owner |
| Date obtained | 2026-11-21 |
| Status | Unmodified. BenchBox stores the archive byte-for-byte and never edits, repacks or patches it. |

## Files beside the archive

- `PDGF-LICENSE.txt`: the bankmark UG (haftungsbeschraenkt) end-user licence for PDGF, extracted unmodified from
  `Tools/PDGF/LICENSE.txt` in the archive.
- `PDGF-THIRD-PARTY-LICENSE.txt`: the PDGF third-party component list, extracted unmodified from
  `Tools/PDGF/THIRD-PARTY-LICENSE.txt` in the archive.
- `EULA.txt`: the TPC End User License Agreement v2.2. The archive holds no TPC licence text; this file is the
  identical copy already shipped in `benchbox/_binaries/tpc-di/` (see `NOTICE.txt` there for its source).

## Use in BenchBox

On first use BenchBox verifies the archive's SHA-256 and unpacks it into its cache directory
(`$XDG_CACHE_HOME/benchbox/tpcdi-tools/`, or `~/.cache/benchbox/tpcdi-tools/`). It never unpacks into the installed
package and never runs from the unpack itself: PDGF writes `Constants.properties` and its logs beside itself, so each
generation runs DIGen from a throwaway copy of the unpack under `tpcdi-tools/runs/`, with the user's own Java 8 (DIGen
failed on every newer Java tried: 17, 21, 25 and 27). Before DIGen starts, BenchBox prints the licence notice naming this directory; it then
answers DIGen's own licence prompt. No generator code is ported or rewritten.

## Conditions to read before use

The PDGF licence (`PDGF-LICENSE.txt`) states, among other terms:

- Use is limited to generating data for "TPC Benchmark DI" and "for internal, academic, and research purposes only"
  (sections 1.3 and 1.6).
- "You may not extend, modify, decompile, or disassemble this software."
- "You have no right to redistribute the software, on its own, merged into or integrated with other software or
  documentation." The licence says bankmark granted the TPC the right to bundle and distribute PDGF "as part of the
  'TPC Benchmark DI' only" (section 1.6), and "You may not distribute the Software provided" (section 1.8).
- The licence is governed by German law (sections 4 and 5).

The TPC End User License Agreement (`EULA.txt`) governs the TPC software in the archive. Read both before you
generate data. Results produced with these tools are not audited TPC results.

## Owner decision

On 2026-11-21 the BenchBox owner decided to ship the TPC's tools inside the BenchBox package, unmodified, with the
TPC licence, the PDGF licence and this file, so that users need only Java; and ruled that publishing BenchBox results
is research use. The package licence metadata stays MIT only; the licence texts of the shipped tools sit beside them
and in `COPYRIGHT.md`.
