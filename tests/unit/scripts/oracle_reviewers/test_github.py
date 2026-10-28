from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import edits, github
from _project.scripts.oracle_reviewers.retry import State

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/BenchBox"
SHA = "d" * 40
PR = 7


def _run(**over: Any) -> dict[str, Any]:
    run = {
        "path": ".github/workflows/oracle-verdict.yml",
        "repository": {"full_name": REPO},
        "event": "issue_comment",
        "head_sha": SHA,
        "created_at": "2026-10-07T23:05:42Z",
    }
    run.update(over)
    return run


def _always(repo: str, sha: str) -> bool:
    return True


def _never(repo: str, sha: str) -> bool:
    return False


def _pull(base_ref: str, number: int = PR) -> dict[str, Any]:
    return {
        "id": 2000 + number,
        "number": number,
        "url": f"https://api.github.com/repos/{REPO}/pulls/{number}",
        "head": {"ref": "feature", "sha": SHA, "repo": {"id": 1, "name": "BenchBox"}},
        "base": {"ref": base_ref, "sha": "b" * 40, "repo": {"id": 1, "name": "BenchBox"}},
    }


def _target_run(base_ref: str = "develop", **over: Any) -> dict[str, Any]:
    return _run(event="pull_request_target", pull_requests=[_pull(base_ref)], **over)


def test_trusted_run_accepts_this_workflow_from_develop() -> None:
    assert github.trusted_run(_run(), REPO, PR, _always)
    assert github.trusted_run(_target_run(), REPO, PR, _never, [_head_pull()])


def test_trusted_run_accepts_a_pull_request_target_run_whose_head_is_the_pr_head() -> None:
    assert github.trusted_run(_target_run(), REPO, PR, lambda repo, sha: False, [_head_pull()])


@pytest.mark.parametrize("event", ["issue_comment", "schedule", "workflow_dispatch"])
def test_trusted_run_checks_develop_ancestry_for_other_events(event: str) -> None:
    assert github.trusted_run(_run(event=event), REPO, PR, _always)
    assert not github.trusted_run(_run(event=event), REPO, PR, _never)


@pytest.mark.parametrize("pull_requests", [None, [], [_pull("release/1.0")], [_pull("main"), _pull("feature")]])
def test_trusted_run_rejects_a_pull_request_target_run_not_based_on_develop(pull_requests: Any) -> None:
    run = _run(event="pull_request_target", pull_requests=pull_requests)
    assert not github.trusted_run(run, REPO, PR, _always)


def test_trusted_run_accepts_a_pull_request_target_run_for_several_develop_pulls() -> None:
    run = _run(event="pull_request_target", pull_requests=[_pull("develop", 3), _pull("develop")])
    assert github.trusted_run(run, REPO, PR, _never, [_head_pull(3), _head_pull()])


@pytest.mark.parametrize(
    "pull_requests",
    [[_pull("main"), _pull("develop")], [_pull("develop"), _pull("release/1.0")]],
    ids=["main-first", "release-last"],
)
def test_trusted_run_rejects_a_pull_request_target_run_that_also_targets_another_base(pull_requests: Any) -> None:
    run = _run(event="pull_request_target", pull_requests=pull_requests)
    assert not github.trusted_run(run, REPO, PR, _always)


def test_trusted_run_rejects_a_pull_request_target_run_for_another_pull_request() -> None:
    assert not github.trusted_run(_target_run(), REPO, PR + 1, _always)
    run = _run(event="pull_request_target", pull_requests=[_pull("develop", 3)])
    assert not github.trusted_run(run, REPO, PR, _always)


def test_trusted_run_rejects_a_pull_request_target_run_from_another_workflow() -> None:
    assert not github.trusted_run(_target_run(path=".github/workflows/other.yml"), REPO, PR, _always)
    assert not github.trusted_run(_target_run(head_sha="not-a-sha"), REPO, PR, _always)
    assert not github.trusted_run(_target_run(repository={"full_name": "someone/BenchBox"}), REPO, PR, _always)


@pytest.mark.parametrize(
    "run",
    [
        _run(path=".github/workflows/other.yml"),
        _run(event="pull_request"),
        _run(event="push"),
        _run(repository={"full_name": "someone/BenchBox"}),
        _run(head_sha="not-a-sha"),
    ],
)
def test_trusted_run_rejects_other_sources(run: dict[str, Any]) -> None:
    assert not github.trusted_run(run, REPO, PR, _always)


def test_trusted_run_rejects_workflow_code_that_is_not_on_develop() -> None:
    assert not github.trusted_run(_run(), REPO, PR, _never)


@pytest.mark.parametrize(
    ("status", "expected"),
    [("ahead", True), ("identical", True), ("behind", False), ("diverged", False)],
)
def test_on_develop_reads_the_compare_status(monkeypatch: pytest.MonkeyPatch, status: str, expected: bool) -> None:
    paths: list[str] = []

    def get_json(path: str) -> Any:
        paths.append(path)
        return {"status": status}

    monkeypatch.setattr(github, "get_json", get_json)
    assert github.on_develop(REPO, SHA) is expected
    assert paths == [f"repos/{REPO}/compare/{SHA}...develop"]


