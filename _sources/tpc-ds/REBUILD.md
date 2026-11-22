# Rebuilding the bundled TPC-DS tools

The bundled `dsdgen` and `dsqgen` binaries are built from the TPC-DS 4.0.0 tools distribution plus the patches in this directory. The C sources are not tracked (TPC EULA); download the TPC-DS tools from tpc.org and extract `DSGen-software-code-4.0.0/tools/` into `_sources/tpc-ds/tools/`.

## Applying the patches

First restore the tracked files that carry BenchBox build changes (`makefile`, `tokenizer.l`), then apply the patches and build. The `makefile` hunk of `stdout-support.patch` is already in the tracked file, so that patch runs with `-N`.

```bash
git checkout -- _sources/tpc-ds/tools

cd _sources/tpc-ds
patch -p1 -N < stdout-support.patch
patch -p1 < subscale-support.patch
patch -p1 < macos-build.patch
patch -p1 < web-lineitem-item-wrap.patch

../compilation/scripts/compile-all-platforms.sh
```

Apply the patches in this order; each is a unified diff against the result of the previous one. `PATCHES.md` describes `stdout-support.patch` and the sub-SF1 changes that `subscale-support.patch` carries.

## macOS build support (`macos-build.patch`)

The 4.0.0 distribution does not build on current macOS toolchains. This patch changes no generated data:

- `config.h` and `porting.h`: a `MACOS` block defining the 64-bit integer type and `MAXINT`, matching tpcds-kit.
- Files including `<malloc.h>`: skip the include under `MACOS`, where the header does not exist.
- `date.c`, `tdefs.c`, `tdef_functions.c`: give three K&R-style parameter lists explicit `int` types, which clang 16 and later require.

## Web order lineitem item wrap (`web-lineitem-item-wrap.patch`)

`mk_s_web_order_lineitem` in `tools/s_web_order_lineitem.c` steps through a per-order item permutation, but when the cursor passes the last item it adds one instead of wrapping:

```c
if (++nItemIndex  > nItemCount)
   nItemIndex += 1;
```

The patch wraps to the first item, as the catalog channel does:

```c
if (++nItemIndex  > nItemCount)
   nItemIndex = 1;
```

Without the fix, `getPermutationEntry` reads past the end of `pItemPermutation`, which is undefined behaviour, and the value read depends on the platform's memory layout:

- macOS reads item 1, so one web order receives the same item on several lines.
- Linux reads unrelated memory and emits item keys such as `AAAAAAAACICICICI` that match no item. The web returns generated from those lines inherit the bad key.

At SF 1 with seed 20260709, web order 152 in refresh sets 1 and 2 is affected. Engines that enforce `NOT NULL` on the surrogate key (CedarDB, DuckDB) reject the `LF_WR` maintenance insert because the item lookup finds no row; engines without that constraint store a default key. SF 0.01 never reaches the end of the permutation. The catalog channel (`s_catalog_order_lineitem.c`) and the base fact generators (`w_web_sales.c`, `w_store_sales.c`) already wrap to 1.

## Verification

On darwin-arm64, a build of the distribution plus the first three patches reproduces the previously shipped binary byte for byte: every SF 0.01 and SF 1 base table, SF 1 refresh sets 1 and 2, and all 99 `dsqgen` queries (seed 20260709). Adding the wrap fix changes only `s_web_order_lineitem` (11 rows) and `s_web_returns` (2 rows) in each SF 1 refresh set. The rebuilt linux-arm64 and linux-x86_64 binaries produce the same files as darwin-arm64.

`tests/unit/core/tpcds/test_refresh_web_lineitem_items.py` generates SF 1 refresh sets 1 and 2 with the bundled binary and asserts that every web lineitem references a distinct, valid item and every web return references an ordered item.
