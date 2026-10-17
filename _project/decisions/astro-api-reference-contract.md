# API reference: authored contracts replace autodoc

Date: 2026-10-03
Status: Accepted under standing approval S1 of the
[Astro site ADR](adr-astro-unified-site.md) (decision D7).

## Decision

The Python API reference becomes a set of authored Markdown contract pages for
an explicit list of public symbols. The pages do not read docstrings. A reduced
Sphinx autodoc lane is rejected: docstrings are being removed
([comment policy](../../docs/development/comment-policy.md)), so autodoc would
render less every week and would tie the reference to source prose.

## 1. Public-symbol list

[`_project/design/api-public-symbols.json`](../design/api-public-symbols.json)
lists 81 symbols in five areas: benchmarks 28, utilities 21, core 12, results 10
and platforms 10.

A symbol is listed when it meets one of these:

- it is in `benchbox.__all__`;
- autodoc documents it today and hand-written docs, examples or the README
  import it at least once;
- hand-written docs, examples or the README import it at least five times.

Excluded:

- `benchbox.cli.*` and `benchbox.examples.*`;
- modules that are not package exports;
- eight autodoc'd names that nothing outside the package imports:
  - `ExecutionPhases`;
  - `PerformanceRegressionAlert`, `PerformanceSnapshot`, `PerformanceTracker`
    and `TimingStats`;
  - `DataValidationResult` and `TableExpectation`;
  - `SystemInfo`.

Generated query pages under `docs/benchmarks/queries/` do not count as
evidence, since they inflate counts (TPCHavoc 223 to 3).

When two import paths name the same object, the shorter exported path is
canonical (`benchbox.TPCH`) and the others are aliases (`benchbox.tpch.TPCH`).
`benchbox.TPCDI` and the `benchbox.core.tpcdi.*` symbols need the `tpcdi` extra.
The reference states this.

Five names that docs or examples import do not exist in 0.4.1. Fix or remove
them while writing the pages:

- `benchbox.QueryAnalyzer`;
- `benchbox.core.results.comparison.BenchmarkComparator`;
- `benchbox.core.results.loader.ResultLoader`;
- `benchbox.core.visualization.ascii.ASCIIBarChart`;
- `benchbox.experimental.NL2SQLBenchmark`.

## 2. Verification against the released wheel

`scripts/check_api_contract_symbols.py` checks the list:

- **Environment:** it creates an isolated `uv` environment with
  `benchbox[tpcdi]==<version>` from PyPI. The Python version and the dependency
  resolution date are pinned by the `python` and `exclude_newer` fields of
  `source`. It confirms both versions before checking.
- **Imports:** it imports exactly `from <module> import <name>` for each symbol,
  from a working directory outside the repo. The lazy `__getattr__` exports load
  only the listed names.
- **Aliases:** it checks that every alias is the same object.
- **Signatures:** it records `inspect.signature` for each callable and fails on
  drift. It never reads `__doc__`.
- **Isolation:** each symbol is imported in its own process, by
  `scripts/api_contract_probe.py`, so one import cannot mask another.

