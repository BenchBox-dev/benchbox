from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import theseus_ledger_check as check

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SOURCE = "a" * 40
BASELINE = "b" * 40
LOCK: dict[str, Any] = {
    "repository": "owner/engine",
    "source_commit": SOURCE,
    "bootstrap": {"baseline_commit": BASELINE},
}


class Recorder:
    def __init__(self, *, history: bool = True, ledger_exit: int = 0, output: str = "", engine: bool = True) -> None:
        self.history = history
        self.engine = engine
        self.ledger_exit = ledger_exit
        self.output = output
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> tuple[int, str]:
        self.commands.append(list(command))
        if "ls-tree" in command:
            return 0, (f"040000 tree {SOURCE}\t{check.ENGINE_DIR}" if self.engine else "")
        if "cat-file" in command:
            return (0 if self.history else 1), ""
        return self.ledger_exit, self.output


def fetcher(baseline: str = BASELINE):
    def fetch(repository: str, commit: str, destination: Path) -> None:
        assert (repository, commit) == ("owner/engine", SOURCE)
        (destination / "tools").mkdir(parents=True)
        (destination / "tools" / "upstream_ledger.json").write_text(
            json.dumps({"schema": 1, "baseline": baseline}), encoding="utf-8"
        )

    return fetch


def test_a_current_ledger_passes_and_checks_the_given_base(tmp_path: Path) -> None:
    run = Recorder()

    assert check.verify(tmp_path, LOCK, "c" * 40, run=run, fetch=fetcher()) == []

    ledger = run.commands[-1]
    assert ledger[1].endswith("upstream_ledger.py")
    assert ledger[2:] == ["check", "--benchbox-repo", str(tmp_path), "--ref", "c" * 40]


def test_a_stale_ledger_fails_with_the_tool_report(tmp_path: Path) -> None:
    run = Recorder(ledger_exit=1, output="ef5250998 pending\nledger stale")

    problems = check.verify(tmp_path, LOCK, "c" * 40, run=run, fetch=fetcher())

    assert problems[0] == "the ledger is stale (exit 1):"
    assert "ef5250998 pending" in problems


def test_an_unrunnable_ledger_check_fails_distinctly(tmp_path: Path) -> None:
    problems = check.verify(tmp_path, LOCK, "c" * 40, run=Recorder(ledger_exit=2), fetch=fetcher())

    assert problems[0].startswith("the ledger check could not run (exit 2)")


def test_a_checkout_without_baseline_history_fails_before_fetching_the_source(tmp_path: Path) -> None:
    def never(repository: str, commit: str, destination: Path) -> None:
        raise AssertionError("the source must not be fetched")

    problems = check.verify(tmp_path, LOCK, "c" * 40, run=Recorder(history=False), fetch=never)

    assert len(problems) == 1 and "no history back to the baseline" in problems[0]


def test_a_ledger_with_a_different_baseline_than_the_lock_fails(tmp_path: Path) -> None:
    problems = check.verify(tmp_path, LOCK, "c" * 40, run=Recorder(), fetch=fetcher("d" * 40))

    assert len(problems) == 1 and "is not the lock's baseline" in problems[0]


def test_a_source_that_cannot_be_fetched_fails(tmp_path: Path) -> None:
    def down(repository: str, commit: str, destination: Path) -> None:
        raise check.LedgerCheckError("offline")

    assert check.verify(tmp_path, LOCK, "c" * 40, run=Recorder(), fetch=down) == ["offline"]


def test_a_fetched_tree_at_another_commit_is_refused(tmp_path: Path) -> None:
    def run(command: Sequence[str]) -> tuple[int, str]:
        return 0, "e" * 40 if "rev-parse" in command else ""

    with pytest.raises(check.LedgerCheckError, match="not the locked"):
        check.fetch_source("owner/engine", SOURCE, tmp_path / "x", run)


def test_the_base_ref_comes_from_the_argument_then_the_environment_then_develop() -> None:
    assert check.resolve_ref({"BASE_SHA": "x"}, "y") == "y"
    assert check.resolve_ref({"BASE_SHA": " x "}, None) == "x"
    assert check.resolve_ref({"BASE_SHA": ""}, None) == "origin/develop"


def test_main_exit_codes_follow_the_decision(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "theseus-lock.json").write_text(json.dumps(LOCK), encoding="utf-8")

    assert check.main(["x", str(tmp_path)], {}, Recorder(history=False)) == 1
    assert "no history back" in capsys.readouterr().err
    unlocked = tmp_path / "unlocked"
    (unlocked / ".github").mkdir(parents=True)
    (unlocked / ".github" / "oracle-reviewers.yml").write_text("reviewers: []\n", encoding="utf-8")
    assert check.main(["x", str(unlocked)], {}, Recorder()) == 1


def test_a_base_without_the_in_tree_engine_skips_the_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "theseus-lock.json").write_text(json.dumps(LOCK), encoding="utf-8")
    recorder = Recorder(history=False, ledger_exit=1, engine=False)

    assert check.main(["x", str(tmp_path), "base123"], {}, recorder) == 0
    assert "already removed" in capsys.readouterr().err
    assert recorder.commands == [["git", "-C", str(tmp_path), "ls-tree", "base123", check.ENGINE_DIR]]


def test_a_base_with_the_in_tree_engine_still_runs_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "theseus-lock.json").write_text(json.dumps(LOCK), encoding="utf-8")
    recorder = Recorder(history=False, engine=True)

    assert check.main(["x", str(tmp_path), "base123"], {}, recorder) == 1
    assert "no history back" in capsys.readouterr().err
    assert recorder.commands[0][3:] == ["ls-tree", "base123", check.ENGINE_DIR]


def test_an_unresolvable_base_is_not_mistaken_for_a_removed_engine(tmp_path: Path) -> None:
    def failing(command: Sequence[str]) -> tuple[int, str]:
        return (128, "fatal: Not a valid object name") if "ls-tree" in command else (1, "")

    assert check.base_has_engine(tmp_path, "nope", failing) is True
