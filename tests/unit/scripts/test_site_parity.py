from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from scripts import site_inventory, site_parity
from scripts.assemble_public_site import RESULTS_FALLBACK

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>{id}</id><title>First</title><link href="{link}"/></entry>
</feed>
"""

THEME_RULE = {
    "id": "theme-ids",
    "kind": "missing fragment",
    "match": r".*#(__toc|svg-[a-z]+)",
    "reason": "theme ids belong to the retired Sphinx theme",
    "owner_approval": "pending",
}
FEED_RULE = {
    "id": "feed-links",
    "kind": "feed regression",
    "match": r".* lost link /blog/blog/.*",
    "reason": "the retired feed linked paths that never existed",
    "owner_approval": "pending",
}
REDIRECT = {
    "path": "/404.html",
    "target": "/results/",
    "reason": "the fallback restores Explorer deep links",
    "owner_approval": "pending",
}
ADDED = {"path": "/sitemap.xml", "reason": "new file", "owner_approval": "pending"}


def _write(root: Path, name: str, content: str = "x") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _objects_inv(entries: list[str]) -> bytes:
    header = b"# Sphinx inventory version 2\n# Project: BenchBox\n# Version: 1\n# The remainder of this file is compressed using zlib.\n"
    return header + zlib.compress("".join(f"{entry} -1 api.html Title\n" for entry in entries).encode())


def _page(root: Path, name: str, body: str, canonical: str | None = "") -> None:
    path = "/" + name
    default = "https://benchbox.dev/" if path == "/index.html" else f"https://benchbox.dev{path}"
    link = f'<link rel="canonical" href="{default if canonical == "" else canonical}">' if canonical is not None else ""
    _write(root, name, f"<title>T</title>{link}{body}")


def _site(
    root: Path,
    *,
    theme: bool,
    feed_link: str,
    feed_id: str = "https://benchbox.dev/blog/a.html",
    tag: bool = True,
    inventory: tuple[str, ...] = ("a std:doc",),
) -> Path:
    toc = '<div id="__toc"></div><svg id="svg-sun"></svg>' if theme else ""
    _page(root, "index.html", "<h1>Home</h1>")
    _page(root, "docs/api.html", f'<h1 id="api">API</h1><dt id="benchbox.TPCH"></dt>{toc}')
    _page(root, "blog/a.html", "<h1>A</h1>")
    _write(root, "blog/atom.xml", ATOM.format(id=feed_id, link=feed_link))
    if tag:
        _page(root, "blog/tag/duckdb.html", "<h1>duckdb</h1>")
    _write(
        root,
        "results/index.html",
        '<title>Results</title><link rel="canonical" href="https://benchbox.dev/results/"><h1>Results</h1>',
    )
    _write(
        root,
        "404.html",
        RESULTS_FALLBACK.replace("<head>", '<head><link rel="canonical" href="https://benchbox.dev/404.html">'),
    )
    (root / "docs" / "objects.inv").write_bytes(_objects_inv(list(inventory)))
    return root


def _inventory(site: Path, destination: Path) -> Path:
    site_inventory.write_sharded(site_inventory.build_inventory(site, "abc123"), destination)
    return destination


def _json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _api_map(path: Path, ids: list[str]) -> Path:
    page = {"url": "/docs/api.html", "ids": {name: {} for name in ids}, "note_anchor": None}
    return _json(path, {"pages": [page]})


def _run(
    tmp_path: Path,
    baseline: Path,
    candidate: Path,
    *,
    rules: list[dict[str, str]] | None = None,
    added_paths: list[dict[str, str]] | None = None,
    api_ids: list[str] | None = None,
    published: Path | None = None,
    extra: list[str] | None = None,
    inventory_losses: list[dict[str, str]] | None = None,
) -> tuple[int, dict]:
    out = tmp_path / "report"
    argv = [
        "--baseline",
        str(_inventory(baseline, tmp_path / "inv-base")),
        "--candidate",
        str(_inventory(candidate, tmp_path / "inv-cand")),
        "--baseline-site",
        str(baseline),
        "--candidate-site",
        str(candidate),
        "--output-dir",
        str(out),
        "--expected-removals",
        str(_json(tmp_path / "removals.json", [])),
        "--allowed-differences",
        str(_json(tmp_path / "allowed.json", [THEME_RULE, FEED_RULE] if rules is None else rules)),
        "--redirect-pages",
        str(_json(tmp_path / "redirects.json", [REDIRECT])),
        "--added-paths",
        str(_json(tmp_path / "added.json", [ADDED] if added_paths is None else added_paths)),
        "--allowed-inventory-losses",
        str(_json(tmp_path / "losses.json", inventory_losses or [])),
        "--known-broken",
        str(_json(tmp_path / "known.json", [])),
        "--api-map",
        str(_api_map(tmp_path / "api.json", ["api", "benchbox.TPCH"] if api_ids is None else api_ids)),
        *(extra or []),
    ]
    if published is not None:
        argv += ["--published-baseline", str(_inventory(published, tmp_path / "inv-published"))]
    code = site_parity.main(argv)
    return code, json.loads((out / site_parity.REPORT_JSON).read_text(encoding="utf-8"))


def test_clean_parity_lists_allowed_differences_and_redirects(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=True, feed_link="/blog/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 0
    assert report["verdict"] == "PASS"
    assert report["broken_public_urls"] == 0
    assert report["broken_fragments"] == 0
    allowed = {row["id"]: row for row in report["allowed_differences"]}
    assert allowed["theme-ids"]["count"] == 2
    assert allowed["feed-links"]["count"] == 1
    assert all(row["reason"] and row["owner_approval"] == "pending" for row in report["allowed_differences"])
    assert [row["path"] for row in report["redirects"]["pages"]] == ["/404.html"]
    assert report["redirects"]["pages"][0]["owner_approval"] == "pending"
    markdown = (tmp_path / "report" / site_parity.REPORT_MARKDOWN).read_text(encoding="utf-8")
    assert "Verdict: PASS" in markdown and "Owner approval: pending" in markdown
    assert "| theme-ids | missing fragment | 2 |" in markdown


def test_missing_tag_page_is_real_breakage_in_its_category(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html", tag=False)

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    assert report["broken_public_urls"] == 1
    categories = {row["category"]: row for row in report["comparisons"][0]["categories"]}
    assert categories["blog tag pages"]["missing"] == 1
    assert report["real_breakage"]["differences"][0]["finding"] == "missing path: /blog/tag/duckdb.html"


def test_lost_fragment_without_a_rule_is_a_broken_fragment(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=True, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")

    code, report = _run(tmp_path, baseline, candidate, rules=[])

    assert code == 1
    assert report["broken_fragments"] == 2
    assert report["comparisons"][0]["by_kind"]["missing fragment"] == {"found": 2, "allowed": 0, "remaining": 2}


def test_undeclared_redirect_and_api_anchor_loss_fail(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    for site in (baseline, candidate):
        _write(site, "docs/old.html", '<meta http-equiv="refresh" content="0; url=/docs/api.html">')

    code, report = _run(tmp_path, baseline, candidate, api_ids=["api", "benchbox.Gone"])

    assert code == 1
    assert report["real_breakage"]["redirect_pages"] == ["/docs/old.html"]
    assert report["real_breakage"]["api_url_map"] == ["/docs/api.html#benchbox.Gone"]
    assert report["redirects"]["pages"][1]["reason"] == ""


def test_api_anchors_lost_to_a_theme_rule_are_allowed_not_missing(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=True, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")

    code, report = _run(tmp_path, baseline, candidate, api_ids=["api", "__toc"])

    assert code == 0
    assert report["api_url_map"]["allowed_ids"] == ["/docs/api.html#__toc"]
    assert report["api_url_map"]["missing_ids"] == []


def test_lost_feed_entry_id_is_reported(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    feed = ATOM.format(id="https://benchbox.dev/blog/a.html", link="/blog/a.html").replace(
        "blog/a.html</id>", "blog/z.html</id>"
    )
    _write(candidate, "blog/atom.xml", feed)

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    assert report["comparisons"][0]["feeds"][0]["lost_ids"] == ["https://benchbox.dev/blog/a.html"]
    assert report["real_breakage"]["atom_entry_ids"] == [
        f"{site_parity.SAME_TREE}: /blog/atom.xml: https://benchbox.dev/blog/a.html"
    ]


def test_project_allowance_preserves_legacy_atom_ids_and_allows_canonical_links(tmp_path: Path) -> None:
    legacy_url = "https://benchbox.dev/blog/blog/a.html"
    baseline = _site(tmp_path / "base", theme=False, feed_link=legacy_url, feed_id=legacy_url)
    candidate = _site(
        tmp_path / "cand",
        theme=False,
        feed_link="https://benchbox.dev/blog/a.html",
        feed_id=legacy_url,
    )
    rules = site_inventory.load_allowed_differences(site_parity.DEFAULT_ALLOWED)

    code, report = _run(tmp_path, baseline, candidate, rules=rules)

    assert code == 0
    assert report["comparisons"][0]["feeds"][0]["lost_ids"] == []
    assert report["real_breakage"]["atom_entry_ids"] == []
    allowed = {row["id"]: row for row in report["allowed_differences"]}
    assert allowed["canonical-atom-entry-links"]["count"] == 1
    assert allowed["canonical-atom-entry-links"]["owner_approval"] == "approved"


def test_canonical_atom_allowance_rejects_id_loss_and_canonical_link_loss() -> None:
    rules = site_inventory.load_allowed_differences(site_parity.DEFAULT_ALLOWED)
    report = {kind: [] for kind in site_inventory.REPORT_KINDS}
    report["feed regression"] = [
        "/blog/atom.xml: entry https://benchbox.dev/blog/blog/a.html removed",
        "/blog/atom.xml: entry https://benchbox.dev/blog/blog/a.html lost link /blog/a.html",
    ]

    remaining, allowed = site_inventory.apply_allowed_differences(report, rules)

    assert allowed["canonical-atom-entry-links"] == []
    assert remaining["feed regression"] == report["feed regression"]


def test_browser_failures_fail_the_report(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    failures = ["landing light: axe color-contrast"]
    browser = _json(tmp_path / "browser.json", {"axe": {"docs page light": {}}, "failures": failures})

    code, report = _run(tmp_path, baseline, candidate, extra=["--e2e-report", str(browser)])

    assert code == 1
    assert report["real_breakage"]["browser_checks"] == ["browser.json: landing light: axe color-contrast"]


def test_published_baseline_is_informational_and_never_fails_the_verdict(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    published = _site(tmp_path / "published", theme=False, feed_link="/blog/a.html")
    _write(published, "docs/removed.html", "<title>Gone</title>")

    code, report = _run(tmp_path, baseline, candidate, published=published)

    assert code == 0
    assert [comparison["gating"] for comparison in report["comparisons"]] == [True, False]
    assert report["broken_public_urls"] == 0
    assert report["changed_since_published"]["urls"] == 1


def test_unreviewed_additions_fail_the_report_and_are_listed(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    _write(candidate, "sitemap.xml")
    _write(candidate, "docs/new.html", "<title>New</title>")

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    added = report["comparisons"][0]["added_paths"]
    assert (added["total"], added["unreviewed"]) == (2, ["/docs/new.html"])
    assert added["by_rule"] == {"/sitemap.xml": ["/sitemap.xml"]}


def test_added_path_glob_matches_only_adjacent_tpc_eula_sidecars(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    _write(candidate, "docs/_downloads/abc123/TPC_EULA.txt")
    _write(candidate, "docs/_downloads/abc123/query.sql")
    _write(candidate, "docs/_downloads/abc123/nested/TPC_EULA.txt")

    code, report = _run(
        tmp_path,
        baseline,
        candidate,
        added_paths=[
            {
                "path": "/docs/_downloads/*/TPC_EULA.txt",
                "reason": "adjacent TPC license sidecars accompany downloadable query files",
                "owner_approval": "pending",
            }
        ],
    )

    added = report["comparisons"][0]["added_paths"]
    assert code == 1
    assert added["by_rule"] == {"/docs/_downloads/*/TPC_EULA.txt": ["/docs/_downloads/abc123/TPC_EULA.txt"]}
    assert added["unreviewed"] == [
        "/docs/_downloads/abc123/nested/TPC_EULA.txt",
        "/docs/_downloads/abc123/query.sql",
    ]


def test_committed_policy_files_carry_reasons_and_only_the_signed_approvals() -> None:
    rules = site_inventory.load_allowed_differences(site_parity.DEFAULT_ALLOWED)
    redirects = site_parity.load_redirect_pages(site_parity.DEFAULT_REDIRECTS)
    added = site_parity.load_added_paths(site_parity.DEFAULT_ADDED)
    losses = site_parity.load_inventory_losses(site_parity.DEFAULT_INVENTORY_LOSSES)
    entries = site_parity.removal_entries(sorted(site_parity.DESIGN_DIR.glob("expected-removals-*.json")))

    everything = rules + redirects + added + entries + losses
    assert redirects and added and all(entry["reason"].strip() for entry in everything)
    assert {entry["owner_approval"] for entry in everything} <= {"pending", "approved"}
    sphinx_assets = site_parity.removal_entries([site_parity.DESIGN_DIR / "expected-removals-sphinx-assets.json"])
    signed = rules + redirects + losses + sphinx_assets
    assert {entry["owner_approval"] for entry in signed} == {"approved"}
    assert {entry["owner_approval"] for entry in added} == {"pending"}


CANONICAL_RULES = [
    rule
    for rule in site_inventory.load_allowed_differences(site_parity.DEFAULT_ALLOWED)
    if rule["id"].startswith("canonical-link-")
]


def _canonical_page(site: Path, name: str, canonical: str | None) -> None:
    _page(site, name, "<h1>T</h1>", canonical)


def test_canonical_rules_accept_only_the_pages_own_url(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    for name in ("docs/usage/a.html", "docs/usage/index.html", "docs/usage/b.html", "docs/usage/tag-index.html"):
        _canonical_page(baseline, name, None)
        _canonical_page(candidate, name, "")

    code, report = _run(tmp_path, baseline, candidate, rules=CANONICAL_RULES)

    assert code == 0
    assert report["comparisons"][0]["by_kind"]["changed metadata"]["remaining"] == 0
    assert report["real_breakage"]["canonical_links"] == []


@pytest.mark.parametrize(
    "wrong",
    [
        "https://benchbox.dev/",
        "https://benchbox.dev/docs/usage/other.html",
        "https://benchbox.dev/results/",
        "http://benchbox.dev/docs/usage/a.html",
        "https://www.benchbox.dev/docs/usage/a.html",
        "https://benchbox.dev/docs/usage/a.html?x=1",
        "/docs/usage/a.html",
        "https://benchbox.dev/docs/usage/",
    ],
)
def test_a_canonical_that_names_another_page_stays_remaining(tmp_path: Path, wrong: str) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    _canonical_page(baseline, "docs/usage/a.html", None)
    _canonical_page(candidate, "docs/usage/a.html", wrong)

    code, report = _run(tmp_path, baseline, candidate, rules=CANONICAL_RULES)

    assert code == 1
    finding = f"changed metadata: /docs/usage/a.html [canonical]: '' -> '{wrong}'"
    assert [row["finding"] for row in report["real_breakage"]["differences"]] == [finding]
    assert report["real_breakage"]["canonical_links"] == [
        f"/docs/usage/a.html: canonical '{wrong}', expected 'https://benchbox.dev/docs/usage/a.html'"
    ]


def test_every_built_page_must_carry_its_own_canonical_even_without_a_baseline_page(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    _canonical_page(candidate, "blog.html", "https://benchbox.dev/blog/")
    _canonical_page(candidate, "docs/bare.html", None)

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    assert report["real_breakage"]["canonical_links"] == [
        "/blog.html: canonical 'https://benchbox.dev/blog/', expected 'https://benchbox.dev/blog.html'",
        "/docs/bare.html: canonical '', expected 'https://benchbox.dev/docs/bare.html'",
    ]


def test_inventory_addresses_must_resolve_and_match_sphinx(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    elsewhere = "# Sphinx inventory version 2\n# Project: B\n# Version: 1\n# The remainder of this file is compressed using zlib.\n"
    lines = "a std:doc -1 missing.html T\nb std:label -1 api.html#nope T\n"
    (candidate / "docs" / "objects.inv").write_bytes(elsewhere.encode() + zlib.compress(lines.encode()))
    (baseline / "docs" / "objects.inv").write_bytes(
        elsewhere.encode() + zlib.compress(b"a std:doc -1 api.html T\nb std:label -1 api.html#$ T\n")
    )

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    assert report["real_breakage"]["objects_inventory"] == [
        "std:doc a: missing.html does not resolve in the Astro site",
        "std:label b: api.html#nope does not resolve in the Astro site",
    ]


def test_inventory_address_that_differs_from_a_valid_sphinx_one_fails(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    header = "# Sphinx inventory version 2\n# Project: B\n# Version: 1\n# The remainder of this file is compressed using zlib.\n"
    _page(baseline, "docs/other.html", "<h1>O</h1>")
    _page(candidate, "docs/other.html", "<h1>O</h1>")
    (baseline / "docs" / "objects.inv").write_bytes(header.encode() + zlib.compress(b"a std:doc -1 api.html T\n"))
    (candidate / "docs" / "objects.inv").write_bytes(header.encode() + zlib.compress(b"a std:doc -1 other.html T\n"))

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    assert report["real_breakage"]["objects_inventory"] == ["std:doc a: api.html in Sphinx, other.html in Astro"]


HEADER = (
    "# Sphinx inventory version 2\n# Project: B\n# Version: 1\n# The remainder of this file is compressed using zlib.\n"
)


def _inventory_run(tmp_path: Path, before: bytes, after: bytes) -> tuple[int, dict]:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    (baseline / "docs" / "objects.inv").write_bytes(HEADER.encode() + zlib.compress(before))
    (candidate / "docs" / "objects.inv").write_bytes(HEADER.encode() + zlib.compress(after))
    return _run(tmp_path, baseline, candidate)


def test_a_dangling_sphinx_blog_label_may_move_to_the_matching_blog_page(tmp_path: Path) -> None:
    code, report = _inventory_run(tmp_path, b"p std:label -1 blog/a.html#$ T\n", b"p std:label -1 ../blog/a.html T\n")

    assert code == 0
    assert report["objects_inventory"]["validity_only"] == 1


def test_a_dangling_sphinx_blog_label_mapped_elsewhere_fails(tmp_path: Path) -> None:
    code, report = _inventory_run(tmp_path, b"p std:label -1 blog/a.html#$ T\n", b"p std:label -1 ../index.html T\n")

    assert code == 1
    assert report["real_breakage"]["objects_inventory"] == [
        "std:label p: blog/a.html#$ in Sphinx, ../index.html in Astro"
    ]


def test_a_dangling_sphinx_address_outside_the_blog_still_compares(tmp_path: Path) -> None:
    code, report = _inventory_run(tmp_path, b"p std:label -1 gone.html T\n", b"p std:label -1 api.html T\n")

    assert code == 1
    assert report["objects_inventory"]["validity_only"] == 0


@pytest.mark.parametrize("uri", ["https://example.com/docs/api.html", "http://benchbox.dev/docs/api.html"])
def test_an_inventory_address_off_the_site_fails(tmp_path: Path, uri: str) -> None:
    code, report = _inventory_run(tmp_path, b"a std:doc -1 api.html T\n", f"a std:doc -1 {uri} T\n".encode())

    assert code == 1
    assert report["real_breakage"]["objects_inventory"] == [f"std:doc a: {uri} does not resolve in the Astro site"]


def test_a_fragment_on_a_file_that_is_not_a_page_fails(tmp_path: Path) -> None:
    code, report = _inventory_run(tmp_path, b"a std:doc -1 api.html T\n", b"a std:doc -1 objects.inv#x T\n")

    assert code == 1
    assert report["real_breakage"]["objects_inventory"] == [
        "std:doc a: objects.inv#x does not resolve in the Astro site"
    ]


def test_the_results_page_canonical_is_checked(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 0
    assert report["real_breakage"]["canonical_links"] == []


def test_a_wrong_results_page_canonical_fails(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    wrong = "https://benchbox.dev/results/index.html"
    _write(
        candidate,
        "results/index.html",
        f'<title>Results</title><link rel="canonical" href="{wrong}"><h1>Results</h1>',
    )

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    assert report["real_breakage"]["canonical_links"] == [
        f"/results/index.html: canonical '{wrong}', expected 'https://benchbox.dev/results/'"
    ]


def test_lost_inventory_entries_fail_unless_allowed(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html", inventory=("a std:doc", "b std:label"))
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")

    code, report = _run(tmp_path, baseline, candidate)
    assert code == 1
    assert report["real_breakage"]["objects_inventory"] == ["std:label b"]

    losses = [{"id": "std:label b", "reason": "gone with autodoc", "owner_approval": "pending"}]
    code, report = _run(tmp_path, baseline, candidate, inventory_losses=losses)
    assert code == 0
    assert report["objects_inventory"]["allowed"] == ["std:label b"]


def test_failed_missing_and_stale_gate_steps_fail_the_report(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    failed = _json(tmp_path / "privacy-result.json", {"check": "privacy scan", "exit": 1})
    gone = tmp_path / "explorer-result.json"

    code, report = _run(
        tmp_path,
        baseline,
        candidate,
        extra=["--step-result", str(failed), "--step-result", str(gone), "--e2e-report", str(tmp_path / "none.json")],
    )

    assert code == 1
    assert report["real_breakage"]["gate_steps"] == [
        "privacy scan exited 1",
        "explorer-result: the step result is missing",
    ]
    assert report["real_breakage"]["browser_checks"] == ["none.json: the expected browser report is missing"]


def test_redirect_and_addition_bookkeeping_fails_when_it_drifts(tmp_path: Path) -> None:
    baseline = _site(tmp_path / "base", theme=False, feed_link="/blog/a.html")
    candidate = _site(tmp_path / "cand", theme=False, feed_link="/blog/a.html")
    _write(candidate, "docs/new.html", "<title>New</title>")
    (candidate / "404.html").unlink()

    code, report = _run(tmp_path, baseline, candidate)

    assert code == 1
    assert report["real_breakage"]["redirect_pages"] == [
        "declared but not built: /404.html",
        "present in the Sphinx site and gone: /404.html",
    ]
    assert report["real_breakage"]["unreviewed_added_paths"] == ["/docs/new.html"]
