from __future__ import annotations

import sys

import pytest

import scripts.check_doc_anchors as checker

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_slug_punctuation_case_and_entities() -> None:
    assert checker.slugify_heading("TPC-H Specification Coverage") == "tpc-h-specification-coverage"
    assert checker.slugify_heading("QphH@Size") == "qphhsize"


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
    assert checker.extract_anchor_links(content) == []


def test_main_fails_on_dead_anchor_and_passes_when_fixed(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    (tmp_path / "guide.md").write_text("## TPC-H Specification Coverage\n", encoding="utf-8")
    (tmp_path / "page.md").write_text("[coverage](guide.md#tpc-h-specification-coverage-old)\n", encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_markdown_files", lambda: ["guide.md", "page.md"])
    monkeypatch.setattr(sys, "argv", ["check_doc_anchors.py"])

    assert checker.main() == 1
    assert "page.md:1 -> guide.md#tpc-h-specification-coverage-old" in capsys.readouterr().out

    (tmp_path / "page.md").write_text("[coverage](guide.md#tpc-h-specification-coverage)\n", encoding="utf-8")
    assert checker.main() == 0
    assert "Every Markdown anchor resolves" in capsys.readouterr().out
