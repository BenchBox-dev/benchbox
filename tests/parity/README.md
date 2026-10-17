# CLI ↔ Explorer visualization parity fixtures

`generate_visualization_fixtures.py` writes one JSON fixture per chart-math
helper into `fixtures/`. Each fixture is the checked-in contract between the
Python reference implementations (this directory) and the Explorer's
TypeScript chart-math helpers. The fixtures ship to `BenchBox-dev/benchbox-site`
in the site-inputs bundle (`explorer/parity`), where the Explorer's parity suite
asserts them byte-identically.

- `make parity-fixtures` — regenerate and overwrite the committed fixtures.
- `make parity-check` — regenerate into a tmpdir and fail on any diff
  (the CI `parity-check` job in `.github/workflows/ci.yml`, path-filtered to
  viz changes).

## Environment note: `geomean_ms` last-bit drift

`geomean_ms` is a transcendental computation (`exp`/`log`); its final bits
can vary across libm builds, so a regenerated `fixtures/geomean_ms.json` may
differ from the committed one by ~1 ulp depending on the environment
(observed: `237.9081547062432` committed vs `...34` in at least one Linux
container). `make parity-check` compares byte-exactly. The
`parity-check-promotion` TODO owns the disposition (tolerance vs fixture
regeneration) based on what real GitHub runners produce.