Result for 0.4.1: see [Verification record](#verification-record).

## 3. Provenance and selection rule

Source SHA: `c52e06e6cea4b62f0a7794207fdd798c72dfe8da` (develop, version
0.4.1, the same version as the latest PyPI release).

Docstrings at that SHA are input to select from, not text to copy. Rules for
each statement:

- **Keep** what a caller relies on: purpose, parameters, defaults, return
  values, raised errors, side effects, required extras and compatibility.
- **Check** each kept statement against current behaviour, by running it or
  reading the code, before writing it down.
- **Drop** what explains the implementation, filler, and claims that do not
  hold.

## 4. Page template

One page per area section, at the existing reference path (see §6). Each symbol
gets one section:

```markdown
## `<canonical import path>`

<span id="<old autodoc id>"></span>

<One sentence: what the caller gets.>

**Import:** `from <module> import <name>` · **Extras:** <none | name>

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |

### Returns

<Type and meaning.>

### Raises

<Exception: condition, or "Nothing it raises itself".>

### Example

<A short runnable example with its output.>

### Compatibility

<Aliases, deprecated names and version notes.>
```

Sections with nothing to say are omitted, except Parameters and Returns on
callables.

### Worked example

````markdown
## `benchbox.utils.scale_factor.format_scale_factor`

<span id="benchbox.utils.scale_factor.format_scale_factor"></span>

Returns the scale-factor token that BenchBox uses in file, directory and schema
names.

**Import:** `from benchbox.utils.scale_factor import format_scale_factor` · **Extras:** none

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | required | The benchmark scale factor. |

### Returns

`str`: `sf` followed by digits.

- **Values of 1 or more:** the value without its decimal point (`10` → `sf10`,
  `1.5` → `sf15`).
- **Values below 1:** `0` followed by the decimal digits (`0.1` → `sf01`,
  `0.01` → `sf001`).
- **Zero, negative integers, NaN and values below 1e-10:** return `sf0`.
- **Negative fractions:** return the same token as their absolute value
  (`-0.5` → `sf05`).

The token is not unique: `1.5` and `15` both return `sf15`.

### Raises

`OverflowError` for infinity.

### Example

```python
from benchbox.utils.scale_factor import format_scale_factor

format_scale_factor(1)      # 'sf1'
format_scale_factor(0.01)   # 'sf001'
format_scale_factor(2.25)   # 'sf225'
```
````

Every statement was checked by running the function on 0.4.1, including -1,
-10, -0.5, NaN, 1e-11, 0 and infinity. The non-unique token, the edge cases
and the `OverflowError` are new facts that the docstring did not state.

## 5. Drift check

The tool is `scripts/check_api_contract_symbols.py` (§2). It renders nothing and
checks names, alias identity and signatures against the released wheel. It
imports each symbol explicitly, so lazy exports are covered. No third-party
tool is added.

## 6. URL and anchor map

[`_project/design/site-inventory/api-reference-url-map.json`](../design/site-inventory/api-reference-url-map.json)
lists the 27 reference pages and every `id` on them (autodoc object anchors,
hand-written directive anchors, section ids and others) from the built Sphinx
site at the source SHA.

- **Pages:** every page URL is kept. The authored `.md` page replaces the
  `.rst` at the same path stem, so both renderers serve the same URL.
- **Anchors:** every object anchor and section id stays resolvable on the same
  page. It is either the id of the heading that replaced it, or an alias id
  element (the `<span id>` in the template).

Anchors of dropped symbols, such as the members of `SystemInfo`, point to an
anchored "not part of the public contract" note on the same page. No redirect
pages are needed.

The site inventory records every `id` on every page. Its diff fails when a
baseline id is missing from the same page, unless a reviewed expected-removals
entry covers it (G-c). The map is generated by `scripts/api_reference_url_map.py`
from the built Sphinx site; the command is recorded in the map.

## Verification record

- Symbol import, alias and signature check on 2026-10-03, each symbol in its
  own fresh process: 78 of 81 passed with `--strict`, and 3 failed as
  expected. The environment was built by `scripts/check_api_contract_symbols.py venv`.
  It installs `benchbox[tpcdi]==0.4.1` from PyPI under Python 3.11, with
  dependency resolution pinned to 2026-10-03T00:00:00Z. The check refuses an
  editable install or a package imported from the repo.
- Expected failures:
  - `benchbox.TPCH_DATAFRAME_QUERIES`;
  - `benchbox.core.tpch.dataframe_queries.TPCH_DATAFRAME_QUERIES`;
  - `benchbox.core.tpch.dataframe_queries.get_query`.

  Each fails in a fresh process on 0.4.1 because of a circular import between
  `benchbox.core.tpch.dataframe_queries` and
  `benchbox.core.dataframe.benchmark_suite`. They carry `known_import_failure`
  in the symbol list. The check fails once they import cleanly, so the field is
  removed when the fix ships. The contract page for them must not claim they
  import until then.
- Independent review: a reviewer subagent that did not write this record, 2026-10-03. Round 1: FAIL. Its findings were fixed. Round 2: PASS, no Critical or High findings. Its two Low findings were fixed: the known-failure match is now the exact error, and the unverifiable signature was removed.
- Approver: coordinating session
  <https://claude.ai/code/session_01GZSaWtTiCz9etgrhYRuVzu>, 2026-10-03.
