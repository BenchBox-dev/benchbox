from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _load_token_scan():
    name = "scan_explorer_tokens_public_site_contract"
    path = ROOT / "_project" / "scripts" / "scan_explorer_tokens.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _css_block(css: str, selector: str) -> str:
    start = css.index(selector)
    brace = css.index("{", start)
    depth = 0
    for index in range(brace, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : index]
    raise AssertionError(f"unterminated CSS block for {selector}")


def _variables(css: str, selector: str) -> dict[str, str]:
    block = _css_block(css, selector)
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block))


def _relative_luminance(hex_color: str) -> float:
    raw = hex_color.removeprefix("#")
    channels = [int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted([_relative_luminance(foreground), _relative_luminance(background)], reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize(
    "path",
    [
        "landing/index.html",
        "landing/prompts/index.html",
        "docs/_templates/page.html",
        "results-explorer/index.html",
        "website/src/components/ThemeScript.astro",
    ],
)
def test_early_theme_bootstrap_normalizes_stored_choice(path: str) -> None:
    source = _read(path)

    assert 'localStorage.getItem("benchbox:theme")' in source
    assert 'stored === "light" || stored === "dark" || stored === "system" ? stored : "system"' in source
    assert "document.documentElement.dataset.bbThemeChoice = choice" in source


def test_shared_theme_script_normalizes_public_api_inputs() -> None:
    source = _read("landing/shared/site-theme.js")

    assert "function normalizeChoice(value)" in source
    assert "choice = normalizeChoice(choice)" in source
    assert "return normalizeChoice(window.localStorage.getItem(storageKey))" in source


def test_public_site_token_scan_covers_landing_shell_css() -> None:
    makefile = _read("Makefile")
    scan = _load_token_scan()

    assert "landing/style.css" in makefile
    assert scan.scan_file(ROOT / "landing" / "style.css") == []


TOKENS = "landing/shared/site-tokens.css"
DARK = ':root[data-bb-theme="dark"]'

TEXT_PAIRS = [
    *[
        (text, ground)
        for text in ("text-primary", "text-secondary", "text-muted", "accent-primary")
        for ground in ("bg-primary", "bg-secondary", "bg-tertiary")
    ],
    ("on-accent", "accent-secondary"),
    ("pill-fg", "pill-bg"),
    *[
        (code, "code-bg")
        for code in (
            "code-fg",
            "code-comment",
            "code-punctuation",
            "prism-string",
            "prism-keyword",
            "prism-deleted",
            "prism-variable",
            "prism-operator",
        )
    ],
    *[
        (text, "bb-surface-data")
        for text in (
            "bb-data-fg-primary",
            "bb-data-fg-muted",
            "bb-data-fg-subtle",
            "bb-accent",
            "bb-status-success",
            "bb-status-warning",
            "bb-status-danger",
        )
    ],
    *[(text, "bb-bg-panel") for text in ("bb-fg-primary", "bb-fg-muted", "bb-accent")],
    *[(f"bb-tone-{tone}-fg", f"bb-tone-{tone}-bg") for tone in ("info", "success", "warning", "danger", "neutral")],
    ("bb-fg-on-accent", "bb-accent"),
]

SHARED_FAMILIES = {
    "color": ("bg-", "text-", "accent-", "border-", "code-", "table-", "hero-", "card-"),
    "typography": ("font-", "bb-text-", "bb-font-"),
    "spacing": ("space-", "bb-inset-"),
    "radius": ("radius-", "bb-skeleton-radius-"),
}


def test_one_token_file_defines_every_family_in_both_schemes() -> None:
    css = _read(TOKENS)
    light = _variables(css, ":root")
    dark = _variables(css, DARK)

    for family, prefixes in SHARED_FAMILIES.items():
        assert any(name.startswith(prefixes) for name in light), family
    assert {"bg-primary", "text-primary", "accent-primary", "bb-surface-data", "bb-chart-cat-1"} <= set(dark)
    assert set(dark) - set(light) == set()


def test_website_explorer_and_landing_import_the_shared_token_file() -> None:
    assert "../landing/shared/site-tokens.css" in _read("website/astro.config.ts")
    assert "landing/shared/site-tokens.css" in _read("website/src/layouts/Shell.astro")
    assert "landing/shared/site-tokens.css" in _read("results-explorer/src/index.css")
    assert 'href="shared/site-tokens.css' in _read("landing/index.html")
    assert 'href="../shared/site-tokens.css' in _read("landing/prompts/index.html")
    assert not (ROOT / "website" / "src" / "styles" / "tokens.css").exists()


def _without_final_print_block(css: str) -> str:
    start = css.rindex("@media print")
    body = _css_block(css[start:], "@media print")
    return css[:start] + css[css.index("{", start) + len(body) + 2 :]


TOKEN_DECLARATION = re.compile(r"--[\w-]+\s*:")


def test_consumers_define_no_theme_tokens_of_their_own() -> None:
    explorer_css = _without_final_print_block(_read("results-explorer/src/index.css"))
    assert TOKEN_DECLARATION.findall(explorer_css) == []
    assert "--space-xs:" not in _read("landing/style.css")
    for path in ("website/src/styles/shell.css", "website/src/styles/starlight-map.css"):
        declared = TOKEN_DECLARATION.findall(_read(path))
        assert all(name.startswith(("--header-height", "--sl-", "--pagefind-")) for name in declared), path


@pytest.mark.parametrize("path", ["landing/index.html", "landing/prompts/index.html"])
def test_landing_links_shared_tokens_after_the_legacy_theme(path: str) -> None:
    source = _read(path)

    assert source.index("shared/site-theme.css") < source.index("shared/site-tokens.css")


def test_explorer_tailwind_config_reads_the_shared_variables() -> None:
    config = _read("results-explorer/tailwind.config.js")
    css = _read(TOKENS)
    variables = _variables(css, ":root")

    assert "var(--bb-brand-${stop})" in config
    assert "var(--bb-font-mono-system)" in config
    assert "bb-font-mono-system" in variables
    for stop in re.findall(r"brandStops = \[([^\]]+)\]", config)[0].split(","):
        assert f"bb-brand-{stop.strip()}" in variables
    assert re.search(r"#[0-9a-fA-F]{6}", config) is None


@pytest.mark.parametrize("selector", [":root", DARK])
@pytest.mark.parametrize(("text", "ground"), TEXT_PAIRS)
def test_shared_text_tokens_meet_aa_contrast(selector: str, text: str, ground: str) -> None:
    css = _read(TOKENS)
    tokens = _variables(css, ":root") | _variables(css, selector)

    assert _contrast(tokens[text], tokens[ground]) >= 4.5


def test_results_dark_chart_palette_has_panel_contrast() -> None:
    css = _read(TOKENS)
    light = _variables(css, ":root")
    dark = _variables(css, ':root[data-bb-theme="dark"]')

    chart_tokens = [
        "bb-chart-cat-1",
        "bb-chart-cat-2",
        "bb-chart-cat-3",
        "bb-chart-cat-4",
        "bb-chart-cat-5",
        "bb-chart-cat-6",
        "bb-chart-cat-7",
        "bb-chart-cat-8",
        "bb-chart-cat-9",
        "bb-chart-cat-10",
        "bb-chart-success",
        "bb-chart-warning",
        "bb-chart-danger",
    ]
    for token in chart_tokens:
        assert token in light, f"light chart token missing: {token}"
        assert token in dark, f"dark chart token missing: {token}"
        assert _contrast(light[token], light["bb-surface-data"]) >= 3.0, token
        assert _contrast(dark[token], dark["bb-surface-data"]) >= 3.0, token

    for token in ["bb-chart-label", "bb-chart-label-muted", "bb-chart-axis", "bb-chart-axis-strong"]:
        assert _contrast(light[token], light["bb-surface-data"]) >= 4.5, token
        assert _contrast(dark[token], dark["bb-surface-data"]) >= 4.5, token