def test_latest_state_skips_untrusted_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    state = State(7, "a" * 40, "pending", datetime(2026, 10, 5, tzinfo=UTC))
    listing = {
        "artifacts": [
            {"created_at": "2026-10-05T12:00:00Z", "workflow_run": {"id": 2}, "expired": False},
            {"created_at": "2026-10-05T11:00:00Z", "workflow_run": {"id": 1}, "expired": False},
            {"created_at": "2026-10-05T13:00:00Z", "workflow_run": {"id": 3}, "expired": True},
        ]
    }
    runs = {"2": _run(event="pull_request"), "1": _run(event="schedule")}
    downloaded: list[str] = []

    def get_json(path: str) -> Any:
        if path.startswith(f"repos/{REPO}/actions/artifacts"):
            return listing
        if path.startswith(f"repos/{REPO}/actions/runs/"):
            return runs[path.rsplit("/", 1)[1]]
        if "/compare/" in path:
            return {"status": "ahead"}
        raise AssertionError(f"unexpected request {path}")

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        downloaded.append(argv[3])
        directory = Path(argv[argv.index("-D") + 1])
        (directory / github.STATE_FILE).write_text(json.dumps(state.to_json()), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(github, "get_json", get_json)
    monkeypatch.setattr(github, "get_paginated", lambda path: [])
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    assert github.latest_state(REPO, 7) == state
    assert downloaded == ["1"]


def test_latest_state_skips_a_pull_request_target_run_not_based_on_develop(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = {"artifacts": [{"created_at": "2026-10-05T12:00:00Z", "workflow_run": {"id": 5}, "expired": False}]}

    def get_json(path: str) -> Any:
        if path.startswith(f"repos/{REPO}/actions/artifacts"):
            return listing
        return _target_run("release/1.0")

    def no_download(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("an untrusted run must not be downloaded")

    monkeypatch.setattr(github, "get_json", get_json)
    monkeypatch.setattr(github, "get_paginated", lambda path: [])
    monkeypatch.setattr(github.subprocess, "run", no_download)
    assert github.latest_state(REPO, 7) is None


BEFORE_RUN = datetime(2026, 10, 7, 23, 5, 38, tzinfo=UTC)
AFTER_RUN = datetime(2026, 10, 7, 23, 6, 0, tzinfo=UTC)
BEFORE_PULL = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)


def _head_pull(
    number: int = PR, created_at: datetime = BEFORE_RUN, base_ref: str = "develop", base_changed_at: Any = None
) -> github.HeadPull:
    return github.HeadPull(number, created_at, base_ref, base_changed_at)


def test_trusted_run_needs_the_head_pulls_for_a_pull_request_target_run() -> None:
    assert github.trusted_run(_target_run(), REPO, PR, _never, [_head_pull()])
    assert not github.trusted_run(_target_run(), REPO, PR, _always, None)
    assert not github.trusted_run(_target_run(), REPO, PR, _always, [])


def test_trusted_run_rejects_a_sibling_pull_request_into_another_base() -> None:
    sibling = _head_pull(9, BEFORE_RUN, "release/1.0")
    assert not github.trusted_run(_target_run(), REPO, PR, _always, [_head_pull(), sibling])


def test_trusted_run_rejects_a_sibling_that_was_closed_after_the_run() -> None:
    sibling = _head_pull(9, BEFORE_RUN, "evil")
    run = _run(event="pull_request_target", pull_requests=[_pull("develop")])
    assert not github.trusted_run(run, REPO, PR, _always, [_head_pull(), sibling])


def test_trusted_run_ignores_a_pull_request_opened_after_the_run() -> None:
    later = _head_pull(9, AFTER_RUN, "main")
    assert github.trusted_run(_target_run(), REPO, PR, _always, [_head_pull(), later])


def test_trusted_run_needs_this_pull_request_to_predate_the_run() -> None:
    assert not github.trusted_run(_target_run(), REPO, PR, _always, [_head_pull(created_at=AFTER_RUN)])
    assert not github.trusted_run(_target_run(), REPO, PR, _always, [_head_pull(number=9)])


def test_trusted_run_rejects_a_base_change_after_the_run_started() -> None:
    assert not github.trusted_run(_target_run(), REPO, PR, _always, [_head_pull(base_changed_at=AFTER_RUN)])
    assert github.trusted_run(_target_run(), REPO, PR, _always, [_head_pull(base_changed_at=BEFORE_PULL)])


@pytest.mark.parametrize("created_at", [None, "", "not a time"])
def test_trusted_run_rejects_an_unreadable_start_time(created_at: Any) -> None:
    assert not github.trusted_run(_target_run(created_at=created_at), REPO, PR, _always, [_head_pull()])


def test_a_retarget_does_not_affect_events_that_run_develop_code() -> None:
    run = _run(created_at="2026-10-07T20:00:00Z")
    assert github.trusted_run(run, REPO, PR, _always, [_head_pull(base_changed_at=AFTER_RUN)])


def _pulls_api(pulls: list[dict[str, Any]], timelines: dict[int, list[dict[str, Any]]]) -> Any:
    paths: list[str] = []

    def paginated(path: str) -> list[Any]:
        paths.append(path)
        if "/pulls?" in path:
            return pulls
        return timelines[int(path.split("/issues/")[1].split("/")[0])]

    paginated.paths = paths
    return paginated


def _api_pull(number: int, created_at: str = "2026-10-07T23:05:38Z", base: str = "develop") -> dict[str, Any]:
    return {"number": number, "created_at": created_at, "base": {"ref": base}}


def _full_run(**over: Any) -> dict[str, Any]:
    fields = {"head_repository": {"owner": {"login": "BenchBox-dev"}}, "head_branch": "fix/example", **over}
    return _target_run(**fields)


def test_head_pulls_reads_every_pull_request_for_the_head_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _pulls_api(
        [_api_pull(7), _api_pull(9, base="main")],
        {
            7: [{"event": "labeled", "created_at": "2026-10-07T23:30:00Z"}],
            9: [
                {"event": "base_ref_changed", "created_at": "2026-10-07T21:00:00Z"},
                {"event": "base_ref_changed", "created_at": "2026-10-07T22:00:00Z"},
            ],
        },
    )
    monkeypatch.setattr(github, "get_paginated", api)
    pulls = github.head_pulls(REPO, _full_run())
    assert [(pull.number, pull.base_ref) for pull in pulls] == [(7, "develop"), (9, "main")]
    assert pulls[0].base_changed_at is None
    assert pulls[1].base_changed_at == datetime(2026, 10, 7, 22, 0, tzinfo=UTC)
    assert api.paths[0] == f"repos/{REPO}/pulls?state=all&head=BenchBox-dev:fix/example&per_page=100"
    assert f"repos/{REPO}/issues/9/timeline?per_page=100" in api.paths


@pytest.mark.parametrize(
    ("branch", "encoded"),
    [
        ("fix/some-branch", "fix/some-branch"),
        ("a&per_page=1", "a%26per_page%3D1"),
        ("a#frag", "a%23frag"),
        ("a b", "a%20b"),
        ("100%", "100%25"),
    ],
)
def test_head_pulls_encodes_the_head_branch_in_the_query(
    monkeypatch: pytest.MonkeyPatch, branch: str, encoded: str
) -> None:
    api = _pulls_api([], {})
    monkeypatch.setattr(github, "get_paginated", api)
    github.head_pulls(REPO, _full_run(head_branch=branch))
    assert api.paths == [f"repos/{REPO}/pulls?state=all&head=BenchBox-dev:{encoded}&per_page=100"]


@pytest.mark.parametrize(
    "run", [_full_run(head_branch=None), _full_run(head_repository={"owner": {}}), _full_run(head_repository=None)]
)
def test_head_pulls_needs_the_head_branch_and_owner(monkeypatch: pytest.MonkeyPatch, run: dict[str, Any]) -> None:
    monkeypatch.setattr(github, "get_paginated", _pulls_api([], {}))
    with pytest.raises(github.GitHubError):
        github.head_pulls(REPO, run)


def test_head_pulls_refuses_unreadable_times(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "get_paginated", _pulls_api([_api_pull(7, created_at="?")], {7: []}))
    with pytest.raises(github.GitHubError):
        github.head_pulls(REPO, _full_run())
    bad_event = {"event": "base_ref_changed", "created_at": "?"}
    monkeypatch.setattr(github, "get_paginated", _pulls_api([_api_pull(7)], {7: [bad_event]}))
    with pytest.raises(github.GitHubError):
        github.head_pulls(REPO, _full_run())


def _state_listing(run_id: int) -> dict[str, Any]:
    return {"artifacts": [{"created_at": "2026-10-07T23:10:00Z", "workflow_run": {"id": run_id}, "expired": False}]}


def _no_download(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    raise AssertionError("an untrusted run must not be downloaded")


def _serve(monkeypatch: pytest.MonkeyPatch, run: dict[str, Any]) -> None:
    def get_json(path: str) -> Any:
        return _state_listing(5) if path.startswith(f"repos/{REPO}/actions/artifacts") else run

    monkeypatch.setattr(github, "get_json", get_json)


def test_latest_state_skips_a_run_with_a_sibling_pull_request_into_another_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, _full_run())
    api = _pulls_api([_api_pull(7), _api_pull(9, base="evil")], {7: [], 9: []})
    monkeypatch.setattr(github, "get_paginated", api)
    monkeypatch.setattr(github.subprocess, "run", _no_download)
    assert github.latest_state(REPO, 7) is None


def test_latest_state_fails_closed_when_the_pull_requests_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: str) -> list[Any]:
        raise github.GitHubError("HTTP 502")

    _serve(monkeypatch, _full_run())
    monkeypatch.setattr(github, "get_paginated", broken)
    monkeypatch.setattr(github.subprocess, "run", _no_download)
    assert github.latest_state(REPO, 7) is None


def test_latest_state_downloads_a_run_whose_head_pull_requests_all_target_develop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = State(7, "a" * 40, "pending", datetime(2026, 10, 5, tzinfo=UTC))

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        directory = Path(argv[argv.index("-D") + 1])
        (directory / github.STATE_FILE).write_text(json.dumps(state.to_json()), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    _serve(monkeypatch, _full_run())
    monkeypatch.setattr(github, "get_paginated", _pulls_api([_api_pull(7)], {7: []}))
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    assert github.latest_state(REPO, 7) == state


def test_latest_state_without_artifacts_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "get_json", lambda path: {"artifacts": []})
    assert github.latest_state(REPO, 7) is None


def test_review_threads_pages_and_keeps_the_first_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"hasNextPage": True, "endCursor": "c1", "nodes": [_thread(False, "a.py", "benchbox-oracle", "**High**: A")]},
        {"hasNextPage": False, "endCursor": None, "nodes": [_thread(True, "b.py", None, "")]},
    ]
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str, accept: str | None = None) -> str:
        calls.append(args)
        page = pages[len(calls) - 1]
        connection = {"pageInfo": {"hasNextPage": page["hasNextPage"], "endCursor": page["endCursor"]}}
        connection["nodes"] = page["nodes"]
        return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": connection}}}})

    monkeypatch.setattr(github, "_gh", fake_gh)
    assert github.review_threads(REPO, 7) == [
        {
            "resolved": False,
            "path": "a.py",
            "author": "benchbox-oracle",
            "author_type": "Bot",
            "body": "**High**: A",
            "edited": False,
            "replies": [],
        },
        {
            "resolved": True,
            "path": "b.py",
            "author": None,
            "author_type": None,
            "body": "",
            "edited": False,
            "replies": [],
        },
    ]
    assert "after=c1" in calls[1] and not any(arg.startswith("after=") for arg in calls[0])
    flags = dict(zip(calls[0][1::2], calls[0][2::2], strict=False))
    assert calls[0][0] == "graphql"
    assert [calls[0][index - 1] for index, arg in enumerate(calls[0]) if arg.startswith(("owner=", "name="))] == [
        "-f",
        "-f",
    ]
    assert flags["-F"] == "number=7"


