from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.check_make_targets as make_targets
from tests.utilities import batch_evidence as fx

pytestmark = [pytest.mark.unit, pytest.mark.medium]

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
        self.gate_state = kwargs.get("gate_state", "success")
        self.status_code = kwargs.get("status_code", 0)
        self.dispatch_code = kwargs.get("dispatch_code", 0)
        self.verdict = kwargs["verdict"] if "verdict" in kwargs else {"verdict": "pass"}
        self.core = kwargs.get("core", ("completed", "success"))
        self.lookup_code = kwargs.get("lookup_code", 0)
        self.download = kwargs.get("download", (0, None))
        self.verdict_head = kwargs.get("verdict_head", self.local_head)
        self.changed_files = kwargs.get("changed_files", ["scripts/x.py"])
        self.rules = kwargs.get("rules", ["core"])
        self.core_url = kwargs.get("core_url", "")
        self.ci_runs = kwargs.get("ci_runs", [])
        self.calls: list[list[str]] = []
        self.rules = kwargs.get("rules", ["core"])
        self.statuses = kwargs.get("statuses", {})
        self.status_descriptions = kwargs.get("status_descriptions", {})
        self.extra_runs = kwargs.get("extra_runs", [])
        self.comments = kwargs.get("comments", [])
        self.worktree_extension = kwargs.get("worktree_extension", True)
        self.worktree_config = {
            "benchbox.batch.role": kwargs.get("batch_role"),
            "benchbox.batch.id": kwargs.get("batch_id"),
        }
        self.registry = kwargs.get("registry", {})
        self.tracker_tip = "d" * 40

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
        if cmd[:2] == ["gh", "api"] and cmd[-1] == f"repos/{pr_arm.REPOSITORY}/commits/{self.local_head}/status":
            return self.status_code, self.gate_state + "\n" if self.status_code == 0 else "gh: HTTP 502"
        if cmd[:2] == ["gh", "api"] and f"repos/{pr_arm.REPOSITORY}/commits/{self.local_head}/statuses?" in cmd[-1]:
            lines = [
                "\t".join([name, state, "benchbox-oracle[bot]", "Bot", self.status_descriptions.get(name, "")])
                for name, state in self.statuses.items()
            ]
            return 0, "\n".join(lines)
        if cmd[:2] == ["gh", "api"] and "/actions/artifacts?" in cmd[-1]:
            assert cmd[-1].startswith(f"repos/{pr_arm.REPOSITORY}/actions/artifacts?name=fast-duration-verdict-7-")
            if self.lookup_code != 0:
                return self.lookup_code, "gh: HTTP 502"
            return 0, "" if self.verdict is None else f"555 {self.verdict_head}\n"
        if cmd[:3] == ["gh", "run", "download"]:
            code, text = self.download
            if code != 0:
                return code, text
            Path(cmd[cmd.index("-D") + 1], "fast-duration-verdict.json").write_text(
                text if text is not None else json.dumps(self.verdict), encoding="utf-8"
            )
            return 0, ""
        if cmd[:2] == ["gh", "api"] and "/rules/branches/" in cmd[-1]:
            return 0, "\n".join(self.rules)
        if cmd[:3] == ["gh", "api", "--paginate"] and "/actions/workflows/ci.yml/runs?" in cmd[-1]:
            assert f"head_sha={self.local_head}" in cmd[-1]
            return 0, "\n".join("\t".join(str(field) for field in (run + ("", ""))[:5]) for run in self.ci_runs)
        if cmd[:3] == ["gh", "api", "--paginate"] and "/check-runs?" in cmd[-1]:
            return 0, self._check_runs()
        if cmd[:2] == ["gh", "api"] and cmd[-1].endswith("/pulls/7/files"):
            return 0, "\n".join(self.changed_files)
        if cmd[:3] == ["gh", "workflow", "run"]:
            return self.dispatch_code, "" if self.dispatch_code == 0 else "gh: HTTP 403"
        handled = self._tracker_call(cmd) or self._config_call(cmd)
        if handled is not None:
            return handled
        raise AssertionError(f"unexpected command {cmd}")

    def _tracker_call(self, cmd: list[str]) -> tuple[int, str] | None:
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/git/ref/heads/todo-state"):
            return 0, self.tracker_tip + "\n"
        if cmd[:2] == ["gh", "api"] and cmd[-1].endswith(f"/contents/index.json?ref={self.tracker_tip}"):
            return 0, json.dumps({"schema_version": 4, "items": {}, "batches": self.registry})
        return None

    def _config_call(self, cmd: list[str]) -> tuple[int, str] | None:
        if cmd == ["git", "config", "--get", "extensions.worktreeConfig"]:
            return (0, "true\n") if self.worktree_extension else (1, "")
        if cmd[:3] == ["git", "config", "--worktree"]:
            if not self.worktree_extension:
                return 128, "fatal: --worktree cannot be used with multiple working trees unless worktreeConfig"
            value = self.worktree_config[cmd[3]]
            return (0, value + "\n") if value else (1, "")
        return None

    def _check_runs(self) -> str:
        runs = [("core", *self.core)] + list(self.extra_runs)
        lines = []
        for index, (name, status, conclusion) in enumerate(runs, 1):
            record = {"id": index, "name": name, "status": status, "conclusion": conclusion}
            record["head_sha"] = self.local_head
            if name == "core":
                record["details_url"] = self.core_url
            lines.append(json.dumps({**record, "check_suite": {"id": 9}}))
        return "\n".join(lines)

    def dispatched(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "workflow", "run"]]

    def merged(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "pr", "merge"]]


def _threads(unresolved: bool) -> list[dict]:
    return [{"id": "T1", "path": "scripts/x.py", "isResolved": False, "isOutdated": False}] if unresolved else []


