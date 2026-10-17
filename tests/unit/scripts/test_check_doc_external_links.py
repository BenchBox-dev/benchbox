from __future__ import annotations

import importlib.util
import re
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("check_doc_external_links", ROOT / "scripts/check_doc_external_links.py")
assert SPEC and SPEC.loader
links = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = links
SPEC.loader.exec_module(links)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_extracts_markdown_reference_and_bare_links_outside_code() -> None:
    content = "\n".join(
        [
            "See [the site](https://benchbox.dev/docs/) and <https://example.com/a>.",
            "[ref]: https://example.org/ref",
            "Bare https://example.net/path, then `https://in-code.example`.",
            "```",
            "curl https://fenced.example",
            "```",
        ]
    )
    assert links.extract_urls(content) == [
        (1, "https://benchbox.dev/docs/"),
        (1, "https://example.com/a"),
        (2, "https://example.org/ref"),
        (3, "https://example.net/path"),
    ]


def test_collect_links_records_every_source_location(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    (docs / "guide").mkdir(parents=True)
    (docs / "index.md").write_text("[a](https://example.com/)\n", encoding="utf-8")
    (docs / "guide" / "page.md").write_text("x\nhttps://example.com/\n", encoding="utf-8")
    assert links.collect_links(docs) == {"https://example.com/": ["docs/guide/page.md:2", "docs/index.md:1"]}


def test_ignore_patterns_match_from_the_start_of_the_url(tmp_path: Path) -> None:
    ignore = tmp_path / "ignore.txt"
    ignore.write_text("# comment\n\nhttps://www\\.tpc\\.org/.*\n", encoding="utf-8")
    patterns = links.load_ignore_patterns(ignore)
    assert links.is_ignored("https://www.tpc.org/tpch/", patterns)
    assert not links.is_ignored("https://example.com/?u=https://www.tpc.org/", patterns)


def test_committed_ignore_list_compiles() -> None:
    assert links.load_ignore_patterns()


def test_head_failure_falls_back_to_get() -> None:
    calls = []

    def request(url: str, method: str) -> int:
        calls.append(method)
        return 405 if method == "HEAD" else 200

    assert links.check_url("https://example.com/", request).status == "ok"
    assert calls == ["HEAD", "GET"]


def test_rate_limited_links_are_reported_but_not_broken() -> None:
    assert links.check_url("https://example.com/", lambda url, method: 429).status == "rate-limited"


def test_persistent_errors_are_broken_after_retries() -> None:
    calls = []

    def request(url: str, method: str) -> int:
        calls.append(method)
        raise urllib.error.URLError("timed out")

    result = links.check_url("https://example.com/", request)
    assert result.status == "broken"
    assert result.detail == "timed out"
    assert len(calls) == links.RETRIES + 1


def test_not_found_is_broken() -> None:
    assert links.check_url("https://example.com/", lambda url, method: 404).detail == "HTTP 404"


def test_run_skips_ignored_links() -> None:
    sources = {"https://ignored.example/x": ["docs/a.md:1"], "https://kept.example/": ["docs/a.md:2"]}
    patterns = re.compile(r"https://ignored\.example/.*")
    seen = []

    def check(url: str):
        seen.append(url)
        return links.LinkResult(url, "ok", "200")

    links.run(sources, [patterns], check)
    assert seen == ["https://kept.example/"]


def test_main_fails_and_writes_a_report_when_a_link_is_broken(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(links, "collect_links", lambda: {"https://example.com/": ["docs/a.md:1"]})
    monkeypatch.setattr(links, "load_ignore_patterns", list)
    monkeypatch.setattr(links, "check_url", lambda url: links.LinkResult(url, "broken", "HTTP 404"))
    monkeypatch.setattr(links, "run", lambda sources, patterns: [links.check_url(url) for url in sources])
    report = tmp_path / "out" / "report.json"
    assert links.main(["--report", str(report)]) == 1
    assert '"HTTP 404"' in report.read_text(encoding="utf-8")