def test_review_threads_follow_a_thread_past_its_first_page_of_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    def comment(body: str) -> dict[str, Any]:
        return {"author": {"__typename": "User", "login": "joe"}, "authorAssociation": "OWNER", "body": body}

    thread = _thread(False, "a.py", "benchbox-oracle", "**High**: A")
    thread["comments"] = {
        "pageInfo": {"hasNextPage": True, "endCursor": "k1"},
        "nodes": [thread["comments"]["nodes"][0], comment("early reply")],
    }
    seen: list[tuple[str, ...]] = []

    def fake_gh(*args: str, accept: str | None = None) -> str:
        seen.append(args)
        if any(arg.startswith("thread=") for arg in args):
            assert "after=k1" in args and "thread=T_a.py" in args
            page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [comment("Dispute: late reply")]}
            return json.dumps({"data": {"node": {"comments": page}}})
        connection = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [thread]}
        return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": connection}}}})

    monkeypatch.setattr(github, "_gh", fake_gh)
    [result] = github.review_threads(REPO, 7)
    assert [reply["body"] for reply in result["replies"]] == ["early reply", "Dispute: late reply"]


def test_review_threads_raises_on_graphql_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "_gh", lambda *args, accept=None: json.dumps({"errors": [{"message": "no"}]}))
    with pytest.raises(github.GitHubError, match="GraphQL"):
        github.review_threads(REPO, 7)


