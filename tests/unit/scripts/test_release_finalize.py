from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import release_finalize

pytestmark = [pytest.mark.unit, pytest.mark.medium]

HEAD = "a" * 40
MERGE = "b" * 40


def pr(state: str = "OPEN", head: str = HEAD) -> dict[str, object]:
    return {
        "number": 123,
        "state": state,
        "baseRefName": "release",
        "headRefName": "v9.9.9",
        "headRefOid": head,
        "mergedAt": "2026-09-23T20:00:00Z" if state == "MERGED" else None,
        "mergeCommit": {"oid": MERGE} if state == "MERGED" else None,
    }


def test_open_pr_merge_uses_checked_exact_head(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(release_finalize, "check_required_contexts", lambda *_: None)
    monkeypatch.setattr(release_finalize, "view_pr", lambda _: pr())
    monkeypatch.setattr(release_finalize, "command", lambda *args, **_: calls.append(args) or "")

    release_finalize.merge_pr(pr(), "9.9.9", ("validate-base", "release-required-result"))
    assert calls == [
        (
            "gh",
            "api",
            "--method",
            "PUT",
            "repos/{owner}/{repo}/pulls/123/merge",
            "--raw-field",
            f"sha={HEAD}",
            "--raw-field",
            "merge_method=squash",
        )
    ]


def test_open_pr_head_change_prevents_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_finalize, "check_required_contexts", lambda *_: None)
    monkeypatch.setattr(release_finalize, "view_pr", lambda _: pr(head="c" * 40))
    monkeypatch.setattr(release_finalize, "command", lambda *_args, **_kwargs: pytest.fail("merge attempted"))
    with pytest.raises(release_finalize.FinalizeError, match="head changed"):
        release_finalize.merge_pr(pr(), "9.9.9", ("validate-base", "release-required-result"))


@pytest.mark.parametrize("bucket", ["pending", "fail", "cancel", "skipping", None])
def test_required_contexts_reject_non_green(monkeypatch: pytest.MonkeyPatch, bucket: str | None) -> None:
    rows = [{"name": "validate-base", "bucket": bucket}] if bucket else []
    rows.append({"name": "release-required-result", "bucket": "pass"})
    monkeypatch.setattr(release_finalize, "json_command", lambda *_args, **_kwargs: rows)
    with pytest.raises(release_finalize.FinalizeError):
        release_finalize.check_required_contexts(123, ("validate-base", "release-required-result"))


def test_pending_check_cli_exit_is_a_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release_finalize.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 8, "[]", ""),
    )
    with pytest.raises(release_finalize.FinalizeError, match="pending"):
        release_finalize.check_required_contexts(123, ("validate-base",))


def test_merged_pr_requires_timestamp_and_commit() -> None:
    assert release_finalize.merged_commit(pr("MERGED"), "9.9.9") == MERGE
    for changed in (pr(), {**pr("MERGED"), "mergedAt": None}, {**pr("MERGED"), "mergeCommit": None}):
        with pytest.raises(release_finalize.FinalizeError):
            release_finalize.merged_commit(changed, "9.9.9")


def test_resume_after_merge_skips_checks_and_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        release_finalize, "command", lambda *args, **_: "/tmp/linked" if "--absolute-git-dir" in args else "/tmp/common"
    )
    monkeypatch.setattr(release_finalize.os.path, "samefile", lambda *_: False)
    monkeypatch.setattr(release_finalize, "release_pr", lambda _: pr("MERGED"))
    monkeypatch.setattr(release_finalize, "merge_pr", lambda *_: pytest.fail("merge attempted on resume"))
    monkeypatch.setattr(release_finalize, "tag_merge_commit", lambda *_: "already pushed")
    assert (
        release_finalize.main(["--version", "9.9.9", "--required-contexts", "validate-base release-required-result"])
        == 0
    )
    assert "already pushed" in capsys.readouterr().out


def test_closed_unmerged_pr_cannot_finalize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_finalize, "json_command", lambda *_args, **_kwargs: [pr("CLOSED")])
    with pytest.raises(release_finalize.FinalizeError, match="Expected one open or merged"):
        release_finalize.release_pr("9.9.9")


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


@pytest.fixture
def release_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(primary)], check=True, capture_output=True)
    git(primary, "config", "user.name", "Release Test")
    git(primary, "config", "user.email", "release-test@example.com")
    git(primary, "switch", "-c", "release")
    (primary / "source.txt").write_text("merge commit\n", encoding="utf-8")
    git(primary, "add", "source.txt")
    git(primary, "commit", "-m", "Release PR merge")
    merge = git(primary, "rev-parse", "HEAD")
    git(primary, "push", "-u", "origin", "release")
    git(primary, "worktree", "add", "-b", "fix/finalize", str(linked), "origin/release")
    monkeypatch.chdir(linked)
    return primary, linked, merge


def test_tag_creation_push_resume_and_release_advance(release_repo: tuple[Path, Path, str]) -> None:
    primary, linked, merge = release_repo
    git(linked, "tag", "v9.9.8", merge)
    assert release_finalize.tag_merge_commit("9.9.8", merge) == "pushed"
    assert release_finalize.tag_merge_commit("9.9.9", merge) == "pushed"
    assert release_finalize.tag_merge_commit("9.9.9", merge) == "already pushed"

    (primary / "source.txt").write_text("later release\n", encoding="utf-8")
    git(primary, "add", "source.txt")
    git(primary, "commit", "-m", "Later release")
    git(primary, "push", "origin", "release")
    assert release_finalize.tag_merge_commit("9.9.9", merge) == "already pushed"
    assert git(linked, "rev-parse", "refs/tags/v9.9.9^{commit}") == merge
    assert git(linked, "branch", "--show-current") == "fix/finalize"


def test_tag_conflict_and_unreachable_merge_fail_closed(release_repo: tuple[Path, Path, str]) -> None:
    primary, linked, merge = release_repo
    (primary / "source.txt").write_text("newer\n", encoding="utf-8")
    git(primary, "add", "source.txt")
    git(primary, "commit", "-m", "Newer")
    newer = git(primary, "rev-parse", "HEAD")
    git(primary, "push", "origin", "release")
    git(linked, "tag", "v9.9.9", merge)
    with pytest.raises(release_finalize.FinalizeError, match="Local tag"):
        release_finalize.tag_merge_commit("9.9.9", newer)
    (primary / "source.txt").write_text("unpublished\n", encoding="utf-8")
    git(primary, "add", "source.txt")
    git(primary, "commit", "-m", "Unpublished")
    unpublished = git(primary, "rev-parse", "HEAD")
    with pytest.raises(release_finalize.FinalizeError, match="not reachable"):
        release_finalize.tag_merge_commit("9.9.7", unpublished)
    assert git(linked, "rev-parse", "refs/tags/v9.9.9^{commit}") == merge


def test_remote_tag_conflict_fails_closed(release_repo: tuple[Path, Path, str]) -> None:
    primary, linked, merge = release_repo
    (primary / "source.txt").write_text("newer\n", encoding="utf-8")
    git(primary, "add", "source.txt")
    git(primary, "commit", "-m", "Newer")
    newer = git(primary, "rev-parse", "HEAD")
    git(primary, "push", "origin", "release")
    git(primary, "tag", "v9.9.9", merge)
    git(primary, "push", "origin", "refs/tags/v9.9.9")
    with pytest.raises(release_finalize.FinalizeError, match="Local tag"):
        release_finalize.tag_merge_commit("9.9.9", newer)
    assert git(linked, "rev-parse", "refs/tags/v9.9.9^{commit}") == merge
