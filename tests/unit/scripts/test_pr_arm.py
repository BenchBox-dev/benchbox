from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
HEAD = "a" * 40

skip_on_windows = pytest.mark.skipif(
    os.name == "nt",
    reason="the make recipes run through a POSIX shell and the shim is a shell script; Linux and macOS cover them",
)


def _load():
    spec = importlib.util.spec_from_file_location("pr_arm", ROOT / "scripts/pr_arm.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pr_arm = _load()
LandingError = pr_arm.pr_landing.LandingError


def _view(**overrides) -> dict:
    view = {
        "number": 7,
        "state": "OPEN",
        "isDraft": False,
        "labels": [],
        "reviewDecision": "",
        "headRefOid": HEAD,
        "baseRefName": "develop",
    }
    view.update(overrides)
    return view


class FakeGh:
    def __init__(self, view: dict | str | None = None, listed: list[int] | str | None = None, **kwargs) -> None:
        self.view = _view() if view is None else view
        self.listed = [7] if listed is None else listed
        self.merge_code = kwargs.get("merge_code", 0)
        self.view_code = kwargs.get("view_code", 0)
        self.local_head = kwargs.get("local_head", HEAD)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd == ["git", "rev-parse", "HEAD"]:
            return 0, self.local_head + "\n"
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return 0, "fix/thing\n"
        if cmd[:3] == ["gh", "pr", "list"]:
            body = self.listed if isinstance(self.listed, str) else json.dumps([{"number": n} for n in self.listed])
            return 0, body
        if cmd[:3] == ["gh", "pr", "view"]:
            assert len(cmd) > 3 and not cmd[3].startswith("-"), f"gh pr view needs a selector: {cmd}"
            body = self.view if isinstance(self.view, str) else json.dumps(self.view)
            return self.view_code, body
        if cmd[:3] == ["gh", "pr", "merge"]:
            return self.merge_code, "queued"
        raise AssertionError(f"unexpected command {cmd}")

    def merged(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "pr", "merge"]]


def _arm(gh: FakeGh, pr: str | None = "7", head: str | None = None, *, unpublished=(), threads=False) -> int:
    return pr_arm.arm(
        pr,
        head,
        run=gh,
        unpublished=lambda _checkout: list(unpublished),
        threads=lambda _run, _repo, _number: threads,
    )


def test_arms_the_exact_head_with_squash_auto_and_match_head_commit() -> None:
    gh = FakeGh()
    assert _arm(gh) == 0
    assert gh.merged() == [
        ["gh", "pr", "merge", "7", "--repo", pr_arm.REPOSITORY, "--squash", "--auto", "--match-head-commit", HEAD]
    ]


def test_default_resolves_the_current_branch_pr_before_viewing_it() -> None:
    gh = FakeGh(listed=[7])
    assert _arm(gh, pr=None) == 0
    listing = next(call for call in gh.calls if call[:3] == ["gh", "pr", "list"])
    assert listing[listing.index("--head") + 1] == "fix/thing"
    assert listing[listing.index("--base") + 1] == "develop"
    view = next(call for call in gh.calls if call[:3] == ["gh", "pr", "view"])
    assert view[3] == "7"