def _thread(
    resolved: bool, path: str, author: str | None, body: str, edit: dict[str, Any] | None = None
) -> dict[str, Any]:
    comment = {
        "author": {"__typename": "Bot", "login": author} if author else None,
        "body": body,
        **({"lastEditedAt": None, "includesCreatedEdit": False} if edit is None else edit),
    }
    return {
        "id": f"T_{path}",
        "isResolved": resolved,
        "path": path,
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [comment]},
    }


def _serve_threads(monkeypatch: pytest.MonkeyPatch, nodes: list[dict[str, Any]]) -> None:
    connection = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
    payload = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": connection}}}})
    monkeypatch.setattr(github, "_gh", lambda *args, accept=None: payload)


@pytest.mark.parametrize(
    "edit",
    [
        {"lastEditedAt": "2026-10-20T00:00:00Z", "includesCreatedEdit": False},
        {"lastEditedAt": None, "includesCreatedEdit": True},
    ],
    ids=["edited-later", "created-edit"],
)
def test_review_threads_flag_an_edited_first_comment(monkeypatch: pytest.MonkeyPatch, edit: dict[str, Any]) -> None:
    _serve_threads(monkeypatch, [_thread(False, "a.py", "benchbox-oracle", "**High**: A", edit)])
    assert [thread["edited"] for thread in github.review_threads(REPO, 7)] == [True]


def test_review_threads_refuse_a_first_comment_without_edit_state(monkeypatch: pytest.MonkeyPatch) -> None:
    node = _thread(False, "a.py", "benchbox-oracle", "**High**: A")
    del node["comments"]["nodes"][0]["includesCreatedEdit"]
    _serve_threads(monkeypatch, [node])
    with pytest.raises(github.GitHubError, match="edit state"):
        github.review_threads(REPO, 7)


