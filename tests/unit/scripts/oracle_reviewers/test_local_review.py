from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fcntl")

import yaml

from _project.scripts.oracle_reviewers import cli, local, omp_pin, runner
from _project.scripts.oracle_reviewers.classify import Classification
from _project.scripts.oracle_reviewers.commands import gateway_proxy, omp_env
from _project.scripts.oracle_reviewers.policy import Policy, Reviewer

from .conftest import POLICY_PATH, REPO_ROOT
from .test_attempts import CHECKER, _classification, _finding, _v

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REAL_REACHABLE = local.reachable
LOCAL_TOKEN = "localtok-" + "k3Jd9sLp" * 5
CLOUD_TOKEN = "cloudtok-" + "Wq8Zx2Rm" * 5
CLOUD_URL = "https://cloud-gateway.example.ts.net:4000"
TYPESAFE_KEY = "tsk-live-" + "Zy9Xw8Vu" * 4
OVERRIDE_BODY = "Summary\n\nLocal oracle override: the finding is a known false positive\n"
GATE_FILE = ".github/oracle-reviewers.yml"
POLICY_RELATIVE = ".github/oracle-reviewers.yml"
POLICY_TEXT = POLICY_PATH.read_text(encoding="utf-8")
FAKE_OMP = "#!/bin/sh\necho omp/18.12.1\n"
FAKE_OMP_SHA = hashlib.sha256(FAKE_OMP.encode()).hexdigest()
FAKE_OMP_URL = "https://downloads.example.invalid/omp"
MANIFEST_TEXT = (REPO_ROOT / local.SOUNDNESS_MANIFEST).read_text(encoding="utf-8")
INERT_TEXT = (REPO_ROOT / local.INERT_MANIFEST).read_text(encoding="utf-8")
SECRET_NAMES = (
    "OMP_GATEWAY_TOKEN",
    "ORACLE_GATEWAY_URL",
    "ORACLE_CLOUD_GATEWAY_TOKEN",
    "TYPESAFE_API_KEY",
    "BENCHBOX_EPHEMERAL_CLONE",
    "GITHUB_RUN_ID",
    "NO_PROXY",
    "no_proxy",
)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _pinned_policy_text() -> str:
    data = yaml.safe_load(POLICY_TEXT)
    data["omp"]["builds"][omp_pin.current_platform()] = {"url": FAKE_OMP_URL, "sha256": FAKE_OMP_SHA}
    return yaml.safe_dump(data, sort_keys=False)


def _make_repo(path: Path, changed: str = CHECKER) -> Path:
    path.mkdir()
    _git(path, "init", "--quiet", "--initial-branch=develop")
    for relative, text in (
        (POLICY_RELATIVE, _pinned_policy_text()),
        (local.SOUNDNESS_MANIFEST, MANIFEST_TEXT),
        (local.INERT_MANIFEST, INERT_TEXT),
    ):
        seeded = path / relative
        seeded.parent.mkdir(parents=True, exist_ok=True)
        seeded.write_text(text, encoding="utf-8")
        _git(path, "add", relative)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "--quiet", "-m", "seed")
    _git(path, "switch", "--quiet", "-c", "feat/x")
    target = path / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value = 1\n", encoding="utf-8")
    _git(path, "add", changed)
    _git(path, "commit", "--quiet", "-m", "feat: add it")
    return path


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: Policy) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.policy = policy
        self.repo = _make_repo(tmp_path / "repo")
        self.home = tmp_path / "home"
        token_path = self.home / local.LOCAL_TOKEN_PATH
        token_path.parent.mkdir(parents=True)
        token_path.write_text(LOCAL_TOKEN + "\n", encoding="utf-8")
        for name in SECRET_NAMES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv(local.STATE_DIR_ENV, str(tmp_path / "state"))
        monkeypatch.chdir(self.repo)
        monkeypatch.setattr(local, "reachable", lambda url, proxy="": True)
        monkeypatch.setenv(omp_pin.CACHE_ENV, str(tmp_path / "omp-cache"))
        monkeypatch.delenv(omp_pin.BINARY_ENV, raising=False)
        self.downloads: list[str] = []
        self.omp_content = FAKE_OMP
        monkeypatch.setattr(omp_pin, "download", self._download)
        monkeypatch.setattr(local, "keychain_key", lambda: "")
        self.reviews: list[dict[str, Any]] = []
        self.verdict: dict[str, Any] = _v()
        self.diagnostic = ""
        monkeypatch.setattr(runner, "review", self._review)

    def _download(self, url: str, destination: Path) -> None:
        self.downloads.append(url)
        destination.write_text(self.omp_content, encoding="utf-8")

    def _review(self, reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        self.reviews.append(
            {
                "reviewer": reviewer.name,
                "plan": kwargs["plan"],
                "head": kwargs["head_sha"],
                "binary": os.environ.get(omp_pin.BINARY_ENV),
                "token": os.environ.get("OMP_GATEWAY_TOKEN"),
                "url": os.environ.get("ORACLE_GATEWAY_URL"),
                "omp_env": omp_env(),
                "models": (Path(os.environ["HOME"]) / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8"),
            }
        )
        return runner.ReviewOutcome(self.verdict, None, self.diagnostic)

    def run(self, body: str = "", head: str = "") -> int:
        argv = ["local-review", "--policy", POLICY_RELATIVE, "--base-ref", "develop"]
        if head:
            argv += ["--head", head]
        if body:
            path = self.tmp_path / "body.md"
            path.write_text(body, encoding="utf-8")
            argv += ["--body-file", str(path)]
        return cli.main(argv)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: Policy) -> Env:
    return Env(tmp_path, monkeypatch, policy)


def _high() -> dict[str, Any]:
    return _v("a defect", [_finding("High")])


def test_local_plan_has_the_ci_plan_shape_with_shas_and_no_pull_request(env: Env, tmp_path: Path) -> None:
    out = tmp_path / "plan-out"
    plan = local.build_plan(env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), out)
    ci_shape = set(cli._base_plan(env.policy, "o/r", 7, "x"))
    reviewed = {"chain", "buckets", "reviewed_files", "diff_inline", "protocol", "previous_state", "open_findings"}
    assert set(plan) >= ci_shape | reviewed
    assert plan["pr"] is None
    assert plan["head_sha"] == _git(env.repo, "rev-parse", "HEAD")
    assert plan["base_sha"] == _git(env.repo, "rev-parse", "develop")
    assert plan["base_ref"] == "develop"
    assert plan["decision"] == cli.REVIEW
    assert plan["previous_state"] is None and plan["open_findings"] == {}
    assert plan["protocol"]["kind"] == "first" and plan["protocol"]["prior"] == []
    assert [item["path"] for item in plan["reviewed_files"]] == [CHECKER]
    assert [item["name"] for item in plan["chain"]] == [env.policy.buckets[b] for b in cli.SIZE_BUCKETS]
    assert (out / cli.DIFF_FILE).is_file()