def _arm(gh: FakeGh, pr: str | None = "7", head: str | None = None, *, unpublished=(), threads=False, hidden=()) -> int:
    return pr_arm.arm(
        pr,
        head,
        run=gh,
        unpublished=lambda _checkout: list(unpublished),
        threads=lambda _run, _repo, _number: _threads(threads),
        suites=lambda _run, _repo, _base, _head: list(hidden),
        gate=lambda _run, _repo, _number, _head: [],
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
        (_view(), {"threads": True}, "1 unresolved review thread(s) block the merge"),
        (_view(), {"hidden": ["required check 'review' suite 9 failure is hidden"]}, "is hidden"),
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


def test_an_outdated_unresolved_thread_alone_refuses_arming_and_is_named(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh()
    outdated = [{"id": "T9", "path": "scripts/old.py", "isResolved": False, "isOutdated": True}]
    code = pr_arm.arm(
        "7",
        None,
        run=gh,
        unpublished=lambda _checkout: [],
        threads=lambda _run, _repo, _number: outdated,
        suites=lambda _run, _repo, _base, _head: [],
        gate=lambda _run, _repo, _number, _head: [],
    )
    assert code == 2
    assert gh.merged() == []
    captured = capsys.readouterr()
    assert "scripts/old.py (outdated)" in captured.err
    assert "outcome: refused" in captured.out


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

    code = pr_arm.arm("7", None, run=gh, unpublished=lambda _c: [], threads=broken, suites=lambda *_args: [])
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
    makefile = make_targets.makefile_sources(ROOT)
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
    env = {
        k: v for k, v in os.environ.items() if k not in {"PR", "HEAD", "REPO", "WAIT"} and not k.startswith("PR_ARM_")
    }
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


@pytest.mark.parametrize("name", ["PR", "HEAD", "REPO", "WAIT"])
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


@pytest.mark.parametrize("name", ["PR", "HEAD", "REPO", "WAIT"])
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


def test_fails_closed_when_the_suite_check_cannot_run(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh()

    def broken(*_args) -> list[str]:
        raise LandingError("live check verification failed")

    code = pr_arm.arm("7", None, run=gh, unpublished=lambda _c: [], threads=lambda *_a: False, suites=broken)
    assert code == 1
    assert gh.merged() == []
    assert "live state could not be verified" in capsys.readouterr().err


def test_the_live_suite_check_receives_the_base_branch_and_local_head() -> None:
    seen: list[tuple[str, str]] = []

    def record(_run, _repo, base: str, head: str) -> list[str]:
        seen.append((base, head))
        return []

    pr_arm.arm(
        "7",
        None,
        run=FakeGh(),
        unpublished=lambda _c: [],
        threads=lambda *_a: False,
        suites=record,
        gate=lambda *_a: [],
    )
    assert seen == [("develop", HEAD)]


@pytest.mark.parametrize("state", ["failure", "error", "pending", "", "success"])
def test_arming_never_dispatches_the_gate_whatever_its_status(state: str) -> None:
    gh = FakeGh(gate_state=state)
    assert _arm(gh) == 0
    assert gh.dispatched() == []
    assert not any(call[:3] == ["gh", "workflow", "run"] for call in gh.calls)
    assert len(gh.merged()) == 1


WOULD_BLOCK = {"verdict": "would-block", "sum_ratio": 1.4567, "median_test_ratio": 1.2345}
BASELINE_FILE = "tests/fixtures/fast_tier_baseline/tests-00.json"


def test_a_would_block_verdict_refuses_with_the_ratios_and_the_baseline_route(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(verdict=WOULD_BLOCK)
    assert _arm(gh) == 2
    assert gh.merged() == []
    err = capsys.readouterr().err
    for expected in (
        "would-block",
        "summed-time ratio 1.457",
        "median per-test ratio 1.234",
        "The duration gate blocks `core` on a would-block verdict",
        "scripts/build_fast_duration_baseline.py",
        "tests/fixtures/fast_tier_baseline",
    ):
        assert expected in err


def test_a_would_block_verdict_arms_when_the_pr_updates_the_baseline() -> None:
    gh = FakeGh(verdict=WOULD_BLOCK, changed_files=["scripts/x.py", BASELINE_FILE])
    assert _arm(gh) == 0
    assert len(gh.merged()) == 1


def test_a_would_block_verdict_refuses_when_the_changed_files_cannot_be_listed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Broken(FakeGh):
        def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
            if list(cmd)[:2] == ["gh", "api"] and list(cmd)[-1].endswith("/pulls/7/files"):
                return 1, "gh: HTTP 502"
            return super().__call__(cmd)

    gh = Broken(verdict=WOULD_BLOCK)
    assert _arm(gh) == 1
    assert gh.merged() == []
    assert "live state could not be verified" in capsys.readouterr().err


@pytest.mark.parametrize(
    "verdict",
    [
        {"verdict": "pass", "sum_ratio": 1.01, "median_test_ratio": 1.0},
        {"verdict": "neutral", "reason": "no baseline"},
        {"verdict": "not-applicable", "reason": "no fast-tier tests ran for this head"},
    ],
)
def test_pass_neutral_and_not_applicable_verdicts_arm_normally(verdict: dict | None) -> None:
    gh = FakeGh(verdict=verdict)
    assert _arm(gh) == 0
    assert len(gh.merged()) == 1
    assert not any(call[-1].endswith("/pulls/7/files") for call in gh.calls)


@pytest.mark.parametrize(
    "verdict",
    [
        {"verdict": "rejected", "reason": "calibration missing"},
        {"verdict": "mystery"},
        {"reason": "no verdict field"},
        {"verdict": None},
    ],
)
def test_rejected_and_unknown_duration_verdicts_hold_arming(verdict: dict, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh(verdict=verdict, changed_files=[BASELINE_FILE])
    assert _arm(gh) == 2
    assert gh.merged() == []
    err = capsys.readouterr().err
    assert "verify-fast-duration verdict" in err and "not one of" in err


def test_a_verdict_bound_to_a_superseded_head_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh(verdict=WOULD_BLOCK, verdict_head="c" * 40)
    assert _arm(gh) == 2
    assert gh.merged() == []
    assert not any(call[:3] == ["gh", "run", "download"] for call in gh.calls)
    assert "not bound to" in capsys.readouterr().err


def test_the_verdict_is_looked_up_for_the_current_head_only() -> None:
    gh = FakeGh(verdict=WOULD_BLOCK, changed_files=[BASELINE_FILE])
    _arm(gh)
    lookup = next(call for call in gh.calls if "/actions/artifacts?" in call[-1])
    assert lookup[-1].endswith(f"name=fast-duration-verdict-7-{HEAD}&per_page=5")


def test_a_missing_verdict_refuses_while_the_producing_job_is_pending_and_arms_once_it_exists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending = FakeGh(verdict=None, core=("in_progress", None))
    assert _arm(pending) == 2
    assert pending.merged() == []
    err = capsys.readouterr().err
    assert "no verify-fast-duration verdict is recorded" in err
    assert "core job" in err
    assert "has not finished" in err
    recorded = FakeGh(verdict={"verdict": "pass"}, core=("in_progress", None))
    assert _arm(recorded) == 0
    assert len(recorded.merged()) == 1


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", "success"])
def test_a_missing_verdict_refuses_with_the_rerun_step_when_the_producing_job_finished(
    conclusion: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(verdict=None, core=("completed", conclusion))
    assert _arm(gh) == 2
    assert gh.merged() == []
    err = capsys.readouterr().err
    assert f"ended {conclusion}" in err
    assert "make pr-rerun PR=7" in err
    assert ("push a new head" in err) == (conclusion in {"skipped", "success"})


def test_a_missing_verdict_with_no_producing_job_refuses() -> None:
    class NoCore(FakeGh):
        def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
            if list(cmd)[:3] == ["gh", "api", "--paginate"] and "/check-runs?" in list(cmd)[-1]:
                return 0, ""
            return super().__call__(cmd)

    gh = NoCore(verdict=None)
    assert _arm(gh) == 2
    assert gh.merged() == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"lookup_code": 1}, "could not look up the verify-fast-duration verdict"),
        ({"download": (1, "gh: HTTP 404")}, "could not download the verify-fast-duration verdict"),
        ({"download": (0, "{not json")}, "is unreadable"),
        ({"download": (0, "[1]")}, "is unreadable"),
    ],
)
def test_an_untrustworthy_verdict_refuses_instead_of_arming(
    kwargs: dict, expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(**kwargs)
    assert _arm(gh) == 2
    assert gh.merged() == []
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required"])
def test_visible_terminal_required_check_conclusion_refuses_arming(
    conclusion: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(core=("completed", conclusion))
    assert _arm(gh) == 2
    assert gh.merged() == []
    assert f"required check 'core' completed with conclusion {conclusion!r}" in capsys.readouterr().err


@pytest.mark.parametrize("status", ["in_progress", "queued"])
def test_pending_required_checks_still_arm(status: str) -> None:
    gh = FakeGh(core=(status, None))
    assert _arm(gh) == 0
    assert len(gh.merged()) == 1


def test_non_required_failing_check_does_not_refuse_arming() -> None:
    gh = FakeGh(extra_runs=[("flaky-thing", "completed", "failure")])
    assert _arm(gh) == 0
    assert len(gh.merged()) == 1


@pytest.mark.parametrize("state", ["failure", "error"])
def test_visible_failed_required_status_refuses_arming(state: str, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh(statuses={"review-gate": state}, rules=["core", "review-gate"])
    assert _arm(gh) == 2
    assert gh.merged() == []
    assert f"required status 'review-gate' completed with state {state!r}" in capsys.readouterr().err


def test_pending_required_status_still_arms() -> None:
    gh = FakeGh(statuses={"review-gate": "pending"}, rules=["core", "review-gate"])
    assert _arm(gh) == 0
    assert len(gh.merged()) == 1


def test_waiting_oracle_description_for_another_head_does_not_mask_failure() -> None:
    gh = FakeGh(
        statuses={"review-gate": "failure"},
        status_descriptions={
            "review-gate": f"review: waiting for the review's verdict on {'b' * 40}, or a stand-in attestation"
        },
        rules=["core", "review-gate"],
    )
    assert _arm(gh) == 2
    assert gh.merged() == []


@pytest.mark.parametrize(
    ("description", "arms"),
    [
        (f"Waiting for review-verdict · comment `Stand-in review: APPROVE {'a' * 40}` to waive", True),
        (f"Waiting for review-verdict · comment `Stand-in review: APPROVE {'b' * 40}` to waive", False),
        ("Waiting for review-verdict", False),
    ],
)
def test_waiting_review_description_masks_failure_only_for_this_head(description: str, arms: bool) -> None:
    gh = FakeGh(
        statuses={"review-gate": "failure"},
        status_descriptions={"review-gate": description},
        rules=["core", "review-gate"],
    )
    assert _arm(gh) == (0 if arms else 2)
    assert len(gh.merged()) == (1 if arms else 0)


PLACEHOLDER_URL = f"https://github.com/{pr_arm.REPOSITORY}/actions/runs/90/job/1"
FULL_URL = f"https://github.com/{pr_arm.REPOSITORY}/actions/runs/91/job/1"


def _arm_live(gh: FakeGh) -> int:
    return pr_arm.arm(
        "7",
        None,
        run=gh,
        unpublished=lambda _checkout: [],
        threads=lambda _run, _repo, _number: False,
        gate=lambda *_a: [],
    )


@pytest.mark.parametrize("verdict", [None, {"verdict": "pass"}])
def test_a_placeholder_core_failure_with_the_full_run_in_progress_waits_and_never_advises_a_rerun(
    verdict: dict | None, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(
        verdict=verdict,
        core=("completed", "failure"),
        core_url=PLACEHOLDER_URL,
        ci_runs=[(90, "CI (text edit)", "completed"), (91, "CI", "in_progress")],
    )
    assert _arm_live(gh) == 2
    assert gh.merged() == []
    err = capsys.readouterr().err
    assert "waiting for full CI run 91" in err
    assert "pr-rerun" not in err
    assert "no verdict will arrive" not in err
    assert "completed with conclusion" not in err


NOW = datetime(2026, 11, 11, 18, 0, 0, tzinfo=timezone.utc)


def _stamp(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr_arm, "_now", lambda: NOW)


def _placeholder_gap(full_conclusion: str, full_age: int, placeholder_age: int, **extra: object) -> FakeGh:
    return FakeGh(
        verdict=None,
        core=("completed", "failure"),
        core_url=PLACEHOLDER_URL,
        ci_runs=[
            (90, "CI (text edit)", "completed", "failure", _stamp(placeholder_age)),
            (91, "CI", "completed", full_conclusion, _stamp(full_age)),
        ],
        **extra,
    )


def test_a_placeholder_core_failure_just_after_the_full_run_succeeded_waits_for_the_refresh(
    fixed_now: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _arm_live(_placeholder_gap("success", full_age=20, placeholder_age=900)) == 2
    captured = capsys.readouterr()
    assert "waiting for the placeholder re-run" in captured.err
    assert "pr-rerun" not in captured.err
    assert "completed with conclusion" not in captured.err
    assert "outcome: waiting" in captured.out


def test_a_placeholder_core_failure_with_its_rerun_in_progress_waits_for_the_refresh(
    fixed_now: None, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(
        verdict=None,
        core=("completed", "failure"),
        core_url=PLACEHOLDER_URL,
        ci_runs=[
            (90, "CI (text edit)", "completed", "failure", _stamp(900)),
            (92, "CI (text edit)", "queued", "", _stamp(5)),
            (91, "CI", "completed", "success", _stamp(2000)),
        ],
    )
    assert _arm_live(gh) == 2
    err = capsys.readouterr().err
    assert "waiting for the placeholder re-run of CI run 92" in err
    assert "pr-rerun" not in err


def test_a_placeholder_core_failure_that_ran_again_after_the_full_run_completed_is_refused_with_the_rerun_advice(
    fixed_now: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _arm_live(_placeholder_gap("success", full_age=60, placeholder_age=10)) == 2
    err = capsys.readouterr().err
    assert "waiting for" not in err
    assert "make pr-rerun PR=7" in err
    assert "required check 'core' completed with conclusion 'failure'" in err


def test_a_placeholder_core_failure_past_the_refresh_bound_is_refused_with_the_rerun_advice(
    fixed_now: None, capsys: pytest.CaptureFixture[str]
) -> None:
    gap = pr_arm.PLACEHOLDER_REFRESH_SECONDS + 1
    assert _arm_live(_placeholder_gap("success", full_age=gap, placeholder_age=gap + 900)) == 2
    err = capsys.readouterr().err
    assert "waiting for" not in err
    assert "make pr-rerun PR=7" in err
    assert "required check 'core' completed with conclusion 'failure'" in err


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_a_placeholder_core_failure_behind_a_full_run_that_did_not_succeed_is_refused_with_the_rerun_advice(
    conclusion: str, fixed_now: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _arm_live(_placeholder_gap(conclusion, full_age=20, placeholder_age=900)) == 2
    err = capsys.readouterr().err
    assert "waiting for" not in err
    assert "make pr-rerun PR=7" in err


def test_a_placeholder_core_failure_without_run_timestamps_is_refused_with_the_rerun_advice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(
        verdict=None,
        core=("completed", "failure"),
        core_url=PLACEHOLDER_URL,
        ci_runs=[(90, "CI (text edit)", "completed", "failure"), (91, "CI", "completed", "success")],
    )
    assert _arm_live(gh) == 2
    err = capsys.readouterr().err
    assert "waiting for" not in err
    assert "make pr-rerun PR=7" in err


def test_a_real_core_failure_is_not_mistaken_for_a_placeholder_while_another_run_is_in_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(
        verdict=None,
        core=("completed", "failure"),
        core_url=FULL_URL,
        ci_runs=[(91, "CI", "completed"), (92, "CI", "in_progress")],
    )
    assert _arm_live(gh) == 2
    err = capsys.readouterr().err
    assert "waiting for full CI run" not in err
    assert "make pr-rerun PR=7" in err
    assert "required check 'core' completed with conclusion 'failure'" in err


@pytest.mark.parametrize("verdict", [None, {"verdict": "pass"}])
def test_a_real_core_failure_inside_the_full_run_still_in_progress_waits_for_that_run(
    verdict: dict | None, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(
        verdict=verdict,
        core=("completed", "failure"),
        core_url=FULL_URL,
        ci_runs=[(91, "CI", "in_progress")],
    )
    assert _arm_live(gh) == 2
    assert gh.merged() == []
    err = capsys.readouterr().err
    assert "waiting for full CI run 91 to complete" in err
    assert "no verdict will arrive" not in err
    assert "completed with conclusion" not in err
    assert "make pr-rerun" in err


def test_a_real_core_failure_inside_a_completed_run_advises_the_rerun_now(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(
        verdict=None,
        core=("completed", "failure"),
        core_url=FULL_URL,
        ci_runs=[(91, "CI", "completed")],
    )
    assert _arm_live(gh) == 2
    err = capsys.readouterr().err
    assert "waiting for full CI run" not in err
    assert "make pr-rerun PR=7" in err


def _final_line(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines
    return lines[-1]


def _outcomes(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines() if ": outcome: " in line]


def test_an_armed_pull_request_ends_with_the_armed_outcome_naming_pr_and_head(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(verdict={"verdict": "pass"})
    assert _arm(gh) == 0
    final = _final_line(capsys)
    assert "outcome: armed" in final
    assert "#7" in final
    assert HEAD in final


def test_a_refused_pull_request_ends_with_the_refused_outcome_and_the_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(verdict={"verdict": "pass"})
    assert _arm(gh, unpublished=["3 commits not pushed"]) == 2
    final = _final_line(capsys)
    assert "outcome: refused" in final
    assert "3 commits not pushed" in final
    assert gh.merged() == []


def test_a_failing_merge_ends_with_the_refused_outcome_not_armed(capsys: pytest.CaptureFixture[str]) -> None:
    class MergeFails(FakeGh):
        def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
            if list(cmd)[:3] == ["gh", "pr", "merge"]:
                self.calls.append(list(cmd))
                return 1, "merge exploded"
            return super().__call__(cmd)

    assert _arm(MergeFails(verdict={"verdict": "pass"})) == 1
    final = _final_line(capsys)
    assert "outcome: refused" in final
    assert "merge exploded" in final


def test_a_pull_request_waiting_on_a_full_run_ends_with_the_waiting_outcome(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGh(
        verdict=None,
        core=("completed", "failure"),
        core_url=PLACEHOLDER_URL,
        ci_runs=[(90, "CI (text edit)", "completed"), (91, "CI", "in_progress")],
    )
    assert _arm_live(gh) == 2
    final = _final_line(capsys)
    assert "outcome: waiting" in final
    assert "91" in final


def test_a_pending_producing_job_ends_with_the_waiting_outcome(capsys: pytest.CaptureFixture[str]) -> None:
    assert _arm(FakeGh(verdict=None, core=("in_progress", None))) == 2
    assert "outcome: waiting" in _final_line(capsys)


@pytest.mark.parametrize("repo", ["not-a-repo", "a/b/c"])
def test_early_refusals_also_end_with_exactly_one_refused_outcome(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert pr_arm.arm("7", None, repo=repo, run=FakeGh()) == 2
    outcomes = _outcomes(capsys)
    assert len(outcomes) == 1
    assert "outcome: refused" in outcomes[0]


def test_a_passing_duration_verdict_line_does_not_read_as_an_arming_outcome(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _arm(FakeGh(verdict={"verdict": "pass"})) == 0
    out = capsys.readouterr().out
    duration_lines = [line for line in out.splitlines() if "verify-fast-duration" in line]
    assert duration_lines
    assert not any("arming" in line or "armed" in line or "held" in line for line in duration_lines)
    assert len(_outcomes_from(out)) == 1


def _outcomes_from(text: str) -> list[str]:
    return [line for line in text.splitlines() if ": outcome: " in line]


def test_a_fresh_gate_evaluation_that_does_not_pass_refuses_arming() -> None:
    gh = FakeGh()
    code = pr_arm.arm(
        "7",
        None,
        run=gh,
        unpublished=lambda _c: [],
        threads=lambda *_a: False,
        suites=lambda *_a: [],
        duration=lambda *_a: [],
        visible=lambda *_a: [],
        gate=lambda *_a: ["a fresh review-gate evaluation of aaaaaaaaa does not pass: waiting"],
    )
    assert code == 2
    assert gh.merged() == []


@pytest.mark.parametrize(("exit_code", "expected"), [(0, []), (3, "does not pass"), (2, "failed (exit 2)")])
def test_gate_refusals_map_the_evaluation_exit_code(exit_code: int, expected: object) -> None:
    seen: list[list[str]] = []

    def run(cmd: Sequence[str]) -> tuple[int, str]:
        seen.append(list(cmd))
        return exit_code, "review: waiting for the verdict\n" if exit_code else ""

    found = pr_arm.gate_refusals(run, "o/r", 7, HEAD)
    assert seen[0][-6:] == ["--repo", "o/r", "--pr", "7", "--head-sha", HEAD]
    assert "theseus_cli.py" in " ".join(seen[0])
    assert "--post" not in seen[0]
    assert found == [] if exit_code == 0 else expected in found[0]


def _batch_arm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate=None,
    overlap: bool = False,
    role: str | None = "integration",
    batch_id: str | None = "b",
    write_evidence: bool = True,
    tracker_error: Exception | None = None,
    registry: dict | None = None,
    view: dict | None = None,
) -> tuple[int, FakeGh, list[str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
        fx.git(repo, *args)
    fx.write_commit(repo, {"f.txt": "v1"}, "init")
    batch = fx.BatchFixture(repo, {"a": ["a/**"], "b": ["b/**"]})
    batch.land("a", {"a/x.txt": "a"})
    batch.land("b", {"b/y.txt": "b"})
    if overlap:
        batch.overlap("b", "a", ["a/x.txt"])
    tracker = batch.tracker()
    evidence = batch.evidence()
    if mutate is not None:
        mutate(tracker, evidence)
    head = fx.head_of(repo)
    if write_evidence:
        target = repo / ".todo-batch" / "b" / "final-evidence.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(evidence), encoding="utf-8")
    reads: list[str] = []

    def read(requested: str) -> dict:
        reads.append(requested)
        if tracker_error is not None:
            raise tracker_error
        return tracker

    monkeypatch.setattr(pr_arm.pr_landing, "read_batch_tracker", read)
    if registry is None:
        monkeypatch.setattr(
            pr_arm.pr_landing,
            "discover_batch",
            lambda *args, **kwargs: (batch_id if role == "integration" else None, "rev-1"),
        )
    else:
        monkeypatch.setattr(pr_arm.pr_landing, "pinned_tracker_batches", lambda *args, **kwargs: (registry, "d" * 40))
    gh = FakeGh(view=_view(headRefOid=head, **(view or {})), local_head=head, batch_role=role, batch_id=batch_id)
    code = pr_arm.arm(
        "7",
        None,
        run=gh,
        checkout=repo,
        unpublished=lambda _checkout: [],
        threads=lambda _run, _repo, _number: False,
        suites=lambda _run, _repo, _base, _head: [],
        gate=lambda _run, _repo, _number, _head: [],
    )
    return code, gh, reads


def test_an_integration_worktree_arms_a_pr_whose_batch_evidence_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, gh, reads = _batch_arm(tmp_path, monkeypatch, overlap=True)

    assert code == 0
    assert reads == ["b"]
    assert len(gh.merged()) == 1


@pytest.mark.parametrize("registered_repository", ["BenchBox-dev/BenchBox", "benchbox-dev/benchbox"])
def test_a_markerless_checkout_still_applies_batch_evidence_to_a_registered_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered_repository: str
) -> None:
    registry = {
        "b": {
            "lifecycle": "active",
            "repository": registered_repository,
            "integration_branch": "feat/b",
            "final_pr": {"number": 7, "node_id": "PR_b", "head": "0" * 40},
        }
    }

    code, gh, reads = _batch_arm(tmp_path, monkeypatch, role=None, batch_id=None, registry=registry)
    assert reads == ["b"] and code == 0 and len(gh.merged()) == 1

    (tmp_path / "forged").mkdir()
    forged, forged_gh, _ = _batch_arm(
        tmp_path / "forged",
        monkeypatch,
        role=None,
        batch_id=None,
        registry=registry,
        mutate=lambda _tracker, evidence: evidence["member_reviews"]["b"].update(outcome="override"),
    )
    assert forged == 2 and forged_gh.merged() == []


def test_a_pr_from_a_registered_member_branch_is_refused_instead_of_armed_as_ordinary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = {"b": {"lifecycle": "active", "repository": "BenchBox-dev/BenchBox", "integration_branch": "feat/b"}}

    code, gh, reads = _batch_arm(
        tmp_path, monkeypatch, role=None, batch_id=None, registry=registry, view={"headRefName": "feat/b--a"}
    )

    assert code == 1 and reads == [] and gh.merged() == []
    assert "is a member branch of batch 'b'" in capsys.readouterr().err


def _drop_acceptance(tracker: dict, evidence: dict) -> None:
    del evidence["current_acceptance"]["a"]


def _move_tracker_head(tracker: dict, evidence: dict) -> None:
    tracker["final_pr"]["head"] = "f" * 40


def _other_pr(tracker: dict, evidence: dict) -> None:
    tracker["final_pr"]["number"] = 8
    evidence["tracker"]["final_pr"]["number"] = 8


def _aborted_batch(tracker: dict, evidence: dict) -> None:
    tracker["lifecycle"] = "aborted"


def _completed_batch(tracker: dict, evidence: dict) -> None:
    tracker["lifecycle"] = "completed"


def _reviewed_with_findings(tracker: dict, evidence: dict) -> None:
    evidence["member_reviews"]["b"]["outcome"] = "findings"


@pytest.mark.parametrize(
    "mutate",
    [_drop_acceptance, _move_tracker_head, _other_pr, _completed_batch, _reviewed_with_findings],
)
def test_an_integration_worktree_refuses_a_pr_whose_batch_evidence_does_not_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate
) -> None:
    code, gh, _ = _batch_arm(tmp_path, monkeypatch, overlap=True, mutate=mutate)

    assert code == 2
    assert gh.merged() == []


def test_an_aborted_batch_is_armed_as_an_ordinary_pr_despite_the_worktree_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, gh, reads = _batch_arm(tmp_path, monkeypatch, overlap=True, mutate=_aborted_batch, write_evidence=False)

    assert code == 0
    assert reads == ["b"]
    assert len(gh.merged()) == 1


def test_an_integration_worktree_without_a_batch_id_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, gh, reads = _batch_arm(tmp_path, monkeypatch, batch_id=None)

    assert code == 2
    assert reads == []
    assert gh.merged() == []


@pytest.mark.parametrize("failure", ["no-evidence-file", "tracker-unreachable"])
def test_an_integration_worktree_fails_closed_when_the_evidence_or_tracker_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    code, gh, _ = _batch_arm(
        tmp_path,
        monkeypatch,
        write_evidence=failure != "no-evidence-file",
        tracker_error=LandingError("tracker offline") if failure == "tracker-unreachable" else None,
    )

    assert code == 1
    assert gh.merged() == []


@pytest.mark.parametrize("role", [None, "member"])
def test_an_ordinary_or_member_worktree_never_reads_the_tracker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str | None
) -> None:
    code, gh, reads = _batch_arm(tmp_path, monkeypatch, role=role, tracker_error=LandingError("tracker offline"))

    assert code == 0
    assert reads == []
    assert len(gh.merged()) == 1


def test_a_clone_without_the_worktree_config_extension_arms_an_ordinary_pr() -> None:
    gh = FakeGh(worktree_extension=False)

    assert _arm(gh) == 0
    assert len(gh.merged()) == 1
    assert not any(call[:3] == ["git", "config", "--worktree"] for call in gh.calls)


def test_a_worktree_config_read_failure_with_the_extension_enabled_still_fails_closed() -> None:
    gh = FakeGh(batch_role="integration")
    gh.worktree_config["benchbox.batch.role"] = "integration"
    original = gh.__call__

    def failing(cmd):
        if list(cmd)[:3] == ["git", "config", "--worktree"]:
            return 128, "fatal: broken"
        return original(cmd)

    code = pr_arm.arm(
        "7",
        None,
        run=failing,
        unpublished=lambda _checkout: [],
        threads=lambda _run, _repo, _number: False,
        suites=lambda _run, _repo, _base, _head: [],
        gate=lambda _run, _repo, _number, _head: [],
    )

    assert code == 1
    assert gh.merged() == []


class WaitClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []
        self.on_sleep: list = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        for action in self.on_sleep:
            action()


def _scripted(outcomes: list[tuple[str, int]]) -> tuple[list[int], object]:
    attempts: list[int] = []

    def attempt() -> int:
        line, code = outcomes[min(len(attempts), len(outcomes) - 1)]
        attempts.append(code)
        return pr_arm._outcome(line, code)

    return attempts, attempt


WAITING = ("waiting: PR #7 is not armed yet: the core job that records it has not finished for aaaaaaaaa", 2)
ARMED = (f"armed: PR #7 at head {HEAD}", 0)
REFUSED = ("refused: PR #7 was not armed: 1 unresolved review thread(s) block the merge", 2)


def _last_outcome_line(capsys: pytest.CaptureFixture[str]) -> str:
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("pr-arm: outcome: ")][-1]


def test_a_wait_arms_once_the_outcome_stops_being_waiting(capsys: pytest.CaptureFixture[str]) -> None:
    time = WaitClock()
    gates: list[int] = []
    attempts, attempt = _scripted([WAITING, WAITING, ARMED])

    code = pr_arm.arm_waiting(attempt, lambda: gates.append(1), 60, clock=time.clock, sleep=time.sleep)

    assert code == 0
    assert len(attempts) == 3
    assert time.sleeps == [pr_arm.WAIT_INTERVAL_SECONDS] * 2
    assert len(gates) == 2
    assert _last_outcome_line(capsys).startswith("pr-arm: outcome: armed: ")


def test_a_wait_stops_at_the_first_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    time = WaitClock()
    attempts, attempt = _scripted([WAITING, REFUSED, ARMED])

    assert pr_arm.arm_waiting(attempt, lambda: None, 60, clock=time.clock, sleep=time.sleep) == 2
    assert len(attempts) == 2
    assert _last_outcome_line(capsys).startswith("pr-arm: outcome: refused: ")


def test_a_refusal_on_the_first_check_never_waits() -> None:
    time = WaitClock()
    gates: list[int] = []
    attempts, attempt = _scripted([REFUSED])

    assert pr_arm.arm_waiting(attempt, lambda: gates.append(1), 60, clock=time.clock, sleep=time.sleep) == 2
    assert (len(attempts), time.sleeps, gates) == (1, [], [])


def test_a_wait_that_runs_out_reports_the_last_waiting_outcome_as_expired(
    capsys: pytest.CaptureFixture[str],
) -> None:
    time = WaitClock()
    attempts, attempt = _scripted([WAITING])

    assert pr_arm.arm_waiting(attempt, lambda: None, 7, clock=time.clock, sleep=time.sleep) == 2
    assert time.sleeps == [180.0, 180.0, 60.0]
    assert len(attempts) == 4
    last = _last_outcome_line(capsys)
    assert last.startswith("pr-arm: outcome: waiting: ")
    assert last.endswith("(WAIT=7 expired)")


def test_a_trunk_that_turns_red_while_waiting_ends_the_wait_without_arming(
    capsys: pytest.CaptureFixture[str],
) -> None:
    time = WaitClock()
    attempts, attempt = _scripted([WAITING, ARMED])

    code = pr_arm.arm_waiting(attempt, lambda: "develop is red: culprit #9", 60, clock=time.clock, sleep=time.sleep)

    assert code == 2
    assert len(attempts) == 1
    last = _last_outcome_line(capsys)
    assert last.startswith("pr-arm: outcome: refused: ")
    assert "develop is red: culprit #9" in last


def test_a_wait_over_the_real_checks_arms_when_the_verdict_arrives() -> None:
    time = WaitClock()
    gh = FakeGh(verdict=None, core=("in_progress", None))
    time.on_sleep.append(lambda: setattr(gh, "verdict", {"verdict": "pass"}))

    code = pr_arm.arm_waiting(lambda: _arm(gh), lambda: None, 30, clock=time.clock, sleep=time.sleep)

    assert code == 0
    assert time.sleeps == [pr_arm.WAIT_INTERVAL_SECONDS]
    assert len(gh.merged()) == 1


@pytest.mark.parametrize(
    ("source", "kwargs"),
    [
        ("a hold label", {"view": _view(labels=[{"name": pr_arm.HOLD_LABEL}])}),
        ("a draft", {"view": _view(isDraft=True)}),
        ("a failed required check", {"core": ("completed", "failure"), "verdict": None}),
    ],
)
def test_a_wait_over_the_real_checks_ends_at_once_on_a_refusal(source: str, kwargs: dict) -> None:
    time = WaitClock()
    gh = FakeGh(**kwargs)

    assert pr_arm.arm_waiting(lambda: _arm(gh), lambda: None, 30, clock=time.clock, sleep=time.sleep) != 0
    assert time.sleeps == [], source
    assert gh.merged() == []


def test_a_wait_over_the_real_checks_ends_at_once_on_an_open_review_thread() -> None:
    time = WaitClock()
    gh = FakeGh()

    assert pr_arm.arm_waiting(lambda: _arm(gh, threads=True), lambda: None, 30, clock=time.clock, sleep=time.sleep) == 2
    assert time.sleeps == []
    assert gh.merged() == []


@pytest.mark.parametrize(("raw", "expected"), [(None, None), ("", None), ("1", 1), ("120", 120)])
def test_wait_minutes_accepts_whole_minutes_in_range(raw: str | None, expected: int | None) -> None:
    assert pr_arm.wait_minutes(raw) == expected


@pytest.mark.parametrize("raw", ["0", "121", "1.5", "abc", "-1", " 5", "05", "5m", "5; touch x"])
def test_an_invalid_wait_is_refused_before_any_check(
    raw: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: list[int] = []
    monkeypatch.setattr(pr_arm, "arm", lambda *a, **k: called.append(1) or 0)

    assert pr_arm.main([], env={"PR_ARM_WAIT": raw, "PR_ARM_WAIT_SET": "1"}) == 2
    assert called == []
    assert "whole number of minutes from 1 to 120" in capsys.readouterr().out


def test_main_waits_only_when_wait_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[int] = []
    options: list[dict] = []
    monkeypatch.setattr(pr_arm, "arm", lambda pr, head, repo, **kwargs: options.append(kwargs) or 0)
    monkeypatch.setattr(pr_arm, "arm_waiting", lambda attempt, gate, minutes: waits.append(minutes) or attempt())

    assert pr_arm.main([], env={}) == 0
    assert pr_arm.main([], env={"PR_ARM_WAIT": "", "PR_ARM_WAIT_SET": "1"}) == 0
    assert waits == []
    assert options == [{}, {}]
    assert pr_arm.main([], env={"PR_ARM_WAIT": "15", "PR_ARM_WAIT_SET": "1"}) == 0
    assert waits == [15]
    assert options[-1] == {"wait_on_pending_review": True}


def test_the_trunk_check_in_a_wait_uses_the_pr_or_the_current_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(pr_arm.trunk_revert, "arm_gate", lambda ref, run, repo: seen.append((ref, repo)))

    assert pr_arm.trunk_gate_refusal("7", "o/r", run=lambda cmd: (0, "")) is None
    assert pr_arm.trunk_gate_refusal(None, "o/r", run=lambda cmd: (0, "fix/thing\n")) is None
    assert seen == [("7", "o/r"), ("fix/thing", "o/r")]


def test_the_trunk_check_in_a_wait_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(ref: str, run: object, repo: str) -> None:
        raise pr_arm.trunk_revert.TrunkError("gh: HTTP 502")

    monkeypatch.setattr(pr_arm.trunk_revert, "arm_gate", broken)

    assert "could not be read" in str(pr_arm.trunk_gate_refusal("7", "o/r"))
    assert "current branch" in str(pr_arm.trunk_gate_refusal(None, "o/r", run=lambda cmd: (1, "")))


PENDING_VERDICT = (
    f"a fresh review-gate evaluation of {HEAD[:9]} does not pass: review: waiting for the review's verdict on "
    f"{HEAD}, or a stand-in attestation 'Stand-in review: APPROVE {HEAD}' from a listed attester"
)
OPEN_FINDINGS = f"a fresh review-gate evaluation of {HEAD[:9]} does not pass: review: SHIP WITH FIXES on {HEAD}"
OWNER_ACTION = (
    f"a fresh review-gate evaluation of {HEAD[:9]} does not pass: Waiting for review-verdict: the owner must post "
    f"'Stand-in review: APPROVE {HEAD}'"
)
OTHER_HEAD = PENDING_VERDICT.replace(HEAD, "b" * 40)
GATE_ERROR = f"a fresh review-gate evaluation of {HEAD[:9]} failed (exit 1): review: waiting for the review's verdict"


def _arm_with_gate(gate_reasons: list[str], *, wait: bool, threads: bool = False, gh: FakeGh | None = None) -> int:
    return pr_arm.arm(
        "7",
        None,
        run=gh or FakeGh(),
        unpublished=lambda _checkout: [],
        threads=lambda _run, _repo, _number: _threads(threads),
        suites=lambda _run, _repo, _base, _head: [],
        gate=lambda _run, _repo, _number, _head: list(gate_reasons),
        wait_on_pending_review=wait,
    )


def test_a_pending_review_verdict_is_refused_without_a_wait(capsys: pytest.CaptureFixture[str]) -> None:
    assert _arm_with_gate([PENDING_VERDICT], wait=False) == 2
    assert _last_outcome_line(capsys).startswith("pr-arm: outcome: refused: ")


def test_a_pending_review_verdict_counts_as_waiting_inside_a_wait(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh()

    assert _arm_with_gate([PENDING_VERDICT], wait=True, gh=gh) == 2
    assert _last_outcome_line(capsys).startswith("pr-arm: outcome: waiting: ")
    assert gh.merged() == []


@pytest.mark.parametrize("reason", [OPEN_FINDINGS, OWNER_ACTION, OTHER_HEAD, GATE_ERROR])
def test_only_a_pending_verdict_for_this_head_counts_as_waiting(
    reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _arm_with_gate([reason], wait=True) == 2
    assert _last_outcome_line(capsys).startswith("pr-arm: outcome: refused: ")


def test_a_pending_verdict_beside_a_real_refusal_is_still_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert _arm_with_gate([PENDING_VERDICT], wait=True, threads=True) == 2
    assert _last_outcome_line(capsys).startswith("pr-arm: outcome: refused: ")


def test_a_wait_arms_once_the_pending_verdict_passes() -> None:
    time = WaitClock()
    gh = FakeGh()
    gate_reasons = [PENDING_VERDICT]
    time.on_sleep.append(gate_reasons.clear)

    code = pr_arm.arm_waiting(
        lambda: _arm_with_gate(gate_reasons, wait=True, gh=gh), lambda: None, 30, clock=time.clock, sleep=time.sleep
    )

    assert code == 0
    assert time.sleeps == [pr_arm.WAIT_INTERVAL_SECONDS]
    assert len(gh.merged()) == 1


def test_a_wait_ends_when_the_pending_verdict_turns_into_open_findings(capsys: pytest.CaptureFixture[str]) -> None:
    time = WaitClock()
    gh = FakeGh()
    gate_reasons = [PENDING_VERDICT]
    time.on_sleep.append(lambda: gate_reasons.__setitem__(0, OPEN_FINDINGS))

    code = pr_arm.arm_waiting(
        lambda: _arm_with_gate(gate_reasons, wait=True, gh=gh), lambda: None, 30, clock=time.clock, sleep=time.sleep
    )

    assert code == 2
    assert time.sleeps == [pr_arm.WAIT_INTERVAL_SECONDS]
    assert gh.merged() == []
    assert _last_outcome_line(capsys).startswith("pr-arm: outcome: refused: ")