def _edit_payload(*states: tuple[str, Any, bool]) -> str:
    nodes = [{"id": node_id, "lastEditedAt": at, "includesCreatedEdit": created} for node_id, at, created in states]
    return json.dumps({"data": {"nodes": nodes}})


def test_review_edit_states_batch_every_id_into_one_graphql_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str, accept: str | None = None) -> str:
        calls.append(args)
        return _edit_payload(("PRR_1", None, False), ("PRR_2", "2026-10-20T00:00:00Z", False))

    monkeypatch.setattr(github, "_gh", fake_gh)
    assert github.review_edit_states(["PRR_1", "PRR_2", "PRR_1"]) == {
        "PRR_1": (None, False),
        "PRR_2": ("2026-10-20T00:00:00Z", False),
    }
    assert len(calls) == 1
    assert calls[0][0] == "graphql"
    assert [arg for arg in calls[0] if arg.startswith("ids[]=")] == ["ids[]=PRR_1", "ids[]=PRR_2"]
    assert "lastEditedAt" in calls[0][2] and "includesCreatedEdit" in calls[0][2]


def test_review_edit_states_split_more_than_one_hundred_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    def fake_gh(*args: str, accept: str | None = None) -> str:
        ids = [arg.removeprefix("ids[]=") for arg in args if arg.startswith("ids[]=")]
        sizes.append(len(ids))
        return _edit_payload(*((node_id, None, False) for node_id in ids))

    monkeypatch.setattr(github, "_gh", fake_gh)
    states = github.review_edit_states([f"PRR_{index}" for index in range(150)])
    assert sizes == [100, 50] and len(states) == 150


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps({"errors": [{"message": "rate limited"}]}),
        json.dumps({"data": {"nodes": [None]}}),
        json.dumps({"data": {"nodes": [{"id": "PRR_1"}]}}),
        "not json",
    ],
    ids=["graphql-error", "null-node", "missing-fields", "malformed"],
)
def test_review_edit_states_raise_when_the_edit_state_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    monkeypatch.setattr(github, "_gh", lambda *args, accept=None: reply)
    with pytest.raises(edits.EditStateError):
        github.review_edit_states(["PRR_1"])


def test_review_edit_states_raise_when_gh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: str, accept: str | None = None) -> str:
        raise github.GitHubError("gh api failed")

    monkeypatch.setattr(github, "_gh", broken)
    with pytest.raises(edits.EditStateError, match="could not be read"):
        github.review_edit_states(["PRR_1"])


@pytest.mark.skipif(sys.platform == "win32", reason="the fake gh is a POSIX shebang script and PATH is colon-joined")
def test_oracle_reviews_reads_every_page_through_gh_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        [
            {
                "id": index,
                "node_id": f"PRR_{index}",
                "user": {"login": "benchbox-oracle[bot]", "type": "Bot"},
                "body": f"r{index}",
            }
            for index in part
        ]
        for part in (range(100), range(100, 151))
    ]
    argv_log = tmp_path / "argv.json"
    script = tmp_path / "bin" / "gh"
    script.parent.mkdir()
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"pages = {pages!r}\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "args = sys.argv[1:]\n"
        "if '--paginate' in args and '--slurp' in args:\n"
        "    print(json.dumps(pages))\n"
        "elif '--paginate' in args:\n"
        "    print(''.join(json.dumps(page) for page in pages))\n"
        "else:\n"
        "    print(json.dumps(pages[0]))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{script.parent}:/usr/bin:/bin")
    reviews = github.oracle_reviews(REPO, PR)
    assert [review["id"] for review in reviews] == list(range(151))
    assert reviews[150]["login"] == "benchbox-oracle[bot]" and reviews[150]["body"] == "r150"
    assert reviews[150]["node_id"] == "PRR_150"
    argv = json.loads(argv_log.read_text(encoding="utf-8"))
    assert argv[0] == "api" and argv[-1] == f"repos/{REPO}/pulls/{PR}/reviews?per_page=100"


def _state_sources(
    monkeypatch: pytest.MonkeyPatch, artifacts: dict[str, list[dict[str, Any]]], runs: dict[str, dict[str, Any]]
) -> list[str]:
    downloaded: list[str] = []

    def get_json(path: str) -> Any:
        if path.startswith(f"repos/{REPO}/actions/artifacts?name="):
            return {"artifacts": artifacts.get(path.split("name=", 1)[1].split("&", 1)[0], [])}
        if path.startswith(f"repos/{REPO}/actions/runs/"):
            return runs[path.rsplit("/", 1)[1]]
        if "/compare/" in path:
            return {"status": "ahead"}
        raise AssertionError(f"unexpected request {path}")

    def fake_download(repo: str, run_id: str, name: str, directory: str) -> None:
        downloaded.append(name)
        state = State(7, "a" * 40, "failure", datetime(2026, 10, 5, tzinfo=UTC), strikes=1)
        (Path(directory) / github.STATE_FILE).write_text(json.dumps(state.to_json()), "utf-8")

    monkeypatch.setattr(github, "get_json", get_json)
    monkeypatch.setattr(github, "get_paginated", lambda path: [])
    monkeypatch.setattr(github, "download_state_artifact", fake_download)
    return downloaded