@pytest.mark.parametrize("listed", [[], [7, 8]])
def test_default_refuses_when_the_branch_does_not_name_exactly_one_pr(
    listed: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(listed=listed)
    assert _arm(gh, pr=None) == 1
    assert gh.merged() == []
    assert "expected exactly one open PR" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("view", "extra", "expected"),
    [
        (_view(labels=[{"name": "no-auto-merge"}]), {}, "durable hold label"),
        (_view(labels=[{"name": "bug"}, {"name": "no-auto-merge"}]), {}, "durable hold label"),
        (_view(reviewDecision="CHANGES_REQUESTED"), {}, "requested changes"),
        (_view(isDraft=True), {}, "draft"),
        (_view(state="CLOSED"), {}, "not OPEN"),
        (_view(state="MERGED"), {}, "not OPEN"),
        (_view(baseRefName="feature/x"), {}, "arms only PRs into 'develop'"),
        (_view(baseRefName="release"), {}, "arms only PRs into 'develop'"),
        (_view(headRefOid="b" * 40), {}, "push first"),
        (_view(), {"threads": True}, "unresolved, non-outdated review thread"),
        (_view(), {"unpublished": ["uncommitted working-tree changes"]}, "unpublished work"),
        (_view(), {"unpublished": ["2 local commit(s) not pushed to upstream"]}, "not pushed"),
    ],
)
def test_refuses_without_merging_when_the_live_pr_says_not_ready(
    view: dict, extra: dict, expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(view=view)
    assert _arm(gh, **extra) == 2
    assert gh.merged() == []
    assert expected in capsys.readouterr().err


def test_an_expected_head_that_is_not_local_head_is_refused_before_any_gh_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(view=_view(headRefOid="a" * 40), local_head="b" * 40)
    assert _arm(gh, head="a" * 40) == 2
    assert gh.merged() == []
    assert not any(call[:2] == ["gh", "pr"] for call in gh.calls)
    assert "is not local HEAD" in capsys.readouterr().err


def test_a_matching_expected_head_is_accepted() -> None:
    gh = FakeGh()
    assert _arm(gh, head=HEAD) == 0
    assert gh.merged()[0][-1] == HEAD


def test_reports_every_reason_not_just_the_first(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh(view=_view(isDraft=True, labels=[{"name": "no-auto-merge"}], baseRefName="release"))
    assert _arm(gh, threads=True) == 2
    err = capsys.readouterr().err
    for expected in ("draft", "durable hold label", "arms only PRs into", "review thread"):
        assert expected in err


@pytest.mark.parametrize(
    ("gh", "expected"),
    [
        (FakeGh(view="gh: not found", view_code=1), "cannot read the pull request"),
        (FakeGh(view="not json"), "live state could not be verified"),
        (FakeGh(view="[]"), "live state could not be verified"),
        (FakeGh(view="{}"), "live state could not be verified"),
        (FakeGh(listed="not json"), "live state could not be verified"),
    ],
)
def test_fails_closed_when_the_live_state_cannot_be_read(
    gh: FakeGh, expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _arm(gh, pr=None if gh.listed == "not json" else "7") == 1
    assert gh.merged() == []
    assert expected in capsys.readouterr().err


def test_fails_closed_when_the_review_thread_check_cannot_run(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh()

    def broken(*_args) -> bool:
        raise LandingError("live review-thread verification failed for PR #7")

    code = pr_arm.arm("7", None, run=gh, unpublished=lambda _c: [], threads=broken)
    assert code == 1
    assert gh.merged() == []
    assert "live state could not be verified" in capsys.readouterr().err


def test_fails_closed_when_a_command_cannot_be_executed(capsys: pytest.CaptureFixture[str]) -> None:
    def missing(_cmd: Sequence[str]) -> tuple[int, str]:
        raise FileNotFoundError("gh")

    assert pr_arm.arm("7", None, run=missing, unpublished=lambda _c: [], threads=lambda *_a: False) == 1
    assert "live state could not be verified" in capsys.readouterr().err


@pytest.mark.parametrize(
    "selector",
    [
        "https://github.com/other-org/other-repo/pull/7",
        "other-org/other-repo#7",
        "feature/branch",
        "7abc",
        "-7",
        "0",
        "07 ",
        " 7",
        "7\n8",
        "",
    ],
)
def test_only_a_plain_pr_number_may_reach_gh(selector: str, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh()
    assert _arm(gh, pr=selector) == 2
    assert gh.calls == [["git", "rev-parse", "HEAD"]]
    assert gh.merged() == []
    assert "plain PR number" in capsys.readouterr().err


def test_a_hold_label_wins_even_when_everything_else_is_fine() -> None:
    gh = FakeGh(view=_view(labels=[{"name": "no-auto-merge"}], reviewDecision="APPROVED"))
    assert _arm(gh) == 2
    assert gh.merged() == []


def test_passes_through_a_failing_merge() -> None:
    gh = FakeGh(merge_code=1)
    assert _arm(gh) == 1
    assert len(gh.merged()) == 1


def test_makefile_exposes_pr_arm_through_the_helper_and_declares_it_phony() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\npr-arm:\n" in makefile
    assert "scripts/pr_arm.py" in makefile
    assert ".PHONY: pr-arm" in makefile.splitlines()


def _make_pr_arm(tmp_path: Path, *assignments: str) -> tuple[list[str], dict[str, str]]:
    shim = tmp_path / "uv"
    shim.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORD/argv"\nenv | grep "^PR_ARM_" | sort > "$RECORD/env"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in {"PR", "HEAD", "REPO"} and not k.startswith("PR_ARM_")}
    env.update({"PATH": f"{tmp_path}{os.pathsep}{env['PATH']}", "RECORD": str(tmp_path)})
    result = subprocess.run(
        ["make", "--no-print-directory", "pr-arm", *assignments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    argv = (tmp_path / "argv").read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in (tmp_path / "env").read_text(encoding="utf-8").splitlines()]
    return argv, dict(pairs)


@pytest.mark.parametrize(
    "value",
    [
        "7 --pr 8",
        "7 --repo another/repository",
        "7; echo injected",
        "$(echo injected)",
        "'7'",
        '"7"',
        "7 ",
        "https://github.com/other-org/other-repo/pull/7",
    ],
)
@pytest.mark.parametrize("name", ["PR", "HEAD", "REPO"])
@skip_on_windows
def test_the_make_wrapper_hands_a_value_over_as_one_untouched_environment_value(
    tmp_path: Path, name: str, value: str
) -> None:
    argv, env = _make_pr_arm(tmp_path, f"{name}={value}")
    assert argv == ["run", "--", "python", "scripts/pr_arm.py"]
    assert env[f"PR_ARM_{name}"] == value
    assert env[f"PR_ARM_{name}_SET"] == "1"


@pytest.mark.parametrize("name", ["PR", "HEAD", "REPO"])
@skip_on_windows
def test_the_make_wrapper_does_not_run_shell_metacharacters_in_a_value(tmp_path: Path, name: str) -> None:
    marker = tmp_path / "ran"
    for value in (f"7; touch {marker}", f"7`touch {marker}`", f"7 && touch {marker}", f"7 | touch {marker}"):
        _, env = _make_pr_arm(tmp_path, f"{name}={value}")
        assert env[f"PR_ARM_{name}"] == value
    assert not marker.exists()


def _make_other_target(tmp_path: Path, target: str, *assignments: str) -> subprocess.CompletedProcess[str]:
    shim = tmp_path / "uv"
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in {"PR", "HEAD", "REPO"} and not k.startswith("PR_ARM_")}
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["make", "--no-print-directory", target, *assignments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("target", "variable"),
    [("pr-landing-withdraw", "PR"), ("pr-landing-ready", "PR"), ("pr-ready", "HEAD"), ("pr-ready", "PR")],
)
@skip_on_windows
def test_making_pr_arm_literal_does_not_change_how_other_targets_see_a_value(
    tmp_path: Path, target: str, variable: str
) -> None:
    marker = tmp_path / "ran"
    result = _make_other_target(tmp_path, target, f"{variable}=$(touch {marker})")
    assert not marker.exists(), result.stderr
    assert result.returncode != 0


@pytest.mark.parametrize("name", ["PR", "HEAD", "REPO"])
@skip_on_windows
def test_the_make_wrapper_tells_an_omitted_variable_from_an_empty_one(tmp_path: Path, name: str) -> None:
    _, omitted = _make_pr_arm(tmp_path)
    assert omitted.get(f"PR_ARM_{name}_SET", "") == ""
    _, empty = _make_pr_arm(tmp_path, f"{name}=")
    assert empty[f"PR_ARM_{name}_SET"] == "1"
    assert empty[f"PR_ARM_{name}"] == ""


def test_main_passes_the_raw_environment_values_to_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple] = []
    monkeypatch.setattr(pr_arm, "arm", lambda pr, head, repo: seen.append((pr, head, repo)) or 0)
    env = {
        "PR_ARM_PR": "7 --pr 8",
        "PR_ARM_PR_SET": "1",
        "PR_ARM_HEAD": HEAD,
        "PR_ARM_HEAD_SET": "1",
        "PR_ARM_REPO": "other-org/other-repo",
        "PR_ARM_REPO_SET": "1",
    }
    assert pr_arm.main([], env=env) == 0
    assert seen == [("7 --pr 8", HEAD, "other-org/other-repo")]
    seen.clear()
    assert pr_arm.main([], env={}) == 0
    assert seen == [(None, None, pr_arm.REPOSITORY)]
    seen.clear()
    assert pr_arm.main([], env={"PR_ARM_PR": "", "PR_ARM_PR_SET": "1"}) == 0
    assert seen == [("", None, pr_arm.REPOSITORY)]


@pytest.mark.parametrize("repo", ["other", "a/b/c", "a b/c", "a/b;c", "", "https://github.com/a/b"])
def test_a_repository_that_is_not_owner_slash_name_is_refused_before_any_command(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def run(cmd: Sequence[str]) -> tuple[int, str]:
        calls.append(list(cmd))
        return 0, ""

    assert pr_arm.arm("7", None, repo=repo, run=run) == 2
    assert calls == []
    assert "owner/name" in capsys.readouterr().err


@pytest.mark.parametrize("head", ["abc123", "A" * 40, "a" * 39, "a" * 41, "", "main", HEAD + " --force"])
def test_a_head_that_is_not_a_full_lowercase_sha_is_refused_before_any_command(
    head: str, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def run(cmd: Sequence[str]) -> tuple[int, str]:
        calls.append(list(cmd))
        return 0, ""

    assert pr_arm.arm("7", head, run=run) == 2
    assert calls == []
    assert "full lowercase commit SHA" in capsys.readouterr().err