def test_plan_cli_local_mode_writes_the_plan_without_a_pull_request(env: Env, tmp_path: Path) -> None:
    out = tmp_path / "plan-cli"
    code = cli.main(["plan", "--local", "--policy", str(POLICY_PATH), "--base-ref", "develop", "--out", str(out)])
    assert code == 0
    plan = json.loads((out / cli.PLAN_FILE).read_text(encoding="utf-8"))
    assert plan["pr"] is None and plan["decision"] == cli.REVIEW


def test_plan_requires_a_pull_request_or_local_mode(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--pr or --local"):
        cli.main(["plan", "--policy", str(POLICY_PATH), "--out", str(tmp_path)])


def test_local_plan_reviews_committed_head_not_uncommitted_changes(env: Env, tmp_path: Path) -> None:
    (env.repo / CHECKER).write_text("value = 2\n", encoding="utf-8")
    (env.repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), tmp_path / "plan-dirty"
    )
    assert plan["diff_inline"] is not None
    assert "value = 1" in plan["diff_inline"] and "value = 2" not in plan["diff_inline"]
    assert "untracked.py" not in plan["diff_inline"]


def test_local_prompt_names_the_change_as_unopened(env: Env, tmp_path: Path) -> None:
    out = tmp_path / "plan-prompt"
    plan = local.build_plan(env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), out)
    classification = Classification("medium", "routine", "standard", 0.0, (), ())
    prompt = cli._assemble_prompt(plan, out, classification, tmp_path)
    assert "pull request unopened in" in prompt