def _artifact(run_id: int) -> dict[str, Any]:
    return {"created_at": "2026-10-05T12:00:00Z", "workflow_run": {"id": run_id}, "expired": False}


def test_latest_state_downloads_matching_verdict_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    name = github.state_artifact_name(7, github.STATE_ARTIFACT_PREFIX)
    downloaded = _state_sources(monkeypatch, {name: [_artifact(1)]}, {"1": _run(path=github.WORKFLOW_PATH)})
    state = github.latest_state(REPO, 7)
    assert state is not None and state.strikes == 1
    assert downloaded == [name]


def test_latest_state_falls_back_to_the_attempt_state_when_publishing_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = github.attempt_state_artifact_name(7)
    downloaded = _state_sources(monkeypatch, {attempt: [_artifact(1)]}, {"1": _run(path=github.WORKFLOW_PATH)})
    assert github.latest_state(REPO, 7) is not None
    assert downloaded == [attempt]


def test_latest_state_prefers_the_published_state_over_a_same_second_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    published = github.state_artifact_name(7)
    attempt = github.attempt_state_artifact_name(7)
    downloaded = _state_sources(
        monkeypatch,
        {attempt: [_artifact(1)], published: [_artifact(1)]},
        {"1": _run(path=github.WORKFLOW_PATH)},
    )
    assert github.latest_state(REPO, 7) is not None
    assert downloaded == [published]


def test_latest_state_ignores_artifacts_from_another_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    name = github.state_artifact_name(7, github.STATE_ARTIFACT_PREFIX)
    downloaded = _state_sources(monkeypatch, {name: [_artifact(1)]}, {"1": _run(path=".github/workflows/other.yml")})
    assert github.latest_state(REPO, 7) is None
    assert downloaded == []


def test_gh_returns_diff_bytes_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"diff --git a/EULA.txt b/EULA.txt\n\x93smart quote\x94\n"

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, payload, b"")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    assert github.get_diff(REPO, PR) == payload


def test_gh_reports_decodable_stderr_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", b"\xffailure")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    with pytest.raises(github.GitHubError, match="ailure"):
        github._gh("repos/x")


def test_merge_base_reads_the_compare_merge_base_and_none_when_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    paths: list[str] = []

    def get_json(path: str) -> Any:
        paths.append(path)
        if "bad" in path:
            raise github.GitHubError("down")
        return {"merge_base_commit": {"sha": "c" * 40 if "good" in path else "short"}}

    monkeypatch.setattr(github, "get_json", get_json)
    assert github.merge_base(REPO, "good", "head") == "c" * 40
    assert paths == [f"repos/{REPO}/compare/good...head?per_page=1"]
    assert github.merge_base(REPO, "bad", "head") is None
    assert github.merge_base(REPO, "other", "head") is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "truncated": False,
                "tree": [
                    {"path": "a.py", "mode": "100755", "type": "blob", "sha": "1" * 40},
                    {"path": "dir", "mode": "040000", "type": "tree", "sha": "2" * 40},
                    {"path": "mod", "mode": "160000", "type": "commit", "sha": "3" * 40},
                ],
            },
            {"a.py": ("100755", "1" * 40), "mod": ("160000", "3" * 40)},
        ),
        ({"truncated": True, "tree": []}, None),
        ({"truncated": False}, None),
    ],
    ids=["entries", "truncated", "no-tree"],
)
def test_tree_blobs_keeps_modes_and_full_ids_and_refuses_a_truncated_tree(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], expected: dict[str, tuple[str, str]] | None
) -> None:
    monkeypatch.setattr(github, "get_json", lambda path: payload)
    assert github.tree_blobs(REPO, "c" * 40) == expected


def test_tree_blobs_is_none_when_the_tree_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_json(path: str) -> Any:
        raise github.GitHubError("down")

    monkeypatch.setattr(github, "get_json", get_json)
    assert github.tree_blobs(REPO, "c" * 40) is None


def test_failed_steps_names_every_step_that_failed_timed_out_or_was_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = {
        "jobs": [
            {
                "steps": [
                    {"name": "Plan the review", "conclusion": "success"},
                    {"name": "Verify Linux process isolation", "conclusion": "failure"},
                    {"name": "Run the attempt loop", "conclusion": "timed_out"},
                    {"name": "Late checkout", "conclusion": "cancelled"},
                    {"name": "Skipped post", "conclusion": "skipped"},
                    {"name": "Surface", "conclusion": None},
                ]
            }
        ]
    }
    monkeypatch.setattr(github, "get_json", lambda path: listing)
    assert github.failed_steps(REPO, 5) == [
        "Verify Linux process isolation",
        "Run the attempt loop",
        "Late checkout",
    ]


@pytest.mark.parametrize(
    ("creator", "expected"),
    [
        ({"login": "benchbox-oracle[bot]", "type": "Bot"}, True),
        ({"login": "benchbox-oracle", "type": "Bot"}, True),
        ({"login": "benchbox-oracle[bot]", "type": "User"}, False),
        ({"login": "someone", "type": "User"}, False),
        ("benchbox-oracle[bot]", False),
        ({}, False),
        (None, False),
    ],
)
def test_only_the_oracle_app_marks_a_status_as_its_own(creator: Any, expected: bool) -> None:
    assert github.is_app_status({"creator": creator}, "benchbox-oracle") is expected


