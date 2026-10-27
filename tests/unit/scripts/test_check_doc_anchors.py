from __future__ import annotations

import json
import sys
from pathlib import Path

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


@pytest.mark.parametrize(
    ("links", "allowed", "expected"),
    [
        ("[old](guide.md#old)\n", 1, 0),
        ("[new](guide.md#new)\n", 1, 1),
        ("[old](guide.md#old)\n[old again](guide.md#old)\n", 1, 1),
        ("[valid](guide.md#real)\n", 1, 1),
    ],
)
def test_exact_baseline_rejects_replacement_duplicates_and_stale_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, links: str, allowed: int, expected: int
) -> None:
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checker, "BASELINE_PATH", "baseline.json")
    monkeypatch.setattr(checker, "tracked_markdown_files", lambda: ["guide.md", "page.md"])
    monkeypatch.setattr(sys, "argv", ["check_doc_anchors.py"])
    (tmp_path / "guide.md").write_text("# Real\n", encoding="utf-8")
    (tmp_path / "page.md").write_text(links, encoding="utf-8")
    (tmp_path / "baseline.json").write_text(
        json.dumps([["page.md", "guide.md#old", "guide.md"]] * allowed), encoding="utf-8"
    )
    assert checker.main() == expected


@pytest.mark.parametrize("target", ["real", "new"])
def test_baseline_rewrite_only_shrinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checker, "BASELINE_PATH", "baseline.json")
    monkeypatch.setattr(checker, "tracked_markdown_files", lambda: ["guide.md", "page.md"])
    monkeypatch.setattr(sys, "argv", ["check_doc_anchors.py", "--write-baseline"])
    (tmp_path / "guide.md").write_text("# Real\n", encoding="utf-8")
    (tmp_path / "page.md").write_text(f"[link](guide.md#{target})\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    original = [["page.md", "guide.md#old", "guide.md"]]
    baseline.write_text(json.dumps(original), encoding="utf-8")
    assert checker.main() == (0 if target == "real" else 1)
    assert json.loads(baseline.read_text(encoding="utf-8")) == ([] if target == "real" else original)
