# TPC Software Licensing Analysis

**Date:** 2026-04-14
**Author:** Research for TODO `patch-and-redistribute-tpcds-dsdgen-subscale-support` w2
**Source:** TPC End User License Agreement v2.2 (`_sources/tpc-ds/EULA.txt`)

For how BenchBox labels and limits published TPC-derived results, see
[Publication limits](https://benchbox.dev/docs/concepts/benchmark-methodology.html#publication-limits).

## Question

Can BenchBox legally redistribute patched (modified) `dsdgen` and `dsqgen` binaries
derived from TPC-DS source code?

## Short Answer

**Yes - redistribution of modified binaries is permitted under the TPC EULA v2.2**,
subject to three concrete requirements that BenchBox must satisfy before publishing
any patched bundle. Two of these requirements also apply to the unmodified binaries
that BenchBox already ships and are currently unmet (see Gap Analysis below).

## Relevant EULA Clauses

### Clause 4b - Modification is explicitly permitted

> You may modify the Software.

Modification is an affirmative grant, not a default that requires a waiver.

### Clause 9 - Redistribution of modified software is permitted with conditions

> You may distribute the Software **as provided or as modified** as permitted under
> clause 4b of this Agreement, provided You comply with all of the terms of this
> Agreement and the following conditions:
>
> a. If You distribute the Software in **modified form**, You may only do so under a
>    license that at a minimum provides all of the protections and conditions of use
>    contained within this Agreement;
>
> b. You must include on each copy of the Software that You distribute the following
>    legend in all caps, at the top of the label and license, and in a font not less
>    than 12 point and no less prominent than any other printing:
>    **"THE TPC SOFTWARE IS AVAILABLE WITHOUT CHARGE FROM TPC."**
>
> c. You must retain all copyright, patent, trademark, and attribution notices that
>    are present in the Software; and
>
> d. **You may not charge a fee** for the distribution of this Software, including any
>    modifications permitted under clause 4.b.

### Clause 8 - TPC license terms travel with merged/integrated materials

> Any portion of the Materials merged into or integrated with other software or
> documentation will continue to be subject to the terms and conditions of this
> Agreement.

## Analysis

### What the EULA requires for modified binary distribution

| Requirement | EULA clause | BenchBox obligation | Status |
|-------------|-------------|---------------------|--------|
| Use a compatible license for TPC portions | 9a | TPC EULA governs dsdgen/dsqgen; BenchBox MIT covers BenchBox's own Python/docs. These are separable - Clause 8 ensures TPC software stays under TPC EULA regardless of the outer project license. | ✓ Architecture already separates concerns |
| Include "AVAILABLE WITHOUT CHARGE FROM TPC" notice | 9b | Must be present in each distributed bundle directory | ✗ **Missing** from current `_binaries/tpc-ds/*/` |
| Retain all copyright/attribution notices | 9c | Copyright notices in source file headers must survive into the distributed binary package | ✗ **No EULA.txt** in current `_binaries/tpc-ds/*/` |
| No fee for distribution | 9d | BenchBox is free/open-source - no fee is charged | ✓ Satisfied |

### BenchBox MIT License compatibility

BenchBox's root `LICENSE` (MIT) applies to BenchBox's own code. The TPC software is
a separate component that carries its own license (TPC EULA v2.2) via Clause 8. This
is the standard multi-license pattern used by projects that bundle third-party
components: MIT for the project's own code, component-specific license for the bundled
component.

The TPC EULA's Clause 9a requires that the distribution license "at a minimum provides
all the protections and conditions of use contained within this Agreement." The correct
interpretation is that the **TPC portions** of the distribution must be licensed under
TPC EULA (or a license at least as protective), not that the entire BenchBox project
must be relicensed. BenchBox's MIT license does not purport to relicense the TPC
software; it covers BenchBox's own contributions.

No amendments to `LICENSE` are required, but BenchBox should document the license
split explicitly (see Remediation below).

### Gap analysis - current unmodified binary distribution

The unmodified binaries currently shipped in `_binaries/tpc-ds/*/` are missing two
required elements that the EULA requires even for verbatim copies:

1. **No EULA.txt** in each bundle directory (required by Clause 9a - the EULA must
   travel with the distribution)
2. **No "AVAILABLE WITHOUT CHARGE FROM TPC" notice** (required by Clause 9b)

Patched binary distribution must remedy both gaps simultaneously. Since the patched
builds are a superset of what's already shipped, fixing these gaps covers both the
existing unmodified bundles and the new patched ones.

## Conclusion

Redistribution of patched `dsdgen`/`dsqgen` binaries is **legally viable under TPC
EULA v2.2**. The required remediation is mechanical and does not require TPC approval
or special licensing:

### Required actions before publishing any patched bundle

1. **Add `EULA.txt` to each bundle directory** (`_binaries/tpc-ds/darwin-arm64/`,
   `darwin-x86_64/`, `linux-arm64/`, `linux-x86_64/`, `windows-arm64/`,
   `windows-x86_64/`) - copy from `_sources/tpc-ds/EULA.txt`.

2. **Add a `NOTICE.txt` to each bundle directory** containing (verbatim, per Clause
   9b requirement for 12pt or equivalent prominence):
   ```
   THE TPC SOFTWARE IS AVAILABLE WITHOUT CHARGE FROM TPC.
   ```
   and attribution for the TPC-DS benchmark:
   ```
   TPC-DS benchmark tools Copyright Transaction Processing Performance Council (TPC) 2001 - 2021.
   Licensed under the TPC End User License Agreement v2.2 (see EULA.txt).
   ```

3. **Document the license split in `README.md` or `CONTRIBUTING.md`**: clarify that
   the Python/docs portions of BenchBox are MIT, and the TPC benchmark tooling in
   `_binaries/tpc-ds/` is governed by the TPC EULA v2.2.

4. **Retain source-level copyright headers** in the patched C source files - the
   existing patch set preserves all upstream headers, so this is already satisfied.

### What does NOT require TPC approval

- Modifying the source code (Clause 4b is an affirmative grant)
- Distributing the modified binaries (Clause 9 covers this)
- Keeping BenchBox under MIT for the non-TPC portions (Clause 8 only applies to
  the TPC portions, which stay under TPC EULA)

### Applicable to Windows target

No special Windows-specific licensing considerations. The same EULA governs all
platforms. The cross-compilation toolchain constraint (no MinGW on the current host)
is an engineering constraint, not a legal one.

## References

- TPC End User License Agreement v2.2: `_sources/tpc-ds/EULA.txt`
- BenchBox License: `LICENSE` (MIT)
- TPC website: https://www.tpc.org
