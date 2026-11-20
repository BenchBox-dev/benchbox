from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import sqlglot_delivery as delivery
from scripts.check_line_sqlglot_adoption import main, problems

pytestmark = [pytest.mark.unit, pytest.mark.fast]

OLD, NEW = "30.31.0.1", "30.33.0.1"
LINE = "release/0.5.x"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=root,
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, delivery.PIN, json.dumps({"version": OLD}))
    write(root, delivery.BASELINE, "{}")
    write(root, "src/app.py", "x = 1\n")
    return root, commit(root, "base")


def adopt(root: Path, extra: tuple[str, ...] = ()) -> None:
    write(root, delivery.PIN, json.dumps({"version": NEW}))
    write(root, delivery.BASELINE, '{"identity": 1}')
    write(root, delivery.fragment_for(NEW), "{}")
    write(root, f"benchbox/_vendor/benchbox_sqlglot-{NEW}-py3-none-any.whl.zip", "wheel")
    for name in extra:
        write(root, name, "changed\n")


def change() -> delivery.TranslationChange:
    return {"platform": "questdb", "benchmark": "tpchavoc", "query": "18_v10", "before": "a b", "after": "a c"}


def never(root: Path, base: str, head: str) -> list[delivery.TranslationChange]:
    raise AssertionError("the translation report must not be computed")


def test_a_pull_request_into_develop_is_not_this_guards_concern(repo: tuple[Path, str]) -> None:
    root, base = repo
    adopt(root, ("src/app.py",))
    commit(root, "mixed")
    assert problems(root, "develop", base, "", never) == []


def test_a_line_pull_request_that_leaves_the_pin_alone_passes(repo: tuple[Path, str]) -> None:
    root, base = repo
    write(root, "src/app.py", "x = 2\n")
    commit(root, "fix")
    assert problems(root, LINE, base, "", never) == []


def test_a_line_pull_request_mixing_the_pin_with_other_changes_is_refused(repo: tuple[Path, str]) -> None:
    root, base = repo
    adopt(root, ("src/app.py",))
    commit(root, "fix and adopt")
    (found,) = problems(root, LINE, base, "", never)
    assert "separate adoption pull request" in found and "src/app.py" in found


def test_a_line_pull_request_that_edits_the_pin_alone_is_refused(repo: tuple[Path, str]) -> None:
    root, base = repo
    write(root, delivery.PIN, json.dumps({"version": NEW}))
    commit(root, "bump")
    (found,) = problems(root, LINE, base, "", never)
    assert "separate adoption pull request" in found


def test_a_pure_adoption_must_carry_the_recomputed_translation_report(repo: tuple[Path, str]) -> None:
    root, base = repo
    adopt(root)
    head = commit(root, "adopt")
    calls: list[tuple[str, str]] = []

    def reporter(path: Path, before: str, after: str) -> list[delivery.TranslationChange]:
        calls.append((before, after))
        return [change()]

    listing = delivery.render_translation_changes([change()])
    assert problems(root, LINE, base, f"intro\n\n{listing}", reporter) == []
    assert calls == [(base, "HEAD")] and head == git(root, "rev-parse", "HEAD")
    (missing,) = problems(root, LINE, base, "intro only", reporter)
    assert "translated-query report" in missing
    (stale,) = problems(root, LINE, base, delivery.render_translation_changes([]), reporter)
    assert "translated-query report" in stale


def test_a_failing_report_computation_fails_the_guard(repo: tuple[Path, str]) -> None:
    root, base = repo
    adopt(root)
    commit(root, "adopt")

    def broken(path: Path, before: str, after: str) -> list[delivery.TranslationChange]:
        raise RuntimeError("emission failed")

    (found,) = problems(root, LINE, base, "", broken)
    assert "emission failed" in found


def test_main_reads_the_body_from_the_environment(
    repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, base = repo
    write(root, "src/app.py", "x = 2\n")
    commit(root, "fix")
    monkeypatch.setenv("PR_BODY", "")
    arguments = ["--root", str(root), "--base-ref", LINE, "--base-sha", base, "--body-env", "PR_BODY"]
    assert main(arguments) == 0
    adopt(root, ("src/app.py",))
    commit(root, "adopt with fix")
    assert main(arguments) == 1
    assert "separate adoption pull request" in capsys.readouterr().err


def test_a_body_edited_in_the_web_interface_keeps_matching_despite_crlf(repo: tuple[Path, str]) -> None:
    root, base = repo
    adopt(root)
    commit(root, "adopt")
    listing = delivery.render_translation_changes([change()])
    body = ("intro\n\n" + listing).replace("\n", "\r\n")
    assert problems(root, LINE, base, body, lambda path, before, after: [change()]) == []


def test_an_adoption_that_changes_expected_outcomes_is_refused(repo: tuple[Path, str]) -> None:
    root, base = repo
    adopt(root)
    write(root, delivery.BASELINE, '{"identity": 1, "cases": [1]}')
    commit(root, "adopt")
    (found,) = problems(root, LINE, base, "", never)
    assert "expected outcomes" in found