def test_commit_statuses_lists_paginated_statuses_with_creator_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries: list[Any] = [
        {
            "context": "oracle-verdict",
            "state": "pending",
            "description": "verdict in progress",
            "target_url": "https://github.com/BenchBox-dev/benchbox/actions/runs/1",
            "creator": {"login": "benchbox-oracle[bot]", "type": "Bot"},
            "created_at": "2026-10-26T17:35:11Z",
            "updated_at": "2026-10-26T17:35:11Z",
        },
        "not-a-status",
        {"context": "ci", "state": "success", "creator": {"login": "someone", "type": "User"}},
    ]
    seen: list[str] = []
    monkeypatch.setattr(github, "get_paginated", lambda path: (seen.append(path), entries)[1])
    assert github.commit_statuses(REPO, SHA) == [entries[0], entries[2]]
    assert seen == [f"repos/{REPO}/commits/{SHA}/statuses?per_page=100"]


def test_verdict_runs_retries_once_with_a_short_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    pauses: list[float] = []
    payload = [
        {"total_count": 1, "workflow_runs": [{"id": 3, "display_title": "oracle-verdict (workflow_dispatch pr=7)"}]}
    ]

    def fake_gh(*args: str, accept: str | None = None) -> str:
        calls.append(args)
        if len(calls) == 1:
            raise github.GitHubError("gh api failed: Bad Gateway")
        return json.dumps(payload)

    monkeypatch.setattr(github, "_gh", fake_gh)
    monkeypatch.setattr(github, "sleep", pauses.append)
    assert (
        github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC))
        == payload[0]["workflow_runs"]
    )
    assert len(calls) == 2 and all(call[:2] == ("--paginate", "--slurp") for call in calls)
    assert pauses == [github.RUN_LIST_PAUSE_SECONDS]


def test_verdict_runs_retries_a_json_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(["<html>Bad Gateway</html>", json.dumps([{"total_count": 1, "workflow_runs": [{"id": 3}]}])])
    pauses: list[float] = []
    monkeypatch.setattr(github, "_gh", lambda *args, accept=None: next(replies))
    monkeypatch.setattr(github, "sleep", pauses.append)
    assert github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC)) == [{"id": 3}]
    assert pauses == [github.RUN_LIST_PAUSE_SECONDS]


def test_verdict_runs_raises_a_github_error_when_the_json_stays_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "_gh", lambda *args, accept=None: "not json")
    monkeypatch.setattr(github, "sleep", lambda seconds: None)
    with pytest.raises(github.GitHubError, match="invalid JSON"):
        github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC))


def test_verdict_runs_raises_after_the_bounded_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    pauses: list[float] = []

    def fake_gh(*args: str, accept: str | None = None) -> str:
        calls.append(args)
        raise github.GitHubError("gh api failed: Bad Gateway")

    monkeypatch.setattr(github, "_gh", fake_gh)
    monkeypatch.setattr(github, "sleep", pauses.append)
    with pytest.raises(github.GitHubError, match="Bad Gateway"):
        github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC))
    assert len(calls) == github.RUN_LIST_ATTEMPTS
    assert pauses == [github.RUN_LIST_PAUSE_SECONDS]


def test_in_flight_pulls_reads_only_the_first_page_of_active_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    pages = {
        "status=pending": {"workflow_runs": [{"display_title": "oracle-verdict (workflow_dispatch pr=11)"}]},
        "status=waiting": {"workflow_runs": [{"display_title": "oracle-verdict (workflow_dispatch pr=12)"}]},
        "status=requested": {"workflow_runs": [{"display_title": "oracle-verdict (workflow_dispatch pr=13)"}]},
        "status=queued": {"workflow_runs": [{"display_title": "oracle-verdict (workflow_dispatch pr=7)"}]},
        "status=in_progress": {
            "workflow_runs": [
                {"display_title": "oracle-verdict (pull_request_target pr=9)"},
                {"display_title": "oracle-verdict (schedule pr=sweep)"},
            ]
        },
    }

    def fake_json(path: str) -> dict[str, Any]:
        seen.append(path)
        return next(page for key, page in pages.items() if key in path)

    monkeypatch.setattr(github, "get_json", fake_json)
    assert github.in_flight_pulls(REPO, "oracle-verdict.yml") == frozenset({7, 9, 11, 12, 13})
    assert seen == [
        f"repos/{REPO}/actions/workflows/oracle-verdict.yml/runs?status={status}&per_page=100"
        for status in ("pending", "waiting", "requested", "queued", "in_progress")
    ]
    assert "--paginate" not in " ".join(seen)


def test_post_status_posts_with_the_given_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    environment = dict(os.environ)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    payload = {"state": "pending", "context": "oracle-verdict", "description": "d", "target_url": "u"}
    github.post_status(REPO, SHA, payload, "app-token")
    assert len(calls) == 1
    argv, env = calls[0]
    assert argv[:4] == ["gh", "api", "--method", "POST"] and f"repos/{REPO}/statuses/{SHA}" in argv
    assert env["GH_TOKEN"] == "app-token" and environment.get("GH_TOKEN") != "app-token"


