from __future__ import annotations

import sys

import pytest

import scripts.check_doc_anchors as checker

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_slug_punctuation_case_and_entities() -> None:
    assert checker.slugify_heading("TPC-H Specification Coverage") == "tpc-h-specification-coverage"
    assert checker.slugify_heading("QphH@Size") == "qphhsize"


def test_slug_keeps_intra_word_underscores_and_drops_emphasis_delimiters() -> None:
    assert checker.slugify_heading("taxi_zones Table Structure") == "taxi_zones-table-structure"
    assert checker.slugify_heading("BULK_LOAD (36 operations)") == "bulk_load-36-operations"
    assert checker.slugify_heading("_emphasised_ and __strong__ text") == "emphasised-and-strong-text"


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Versioning & Releases", "versioning--releases"),
        ("Hybrid Platforms (SQL + DataFrame)", "hybrid-platforms-sql--dataframe"),
        ("`_project/scripts/`", "_projectscripts"),
        ("`<result>.manifest.json` fields", "resultmanifestjson-fields"),
    ],
)
def test_slug_matches_github_for_spaced_punctuation_and_code_spans(heading: str, slug: str) -> None:
    assert checker.slugify_heading(heading) == slug


def test_directive_bodies_are_checked_but_code_fences_are_not() -> None:
    content = (
        "# Top\n"
        "\n"
        "````{note}\n"
        "See [other](#missing).\n"
        "## Inside Directive\n"
        "```python\n"
        "[code](#hidden)\n"
        "```\n"
        "````\n"
        "\n"
        "```\n"
        "[plain](#also-hidden)\n"
        "```\n"
    )
    assert [target for _, target in checker.extract_links(content)] == ["#missing"]
    assert checker.collect_anchors(content) == {"top", "inside-directive"}


def test_slug_strips_markup_and_keeps_unicode() -> None:
    assert checker.slugify_heading("`tuning init` - Create Tuning Configuration") == (
        "tuning-init---create-tuning-configuration"
    )
    assert checker.slugify_heading("🎯 By Use Case") == "-by-use-case"
    assert checker.slugify_heading("Привет non-latin 你好") == "привет-non-latin-你好"


def test_duplicate_headings_gain_numeric_suffixes() -> None:
    anchors = checker.collect_anchors("# Repeat\n\n# Repeat\n\n# Repeat\n")
    assert anchors == {"repeat", "repeat-1", "repeat-2"}


def test_explicit_anchors_collected() -> None:
    content = (
        "(cli-compare)=\n"
        "## `compare` - Compare Benchmark Results\n"
        "\n"
        '<span id="benchbox.results"></span>\n'
        "\n"
        '<a name="legacy-target"></a>\n'
        "\n"
        "## Custom {#custom-id}\n"
    )
    anchors = checker.collect_anchors(content)
    assert "cli-compare" in anchors
    assert "benchbox.results" in anchors
    assert "legacy-target" in anchors
    assert "custom-id" in anchors
    assert "compare---compare-benchmark-results" in anchors


def test_fenced_and_inline_code_ignored() -> None:
    content = "```\n# Not A Heading\n```\n\n# Real Heading\n\nUse `#inline-fragment` here.\n"
    assert checker.collect_anchors(content) == {"real-heading"}
    assert checker.extract_links(content) == []


def _seed(tmp_path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> None:
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_files", lambda: sorted(files))
    monkeypatch.setattr(sys, "argv", ["check_doc_anchors.py"])


def test_main_fails_on_dead_anchor_and_passes_when_fixed(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        monkeypatch,
        {
            "guide.md": "## TPC-H Specification Coverage\n",
            "page.md": "[coverage](guide.md#tpc-h-specification-coverage-old)\n",
        },
    )

    assert checker.main() == 1
    assert "page.md:1 -> guide.md#tpc-h-specification-coverage-old (missing anchor in guide.md)" in (
        capsys.readouterr().out
    )

    (tmp_path / "page.md").write_text("[coverage](guide.md#tpc-h-specification-coverage)\n", encoding="utf-8")
    assert checker.main() == 0
    assert "Every relative Markdown link and anchor resolves" in capsys.readouterr().out


def test_main_fails_on_missing_file_with_and_without_anchor(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        monkeypatch,
        {
            "page.md": "intro\n[gone](deleted.md)\n\n[gone too](sub/deleted.md#section)\n[up](../outside.md)\n",
        },
    )

    assert checker.main() == 1
    out = capsys.readouterr().out
    assert "page.md:2 -> deleted.md (missing file)" in out
    assert "page.md:4 -> sub/deleted.md#section (missing file)" in out
    assert "page.md:5 -> ../outside.md (missing file)" in out


def test_links_to_tracked_non_markdown_files_and_directories_pass(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        monkeypatch,
        {
            "pages/page.md": "[script](../scripts/run.py) [dir](../scripts/) [dir2](../scripts) [enc](a%20b.md)\n",
            "pages/a b.md": "# A\n",
            "scripts/run.py": "print()\n",
        },
    )

    assert checker.main() == 0
    capsys.readouterr()


def test_external_and_server_absolute_links_are_not_checked(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        monkeypatch,
        {"page.md": "[a](https://example.com/x#y) [b](mailto:a@b.c) [c](/guide/x) [d](#top)\n\n# Top\n"},
    )

    assert checker.main() == 0
    capsys.readouterr()


def test_excluded_source_roots_are_skipped_and_documented(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert checker.EXCLUDED_SOURCE_ROOTS
    assert all(root.endswith("/") and reason for root, reason in checker.EXCLUDED_SOURCE_ROOTS.items())
    root = next(iter(checker.EXCLUDED_SOURCE_ROOTS))
    live = root.split("/", 1)[0] + "/live/"
    _seed(
        tmp_path,
        monkeypatch,
        {
            f"{root}old.md": "[gone](missing.md)\n",
            f"{live}page.md": "[gone](missing.md)\n",
        },
    )

    assert checker.main() == 1
    out = capsys.readouterr().out
    assert f"{live}page.md:1 -> missing.md (missing file)" in out
    assert f"{root}old.md" not in out
