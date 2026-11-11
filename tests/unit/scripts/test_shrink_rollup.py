from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script():
    name = "shrink_rollup"
    path = REPO_ROOT / "_project" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shrink_rollup = _load_script()


def test_load_rows_parses_merged_fragments_and_skips_template(monkeypatch: pytest.MonkeyPatch) -> None:
    fragment = """---
surface: cli
credited_reduction: "700"
raw_cloc_delta: -900
uncredited_relocation: 100
repair_only_delta:
generated_python_delta: 0
---

## Thesis
"""

    def fake_git(*args: str) -> str:
        if args == ("ls-tree", "-r", "--name-only", "origin/develop", "_project/shrink-ledger"):
            return "\n".join(
                [
                    "_project/shrink-ledger/TEMPLATE.md",
                    "_project/shrink-ledger/cli.md",
                ]
            )
        if args == ("show", "origin/develop:_project/shrink-ledger/cli.md"):
            return fragment
        raise AssertionError(args)

    monkeypatch.setattr(shrink_rollup, "_git", fake_git)

    rows = shrink_rollup.load_rows("origin/develop", "_project/shrink-ledger")

    assert rows == [
        shrink_rollup.LedgerRow(
            path="_project/shrink-ledger/cli.md",
            surface="cli",
            credited_reduction=700,
            raw_cloc_delta=-900,
            uncredited_relocation=100,
            repair_only_delta=0,
            generated_python_delta=0,
        )
    ]


def test_load_rows_treats_missing_ledger_dir_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git(*args: str) -> str:
        if args == ("ls-tree", "-r", "--name-only", "origin/develop", "_project/shrink-ledger"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(shrink_rollup, "_git", fake_git)

    assert shrink_rollup.load_rows("origin/develop", "_project/shrink-ledger") == []


def test_main_reports_git_lookup_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_git(*args: str) -> str:
        raise subprocess.CalledProcessError(128, ["git", *args])

    monkeypatch.setattr(shrink_rollup, "_git", fake_git)

    assert shrink_rollup.main(["--ref", "refs/heads/missing"]) == 2
    assert "shrink-rollup:" in capsys.readouterr().err


def test_main_reports_target_band_and_remaining_distance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        shrink_rollup.LedgerRow(
            path="_project/shrink-ledger/cli.md",
            surface="cli",
            credited_reduction=13_000,
            raw_cloc_delta=-15_000,
            uncredited_relocation=2_000,
            repair_only_delta=0,
            generated_python_delta=0,
        )
    ]
    monkeypatch.setattr(shrink_rollup, "load_rows", lambda ref, ledger_dir: rows)

    assert shrink_rollup.main([]) == 0

    output = capsys.readouterr().out
    assert "Target credited reduction band: 12000-19000" in output
    assert "Remaining to committed floor: 0" in output
    assert "Remaining to stretch target: 6000" in output
    assert "Raw cloc delta sanity check: -15000" in output


def test_main_rejects_inverted_target_band(capsys: pytest.CaptureFixture[str]) -> None:
    assert shrink_rollup.main(["--target-min", "19000", "--target-max", "12000"]) == 2

    assert "--target-min cannot exceed --target-max" in capsys.readouterr().err