def test_high_findings_exit_nonzero_and_label_the_review_as_local_advisory(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.verdict = _high()
    assert env.run() == local.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "advisory" in captured.out and "may differ from the CI oracle" in captured.out
    assert "posts nothing to GitHub" in captured.out
    assert "[High]" in captured.out
    assert "Refusing to open the PR" in captured.err


def test_critical_findings_also_refuse(env: Env) -> None:
    env.verdict = _v("a defect", [_finding("Critical")])
    assert env.run() == local.EXIT_REFUSED


def test_medium_findings_do_not_block(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.verdict = _v("small things", [_finding("Medium")])
    assert env.run() == local.EXIT_PROCEED
    assert "no Critical or High findings" in capsys.readouterr().out


def test_a_clean_review_proceeds(env: Env) -> None:
    assert env.run() == local.EXIT_PROCEED


def test_override_line_lets_blocking_findings_proceed_and_names_the_reason(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.verdict = _high()
    assert env.run(OVERRIDE_BODY) == local.EXIT_PROCEED
    assert "the finding is a known false positive" in capsys.readouterr().out


@pytest.mark.parametrize("body", ["Local oracle override:\n", "Local oracle override:    \n", "Local oracle override:"])
def test_empty_override_reason_is_refused(env: Env, body: str, capsys: pytest.CaptureFixture[str]) -> None:
    env.verdict = _high()
    assert env.run(body) == local.EXIT_REFUSED
    assert "empty reason" in capsys.readouterr().err


def test_an_override_in_a_commit_body_does_not_apply_without_a_body_file(env: Env) -> None:
    env.verdict = _high()
    _git(env.repo, "commit", "--quiet", "--amend", "-m", "feat: add it", "-m", "Local oracle override: accepted risk")
    assert env.run() == local.EXIT_REFUSED


@pytest.mark.parametrize(
    "body",
    [
        "<!--\nLocal oracle override: hidden\n-->\n",
        "```\nLocal oracle override: fenced\n```\n",
        "Local oracle override: <reason>\n",
        "```\nLocal oracle override: never closed\n",
        "~~~\nLocal oracle override: never closed\n",
        "<!--\nLocal oracle override: never closed\n",
        "    Local oracle override: indented code\n",
        "\tLocal oracle override: tab code\n",
        "  Local oracle override: indented\n",
        "````\nx\n```\nLocal oracle override: inside the longer fence\n````\n",
        "~~~~\nx\n~~~\nLocal oracle override: inside the longer fence\n~~~~\n",
        "```\nx\n~~~\nLocal oracle override: wrong closing character\n```\n",
        "```\nx\n    ```\nLocal oracle override: after an indented closer\n```\n",
        "~~~\nx\n\t~~~\nLocal oracle override: after a tab-indented closer\n~~~\n",
        "<details>\nLocal oracle override: folded\n</details>\n",
        "<details><summary>x</summary>\n\nLocal oracle override: folded\n",
    ],
)
def test_an_invisible_or_placeholder_override_is_refused(env: Env, body: str) -> None:
    env.verdict = _high()
    assert env.run(body) == local.EXIT_REFUSED


def test_unreachable_gateway_refuses_and_an_override_does_not_apply(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(local, "reachable", REAL_REACHABLE)
    monkeypatch.setattr(local, "LOCAL_GATEWAY_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(local, "REACHABILITY_SECONDS", 2)
    env.verdict = _high()
    assert env.run(OVERRIDE_BODY) == local.EXIT_UNAVAILABLE
    captured = capsys.readouterr()
    assert "unreachable" in captured.err
    assert "does not cover an unavailable review" in captured.err
    assert env.reviews == []


def test_a_reviewer_absence_refuses_and_an_override_does_not_apply(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from _project.scripts.oracle_reviewers.absence import QUOTA, Absence

    monkeypatch.setattr(
        runner, "review", lambda reviewer, **kwargs: runner.ReviewOutcome(None, Absence(QUOTA, "out"), "")
    )
    assert env.run(OVERRIDE_BODY) == local.EXIT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "no reviewer produced a verdict" in err and "does not cover an unavailable review" in err


def test_absent_keychain_key_selects_the_fixed_default_and_says_so(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    assert env.run() == local.EXIT_PROCEED
    out = capsys.readouterr().out
    assert "fixed default" in out
    assert "the medium reviewer with the standard template" in out
    assert [review["reviewer"] for review in env.reviews] == ["medium"]


def test_absent_keychain_key_selects_the_gate_template_when_review_machinery_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: Policy, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path, monkeypatch, policy)
    gate_repo = _make_repo(tmp_path / "gate-repo", GATE_FILE)
    monkeypatch.chdir(gate_repo)
    assert env.run() == local.EXIT_PROCEED
    out = capsys.readouterr().out
    assert "the medium reviewer with the gate template" in out


def test_a_present_key_runs_the_classifier_and_hides_the_key(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(local, "keychain_key", lambda: TYPESAFE_KEY)
    seen: list[str] = []

    def classify(plan: dict[str, Any]) -> Classification:
        seen.append(os.environ["TYPESAFE_API_KEY"])
        return _classification("small")

    monkeypatch.setattr(cli, "_classify_plan", classify)
    assert env.run() == local.EXIT_PROCEED
    assert seen == [TYPESAFE_KEY]
    assert "TYPESAFE_API_KEY" not in os.environ
    assert "fixed default" not in capsys.readouterr().out
    assert [review["reviewer"] for review in env.reviews] == ["small"]


def test_local_sessions_use_the_oracle_local_client_on_the_loopback_gateway(env: Env) -> None:
    assert env.run() == local.EXIT_PROCEED
    (review,) = env.reviews
    assert review["token"] == LOCAL_TOKEN
    assert review["url"] == local.LOCAL_GATEWAY_URL
    assert local.LOCAL_GATEWAY_URL in review["models"]
    assert local.resolve_credentials().client == local.LOCAL_CLIENT


def test_cloud_environment_credentials_select_the_oracle_cloud_client(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORACLE_GATEWAY_URL", CLOUD_URL)
    monkeypatch.setenv("ORACLE_CLOUD_GATEWAY_TOKEN", CLOUD_TOKEN)
    access = local.resolve_credentials()
    assert (access.client, access.url, access.token) == (local.CLOUD_CLIENT, CLOUD_URL, CLOUD_TOKEN)
    assert env.run() == local.EXIT_PROCEED
    (review,) = env.reviews
    assert review["token"] == CLOUD_TOKEN and review["url"] == CLOUD_URL
    assert CLOUD_URL in review["models"]


def test_a_cloud_session_without_the_cloud_client_refuses_instead_of_using_the_local_one(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(local.CLOUD_SESSION_ENV, "1")
    assert env.run() == local.EXIT_UNAVAILABLE
    assert "oracle-cloud" in capsys.readouterr().err
    assert env.reviews == []


def test_a_missing_local_client_token_refuses(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    (env.home / local.LOCAL_TOKEN_PATH).unlink()
    assert env.run() == local.EXIT_UNAVAILABLE
    assert "oracle-local" in capsys.readouterr().err


def test_the_review_runs_the_cached_pinned_binary_while_another_omp_is_first_on_path(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    other = tmp_path / "path-bin"
    other.mkdir()
    decoy = other / "omp"
    decoy.write_text("#!/bin/sh\necho omp/18.12.6\n", encoding="utf-8")
    decoy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{other}{os.pathsep}{os.environ['PATH']}")
    assert env.run() == local.EXIT_PROCEED
    expected = tmp_path / "omp-cache" / "18.12.1" / omp_pin.current_platform() / "omp"
    assert [review["binary"] for review in env.reviews] == [str(expected)]
    assert env.downloads == [FAKE_OMP_URL]
    assert os.environ.get(omp_pin.BINARY_ENV) is None


def test_a_cached_verified_binary_is_not_downloaded_again(env: Env) -> None:
    assert env.run() == local.EXIT_PROCEED
    env.verdict = _high()
    env.run(head=_git(env.repo, "rev-parse", "HEAD"))
    assert env.downloads == [FAKE_OMP_URL]


def test_a_platform_without_a_pin_refuses_by_name_and_never_falls_back_to_path_omp(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(omp_pin, "current_platform", lambda: "plan9-mips")
    assert env.run(OVERRIDE_BODY) == local.EXIT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "no omp build is pinned for plan9-mips" in err
    assert env.reviews == [] and env.downloads == []


def test_a_download_that_does_not_match_the_pinned_checksum_refuses_and_leaves_no_binary(
    env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env.omp_content = "#!/bin/sh\necho omp/18.12.1\n# tampered\n"
    assert env.run() == local.EXIT_UNAVAILABLE
    assert "does not match the pinned sha256" in capsys.readouterr().err
    assert env.reviews == []
    assert list((tmp_path / "omp-cache").rglob("*")) == [
        tmp_path / "omp-cache" / "18.12.1",
        tmp_path / "omp-cache" / "18.12.1" / omp_pin.current_platform(),
    ]


def test_a_branch_that_removes_the_pin_section_does_not_change_the_pinned_binary(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    edited = yaml.safe_load(_pinned_policy_text())
    del edited["omp"]
    (env.repo / POLICY_RELATIVE).write_text(yaml.safe_dump(edited), encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "feat: drop the pin")
    assert env.run() == local.EXIT_PROCEED
    assert env.downloads == [FAKE_OMP_URL]


def test_a_second_run_on_an_unchanged_patch_reuses_the_cached_review(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.verdict = _high()
    assert env.run() == local.EXIT_REFUSED
    assert len(env.reviews) == 1
    capsys.readouterr()
    assert env.run() == local.EXIT_REFUSED
    assert len(env.reviews) == 1
    assert "Reusing the cached local review" in capsys.readouterr().out
    assert env.run(OVERRIDE_BODY) == local.EXIT_PROCEED
    assert len(env.reviews) == 1


def test_a_changed_patch_is_reviewed_again(env: Env) -> None:
    assert env.run() == local.EXIT_PROCEED
    (env.repo / CHECKER).write_text("value = 3\n", encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "feat: change it")
    assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 2


def test_a_changed_policy_is_reviewed_again(env: Env) -> None:
    assert env.run() == local.EXIT_PROCEED
    _git(env.repo, "switch", "--quiet", "develop")
    (env.repo / POLICY_RELATIVE).write_text(_pinned_policy_text() + "\n# edited\n", encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "edit policy")
    _git(env.repo, "switch", "--quiet", "feat/x")
    assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 2


def test_a_keyed_run_does_not_reuse_a_keyless_default_review(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    assert env.run() == local.EXIT_PROCEED
    monkeypatch.setattr(local, "keychain_key", lambda: TYPESAFE_KEY)
    monkeypatch.setattr(cli, "_classify_plan", lambda plan: _classification("small"))
    assert env.run() == local.EXIT_PROCEED
    assert [review["reviewer"] for review in env.reviews] == ["medium", "small"]


def test_changed_review_machinery_is_reviewed_again(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    assert env.run() == local.EXIT_PROCEED
    monkeypatch.setattr(local, "machinery_digest", lambda: "changed")
    assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 2


def test_the_review_is_pinned_to_the_head_pr_open_will_push(env: Env) -> None:
    reviewed = _git(env.repo, "rev-parse", "HEAD")
    (env.repo / CHECKER).write_text("value = 3\n", encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "feat: a later commit")
    assert env.run(head=reviewed) == local.EXIT_PROCEED
    assert [review["head"] for review in env.reviews] == [reviewed]


def test_the_policy_comes_from_the_base_ref_not_the_working_tree(env: Env) -> None:
    edited = yaml.safe_load(POLICY_TEXT)
    edited["classifier"]["exempt_paths"] = ["benchbox/*"]
    (env.repo / POLICY_RELATIVE).write_text(yaml.safe_dump(edited), encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "feat: exempt everything")
    assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 1


def test_branch_policy_cannot_widen_evidence_exemptions(env: Env) -> None:
    edited = yaml.safe_load(POLICY_TEXT)
    edited.setdefault("evidence", {})["exempt_paths"] = ["benchbox/*", "*.py"]
    (env.repo / POLICY_RELATIVE).write_text(yaml.safe_dump(edited), encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "feat: widen evidence exemptions on branch")
    assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 1
    plan = env.reviews[0]["plan"]
    assert plan["evidence_exempt_paths"] == list(env.policy.evidence.exempt_paths)
    assert CHECKER in plan["evidence_files"]


def test_branch_manifest_edit_cannot_widen_local_exemptions(env: Env) -> None:
    manifest = env.repo / local.SOUNDNESS_MANIFEST
    kept = [line for line in MANIFEST_TEXT.splitlines() if "equivalence/" not in line and ".gitattributes" not in line]
    manifest.write_text("\n".join(kept) + "\n", encoding="utf-8")
    (env.repo / ".gitattributes").write_text("*.py -diff\n", encoding="utf-8")
    _git(env.repo, "add", ".gitattributes")
    _git(env.repo, "commit", "--quiet", "-am", "feat: drop manifest rules on branch")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "manifest-plan"
    )
    assert {CHECKER, ".gitattributes"} <= set(plan["evidence_protected_files"])
    assert {CHECKER, ".gitattributes"} <= set(plan["evidence_files"])


def test_protected_evidence_covers_renames_logic_data_and_machine_read_paths(env: Env) -> None:
    (env.repo / "docs").mkdir(exist_ok=True)
    _git(env.repo, "mv", GATE_FILE, "docs/guide.md")
    (env.repo / "benchbox" / "core").mkdir(parents=True, exist_ok=True)
    (env.repo / "benchbox/core/row_validation.sql").write_bytes(b"select 1\x00")
    (env.repo / "docs/agent").mkdir()
    (env.repo / "docs/agent/overview.md").write_text("sizes\n", encoding="utf-8")
    (env.repo / "docs/plain.md").write_text("plain\n", encoding="utf-8")
    _git(env.repo, "add", "-A")
    _git(env.repo, "commit", "--quiet", "-m", "feat: rename and add")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "protected-plan"
    )
    protected = set(plan["evidence_protected_files"])
    assert {"docs/guide.md", "benchbox/core/row_validation.sql", "docs/agent/overview.md"} <= protected
    assert "docs/plain.md" not in protected


def test_branch_inert_manifest_edit_cannot_unprotect_machine_read_docs(env: Env) -> None:
    kept = [line for line in INERT_TEXT.splitlines() if not line.startswith("except\t")]
    (env.repo / local.INERT_MANIFEST).write_text("\n".join(kept) + "\n", encoding="utf-8")
    (env.repo / "docs" / "agent").mkdir(parents=True)
    (env.repo / "docs/agent/overview.md").write_text("sizes\n", encoding="utf-8")
    _git(env.repo, "add", "-A")
    _git(env.repo, "commit", "--quiet", "-m", "feat: drop except rules on branch")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "inert-plan"
    )
    assert "docs/agent/overview.md" in plan["evidence_protected_files"]


def test_a_deleted_binary_is_never_required_evidence(env: Env) -> None:
    _git(env.repo, "switch", "--quiet", "develop")
    (env.repo / "payload").write_bytes(b"\x00binary\n")
    _git(env.repo, "add", "payload")
    _git(env.repo, "commit", "--quiet", "-m", "chore: add binary data")
    _git(env.repo, "switch", "--quiet", "feat/x")
    _git(env.repo, "rebase", "--quiet", "develop")
    _git(env.repo, "rm", "--quiet", "payload")
    _git(env.repo, "commit", "--quiet", "-m", "feat: drop binary data")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "deleted-plan"
    )
    removed = {item["path"] for item in plan["reviewed_files"] if item["status"] == "removed"}
    assert removed == {"payload"}
    assert "payload" not in plan["evidence_files"]
    assert CHECKER in plan["evidence_files"]


def test_a_not_diffable_source_file_forces_the_file_list_brief(env: Env) -> None:
    (env.repo / ".gitattributes").write_text("*.py -diff\n", encoding="utf-8")
    _git(env.repo, "add", ".gitattributes")
    _git(env.repo, "commit", "--quiet", "-m", "feat: hide python diffs")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "opaque-plan"
    )
    assert CHECKER in plan["binary_files"]
    assert plan["brief_mode"] == "file-list"
    assert plan["diff_inline"] is None


def test_a_font_only_binary_keeps_the_inline_brief(env: Env) -> None:
    (env.repo / "assets").mkdir()
    (env.repo / "assets" / "face.woff2").write_bytes(b"wOF2\x00")
    _git(env.repo, "add", "assets")
    _git(env.repo, "commit", "--quiet", "-m", "feat: add font")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "font-plan"
    )
    assert "assets/face.woff2" in plan["binary_files"]
    assert plan["brief_mode"] == "inline"


def test_a_font_named_binary_under_a_protected_path_forces_the_file_list_brief(env: Env) -> None:
    target = env.repo / "benchbox" / "core" / "equivalence"
    target.mkdir(parents=True, exist_ok=True)
    (target / "face.woff2").write_bytes(b"wOF2\x00")
    _git(env.repo, "add", "benchbox")
    _git(env.repo, "commit", "--quiet", "-m", "feat: add protected font")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "protected-font-plan"
    )
    assert "benchbox/core/equivalence/face.woff2" in plan["binary_files"]
    assert plan["brief_mode"] == "file-list"


def test_exposure_exemptions_do_not_remove_files_from_review(env: Env) -> None:
    (env.repo / "README.md").write_text("changed prose\n", encoding="utf-8")
    (env.repo / "payload").write_bytes(b"\x00binary\n")
    _git(env.repo, "add", "README.md", "payload")
    _git(env.repo, "commit", "--quiet", "-m", "feat: update prose and binary data")
    plan = local.build_plan(
        env.policy, env.repo, "develop", _git(env.repo, "rev-parse", "HEAD"), env.tmp_path / "exempt-plan"
    )
    assert set(plan["binary_files"]) == {"payload"}
    assert {item["path"] for item in plan["reviewed_files"]} >= {"README.md", "payload", CHECKER}
    diff = (env.tmp_path / "exempt-plan" / cli.DIFF_FILE).read_text(encoding="utf-8")
    assert "+changed prose" in diff
    assert "Binary files /dev/null and b/payload differ" in diff
    assert "payload" in plan["scope_paths"]


def test_the_omp_pin_comes_from_the_base_ref_not_the_branch(env: Env) -> None:
    edited = yaml.safe_load(_pinned_policy_text())
    edited["omp"]["builds"][omp_pin.current_platform()] = {
        "url": "https://branch.example.invalid/omp",
        "sha256": "0" * 64,
    }
    (env.repo / POLICY_RELATIVE).write_text(yaml.safe_dump(edited), encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "feat: move the pin")
    assert env.run() == local.EXIT_PROCEED
    assert env.downloads == [FAKE_OMP_URL]


def test_an_unavailable_review_is_never_cached(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from _project.scripts.oracle_reviewers.absence import QUOTA, Absence

    monkeypatch.setattr(
        runner, "review", lambda reviewer, **kwargs: runner.ReviewOutcome(None, Absence(QUOTA, "out"), "")
    )
    assert env.run() == local.EXIT_UNAVAILABLE
    monkeypatch.setattr(runner, "review", env._review)
    assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 1


def test_planted_secret_values_never_appear_in_the_output_or_the_cache(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", TYPESAFE_KEY)
    monkeypatch.setattr(cli, "_classify_plan", lambda plan: _classification("small"))
    leak = f"see {LOCAL_TOKEN} and {TYPESAFE_KEY} at {local.LOCAL_GATEWAY_URL}"
    finding = {**_finding("High"), "detail": leak, "title": f"leaks {LOCAL_TOKEN}"}
    env.verdict = _v(leak, [finding])
    env.diagnostic = leak
    assert env.run() == local.EXIT_REFUSED
    captured = capsys.readouterr()
    for value in (LOCAL_TOKEN, TYPESAFE_KEY):
        assert value not in captured.out and value not in captured.err
    assert "[redacted]" in captured.out
    cached = "".join(path.read_text(encoding="utf-8") for path in (env.tmp_path / "state").rglob("*.json"))
    assert cached and LOCAL_TOKEN not in cached and TYPESAFE_KEY not in cached


def test_planted_cloud_values_never_appear_in_the_output(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ORACLE_GATEWAY_URL", CLOUD_URL)
    monkeypatch.setenv("ORACLE_CLOUD_GATEWAY_TOKEN", CLOUD_TOKEN)
    env.verdict = _v(f"{CLOUD_TOKEN} {CLOUD_URL}", [{**_finding("High"), "detail": f"{CLOUD_TOKEN} {CLOUD_URL}"}])
    assert env.run() == local.EXIT_REFUSED
    captured = capsys.readouterr()
    assert CLOUD_TOKEN not in captured.out + captured.err
    assert CLOUD_URL not in captured.out + captured.err


def test_a_second_review_of_the_same_patch_waits_for_its_lock_then_refuses(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import fcntl

    monkeypatch.setattr(local, "LOCK_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(local, "LOCK_POLL_SECONDS", 0.05)
    seen: list[Path] = []
    real = local._cache_path

    def recording(*args: Any, **kwargs: Any) -> Path:
        path = real(*args, **kwargs)
        seen.append(path)
        return path

    monkeypatch.setattr(local, "_cache_path", recording)
    assert env.run() == local.EXIT_PROCEED
    cache = seen[0]
    cache.unlink()
    with open(cache.with_suffix(".lock"), "a+", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert env.run() == local.EXIT_UNAVAILABLE
    assert "another local oracle review of this patch" in capsys.readouterr().err
    assert len(env.reviews) == 1
    assert env.run() == local.EXIT_PROCEED


def test_a_lock_held_for_another_patch_does_not_delay_this_review(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    monkeypatch.setattr(local, "LOCK_WAIT_SECONDS", 0.3)
    directory = local.state_dir() / "cache"
    directory.mkdir(parents=True)
    with open(directory / ("0" * 64 + ".lock"), "a+", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 1


def _spawn(repo: Path, tmp_path: Path, name: str, seconds: float) -> tuple[subprocess.Popen[str], Path]:
    log = tmp_path / f"{name}.log"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        local.STATE_DIR_ENV: str(tmp_path / "state"),
        "PYTHONPATH": str(REPO_ROOT),
    }
    for secret in SECRET_NAMES:
        env.pop(secret, None)
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.unit.scripts.oracle_reviewers.local_review_process", str(log), str(seconds)],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log


def _wait_for(path: Path, text: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if path.exists() and text in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.05)
    raise AssertionError(f"{text!r} never appeared in {path}")


def _seed_home(tmp_path: Path) -> None:
    token = tmp_path / "home" / local.LOCAL_TOKEN_PATH
    token.parent.mkdir(parents=True)
    token.write_text(LOCAL_TOKEN + "\n", encoding="utf-8")


def test_reviews_of_different_patches_run_at_the_same_time_in_separate_processes(tmp_path: Path) -> None:
    _seed_home(tmp_path)
    first_repo = _make_repo(tmp_path / "first")
    second_repo = _make_repo(tmp_path / "second", "benchbox/core/equivalence/other.py")
    slow, slow_log = _spawn(first_repo, tmp_path, "slow", 8)
    try:
        _wait_for(slow_log, "start")
        fast, _ = _spawn(second_repo, tmp_path, "fast", 0)
        fast_output, _ = fast.communicate(timeout=60)
        assert fast.returncode == local.EXIT_PROCEED, fast_output
        assert slow.poll() is None
        assert "Waiting for another" not in fast_output
    finally:
        slow.communicate(timeout=60)
    assert slow.returncode == local.EXIT_PROCEED


def test_a_second_process_reviewing_the_same_patch_waits_and_reuses_the_result(tmp_path: Path) -> None:
    _seed_home(tmp_path)
    repo = _make_repo(tmp_path / "repo")
    first, first_log = _spawn(repo, tmp_path, "first", 4)
    try:
        _wait_for(first_log, "start")
        second, second_log = _spawn(repo, tmp_path, "second", 4)
        second_output, _ = second.communicate(timeout=120)
    finally:
        first.communicate(timeout=120)
    assert first.returncode == local.EXIT_PROCEED
    assert second.returncode == local.EXIT_PROCEED, second_output
    assert "Waiting for another local oracle review of this patch" in second_output
    assert "Reusing the cached local review" in second_output
    assert not second_log.exists()
    assert first_log.read_text(encoding="utf-8").count("start") == 1


def test_a_change_with_only_exempt_paths_needs_no_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: Policy, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path, monkeypatch, policy)
    inert_repo = _make_repo(tmp_path / "inert-repo", "NOTES.md")
    monkeypatch.chdir(inert_repo)
    assert env.run() == local.EXIT_PROCEED
    assert "no review needed" in capsys.readouterr().out
    assert env.reviews == []


def test_the_reviewer_home_accepts_http_loopback_only_when_the_local_review_asks(tmp_path: Path) -> None:
    cli.write_reviewer_home(tmp_path / "ok", "http://127.0.0.1:4000", True)
    assert "http://127.0.0.1:4000" in (tmp_path / "ok" / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="gateway URL"):
        cli.write_reviewer_home(tmp_path / "ci", "http://127.0.0.1:4000")
    with pytest.raises(SystemExit, match="gateway URL"):
        cli.main(["reviewer-home", "--home", str(tmp_path / "cli"), "--gateway-url", "http://127.0.0.1:4000"])
    for url in ("http://gateway.example.ts.net:4000", "http://127.0.0.1.evil.example:4000"):
        with pytest.raises(SystemExit, match="gateway URL"):
            cli.write_reviewer_home(tmp_path / "bad", url, True)


def test_the_attempt_loop_without_the_local_flag_refuses_a_plaintext_gateway(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(local, "LOCAL_GATEWAY_URL", "http://127.0.0.1:4000")
    original = cli.command_attempts
    monkeypatch.setattr(
        cli,
        "command_attempts",
        lambda args: original(__import__("argparse").Namespace(**{**vars(args), "allow_loopback_http": False})),
    )
    assert env.run() == local.EXIT_UNAVAILABLE
    assert env.reviews == []


def test_soundness_scope_comes_from_the_base_ref_manifest(env: Env) -> None:
    (env.repo / local.SOUNDNESS_MANIFEST).write_text("file\tunrelated.txt\n", encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "feat: shrink my own scope")
    assert local.trusted_predicate(env.repo, "develop")(CHECKER)
    assert not local.soundness_predicate("file\tunrelated.txt\n")(CHECKER)


def test_a_rebase_onto_a_new_base_invalidates_the_cache_for_an_identical_patch(env: Env) -> None:
    assert env.run() == local.EXIT_PROCEED
    _git(env.repo, "switch", "--quiet", "develop")
    (env.repo / "README.md").write_text("moved\n", encoding="utf-8")
    _git(env.repo, "commit", "--quiet", "-am", "develop moves")
    _git(env.repo, "switch", "--quiet", "feat/x")
    _git(env.repo, "rebase", "--quiet", "develop")
    assert env.run() == local.EXIT_PROCEED
    assert len(env.reviews) == 2


def test_local_patch_digest_hashes_exact_diff_bytes(tmp_path: Path, env: Env) -> None:
    digests = []
    for index, byte in enumerate((b"\xe9", b"\xf1")):
        repo = _make_repo(tmp_path / f"latin-{index}")
        (repo / CHECKER).write_bytes(b"value = '" + byte + b"'\n")
        _git(repo, "add", CHECKER)
        _git(repo, "commit", "--quiet", "-m", "latin-1")
        plan = local.build_plan(env.policy, repo, "develop", _git(repo, "rev-parse", "HEAD"), tmp_path / f"out-{index}")
        digests.append(plan["protocol"]["patch_digest"])
    assert digests[0] != digests[1]


class _ProxyServer:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append((self.path, self.headers.get("Host", "")))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _ProxyServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def _write_status(repo: Path, proxy: str) -> None:
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    (git_dir / local.CLOUD_STATUS_FILE).write_text(
        f"gateway=reachable\ngateway_detail=ok\ngateway_proxy={proxy}\nomp=installed\n", encoding="utf-8"
    )


def test_reachable_goes_through_the_gateway_proxy_and_fails_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "REACHABILITY_SECONDS", 3)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    unreachable = "http://127.0.0.1:1"
    with _ProxyServer() as proxy:
        assert REAL_REACHABLE(unreachable, proxy.url) is True
        assert proxy.requests == [(f"{unreachable}/healthz", "127.0.0.1:1")]
    assert REAL_REACHABLE(unreachable) is False


def test_cloud_credentials_read_the_proxy_from_the_gateway_status_file(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORACLE_GATEWAY_URL", CLOUD_URL)
    monkeypatch.setenv("ORACLE_CLOUD_GATEWAY_TOKEN", CLOUD_TOKEN)
    assert local.resolve_credentials().proxy == ""
    _write_status(env.repo, "http://127.0.0.1:1055")
    assert local.resolve_credentials().proxy == "http://127.0.0.1:1055"
    _write_status(env.repo, "http://evil.example:1055")
    assert local.resolve_credentials().proxy == ""


def test_local_credentials_ignore_the_gateway_status_file(env: Env) -> None:
    _write_status(env.repo, "http://127.0.0.1:1055")
    assert local.resolve_credentials().proxy == ""


def test_cloud_review_reaches_the_gateway_through_the_proxy_without_stubbing_reachable(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(local, "reachable", REAL_REACHABLE)
    monkeypatch.setenv("ORACLE_GATEWAY_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ORACLE_CLOUD_GATEWAY_TOKEN", CLOUD_TOKEN)
    with _ProxyServer() as proxy:
        _write_status(env.repo, proxy.url)
        assert env.run() == local.EXIT_PROCEED
        assert [path for path, _ in proxy.requests] == ["http://127.0.0.1:1/healthz"]
    (review,) = env.reviews
    assert review["omp_env"]["HTTPS_PROXY"] == proxy.url
    assert review["omp_env"]["HTTP_PROXY"] == proxy.url
    assert "127.0.0.1" in review["omp_env"]["NO_PROXY"]


def test_cloud_review_without_the_proxy_says_how_to_start_it(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(local, "reachable", REAL_REACHABLE)
    monkeypatch.setattr(local, "REACHABILITY_SECONDS", 2)
    monkeypatch.setenv("ORACLE_GATEWAY_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ORACLE_CLOUD_GATEWAY_TOKEN", CLOUD_TOKEN)
    assert env.run() == local.EXIT_UNAVAILABLE
    assert "make cloud-gateway" in capsys.readouterr().err


def test_local_review_passes_no_proxy_variables_even_when_the_session_has_them(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "x")
    assert env.run() == local.EXIT_PROCEED
    (review,) = env.reviews
    assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} & set(review["omp_env"])


def test_a_session_cannot_inject_a_reviewer_proxy_through_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORACLE_GATEWAY_PROXY", "http://evil.example:8080")
    assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} & set(omp_env())
    with gateway_proxy("http://evil.example:8080"):
        assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} & set(omp_env())
    with gateway_proxy("http://127.0.0.1:1055"):
        assert omp_env()["HTTPS_PROXY"] == "http://127.0.0.1:1055"
    assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} & set(omp_env())
