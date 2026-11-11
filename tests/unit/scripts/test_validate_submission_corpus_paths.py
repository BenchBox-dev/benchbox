from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_submission import main

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _minimal_bundle():
    return {
        "version": "2.1",
        "run": {"id": "a", "timestamp": "2026-04-01T12:00:00", "total_duration_ms": 5000},
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 0.01},
        "platform": {"name": "DuckDB", "version": "1.4.3"},
        "summary": {"validation": "passed", "queries": {"total": 22, "passed": 22, "failed": 0}},
        "queries": [{"id": f"Q{i}", "ms": 100 + i * 10, "status": "SUCCESS"} for i in range(1, 23)],
    }


def test_corpus_flag_missing_file_fails_closed(tmp_path: Path):
    bundle = tmp_path / "b.json"
    bundle.write_text(json.dumps(_minimal_bundle()), encoding="utf-8")
    assert main([str(bundle), "--corpus-changed-paths", str(tmp_path / "missing.txt")]) == 1


def test_corpus_flag_empty_file_passes(tmp_path: Path):
    bundle = tmp_path / "b.json"
    bundle.write_text(json.dumps(_minimal_bundle()), encoding="utf-8")
    empty = tmp_path / "corpus.txt"
    empty.write_text("", encoding="utf-8")

    assert main([str(bundle), "--corpus-changed-paths", str(empty)]) == 0


def test_corpus_flag_valid_paths_pass(tmp_path: Path):
    bundle = tmp_path / "b.json"
    bundle.write_text(json.dumps(_minimal_bundle()), encoding="utf-8")
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("results-data/corpus/foo.json\n", encoding="utf-8")
    assert main([str(bundle), "--corpus-changed-paths", str(corpus)]) == 0


def test_corpus_flag_invalid_path_fails(tmp_path: Path):
    bundle = tmp_path / "b.json"
    bundle.write_text(json.dumps(_minimal_bundle()), encoding="utf-8")
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("/etc/passwd\n", encoding="utf-8")
    assert main([str(bundle), "--corpus-changed-paths", str(corpus)]) == 1


def test_corpus_flag_parity_with_bundle_validation(tmp_path: Path):

    bundle = tmp_path / "b.json"
    bundle.write_text(json.dumps(_minimal_bundle()), encoding="utf-8")
    empty = tmp_path / "corpus.txt"
    empty.write_text("", encoding="utf-8")
    assert main([str(bundle), "--corpus-changed-paths", str(empty), "--require-manifest"]) == 1