def _page(runs: int, total: int | None) -> dict[str, Any]:
    page: dict[str, Any] = {"workflow_runs": [{"id": index} for index in range(runs)]}
    if total is not None:
        page["total_count"] = total
    return page


@pytest.mark.parametrize(("runs", "total"), [(1000, 1972), (2, 3), (0, 1)])
def test_a_capped_runs_listing_raises_without_retrying(monkeypatch: pytest.MonkeyPatch, runs: int, total: int) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        github, "_gh", lambda *args, accept=None: (calls.append(args), json.dumps([_page(runs, total)]))[1]
    )
    monkeypatch.setattr(github, "sleep", lambda seconds: None)
    with pytest.raises(github.GitHubError, match=f"{runs} of {total} runs"):
        github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC))
    assert len(calls) == 1


def test_a_listing_without_a_total_count_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "_gh", lambda *args, accept=None: json.dumps([_page(1, None)]))
    monkeypatch.setattr(github, "sleep", lambda seconds: None)
    with pytest.raises(github.GitHubError, match="total run count"):
        github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC))


def test_a_complete_listing_across_pages_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"total_count": 3, "workflow_runs": [{"id": 1}, {"id": 2}]},
        {"total_count": 3, "workflow_runs": [{"id": 3}]},
    ]
    monkeypatch.setattr(github, "_gh", lambda *args, accept=None: json.dumps(pages))
    assert github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC)) == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (f"oracle-verdict (workflow_dispatch pr=7 head={SHA})", ("7", SHA)),
        (f"oracle-verdict (pull_request_target pr=9 head={SHA})", ("9", SHA)),
        ("oracle-verdict (issue_comment pr=7)", ("7", None)),
        ("oracle-verdict (schedule pr=sweep)", None),
        ("oracle-verdict (workflow_dispatch pr=7 head=nothex)", None),
    ],
)
def test_run_titles_carry_the_pull_request_and_the_head(title: str, expected: tuple[str, str | None] | None) -> None:
    match = github.RUN_TITLE_PULL.search(title)
    assert (match.groups() if match else None) == expected


@pytest.mark.parametrize(
    ("reason", "reason_fields"), [(None, []), ("stateless-retry", ["-f", "reason=stateless-retry"])]
)
def test_dispatch_passes_the_pull_request_and_its_head(
    monkeypatch: pytest.MonkeyPatch, reason: str | None, reason_fields: list[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        github.subprocess,
        "run",
        lambda argv, **kwargs: (calls.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1],
    )
    github.dispatch(REPO, "oracle-verdict.yml", 7, SHA, reason)
    assert calls == [
        [
            "gh",
            "workflow",
            "run",
            "oracle-verdict.yml",
            "--ref",
            "develop",
            "-f",
            "pr=7",
            "-f",
            f"head={SHA}",
            *reason_fields,
            "-R",
            REPO,
        ]
    ]


def _jobs_and_annotations(
    monkeypatch: pytest.MonkeyPatch, jobs: list[dict[str, Any]], annotations: dict[int, list[dict[str, Any]]]
) -> list[str]:
    seen: list[str] = []

    def fake_json(path: str) -> Any:
        seen.append(path)
        if "/annotations" in path:
            return annotations[int(path.split("/check-runs/")[1].split("/")[0])]
        return {"jobs": jobs}

    monkeypatch.setattr(github, "get_json", fake_json)
    return seen


TIMEOUT_NOTE = {"annotation_level": "failure", "message": "The job has exceeded the maximum execution time of 5m0s"}
CONCURRENCY_NOTE = {
    "annotation_level": "failure",
    "message": "Canceling since a higher priority waiting request for oracle-verdict-pr-3291 exists",
}
CANCELED_NOTE = {"annotation_level": "failure", "message": "The operation was canceled."}
RUNNER_NOTE = {"annotation_level": "notice", "message": "The ubuntu-latest label will migrate to Ubuntu 26"}


def test_a_cancelled_job_with_the_timeout_annotation_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [{"id": 1, "conclusion": "success"}, {"id": 2, "conclusion": "cancelled"}]
    seen = _jobs_and_annotations(monkeypatch, jobs, {2: [RUNNER_NOTE, TIMEOUT_NOTE]})
    assert github.run_timed_out(REPO, 40110837085) is True
    assert not any("/check-runs/1/" in path for path in seen)


@pytest.mark.parametrize("notes", [[CONCURRENCY_NOTE], [CONCURRENCY_NOTE, CANCELED_NOTE], [RUNNER_NOTE], []])
def test_a_concurrency_or_manual_cancel_is_not_a_timeout(
    monkeypatch: pytest.MonkeyPatch, notes: list[dict[str, Any]]
) -> None:
    _jobs_and_annotations(monkeypatch, [{"id": 2, "conclusion": "cancelled"}], {2: notes})
    assert github.run_timed_out(REPO, 40216941595) is False
