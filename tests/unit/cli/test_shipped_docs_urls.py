from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHBOX_ROOT = REPO_ROOT / "benchbox"

_ALLOWED_HOSTS = frozenset(
    {
        "benchbox.dev",
        "api.benchbox.dev",
        "staging.benchbox.dev",
    }
)
_DEAD_HOST = "docs.benchbox.dev"
_HOST_RE = re.compile(r"\b(?:[A-Za-z0-9-]+\.)*benchbox\.dev\b", re.IGNORECASE)
_SCAN_SUFFIXES = {".py", ".md", ".txt", ".rst", ".json", ".toml", ".yaml", ".yml"}


def _shipped_text_files() -> list[Path]:
    return sorted(path for path in BENCHBOX_ROOT.rglob("*") if path.is_file() and path.suffix in _SCAN_SUFFIXES)


def test_shipped_package_does_not_cite_dead_docs_host() -> None:
    hits: list[str] = []
    for path in _shipped_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if _DEAD_HOST in text.lower():
            rel = path.relative_to(REPO_ROOT)
            hits.append(str(rel))
    assert hits == [], (
        f"shipped files cite dead hostname {_DEAD_HOST!r} (NXDOMAIN; use https://benchbox.dev/docs/): {hits}"
    )


def test_shipped_package_benchbox_hosts_are_allowlisted() -> None:
    unknown: list[str] = []
    for path in _shipped_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO_ROOT)
        for match in _HOST_RE.finditer(text):
            host = match.group(0).lower()
            if host not in _ALLOWED_HOSTS:
                unknown.append(f"{rel}: {host}")
    assert unknown == [], f"shipped files cite unallowlisted benchbox.dev hosts: {unknown}"


def test_top_level_help_uses_live_docs_url_and_long_run_flags() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "https://benchbox.dev/docs/" in result.output
    assert _DEAD_HOST not in result.output
    assert "run --platform duckdb --benchmark tpch" in result.output
    assert "run -p duckdb" not in result.output


_OWNERSHIP_MANIFEST = REPO_ROOT / "quality" / "docs-ownership.json"
_DOC_URL_RE = re.compile(r"https://benchbox\.dev/(docs(?:/[^\s<>\"'`\[\]()#?\\|]*)?)")
_URL_TRAILING_PUNCTUATION = ".,;:!*_"
_OUTPUT_PATH_ALLOWED = frozenset({("benchbox/core/tpcds_obt/lineage.py", "docs/benchmarks/tpc-ds-obt-lineage.md")})
_LINK_SURFACES = (
    "README.md",
    "CONTRIBUTING.md",
    "docker/velox/README.md",
    "examples",
    "benchbox/core/cost/README.md",
    "benchbox/core/tuning/templates/README.md",
    "benchbox/core/write_primitives/README.md",
    "results-data/README.md",
    "results-data/SEED_CORPUS_SPEC.md",
)


def _site_owned_pages() -> frozenset[str]:
    files = json.loads(_OWNERSHIP_MANIFEST.read_text(encoding="utf-8"))["files"]
    return frozenset(path for path, owner in files.items() if owner == "site-owned")


def _repo_path_matcher(site_owned: frozenset[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(page) for page in sorted(site_owned, key=len, reverse=True))
    return re.compile(rf"(?<![\w\-/.])(?:\.{{1,2}}/)*(?:\./)?({alternatives})(?![\w\-]|\.\w)")


def _repo_path_citations(text: str, site_owned: frozenset[str]) -> list[str]:
    return [match.group(1) for match in _repo_path_matcher(site_owned).finditer(text)]


def _link_surface_files() -> list[Path]:
    paths: list[Path] = []
    for entry in _LINK_SURFACES:
        root = REPO_ROOT / entry
        if root.is_dir():
            paths.extend(p for p in root.rglob("*") if p.is_file() and p.suffix in {".md", ".py"})
        else:
            paths.append(root)
    paths.extend(path for path in _shipped_text_files() if path.suffix in {".py", ".md"})
    return sorted(set(paths))


def test_link_surfaces_do_not_cite_site_owned_pages_by_repo_path() -> None:
    site_owned = _site_owned_pages()
    hits: list[str] = []
    for path in _link_surface_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO_ROOT).as_posix()
        for cited in _repo_path_citations(text, site_owned):
            if (rel, cited) not in _OUTPUT_PATH_ALLOWED:
                hits.append(f"{rel}: {cited}")
    assert hits == [], f"cite the published https://benchbox.dev/docs/...html URL instead of a repo path: {hits}"


def _unmapped_doc_urls(text: str, site_owned: frozenset[str]) -> list[str]:
    unmapped: list[str] = []
    for match in _DOC_URL_RE.finditer(text):
        target = match.group(1).rstrip(_URL_TRAILING_PUNCTUATION)
        if target in {"docs", "docs/"}:
            continue
        if target.endswith("/"):
            mapped = f"{target}index.md" in site_owned or f"{target}README.md" in site_owned
        elif target.endswith(".html"):
            stem = target[: -len(".html")]
            mapped = f"{stem}.md" in site_owned or f"{stem}/README.md" in site_owned
        else:
            mapped = False
        if not mapped:
            unmapped.append(f"https://benchbox.dev/{target}")
    return unmapped


