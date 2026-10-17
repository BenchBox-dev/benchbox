# Results Explorer — Token-Scan Gate

The Results Explorer retheme moves public surfaces onto CSS-variable tokens
defined in the single shared token file `landing/shared/site-tokens.css`
(consumed by `website/`, the Results Explorer and the landing page) and the
legacy static theme at `landing/shared/site-theme.css`. This gate keeps the contract durable: a PR
that reintroduces a raw Tailwind palette literal (`text-gray-700`,
`bg-blue-500`, `border-red-300`, …), arbitrary color literal, SVG hex color,
or raw `rgb()` / `rgba()` value breaks CI rather than ships silently.

## Running locally

```bash
make lint-explorer-tokens
make lint-site-theme-tokens
```

The scan is stdlib-only Python; no `uv sync` is required before it runs.

## What the gate flags

A regex match for any combination of:

- **utility prefixes** — `text`, `bg`, `border`, `divide`, `ring`,
  `placeholder`, `fill`, `stroke`, `outline`, `shadow`
- **palettes** — `slate`, `gray`, `zinc`, `neutral`, `stone`, `red`,
  `orange`, `amber`, `yellow`, `lime`, `green`, `emerald`, `teal`, `cyan`,
  `sky`, `blue`, `indigo`, `violet`, `purple`, `fuchsia`, `pink`, `rose`
- **stops** — `50`, `100`, `200`, `300`, `400`, `500`, `600`, `700`,
  `800`, `900`, `950`
- **arbitrary color classes** — `text-[#374151]`,
  `bg-[rgb(55,65,81)]`, `stroke-[rgba(...)]`
- **raw color functions / SVG literals** — `#374151`, `#374151cc`,
  `rgb(...)`, `rgba(...)`

Files scanned: `*.tsx`, `*.ts`, `*.jsx`, `*.js`, `*.css`, `*.html` under
`results-explorer/src/` for `make lint-explorer-tokens`. The
`make lint-site-theme-tokens` target runs the same scan over the static
theme/header pages and docs adapter CSS:

- `landing/shared/`
- `landing/index.html`
- `landing/style.css`
- `landing/prompts/index.html`
- `landing/prompts/prompts.css`
- `docs/_templates/page.html`
- `docs/_static/custom.css`
- `results-explorer/index.html`
- `results-explorer/src/components/Layout.tsx`

### Known coverage gaps

The following do **not** trip the gate:

- **Concatenated classnames** — `"text-gray-" + n` or
  `` `text-${color}-700` `` are not matched (the regex needs a literal
  contiguous token).

## Allowlisting an intentional literal

Append an inline marker on the same line as the literal:

```tsx
<div class="text-gray-700" />
```

```css
.legacy-badge { color: theme('colors.gray.700'); }
```

The marker is `allow-explorer-token-literal: <reason>`, written in a trailing comment in the file's own syntax: `//` for JS, TS, TSX and JSX, and `/* ... */` for CSS. In the examples above, the TSX line carries the reason `third-party widget skin` and the CSS line carries `legacy alias retained for badge migration`.

The marker requires a non-empty reason. Lines without a reason still trip
the gate.

Prefer adding a CSS variable token (`var(--bb-...)`) over allowlisting.
The allowlist is for legitimate exemptions only — third-party widget
skins, deliberate palette exports for design tooling, and similar.

## CI wiring

`.github/workflows/site-docs.yml` job `explorer-tokens` runs `make
lint-explorer-tokens` on a pull request that changes files under
`results-explorer/src/` (the `explorer-tokens` group in
`.github/path-filters.yml`). The shared site theme has its own
`site-theme-tokens` job.

### When the gate is wrong (false positive)

If the regex flags a legitimate literal, such as a comment or string that
happens to spell a Tailwind token, allowlist the line with
`// allow-explorer-token-literal: <reason>` (or the `/* … */` form for CSS).
Make the reason concrete enough that a later reviewer can decide whether to
remove the marker. If the false positive comes from the regex itself, open an
issue describing the shape so the regex can be tightened and the marker
removed.

Matching a literal in a comment or string is deliberate (see
`tests/unit/scripts/test_scan_explorer_tokens.py::test_literal_re_matches_inside_comments_and_strings_by_design`).

## Extending or removing the gate

The scan script that the `lint-explorer-tokens` and `lint-site-theme-tokens`
Makefile targets call defines `UTILITIES`, `PALETTES`, `STOPS`,
`ARBITRARY_COLOR_RE`, `HEX_RE`, and `RGB_RE`. Edit them to widen or narrow
coverage. If an ESLint or stylelint rule replaces the scan, retire the script
in the same change that wires the new rule into `npm run lint`.