def test_link_surfaces_doc_urls_map_to_site_owned_pages() -> None:
    site_owned = _site_owned_pages()
    unmapped: list[str] = []
    for path in _link_surface_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        unmapped.extend(f"{path.relative_to(REPO_ROOT)}: {url}" for url in _unmapped_doc_urls(text, site_owned))
    assert unmapped == [], f"benchbox.dev doc URLs without a site-owned source page: {unmapped}"


_URL_SITE_OWNED = frozenset({"docs/usage/getting-started.md", "docs/usage/index.md"})


@pytest.mark.parametrize(
    "text",
    [
        "see https://benchbox.dev/docs/usage/getting-started.html",
        "see https://benchbox.dev/docs/usage/getting-started.html.",
        "see https://benchbox.dev/docs/usage/getting-started.html, then",
        "see https://benchbox.dev/docs/usage/getting-started.html; then",
        "see https://benchbox.dev/docs/usage/getting-started.html: then",
        "(see https://benchbox.dev/docs/usage/getting-started.html)",
        "(see https://benchbox.dev/docs/usage/getting-started.html).",
        "[guide](https://benchbox.dev/docs/usage/getting-started.html)",
        "<https://benchbox.dev/docs/usage/getting-started.html>",
        "`https://benchbox.dev/docs/usage/getting-started.html`",
        "https://benchbox.dev/docs/usage/getting-started.html#installing.",
        "https://benchbox.dev/docs/usage/getting-started.html?x=1",
        '"https://benchbox.dev/docs/usage/getting-started.html".',
        "**https://benchbox.dev/docs/usage/getting-started.html**",
        "https://benchbox.dev/docs/usage/",
        "https://benchbox.dev/docs/",
    ],
)
def test_doc_url_extraction_accepts_mapped_urls_in_any_punctuation(text: str) -> None:
    assert _unmapped_doc_urls(text, _URL_SITE_OWNED) == []


@pytest.mark.parametrize(
    "text",
    [
        "see https://benchbox.dev/docs/missing/page.html",
        "see https://benchbox.dev/docs/missing/page.html.",
        "see https://benchbox.dev/docs/missing/page.html,",
        "see https://benchbox.dev/docs/missing/page.html;",
        "see https://benchbox.dev/docs/missing/page.html:",
        "(see https://benchbox.dev/docs/missing/page.html)",
        "(see https://benchbox.dev/docs/missing/page.html).",
        "[guide](https://benchbox.dev/docs/missing/page.html)",
        "<https://benchbox.dev/docs/missing/page.html>",
        "https://benchbox.dev/docs/missing/page.html#frag.",
        "https://benchbox.dev/docs/usage/getting-started.md",
        "https://benchbox.dev/docs/usage/getting-started",
        "https://benchbox.dev/docs/missing/",
    ],
)
def test_doc_url_extraction_flags_unmapped_urls_in_any_punctuation(text: str) -> None:
    assert len(_unmapped_doc_urls(text, _URL_SITE_OWNED)) == 1


@pytest.mark.parametrize(
    "text",
    [
        "see docs/usage/getting-started.md for setup",
        "see ./docs/usage/getting-started.md for setup",
        "[guide](docs/usage/getting-started.md)",
        "[guide](./docs/usage/getting-started.md)",
        "[guide](../docs/usage/getting-started.md)",
        "[guide](../../docs/usage/getting-started.md)",
        "[guide](docs/usage/getting-started.md#installing)",
        "[guide](../docs/usage/getting-started.md#installing)",
        "[guide](<docs/usage/getting-started.md>)",
        "[guide](<../docs/usage/getting-started.md#installing>)",
        "<docs/usage/getting-started.md>",
        "`docs/usage/getting-started.md`",
        "`../docs/usage/getting-started.md#installing`",
        "[docs/usage/getting-started.md](../docs/usage/getting-started.md)",
        '"See docs/usage/getting-started.md."',
        "docs/usage/getting-started.md",
    ],
)
def test_repo_path_matcher_flags_every_relative_form(text: str) -> None:
    site_owned = frozenset({"docs/usage/getting-started.md", "docs/usage/index.md"})
    assert _repo_path_citations(text, site_owned)


@pytest.mark.parametrize(
    "text",
    [
        "https://benchbox.dev/docs/usage/getting-started.html",
        "docs/internal/release-guide.md",
        "../docs/agent/review-protocol.md",
        "docs/usage/getting-started.md.bak",
        "mydocs/usage/getting-started.md",
        "docs/usage/other-page.md",
    ],
)
def test_repo_path_matcher_ignores_other_text(text: str) -> None:
    site_owned = frozenset({"docs/usage/getting-started.md", "docs/usage/index.md"})
    assert _repo_path_citations(text, site_owned) == []


def test_repo_path_matcher_covers_every_site_owned_page() -> None:
    site_owned = _site_owned_pages()
    assert site_owned
    for page in sorted(site_owned):
        assert _repo_path_citations(f"[x](../{page}#a)", site_owned) == [page]
