from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_project/scripts"))
sys.modules["oracle_review_check"] = importlib.import_module("_project.scripts.oracle_review_check")
digest = importlib.import_module("_project.scripts.soundness_merge_digest")

HEAD = "a" * 40
OLD = "b" * 40
MERGED = "e" * 40
SINCE = "1" * 40
BEFORE = "2026-10-02T09:00:00Z"
PUSHED = "2026-10-02T10:00:00Z"
AFTER = "2026-10-02T11:00:00Z"
MERGED_AT = "2026-10-02T12:00:00Z"
LATER = "2026-10-02T13:00:00Z"
ORACLE_BOT = f"{digest.ORACLE_LOGIN}[bot]"
CONNECTOR = "chatgpt-codex-connector[bot]"


class Shell:
    def __init__(self):
        self.calls = []
        self.replies = {}
        self.graphql = None
        self.handlers = []

    def __call__(self, args, *, input_text=None, cwd=None):
        key = tuple(args)
        self.calls.append((key, cwd))
        if key[:3] == ("gh", "api", "graphql") and self.graphql is not None:
            return json.dumps(self.graphql(key) if callable(self.graphql) else self.graphql)
        for prefix, handler in self.handlers:
            if key[: len(prefix)] == prefix:
                return handler(key, input_text, cwd)
        if key not in self.replies:
            raise AssertionError(f"unexpected command {list(key)}")
        reply = self.replies[key]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def commands(self):
        return [key for key, _ in self.calls]

    def pages(self, endpoint, *pages):
        self.replies[("gh", "api", "--paginate", "--slurp", endpoint)] = json.dumps(list(pages))

    def api(self, endpoint, payload):
        self.replies[("gh", "api", endpoint)] = json.dumps(payload)

    def reply(self, *command, text=""):
        self.replies[command] = text


@pytest.fixture
def shell(monkeypatch):
    fake = Shell()
    monkeypatch.setattr(digest, "run", fake)
    return fake


def commit(sha, arrived_at, refresh=False, committed_at=""):
    return digest.PullCommit(sha, arrived_at, refresh, committed_at)


def review(login, sha, submitted_at=AFTER, state="COMMENTED", user_type="", body="", unverifiable=""):
    return digest.Review(login, sha, submitted_at, state, user_type, body, "", unverifiable)


def evidence(**overrides):
    fields = {
        "number": 7,
        "author": "dev",
        "merged_at": MERGED_AT,
        "commits": (commit(OLD, BEFORE), commit(HEAD, PUSHED)),
    }
    fields.update(overrides)
    return digest.PullEvidence(**fields)


def standin(sha=HEAD, at=AFTER):
    return digest.Comment("joeharris76", f"Stand-in oracle review: APPROVE {sha}", at, "User")


def oracle_review(state="failure", at=AFTER, body=None, sha=HEAD, review_state="COMMENTED"):
    text = body if body is not None else f"### oracle-verdict: {state} for `{sha}`\n\nDetails."
    return review(ORACLE_BOT, sha, at, review_state, "Bot", text)


def test_run_returns_what_the_command_wrote():
    assert digest.run([sys.executable, "-c", "print('ok')"]) == "ok\n"


def test_run_feeds_the_input_text_to_the_command():
    code = "import sys; sys.stdout.write(sys.stdin.read().upper())"
    assert digest.run([sys.executable, "-c", code], input_text="abc") == "ABC"


def test_run_executes_in_the_given_directory(tmp_path):
    out = digest.run([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
    assert Path(out.strip()).resolve() == tmp_path.resolve()


def test_run_refuses_a_failing_command_and_quotes_a_bounded_stderr():
    code = "import sys; sys.stderr.write('x' * 400); sys.exit(3)"
    command = [sys.executable, "-c", code, "alpha", "omega"]
    with pytest.raises(digest.ReadError) as error:
        digest.run(command)
    message = str(error.value)
    assert " ".join(command[:4]) in message
    assert "omega" not in message
    assert "x" * 300 in message
    assert "x" * 301 not in message


def test_run_reports_a_program_that_cannot_start_as_a_read_error():
    with pytest.raises(digest.ReadError) as error:
        digest.run(["no-such-program-for-the-digest"])
    assert "no-such-program-for-the-digest" in str(error.value)


def test_gh_json_runs_gh_with_the_arguments(shell):
    shell.reply("gh", "api", "repos/o/r", text='{"a": 1}')
    assert digest.gh_json("api", "repos/o/r") == {"a": 1}


def test_gh_pages_reads_every_page_and_wraps_a_single_object(shell):
    shell.pages("repos/o/r/x", [{"n": 1}], {"n": 2})
    assert digest.gh_pages("repos/o/r/x") == [{"n": 1}, {"n": 2}]


def test_the_commit_subject_and_changed_files_come_from_git(shell):
    shell.reply("git", "show", "-s", "--format=%s", HEAD, text="  the subject \n")
    shell.reply("git", "diff", "--name-only", "--no-renames", f"{HEAD}~1", HEAD, text="a\nb\n")
    assert digest.commit_subject(HEAD) == "the subject"
    assert digest.commit_files(HEAD) == ["a", "b"]


def pull_payload(commits=2, **overrides):
    payload = {
        "head": {"sha": HEAD, "ref": "feature", "repo": {"full_name": "o/r"}},
        "commits": commits,
        "merged_at": MERGED_AT,
        "created_at": BEFORE,
        "user": {"login": "dev"},
    }
    payload.update(overrides)
    return payload


def api_commit(sha, committed_at, parents=1):
    return {"sha": sha, "commit": {"committer": {"date": committed_at}}, "parents": [{"sha": "p"}] * parents}


def test_commits_are_read_in_the_given_checkout_with_the_server_arrival_of_the_last_content_commit(monkeypatch, shell):
    checkout = Path("/checkout")
    shell.pages("repos/o/r/pulls/7/commits", [api_commit(OLD, BEFORE, 2), api_commit(HEAD, "2000-01-01T00:00:00Z")])
    shell.reply("git", "fetch", "--no-tags", "origin", "refs/pull/7/head")
    shell.reply("git", "rev-parse", "FETCH_HEAD", text=HEAD + "\n")
    runs = {"workflow_runs": [{"event": "pull_request", "created_at": PUSHED, "pull_requests": [{"number": 7}]}]}
    shell.api(f"repos/o/r/actions/runs?head_sha={HEAD}&per_page=100", runs)
    judged = []

    def merge_adds_content(sha, base, *, cwd=None):
        judged.append((sha, base, cwd))
        return False

    monkeypatch.setattr(digest, "merge_adds_content", merge_adds_content)
    result = digest.collect_commits("o/r", 7, pull_payload(), "the-base", cwd=checkout)
    assert result == (commit(OLD, BEFORE, True, BEFORE), commit(HEAD, PUSHED, False, "2000-01-01T00:00:00Z"))
    assert judged == [(OLD, "the-base", checkout)]
    assert {cwd for key, cwd in shell.calls if key[0] == "git"} == {checkout}


def thread_node(resolved, login, sha, body="**High**: title"):
    comment = {
        "author": {"login": login},
        "originalCommit": {"oid": sha},
        "body": body,
        "lastEditedAt": None,
        "includesCreatedEdit": False,
    }
    return {"isResolved": resolved, "comments": {"nodes": [comment]}}


def graphql_threads(nodes):
    return {
        "data": {"repository": {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": nodes}}}}
    }


def test_threads_are_read_with_one_graphql_query_naming_owner_repo_pull_and_page(monkeypatch):
    seen = []

    def fake_json(*args):
        seen.append(args)
        return graphql_threads([thread_node(True, "reviewer", HEAD, "plain")])

    monkeypatch.setattr(digest, "gh_json", fake_json)
    threads = digest.collect_threads("owner/name", 7)
    assert threads == (digest.Thread(True, "reviewer", HEAD),)
    (args,) = seen
    assert args[:3] == ("api", "graphql", "-f")
    assert args[3].startswith("query=")
    assert args[4:] == ("-f", "o=owner", "-f", "n=name", "-F", "p=7", "-F", "first=100")


def test_an_oracle_thread_keeps_the_severity_and_a_missing_commit_is_empty(monkeypatch):
    nodes = [thread_node(False, "benchbox-oracle", HEAD), {"isResolved": True, "comments": {"nodes": [{"body": "x"}]}}]
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads(nodes))
    assert digest.collect_threads("o/r", 7) == (
        digest.Thread(False, "benchbox-oracle", HEAD, "High"),
        digest.Thread(True, "", ""),
    )


def trunk_run(run_id=9, created="2026-10-21T07:00:00Z", updated="2026-10-21T08:00:00Z", **fields):
    run = {
        "id": run_id,
        "status": "completed",
        "conclusion": "failure",
        "created_at": created,
        "updated_at": updated,
        "head_sha": "d" * 40,
    }
    run.update(fields)
    return run


ARMED = "2026-10-21T11:41:17Z"


class Attempts:
    def __init__(self, records):
        self.records = records
        self.asked = []

    def __call__(self, repo, run_id, attempt):
        self.asked.append((repo, run_id, attempt))
        return self.records[attempt]


def test_a_run_with_no_status_is_taken_as_completed():
    run = {"conclusion": "failure", "updated_at": "2026-10-21T08:00:00Z"}
    assert digest.trunk_run_state("o/r", run, ARMED) == (True, "failure", "2026-10-21T08:00:00Z")


def test_an_unfinished_run_is_not_a_completed_state():
    assert digest.trunk_run_state("o/r", trunk_run(status="in_progress"), ARMED) == (False, None, "")


def test_a_run_with_no_update_time_has_an_empty_stamp():
    run = {"status": "completed", "conclusion": "failure"}
    assert digest.trunk_run_state("o/r", run, ARMED) == (False, "failure", "")


@pytest.mark.parametrize("attempt", [None, 1, 3])
def test_a_first_attempt_run_updated_after_the_arming_is_not_yet_complete(monkeypatch, attempt):
    late = {"conclusion": "success", "updated_at": "2026-10-21T12:00:00Z"}
    asked = Attempts({2: late, 1: late})
    monkeypatch.setattr(digest, "trunk_attempt", asked)
    updated = "2026-10-21T12:00:00Z"
    run = trunk_run(updated=updated, run_attempt=attempt)
    state = digest.trunk_run_state("o/r", run, ARMED)
    if attempt == 3:
        assert state == (False, None, "")
        assert [item[2] for item in asked.asked] == [2, 1]
    else:
        assert state == (False, "failure", updated)
        assert asked.asked == []


@pytest.mark.parametrize("attempt", [None, 1, 3])
def test_a_run_updated_exactly_at_the_arming_is_complete_without_reading_attempts(monkeypatch, attempt):
    asked = Attempts({})
    monkeypatch.setattr(digest, "trunk_attempt", asked)
    run = trunk_run(updated=ARMED, run_attempt=attempt)
    assert digest.trunk_run_state("o/r", run, ARMED) == (True, "failure", ARMED)
    assert asked.asked == []


def test_a_rerun_resolves_to_the_newest_attempt_that_finished_by_the_arming(monkeypatch):
    asked = Attempts(
        {
            3: {"conclusion": "success", "updated_at": "2026-10-21T11:55:00Z"},
            2: {"conclusion": "timed_out", "updated_at": "2026-10-21T11:30:00Z"},
            1: {"conclusion": "failure", "updated_at": "2026-10-21T10:00:00Z"},
        }
    )
    monkeypatch.setattr(digest, "trunk_attempt", asked)
    run = trunk_run(run_id=77, updated="2026-10-21T12:30:00Z", run_attempt=4)
    assert digest.trunk_run_state("o/r", run, ARMED) == (True, "timed_out", "2026-10-21T11:30:00Z")
    assert asked.asked == [("o/r", 77, 3), ("o/r", 77, 2)]


def test_an_attempt_finished_exactly_at_the_arming_counts(monkeypatch):
    asked = Attempts({1: {"conclusion": "failure", "updated_at": ARMED}})
    monkeypatch.setattr(digest, "trunk_attempt", asked)
    run = trunk_run(updated="2026-10-21T12:30:00Z", run_attempt=2)
    assert digest.trunk_run_state("o/r", run, ARMED) == (True, "failure", ARMED)


def test_no_attempt_finishing_by_the_arming_leaves_the_state_open(monkeypatch):
    asked = Attempts({2: {"conclusion": "failure"}, 1: {"conclusion": "failure", "updated_at": "2026-10-21T12:00:00Z"}})
    monkeypatch.setattr(digest, "trunk_attempt", asked)
    run = trunk_run(updated="2026-10-21T12:30:00Z", run_attempt=3)
    assert digest.trunk_run_state("o/r", run, ARMED) == (False, None, "")
    assert [item[2] for item in asked.asked] == [2, 1]


def test_a_trunk_attempt_is_read_from_its_run_endpoint(shell):
    shell.api("repos/o/r/actions/runs/9/attempts/2", {"conclusion": "failure"})
    assert digest.trunk_attempt("o/r", 9, 2) == {"conclusion": "failure"}


def armed_pull(*armed_at, number=7):
    return evidence(number=number, armed_at=tuple(armed_at))


def test_a_run_created_after_the_arming_never_decides_the_state():
    later = trunk_run(1, created="2026-10-21T11:50:00Z", updated="2026-10-21T11:00:00Z")
    older = trunk_run(2, created="2026-10-21T06:00:00Z", updated="2026-10-21T08:00:00Z", conclusion="success")
    assert digest.red_armings("o/r", [armed_pull(ARMED)], [later, older]) == ([], [])


def test_a_run_created_exactly_at_the_arming_still_counts():
    run = trunk_run(1, created=ARMED, updated="2026-10-21T11:00:00Z")
    red, unknown = digest.red_armings("o/r", [armed_pull(ARMED)], [run])
    assert [(a.number, a.run_id) for a in red] == [(7, 1)]
    assert unknown == []


def test_a_skipped_run_does_not_hide_the_ones_after_it():
    later = trunk_run(1, created="2026-10-21T11:50:00Z", updated="2026-10-21T08:00:00Z", conclusion="success")
    older = trunk_run(2, created="2026-10-21T06:00:00Z", updated="2026-10-21T07:00:00Z")
    red, _ = digest.red_armings("o/r", [armed_pull(ARMED)], [later, older])
    assert [a.run_id for a in red] == [2]


def test_the_newest_finished_run_decides_not_the_alphabetically_last_conclusion():
    older_green = trunk_run(1, updated="2026-10-21T07:00:00Z", conclusion="success")
    newer_red = trunk_run(2, updated="2026-10-21T08:00:00Z", conclusion="failure")
    red, _ = digest.red_armings("o/r", [armed_pull(ARMED)], [older_green, newer_red])
    assert [(a.run_id, a.conclusion) for a in red] == [(2, "failure")]


def test_two_runs_finishing_together_do_not_break_the_decision():
    first = trunk_run(1, updated="2026-10-21T08:00:00Z")
    second = trunk_run(2, updated="2026-10-21T08:00:00Z")
    red, _ = digest.red_armings("o/r", [armed_pull(ARMED)], [first, second])
    assert [a.run_id for a in red] == [1]


def test_a_run_without_a_head_commit_is_reported_with_an_empty_head():
    run = trunk_run(1)
    del run["head_sha"]
    red, _ = digest.red_armings("o/r", [armed_pull(ARMED)], [run])
    assert red[0].run_head == ""


def test_a_run_that_needs_attempts_is_resolved_against_the_repository_asked(monkeypatch):
    asked = Attempts({1: {"conclusion": "failure", "updated_at": "2026-10-21T10:00:00Z"}})
    monkeypatch.setattr(digest, "trunk_attempt", asked)
    run = trunk_run(5, updated="2026-10-21T12:30:00Z", run_attempt=2)
    red, _ = digest.red_armings("owner/name", [armed_pull(ARMED)], [run])
    assert asked.asked == [("owner/name", 5, 1)]
    assert [a.completed_at for a in red] == ["2026-10-21T10:00:00Z"]


def test_an_unresolvable_arming_is_reported_unknown_with_its_pull_and_time_and_does_not_stop_the_rest(monkeypatch):
    def broken(repo, run_id, attempt):
        raise digest.ReadError("attempt gone")

    monkeypatch.setattr(digest, "trunk_attempt", broken)
    rerun = trunk_run(5, updated="2026-10-21T12:30:00Z", run_attempt=2)
    early, late = "2026-10-21T11:00:00Z", ARMED
    red, unknown = digest.red_armings("o/r", [armed_pull(early, late, number=9)], [rerun])
    assert red == []
    assert [(u.number, u.armed_at) for u in unknown] == [(9, early), (9, late)]


def test_an_arming_with_no_finished_run_does_not_stop_the_later_armings():
    run = trunk_run(1, created="2026-10-21T10:00:00Z", updated="2026-10-21T10:30:00Z")
    red, unknown = digest.red_armings("o/r", [armed_pull("2026-10-21T05:00:00Z", ARMED)], [run])
    assert [a.armed_at for a in red] == [ARMED]
    assert [u.armed_at for u in unknown] == ["2026-10-21T05:00:00Z"]


def test_armings_are_listed_by_time_then_pull_number():
    run = trunk_run(1)
    pulls = [
        armed_pull("2026-10-21T12:00:00Z", number=5),
        armed_pull("2026-10-21T11:00:00Z", number=9),
        armed_pull("2026-10-21T11:00:00Z", number=3),
    ]
    red, _ = digest.red_armings("o/r", pulls, [run])
    assert [(a.armed_at, a.number) for a in red] == [
        ("2026-10-21T11:00:00Z", 3),
        ("2026-10-21T11:00:00Z", 9),
        ("2026-10-21T12:00:00Z", 5),
    ]
    _, unknown = digest.red_armings("o/r", pulls, [])
    assert [(a.armed_at, a.number) for a in unknown] == [
        ("2026-10-21T11:00:00Z", 3),
        ("2026-10-21T11:00:00Z", 9),
        ("2026-10-21T12:00:00Z", 5),
    ]


def test_the_unknown_reason_tells_a_missing_history_from_an_unfinished_one():
    created_after = [{"created_at": "2026-10-21T12:00:00Z"}]
    created_at_arming = [{"created_at": ARMED}]
    assert digest.unknown_trunk_reason(created_after, ARMED) != digest.unknown_trunk_reason(created_at_arming, ARMED)
    assert digest.unknown_trunk_reason([], ARMED) == digest.unknown_trunk_reason(created_after, ARMED)
    assert digest.unknown_trunk_reason(created_at_arming, ARMED) == digest.unknown_trunk_reason(
        [{"created_at": "2026-10-21T06:00:00Z"}], ARMED
    )
    assert digest.unknown_trunk_reason([{}], ARMED) == digest.unknown_trunk_reason(created_at_arming, ARMED)


def trunk_page_url(page):
    return (
        "repos/o/r/actions/workflows/trunk.yml/runs"
        f"?branch=develop&event=push&status=completed&per_page=100&page={page}"
    )


def test_trunk_runs_stop_after_ten_full_pages(shell):
    for page in range(1, 11):
        shell.api(trunk_page_url(page), {"workflow_runs": [{"id": page * 1000 + i} for i in range(100)]})
    assert len(digest.fetch_trunk_runs("o/r")) == 1000
    assert len(shell.calls) == 10


STATE_ISSUES = "repos/o/r/issues?labels=soundness-merge-digest&state=all&per_page=100"
GAP_ISSUES = "repos/o/r/issues?labels=soundness-review-gap&state=all&per_page=100"


def test_the_state_issue_is_found_by_its_label(shell):
    shell.pages(STATE_ISSUES, [{"number": 4, "body": f"<!-- soundness-digest-checkpoint: {HEAD} -->"}])
    assert digest.read_checkpoint("o/r") == HEAD
    assert digest.find_state_issue("o/r")["number"] == 4


def test_gap_issues_are_listed_by_their_label(shell):
    shell.pages(GAP_ISSUES, [{"number": 9}])
    assert digest.list_gap_issues("o/r") == [{"number": 9}]


def test_a_state_issue_without_a_marker_is_refused_whatever_its_body(shell):
    shell.pages(STATE_ISSUES, [{"number": 4, "body": None}])
    with pytest.raises(digest.ReadError):
        digest.read_checkpoint("o/r")


def test_the_first_checkpoint_creates_the_label_and_the_state_issue(shell):
    shell.pages(STATE_ISSUES, [])
    body = f"<!-- soundness-digest-checkpoint: {HEAD} -->\n\nthe report\n"
    shell.reply("gh", "label", "create", "soundness-merge-digest", "--repo", "o/r", "--force", "--color", "0e8a16")
    shell.reply(
        "gh",
        "issue",
        "create",
        "--repo",
        "o/r",
        "--title",
        "Soundness merge digest",
        "--label",
        "soundness-merge-digest",
        "--body",
        body,
    )
    digest.write_checkpoint("o/r", HEAD, "the report")
    assert [key[:3] for key in shell.commands[1:]] == [("gh", "label", "create"), ("gh", "issue", "create")]


def test_a_later_checkpoint_edits_the_state_issue_body(shell):
    shell.pages(STATE_ISSUES, [{"number": 4, "body": "old"}])
    body = f"<!-- soundness-digest-checkpoint: {HEAD} -->\n\nthe report\n"
    shell.reply("gh", "issue", "edit", "4", "--repo", "o/r", "--body", body)
    digest.write_checkpoint("o/r", HEAD, "the report")
    assert shell.commands[-1] == ("gh", "issue", "edit", "4", "--repo", "o/r", "--body", body)
    assert len(shell.calls) == 2


def classified(sha, subject="subject", pull=None, files=("AGENTS.md",)):
    return digest.classify(sha, subject, list(files), pull)


def test_gap_issues_open_only_for_new_entries_that_need_attention(shell):
    new, known, fine = "d" * 40, "f" * 40, "c" * 40
    long_subject = "s" * 300
    reviewed = evidence(number=8, comments=(standin(),))
    entries = [
        classified(new, long_subject, files=("AGENTS.md", "f.py")),
        classified(known),
        classified(fine, pull=reviewed),
    ]
    shell.pages(GAP_ISSUES, [{"number": 3, "body": f"<!-- soundness-gap:{known} -->"}, {"number": 4, "body": None}])
    shell.reply("gh", "label", "create", "soundness-review-gap", "--repo", "o/r", "--force", "--color", "b60205")
    title = f"Soundness-path commit {new[:9]} needs an external review: {long_subject}"[:200]
    command = (
        "gh",
        "issue",
        "create",
        "--repo",
        "o/r",
        "--title",
        title,
        "--label",
        "soundness-review-gap",
        "--body",
        digest.gap_issue_body(entries[0]),
    )
    shell.replies[command] = ""
    assert digest.open_gap_issues("o/r", entries) == [new]
    assert command in shell.commands
    assert len(shell.calls) == 2 + 1


def test_gap_closures_read_the_comments_and_entries_of_the_repository_asked(shell, monkeypatch):
    body = digest.gap_issue_body(classified(HEAD, pull=evidence(number=8)))
    shell.pages(GAP_ISSUES, [{"number": 40, "state": "open", "body": body}])
    shell.pages("repos/o/r/issues/40/comments", [])
    seen = []
    reviewed = classified(HEAD, pull=evidence(comments=(standin(),)))
    monkeypatch.setattr(digest, "collect_entry", lambda repo, sha: seen.append((repo, sha)) or reviewed)
    assert [closure.number for closure in digest.gap_closures("o/r", [])] == [40]
    assert seen == [("o/r", HEAD)]


def test_a_closure_comment_carries_the_marker_signals_and_summary():
    entry = classified(HEAD, pull=evidence(comments=(standin(),), reviews=(oracle_review("success", PUSHED),)))
    assert entry.signals == ("oracle-review", "stand-in")
    closure = digest.resolved_closure(30, entry)
    assert closure.summary == "review signal found (oracle-review, stand-in)"
    assert f"<!-- soundness-gap-closed:{HEAD} -->" in closure.comment
    assert "Review signal: oracle-review, stand-in." in closure.comment


def test_an_attested_closure_names_the_reviewer_the_attester_and_the_evidence():
    comment = {
        "user": {"login": "joeharris76"},
        "body": "External review: muse\n\nVerified clean.",
        "html_url": "https://x/1",
    }
    closure = digest.attested_closure(31, HEAD, comment)
    assert closure.sha == HEAD
    assert closure.summary == "external review by muse from joeharris76"
    assert closure.comment.endswith("Evidence: https://x/1")
    assert "joeharris76 recorded an external review by muse" in closure.comment
    del comment["html_url"]
    assert digest.attested_closure(31, HEAD, comment).comment.endswith("Evidence: ")


def test_an_attested_review_needs_a_body():
    comment = {"user": {"login": "joeharris76", "type": "User"}, "body": None}
    assert digest.attested_review([comment]) is None


def test_an_issue_with_no_body_is_never_closed_by_re_evaluation(shell):
    shell.pages(GAP_ISSUES, [{"number": 5, "state": "open", "body": None}])
    assert digest.gap_closures("o/r", []) == []
    assert digest.opened_for_missing_signal({"body": None}) is False


def test_closing_a_gap_issue_posts_the_comment_and_completes_it(shell):
    closure = digest.Closure(30, HEAD, "summary", "the comment")
    command = ("gh", "issue", "close", "30", "--repo", "o/r", "--reason", "completed", "--comment", "the comment")
    shell.replies[command] = ""
    digest.close_gap_issues("o/r", [closure])
    assert shell.commands == [command]


def test_entries_are_collected_oldest_first_from_the_window_and_empty_ones_dropped(shell, monkeypatch):
    shell.reply(
        "git", "log", "--first-parent", "--reverse", "--format=%H", f"{SINCE}..origin/develop", text=f"{OLD}\n{HEAD}\n"
    )
    kept = classified(HEAD)
    seen = []

    def collect_entry(repo, sha):
        seen.append((repo, sha))
        return kept if sha == HEAD else None

    monkeypatch.setattr(digest, "collect_entry", collect_entry)
    assert digest.collect_entries("o/r", "origin/develop", SINCE) == [kept]
    assert seen == [("o/r", OLD), ("o/r", HEAD)]


def test_an_entry_is_built_from_the_commit_files_subject_and_pull(shell, monkeypatch):
    shell.reply("git", "diff", "--name-only", "--no-renames", f"{HEAD}~1", HEAD, text="AGENTS.md\nother\n")
    shell.reply("git", "show", "-s", "--format=%s", HEAD, text="the subject\n")
    pull = evidence(comments=(standin(),))
    seen = []
    monkeypatch.setattr(digest, "soundness_files", lambda sha, files: seen.append((sha, files)) or ["AGENTS.md"])
    monkeypatch.setattr(digest, "collect_pull", lambda repo, sha: pull if (repo, sha) == ("o/r", HEAD) else None)
    entry = digest.collect_entry("o/r", HEAD)
    assert entry == digest.classify(HEAD, "the subject", ["AGENTS.md"], pull)
    assert seen == [(HEAD, ["AGENTS.md", "other"])]


def test_a_commit_with_no_soundness_file_yields_no_entry(shell, monkeypatch):
    shell.reply("git", "diff", "--name-only", "--no-renames", f"{HEAD}~1", HEAD, text="other\n")
    monkeypatch.setattr(digest, "soundness_files", lambda sha, files: [])
    assert digest.collect_entry("o/r", HEAD) is None


ReadError = digest.ReadError


def test_a_pull_that_fails_to_read_is_reported_with_its_number(monkeypatch):
    monkeypatch.setattr(digest, "merged_commits", lambda ref, since: [HEAD])
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 12)

    def broken(repo, sha):
        raise KeyError("head")

    monkeypatch.setattr(digest, "collect_pull", broken)
    _, unreadable = digest.collect_window_pulls("o/r", "ref", "since")
    assert [(item.number, item.sha) for item in unreadable] == [(12, HEAD)]


def test_collect_pull_reads_every_field_of_the_pull_request(shell):
    sparse = {}
    shell.pages(
        "repos/o/r/commits/" + MERGED + "/pulls",
        [{"number": 7, "merge_commit_sha": MERGED, "merged_at": AFTER, "base": {"ref": "develop"}}],
    )
    shell.api("repos/o/r/pulls/7", pull_payload(commits=1))
    shell.pages("repos/o/r/pulls/7/commits", [api_commit(HEAD, PUSHED)])
    shell.api(
        f"repos/o/r/actions/runs?head_sha={HEAD}&per_page=100",
        {"workflow_runs": [{"event": "pull_request", "created_at": AFTER, "pull_requests": [{"number": 7}]}]},
    )
    shell.pages(
        "repos/o/r/issues/7/timeline",
        [
            {"event": "auto_merge_enabled", "created_at": LATER},
            {"event": "auto_merge_enabled"},
            {"event": "added_to_merge_queue", "created_at": AFTER},
            {"event": "base_ref_changed", "created_at": BEFORE},
        ],
    )
    full_review = {
        "user": {"login": "rev", "type": "User"},
        "commit_id": HEAD,
        "submitted_at": AFTER,
        "state": "APPROVED",
        "body": "lgtm",
        "node_id": "PRR_1",
    }
    shell.pages("repos/o/r/pulls/7/reviews", [full_review, sparse])
    shell.pages(
        "repos/o/r/issues/7/reactions",
        [{"user": {"login": "rev"}, "content": "+1", "created_at": AFTER}, {"content": "eyes", "created_at": LATER}],
    )
    shell.pages(
        "repos/o/r/issues/7/comments",
        [
            {
                "user": {"login": "joeharris76", "type": "User"},
                "body": "text",
                "created_at": AFTER,
                "updated_at": LATER,
            },
            {"created_at": BEFORE},
        ],
    )
    shell.graphql = graphql_threads([thread_node(True, "rev", HEAD, "plain")])
    pull = digest.collect_pull("o/r", MERGED)
    assert pull == digest.PullEvidence(
        number=7,
        author="dev",
        merged_at=MERGED_AT,
        commits=(commit(HEAD, AFTER, False, PUSHED),),
        reviews=(
            digest.Review("rev", HEAD, AFTER, "APPROVED", "User", "lgtm", "PRR_1"),
            digest.Review("", "", "", "", "", "", ""),
        ),
        reactions=(digest.Reaction("rev", "+1", AFTER), digest.Reaction("", "eyes", LATER)),
        comments=(
            digest.Comment("joeharris76", "text", AFTER, "User", LATER),
            digest.Comment("", "", BEFORE, "", None),
        ),
        threads=(digest.Thread(True, "rev", HEAD),),
        base_changed_at=BEFORE,
        armed_at=(AFTER, LATER),
    )


def test_a_commit_without_a_merged_pull_request_has_no_evidence(shell):
    shell.pages("repos/o/r/commits/" + MERGED + "/pulls", [])
    assert digest.collect_pull("o/r", MERGED) is None


def test_a_pull_request_with_no_arming_and_no_base_change_records_none(shell):
    shell.pages(
        "repos/o/r/commits/" + MERGED + "/pulls",
        [{"number": 7, "merge_commit_sha": MERGED, "merged_at": AFTER, "base": {"ref": "develop"}}],
    )
    shell.api("repos/o/r/pulls/7", pull_payload(commits=1))
    shell.pages("repos/o/r/pulls/7/commits", [api_commit(HEAD, PUSHED)])
    shell.api(f"repos/o/r/actions/runs?head_sha={HEAD}&per_page=100", {"workflow_runs": []})
    for tail in ("timeline", "reactions", "comments"):
        shell.pages(f"repos/o/r/issues/7/{tail}")
    shell.pages("repos/o/r/pulls/7/reviews")
    shell.graphql = graphql_threads([])
    pull = digest.collect_pull("o/r", MERGED)
    assert (pull.base_changed_at, pull.armed_at, pull.commits) == ("", (), (commit(HEAD, MERGED_AT, False, PUSHED),))


def test_the_window_pull_endpoint_is_asked_for_the_merge_commit(shell):
    shell.pages("repos/o/r/commits/" + MERGED + "/pulls", [])
    assert digest.merged_pull_number("o/r", MERGED) is None


def test_the_arrival_query_names_the_head_and_a_run_at_the_pull_creation_counts(shell):
    shell.api(
        f"repos/o/r/actions/runs?head_sha={HEAD}&per_page=100",
        {"workflow_runs": [{"event": "pull_request", "created_at": BEFORE, "pull_requests": [{"number": 7}]}]},
    )
    assert digest.server_arrival("o/r", HEAD, 7, pull_payload()) == BEFORE


@pytest.mark.parametrize(
    ("verdict_body", "expected"),
    [
        (None, None),
        ("### oracle-verdict: success for `{head}`", None),
        ("### oracle-verdict: error for `{head}`", "error"),
        ("### oracle-verdict: failure for `{head}`\n\nDecision: **DO NOT SHIP**.", "DO NOT SHIP"),
    ],
)
def test_the_blocking_decision_is_the_latest_blocking_verdict(verdict_body, expected):
    reviews = ()
    if verdict_body is not None:
        reviews = (oracle_review(body=verdict_body.format(head=HEAD)),)
    assert digest.blocking_verdict_decision(evidence(reviews=reviews)) == expected


def test_the_latest_blocking_review_by_time_decides_whatever_the_listing_order():
    later = oracle_review(at=AFTER, body=f"### oracle-verdict: failure for `{HEAD}`\n\nDecision: **LATER**.")
    earlier = oracle_review(at=PUSHED, body=f"### oracle-verdict: failure for `{HEAD}`\n\nDecision: **EARLIER**.")
    assert digest.blocking_verdict_decision(evidence(reviews=(later, earlier))) == "LATER"
    assert digest.blocking_verdict_decision(evidence(reviews=(earlier, later))) == "LATER"


@pytest.mark.parametrize("state", ["DISMISSED", "PENDING"])
def test_a_dismissed_or_pending_oracle_review_is_not_a_blocking_verdict(state):
    assert digest.blocking_verdict_decision(evidence(reviews=(oracle_review(review_state=state),))) is None


def test_an_unverifiable_oracle_review_is_counted_only_when_asked_for():
    flagged = review(ORACLE_BOT, HEAD, AFTER, "COMMENTED", "Bot", "", "edited")
    pull = evidence(reviews=(flagged,))
    assert digest.unverifiable_oracle_evidence(pull) is True
    assert digest._oracle_reviews(pull, {HEAD}) == []
    assert digest._oracle_reviews(pull, {HEAD}, include_unverifiable=True) == [flagged]


def test_with_no_commits_there_is_no_standin_floor():
    assert digest._standin_after(evidence(commits=())) == ""


def test_the_connector_review_stops_counting_at_the_cut_over_instant():
    pull = evidence(
        merged_at=digest.CONNECTOR_CUTOFF,
        reviews=(review(CONNECTOR, HEAD, "2026-10-08T00:00:00Z"),),
        reactions=(digest.Reaction(CONNECTOR, "+1", digest.CONNECTOR_CUTOFF),),
    )
    assert digest.review_signals(pull) == ()


def test_a_connector_review_submitted_at_the_merge_instant_counts():
    assert digest.review_signals(evidence(reviews=(review(CONNECTOR, HEAD, MERGED_AT),))) == ("connector-review",)


@pytest.mark.parametrize("at", [PUSHED, MERGED_AT])
def test_connector_approval_counts_at_both_window_edges(at):
    pull = evidence(reactions=(digest.Reaction(CONNECTOR, "+1", at),))
    assert digest.review_signals(pull) == ("connector-approval",)


def test_a_standin_posted_at_the_merge_instant_counts():
    assert digest.review_signals(evidence(comments=(standin(HEAD, MERGED_AT),))) == ("stand-in",)


def test_an_instruction_at_the_standin_instant_gives_a_zero_gap():
    instruction = digest.Comment("joeharris76", 'Owner instruction: "go"', AFTER, "User")
    assert digest.instruction_gap_seconds(evidence(comments=(instruction,)), AFTER) == 0


def bot_login_thread(severity="High", resolved=True, **fields):
    return digest.Thread(resolved, ORACLE_BOT, HEAD, severity, **fields)


def test_a_finding_posted_by_the_app_account_is_still_the_oracles():
    assert digest.is_overridden_finding(bot_login_thread("High")) is True
    assert digest.is_overridden_finding(bot_login_thread("Medium")) is False
    assert digest.is_overridden_finding(bot_login_thread("Medium", unverifiable="edited")) is True
    assert digest.is_overridden_finding(digest.Thread(True, f"[bot]{digest.ORACLE_LOGIN}", HEAD, "High")) is False
    assert digest.is_overridden_finding(digest.Thread(True, f"{digest.ORACLE_LOGIN}-x", HEAD, "High")) is False


def test_an_unresolved_app_thread_counts_as_an_open_finding():
    pull = evidence(threads=(bot_login_thread("Low", resolved=False),))
    assert digest.open_finding_severities(pull) == ("Low",)


def test_open_findings_are_listed_by_severity():
    threads = (
        bot_login_thread("Low", resolved=False),
        bot_login_thread("Critical", resolved=False),
        bot_login_thread("Medium", resolved=False),
    )
    assert digest.open_finding_severities(evidence(threads=threads)) == ("Critical", "Medium", "Low")


def test_bullets_before_any_open_section_are_not_open_findings():
    body = "\n".join(["- **High** early", "**Still open**", "- **Low** counted"])
    assert digest.still_open_severities(body) == ["Low"]


def test_a_withdrawn_list_closes_the_findings_and_a_later_open_section_reopens_them():
    body = "\n".join(
        [
            "**Still open**",
            "- **High** first",
            "Withdrawn as never defects:",
            "- **Critical** withdrawn",
            "**Still open**",
            "- **Low** again",
        ]
    )
    assert digest.still_open_severities(body) == ["High", "Low"]


def test_a_blank_line_does_not_end_the_findings():
    body = "\n".join(["**Still open**", "", "- **High** first", "", "- **Low** second"])
    assert digest.still_open_severities(body) == ["High", "Low"]


def test_withdrawn_items_run_through_blank_lines_until_other_text():
    body = "\n".join(["intro", "Withdrawn as never defects:", "", "- one", "", "* two", "text", "- not an item"])
    assert digest.withdrawn_in_body(body) == ["one", "two"]


def test_a_withdrawn_heading_may_carry_the_first_item_and_several_headings_add_up():
    body = "\n".join(
        ["**Withdrawn as never defects:** first", "- second", "x", "Withdrawn as never defects:", "- third"]
    )
    assert digest.withdrawn_in_body(body) == ["first", "second", "third"]


def test_a_withdrawn_list_may_end_the_body_and_none_body_has_none():
    assert digest.withdrawn_in_body("Withdrawn as never defects:\n- last") == ["last"]
    assert digest.withdrawn_in_body("Withdrawn as never defects:") == []
    assert digest.withdrawn_in_body(None) == []


def test_a_withdrawal_names_the_last_commit_and_lists_pulls_by_number():
    body = f"### oracle-verdict: failure for `{HEAD}`\n\nWithdrawn as never defects:\n- one\n- one\n- two\n"
    first = evidence(number=9, reviews=(oracle_review(body=body),))
    second = evidence(number=3, reviews=(oracle_review(body=body),))
    bare = evidence(number=4, commits=(), reviews=(oracle_review(body=body),))
    withdrawals = digest.withdrawn_findings([first, second])
    assert [(w.number, w.head, w.items) for w in withdrawals] == [(3, HEAD, ("one", "two")), (9, HEAD, ("one", "two"))]
    assert digest.withdrawn_findings([bare]) == []


def test_a_standin_with_an_oracle_success_review_is_not_an_override():
    pull = evidence(
        reviews=(oracle_review("success", PUSHED),),
        comments=(standin(HEAD, AFTER),),
        threads=(bot_login_thread("High", resolved=False),),
    )
    assert "oracle-review" in digest.review_signals(pull)
    assert digest.standin_overrides([pull]) == []


def test_overrides_are_found_past_pulls_that_are_not_overrides():
    plain = evidence(number=4)
    overridden = evidence(
        number=5, comments=(standin(HEAD, AFTER),), threads=(bot_login_thread("Medium", resolved=False),)
    )
    found = digest.standin_overrides([plain, overridden])
    assert [(o.number, o.severities, o.merged_at, o.attester, o.head) for o in found] == [
        (5, ("Medium",), MERGED_AT, "joeharris76", HEAD)
    ]


def test_an_override_with_unverifiable_evidence_lists_it_next_to_the_decision():
    flagged = review(
        ORACLE_BOT, HEAD, PUSHED, "COMMENTED", "Bot", f"### oracle-verdict: failure for `{HEAD}`", "edited"
    )
    pull = evidence(reviews=(flagged,), comments=(standin(HEAD, AFTER),))
    assert digest.standin_overrides([pull])[0].severities == (digest.UNVERIFIABLE_FINDING,)


def test_an_override_without_open_findings_or_blocking_verdict_is_not_listed():
    assert digest.standin_overrides([evidence(comments=(standin(HEAD, AFTER),))]) == []


def test_the_instruction_gap_is_measured_to_the_standin_post():
    instruction = digest.Comment("joeharris76", "Owner instruction: go", PUSHED, "User")
    pull = evidence(comments=(instruction, standin(HEAD, AFTER)), threads=(bot_login_thread("High", resolved=False),))
    assert digest.standin_overrides([pull])[0].instruction_gap_seconds == 3600


def test_the_unverifiable_overrides_of_a_standin_name_the_pull_in_the_rendering():
    lines = digest.render_unverifiable([evidence(threads=(bot_login_thread(unverifiable="edited"),))])
    assert "- #7: review thread: edited." in lines.splitlines()


def test_unreadable_pulls_are_named_by_number_or_by_abbreviated_commit():
    text = digest.render_unreadable([digest.UnreadablePull(None, HEAD, "x"), digest.UnreadablePull(4, HEAD, "y")])
    lines = text.splitlines()
    assert f"- `{HEAD[:9]}`: unreadable (x)." in lines
    assert "- #4: unreadable (y)." in lines
    assert digest.render_unreadable([]) == ""


def test_render_is_a_markdown_table_with_a_head_and_one_row_per_commit():
    reviewed = evidence(reviews=(review(CONNECTOR, HEAD),))
    entries = [classified("c" * 40, "reviewed", pull=reviewed), classified("d" * 40, "bare")]
    lines = digest.render(entries, since="1" * 40, until="2" * 40).split("\n")
    assert lines[0] == "Soundness-path commits on develop from 111111111 to 222222222: 2."
    assert lines[1] == ""
    assert lines[2] == "| Commit | Pull request | Review signal | Attention |"
    assert lines[3] == "|---|---|---|---|"
    assert lines[4] == "| `ccccccccc` reviewed | #7 | connector-review | - |"
    assert lines[5] == "| `ddddddddd` bare | none | none | no merged pull request |"
    assert len(lines) == 6


def test_an_empty_window_renders_the_header_a_blank_line_and_none():
    assert digest.render([], since="1" * 40, until="2" * 40).split("\n") == [
        "Soundness-path commits on develop from 111111111 to 222222222: 0.",
        "",
        "None.",
    ]


def test_a_signal_list_and_reason_list_are_joined_with_separators():
    pull = evidence(
        reviews=(review(CONNECTOR, HEAD),),
        reactions=(digest.Reaction(CONNECTOR, "+1", AFTER),),
        threads=(digest.Thread(True, CONNECTOR, HEAD),),
    )
    entry = classified("c" * 40, pull=pull)
    row = digest.render([entry], since="1" * 40, until="2" * 40).split("\n")[-1]
    assert (
        row
        == "| `ccccccccc` subject | #7 | connector-review, connector-approval | 1 reviewer thread(s) resolved with no later commit |"
    )
    entry = classified("c" * 40, pull=evidence(threads=(digest.Thread(True, CONNECTOR, HEAD),)))
    row = digest.render([entry], since="1" * 40, until="2" * 40).split("\n")[-1]
    assert row.endswith(f"| none | {digest.REASON_NO_REVIEW}; 1 reviewer thread(s) resolved with no later commit |")


def test_the_gap_issue_body_states_marker_commit_reason_classes_files_and_the_close_forms():
    entry = classified(HEAD, files=("AGENTS.md", "f.py"))
    lines = digest.gap_issue_body(entry).split("\n")
    assert lines[0] == f"<!-- soundness-gap:{HEAD} -->"
    assert lines[1].startswith(f"Commit `{HEAD}` (no pull request) changed paths")
    assert lines[3] == "Why it is listed: no merged pull request."
    assert lines[5] == "Reason class: no review signal."
    assert lines[7:10] == ["Soundness-path files:", "- `AGENTS.md`", "- `f.py`"]
    assert lines[11].startswith("Next step: run an external adversarial review")
    for form in (
        "External review: codex",
        "External review: muse",
        "External review: agy",
        "Verified clean",
        "**Severity**:",
    ):
        assert f"`{form}`" in lines[11]
    assert lines[11].endswith("only by an attester's review.")
    assert len(lines) == 12


def test_the_gap_issue_body_names_the_pull_number_when_there_is_one():
    entry = classified(HEAD, pull=evidence(number=2915))
    assert f"Commit `{HEAD}` (#2915) changed paths" in digest.gap_issue_body(entry)


def test_the_standin_override_table_lists_each_override_and_the_missing_instruction():
    override = digest.StandinOverride(7, HEAD, "joeharris76", None, ("High", "Low"), MERGED_AT)
    lines = digest.render_standin_overrides([override]).split("\n")
    assert lines[0] == ""
    assert lines[1].startswith("Stand-in overrides:")
    assert lines[2] == "| Pull request | Head | Attester | Instruction gap | Open findings |"
    assert lines[3] == "|---|---|---|---|---|"
    assert lines[4] == f"| #7 | `{HEAD[:9]}` | joeharris76 | no Owner instruction comment | High, Low |"
    assert len(lines) == 5
    assert digest.render_standin_overrides([]).split("\n")[2:] == ["None."]


def test_the_red_arming_section_is_a_blank_a_heading_and_one_line_per_arming():
    arming = digest.RedArming(7, ARMED, "failure", 9, "ddddddddd", "2026-10-21T08:00:00Z")
    unknown = digest.UnknownArming(8, ARMED, "no history")
    lines = digest.render_red_armings([arming], [unknown]).split("\n")
    assert lines[0] == ""
    assert lines[1].startswith("Armed while Trunk was red:")
    assert lines[2].startswith(f"- #7 armed at {ARMED}")
    assert "(run 9, `ddddddddd`, completed 2026-10-21T08:00:00Z)" in lines[2]
    assert lines[3] == f"- #8 armed at {ARMED}; trunk state unknown (no history)."
    assert len(lines) == 4
    assert digest.render_red_armings([]).split("\n")[2:] == ["None."]
    assert digest.render_red_armings([], [unknown]).split("\n")[2:] == [lines[3]]


def test_the_withdrawal_section_lists_each_item_under_its_pull():
    withdrawal = digest.Withdrawal(7, HEAD, ("one", "two"))
    lines = digest.render_withdrawals([withdrawal]).split("\n")
    assert lines[0] == ""
    assert lines[1].startswith("Oracle withdrawals:")
    assert lines[2:] == [f"- #7 (`{HEAD[:9]}`):", "  - one", "  - two"]


def test_no_withdrawals_render_nothing():
    assert digest.render_withdrawals([]) == ""


def test_the_closure_list_is_headed_and_one_line_per_issue():
    assert digest.render_closures([]).split("\n") == ["", "Gap issues to close:", "None."]
    closures = [digest.Closure(30, HEAD, "first", "c"), digest.Closure(31, OLD, "second", "c")]
    assert digest.render_closures(closures).split("\n") == [
        "",
        "Gap issues to close:",
        f"- #30 (`{HEAD[:9]}`): first",
        f"- #31 (`{OLD[:9]}`): second",
    ]


def test_a_closure_comment_names_the_commit_and_pull_of_the_gap():
    entry = classified(HEAD, pull=evidence(number=12, comments=(standin(),)))
    comment = digest.resolved_closure(30, entry).comment
    assert f"`{HEAD}` (#12)" in comment
    assert comment.splitlines()[0] == f"<!-- soundness-gap-closed:{HEAD} -->"


def test_the_default_options_name_the_repository_and_the_integration_branch():
    args = digest._parse_args([])
    assert (args.repo, args.ref, args.since, args.apply, args.bootstrap) == (
        "BenchBox-dev/BenchBox",
        "origin/develop",
        None,
        False,
        False,
    )


def test_every_option_is_read():
    args = digest._parse_args(["--repo", "a/b", "--ref", "r", "--since", "s", "--apply", "--bootstrap"])
    assert (args.repo, args.ref, args.since, args.apply, args.bootstrap) == ("a/b", "r", "s", True, True)


class Wired:
    def __init__(self, monkeypatch):
        self.log = []
        self.git = {
            ("git", "rev-parse", "origin/develop"): HEAD + "\n",
            ("git", "rev-parse", "release"): MERGED + "\n",
            ("git", "rev-parse", "--verify", f"{SINCE}^{{commit}}"): SINCE + "\n",
            ("git", "rev-parse", "--verify", f"{OLD}^{{commit}}"): OLD + "\n",
            ("git", "rev-list", "--first-parent", HEAD): f"{HEAD}\n{SINCE}\n",
            ("git", "rev-list", "--first-parent", MERGED): f"{MERGED}\n{OLD}\n",
        }
        self.checkpoint = SINCE
        self.entries = [classified("d" * 40, "unreviewed", pull=evidence(number=8))]
        self.window = ([], [])
        self.trunk_runs = []
        self.closures = []
        self.overrides = None
        self.red = None
        self.withdrawals = None
        monkeypatch.setattr(digest, "run", self.run)
        monkeypatch.setattr(digest, "read_checkpoint", self.read_checkpoint)
        monkeypatch.setattr(digest, "collect_entries", self.collect_entries)
        monkeypatch.setattr(digest, "collect_window_pulls", self.collect_window_pulls)
        monkeypatch.setattr(digest, "fetch_trunk_runs", self.fetch_trunk_runs)
        monkeypatch.setattr(digest, "gap_closures", self.gap_closures)
        monkeypatch.setattr(digest, "open_gap_issues", self.open_gap_issues)
        monkeypatch.setattr(digest, "close_gap_issues", self.close_gap_issues)
        monkeypatch.setattr(digest, "write_checkpoint", self.write_checkpoint)
        for name in ("overrides", "red", "withdrawals"):
            monkeypatch.setattr(digest, self.hooks[name], self.make_hook(name))

    hooks = {"overrides": "standin_overrides", "red": "red_armings", "withdrawals": "withdrawn_findings"}

    def make_hook(self, name):
        real = getattr(digest, self.hooks[name])

        def hook(*args):
            self.log.append((self.hooks[name], args))
            outcome = getattr(self, name)
            if isinstance(outcome, Exception):
                raise outcome
            return real(*args) if outcome is None else outcome

        return hook

    def run(self, args, **_):
        key = tuple(args)
        if key not in self.git:
            raise AssertionError(f"unexpected command {list(key)}")
        return self.git[key]

    def read_checkpoint(self, repo):
        self.log.append(("read_checkpoint", (repo,)))
        return self.checkpoint

    def collect_entries(self, repo, ref, since):
        self.log.append(("collect_entries", (repo, ref, since)))
        if isinstance(self.entries, Exception):
            raise self.entries
        return self.entries

    def collect_window_pulls(self, repo, ref, since):
        self.log.append(("collect_window_pulls", (repo, ref, since)))
        if isinstance(self.window, Exception):
            raise self.window
        return self.window

    def fetch_trunk_runs(self, repo):
        self.log.append(("fetch_trunk_runs", (repo,)))
        return self.trunk_runs

    def gap_closures(self, repo, entries):
        self.log.append(("gap_closures", (repo, entries)))
        return self.closures

    def open_gap_issues(self, repo, entries):
        self.log.append(("open_gap_issues", (repo, entries)))
        return [entry.sha for entry in entries]

    def close_gap_issues(self, repo, closures):
        self.log.append(("close_gap_issues", (repo, closures)))

    def write_checkpoint(self, repo, sha, report):
        self.log.append(("write_checkpoint", (repo, sha, report)))

    def calls(self, name):
        return [args for logged, args in self.log if logged == name]

    @property
    def order(self):
        return [name for name, _ in self.log]


@pytest.fixture
def wired(monkeypatch):
    return Wired(monkeypatch)


def test_a_default_dry_run_reads_the_default_repository_and_branch_and_changes_nothing(wired, capsys):
    assert digest.main([]) == 0
    repo = "BenchBox-dev/BenchBox"
    assert wired.calls("read_checkpoint") == [(repo,)]
    assert wired.calls("collect_entries") == [(repo, HEAD, SINCE)]
    assert wired.calls("collect_window_pulls") == [(repo, HEAD, SINCE)]
    assert wired.calls("fetch_trunk_runs") == [(repo,)]
    assert wired.calls("gap_closures") == [(repo, wired.entries)]
    assert wired.calls("open_gap_issues") == wired.calls("close_gap_issues") == wired.calls("write_checkpoint") == []
    out = capsys.readouterr().out
    assert out.startswith("Soundness-path commits on develop from 111111111 to aaaaaaaaa: 1.")
    assert out.rstrip().endswith("Gap issues to close:\nNone.")
    assert "Opened" not in out


def test_an_apply_run_opens_then_closes_then_records_the_checkpoint_with_the_report(wired, capsys):
    wired.closures = [digest.Closure(30, SINCE, "summary", "comment")]
    assert digest.main(["--apply", "--repo", "o/r", "--ref", "release", "--since", OLD]) == 0
    assert wired.order[-3:] == ["open_gap_issues", "close_gap_issues", "write_checkpoint"]
    assert wired.calls("read_checkpoint") == []
    assert wired.calls("collect_entries") == [("o/r", MERGED, OLD)]
    assert wired.calls("open_gap_issues") == [("o/r", wired.entries)]
    assert wired.calls("close_gap_issues") == [("o/r", wired.closures)]
    ((repo, sha, report),) = wired.calls("write_checkpoint")
    assert (repo, sha) == ("o/r", MERGED)
    out = capsys.readouterr().out
    assert out.startswith(report + "\n")
    assert report.startswith(f"Soundness-path commits on develop from {OLD[:9]} to {MERGED[:9]}: 1.")
    assert "- #30 (`111111111`): summary" in out
    assert out.rstrip().endswith(f"Opened 1 issue(s), closed 1; checkpoint is {MERGED[:9]}.")


def test_bootstrap_records_the_head_for_the_repository_asked(wired, capsys):
    wired.checkpoint = None
    assert digest.main(["--apply", "--bootstrap", "--repo", "o/r"]) == 0
    assert wired.calls("read_checkpoint") == [("o/r",)]
    ((repo, sha, report),) = wired.calls("write_checkpoint")
    assert (repo, sha) == ("o/r", HEAD)
    assert isinstance(report, str) and report
    assert capsys.readouterr().out == f"Recorded checkpoint {HEAD[:9]}.\n"
    assert wired.calls("collect_entries") == []


def test_bootstrap_without_apply_says_so_on_stderr(wired, capsys):
    assert digest.main(["--bootstrap"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--apply" in captured.err
    assert wired.calls("write_checkpoint") == []


def test_bootstrap_over_a_stored_checkpoint_says_so_on_stderr(wired, capsys):
    assert digest.main(["--apply", "--bootstrap"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "checkpoint" in captured.err
    assert wired.calls("read_checkpoint") == [("BenchBox-dev/BenchBox",)]
    assert wired.calls("write_checkpoint") == []


def test_a_run_with_no_checkpoint_and_no_since_says_so_on_stderr(wired, capsys):
    wired.checkpoint = None
    assert digest.main(["--apply"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--since" in captured.err
    assert wired.calls("write_checkpoint") == wired.calls("collect_entries") == []


def test_a_checkpoint_off_the_first_parent_history_names_the_commit_and_the_ref(wired, capsys):
    wired.checkpoint = OLD
    assert digest.main(["--apply", "--ref", "origin/develop"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert OLD[:9] in captured.err and OLD[:10] not in captured.err
    assert "origin/develop" in captured.err
    assert wired.calls("write_checkpoint") == wired.calls("collect_entries") == []


def test_a_failure_is_reported_on_stderr_and_not_stdout(wired, capsys):
    wired.entries = digest.ReadError("listing gone")
    assert digest.main(["--apply"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "listing gone" in captured.err
    assert wired.calls("write_checkpoint") == []


UNREADABLE_WINDOW = "The window's pull requests were unreadable: window gone."


@pytest.mark.parametrize("error", [ReadError("x"), KeyError("x"), TypeError("x"), ValueError("x")])
def test_any_read_failure_of_the_window_is_survived(wired, capsys, error):
    wired.window = error
    assert digest.main([]) == 0
    assert "were unreadable" in capsys.readouterr().out


def override_item():
    return digest.StandinOverride(7, HEAD, "joeharris76", 5, ("High",), MERGED_AT)


def red_item():
    return digest.RedArming(7, ARMED, "failure", 9, "ddddddddd", "2026-10-21T08:00:00Z")


def unknown_item():
    return digest.UnknownArming(7, ARMED, "no history")


def withdrawal_item():
    return digest.Withdrawal(7, HEAD, ("one",))


def test_the_window_pulls_and_trunk_runs_feed_the_supplements(wired, capsys):
    pulls = [evidence(number=8, armed_at=(ARMED,))]
    wired.window = (pulls, [])
    wired.trunk_runs = [trunk_run(3)]
    assert digest.main([]) == 0
    assert wired.calls("standin_overrides") == [(pulls,)]
    assert wired.calls("red_armings") == [("BenchBox-dev/BenchBox", pulls, wired.trunk_runs)]
    assert wired.calls("withdrawn_findings") == [(pulls,)]
    out = capsys.readouterr().out
    assert "- #8 armed at" in out
    assert "(run 3," in out


def test_each_supplement_section_lands_in_the_report_in_order(wired, capsys):
    wired.window = ([evidence()], [digest.UnreadablePull(9, HEAD, "bad")])
    wired.overrides = [override_item()]
    wired.red = ([red_item()], [unknown_item()])
    wired.withdrawals = [withdrawal_item()]
    assert digest.main([]) == 0
    out = capsys.readouterr().out
    markers = [
        "Soundness-path commits on develop",
        "Unreadable pull requests:",
        "Stand-in overrides:",
        "Armed while Trunk was red:",
        "Oracle withdrawals:",
        "Gap issues to close:",
    ]
    positions = [out.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert "- #7 armed at" in out
    assert "trunk state unknown (no history)" in out
    assert "  - one" in out


def test_unverifiable_evidence_is_listed_ahead_of_the_overrides(wired, capsys):
    wired.window = ([evidence(threads=(bot_login_thread(unverifiable="edited"),))], [])
    assert digest.main([]) == 0
    out = capsys.readouterr().out
    assert out.index("- #7: review thread: edited.") < out.index("Stand-in overrides:")


@pytest.mark.parametrize("failing", ["overrides", "red", "withdrawals"])
def test_a_failing_supplement_is_reported_and_the_others_still_land(wired, capsys, failing):
    wired.window = ([evidence()], [])
    wired.overrides = [override_item()]
    wired.red = ([red_item()], [])
    wired.withdrawals = [withdrawal_item()]
    setattr(wired, failing, ReadError("section broke"))
    assert digest.main([]) == 0
    out = capsys.readouterr().out
    unavailable = {
        "overrides": "Stand-in overrides are unavailable for this window: section broke.",
        "red": "Red-trunk armings are unavailable for this window: section broke.",
        "withdrawals": "Oracle withdrawals are unavailable for this window: section broke.",
    }
    present = {
        "overrides": "| #7 | `aaaaaaaaa` | joeharris76 | 5s | High |",
        "red": "- #7 armed at",
        "withdrawals": "- #7 (`aaaaaaaaa`):",
    }
    for name, text in unavailable.items():
        assert (text in out) is (name == failing)
    for name, text in present.items():
        assert (text in out) is (name != failing)


@pytest.mark.parametrize("withdrawn", [False, True])
@pytest.mark.parametrize("red_state", ["none", "red", "unknown"])
@pytest.mark.parametrize("overridden", [False, True])
def test_the_coverage_note_appears_only_while_some_section_reads_none(wired, capsys, overridden, red_state, withdrawn):
    wired.window = ([evidence()], [digest.UnreadablePull(9, HEAD, "bad")])
    wired.overrides = [override_item()] if overridden else []
    wired.red = ([red_item()] if red_state == "red" else [], [unknown_item()] if red_state == "unknown" else [])
    wired.withdrawals = [withdrawal_item()] if withdrawn else []
    assert digest.main([]) == 0
    expected = (not overridden) or red_state == "none" or (not withdrawn)
    assert ("Coverage:" in capsys.readouterr().out) is expected


def test_no_coverage_note_without_unreadable_pulls(wired, capsys):
    wired.window = ([evidence()], [])
    assert digest.main([]) == 0
    assert "Coverage:" not in capsys.readouterr().out


def edit_node(node_id):
    return {"id": node_id, "lastEditedAt": None, "includesCreatedEdit": False}


def test_review_edit_states_are_read_in_batches_of_unique_node_ids(shell):
    from oracle_reviewers import edits

    seen = []

    def graphql(command):
        seen.append(command)
        ids = [item.removeprefix("ids[]=") for item in command if item.startswith("ids[]=")]
        return {"data": {"nodes": [edit_node(node_id) for node_id in ids]}}

    shell.graphql = graphql
    node_ids = ["PRR_2", "PRR_1", "PRR_2"] + [f"N_{i}" for i in range(100)]
    states = digest._graphql_review_edits(node_ids)
    assert set(states) == set(dict.fromkeys(node_ids))
    assert all(state == (None, False) for state in states.values())
    assert len(seen) == 2
    head = ("gh", "api", "graphql", "-f", f"query={edits.REVIEW_EDITS_QUERY}")
    assert seen[0][:5] == head
    assert seen[0][5:9] == ("-f", "ids[]=PRR_2", "-f", "ids[]=PRR_1")
    assert [item for item in seen[0][5:] if item != "-f"] == [
        f"ids[]={node_id}" for node_id in list(dict.fromkeys(node_ids))[:100]
    ]
    assert seen[1] == head + ("-f", "ids[]=N_98", "-f", "ids[]=N_99")


def bare_app_review(state="COMMENTED", body="", node_id="", login=ORACLE_BOT, user_type="Bot"):
    return digest.Review(login, HEAD, AFTER, state, user_type, body, node_id)


@pytest.mark.parametrize(
    "candidate",
    [
        bare_app_review(login="someone[bot]"),
        bare_app_review(user_type="User"),
        bare_app_review(state="PENDING"),
        bare_app_review(state="DISMISSED"),
    ],
    ids=["other-bot", "app-as-user", "pending", "dismissed"],
)
def test_only_a_submitted_review_by_the_app_bot_is_checked_for_edits(candidate):
    assert digest.verify_oracle_reviews("o/r", [candidate]) == (candidate,)


def test_a_review_with_no_node_id_cannot_have_its_edit_state_read():
    (checked,) = digest.verify_oracle_reviews("o/r", [bare_app_review()])
    assert "no node id" in checked.unverifiable


def test_an_edited_review_does_not_stop_the_checks_of_the_reviews_after_it(monkeypatch):
    serve_status = [
        {
            "id": 1,
            "context": "oracle-verdict",
            "state": "failure",
            "description": "d",
            "creator": {"login": ORACLE_BOT, "type": "Bot"},
            "created_at": AFTER,
        }
    ]
    monkeypatch.setattr(digest, "_graphql_review_edits", lambda ids: {"PRR_1": (LATER, False), "PRR_2": (None, False)})
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: serve_status)
    from oracle_reviewers import protocol

    def body(decision, state):
        record = {
            "v": 1,
            "cycle": 1,
            "round": 1,
            "kind": "first",
            "decision": decision,
            "head_sha": HEAD,
            "base_ref": "develop",
            "reviewer": "small",
            "tier": "small",
            "strikes_after": 0,
            "open_defects": [],
            "next_id": 1,
            "patch_digest": "d" * 32,
            "carried": False,
            "summary": "",
        }
        return f"### oracle-verdict: {state} for `{HEAD}`\n\nText.\n\n" + protocol.encode_marker(record)

    edited = bare_app_review(body=body("SHIP", "success"), node_id="PRR_1")
    disagreeing = bare_app_review(body=body("SHIP", "success"), node_id="PRR_2")
    user = bare_app_review(login="joe", user_type="User")
    first, second, third = digest.verify_oracle_reviews("o/r", [edited, disagreeing, user])
    assert "edited" in first.unverifiable
    assert "failure status" in second.unverifiable
    assert third.unverifiable == ""


def test_a_ship_review_with_a_run_id_no_status_carries_is_not_matched_to_a_bare_status(monkeypatch):
    from oracle_reviewers import protocol

    record = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": "SHIP",
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "small",
        "tier": "small",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
        "run_uuid": "2" * 32,
    }
    body = f"### oracle-verdict: success for `{HEAD}`\n\nText.\n\n" + protocol.encode_marker(record)
    status = {
        "id": 1,
        "context": "oracle-verdict",
        "state": "success",
        "description": "bare",
        "creator": {"login": ORACLE_BOT, "type": "Bot"},
        "created_at": AFTER,
    }
    monkeypatch.setattr(digest, "_graphql_review_edits", lambda ids: {"PRR_1": (None, False)})
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: [status])
    (checked,) = digest.verify_oracle_reviews("o/r", [bare_app_review(body=body, node_id="PRR_1")])
    assert "no oracle-verdict status" in checked.unverifiable


def test_an_oracle_thread_posted_by_the_app_bot_account_is_checked_for_edits(monkeypatch):
    node = thread_node(False, ORACLE_BOT, HEAD)
    node["comments"]["nodes"][0]["lastEditedAt"] = LATER
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads([node]))
    (thread,) = digest.collect_threads("o/r", 7)
    assert thread.severity == "" and "edited" in thread.unverifiable


def test_an_oracle_review_at_the_retarget_instant_or_at_the_merge_instant():
    verdict = f"### oracle-verdict: failure for `{HEAD}`"
    at_retarget = evidence(base_changed_at=AFTER, reviews=(oracle_review(at=AFTER, body=verdict),))
    at_merge = evidence(reviews=(oracle_review(at=MERGED_AT, body=verdict),))
    assert digest._oracle_reviews(at_retarget, {HEAD}) == []
    assert len(digest._oracle_reviews(at_merge, {HEAD})) == 1


def three_commit_pull(**fields):
    commits = (commit(OLD, BEFORE), commit("c" * 40, PUSHED), commit(HEAD, PUSHED))
    return evidence(commits=commits, **fields)


def test_an_override_names_the_last_commit_of_the_pull():
    pull = three_commit_pull(comments=(standin(HEAD, AFTER),), threads=(bot_login_thread("Medium", resolved=False),))
    assert digest.standin_overrides([pull])[0].head == HEAD


def test_a_withdrawal_names_the_last_commit_of_the_pull():
    body = f"### oracle-verdict: failure for `{HEAD}`\n\nWithdrawn as never defects:\n- one\n"
    pull = three_commit_pull(reviews=(oracle_review(body=body),))
    assert digest.withdrawn_findings([pull])[0].head == HEAD


def test_overrides_are_listed_by_merge_time_then_pull_number_and_skip_pulls_without_findings():
    def overridden(number, merged_at):
        return evidence(
            number=number,
            merged_at=merged_at,
            comments=(standin(HEAD, AFTER),),
            threads=(bot_login_thread("Low", resolved=False),),
        )

    clean = evidence(number=1, comments=(standin(HEAD, AFTER),))
    pulls = [overridden(9, MERGED_AT), clean, overridden(4, MERGED_AT), overridden(8, "2026-10-02T11:30:00Z")]
    assert [o.number for o in digest.standin_overrides(pulls)] == [8, 4, 9]


def test_a_pull_merge_base_is_the_commit_before_the_merge_commit(shell, monkeypatch):
    shell.pages(
        f"repos/o/r/commits/{MERGED}/pulls",
        [{"number": 7, "merge_commit_sha": MERGED, "merged_at": AFTER, "base": {"ref": "develop"}}],
    )
    shell.api("repos/o/r/pulls/7", pull_payload(commits=2))
    shell.pages("repos/o/r/pulls/7/commits", [api_commit(OLD, BEFORE, 2), api_commit(HEAD, PUSHED)])
    shell.reply("git", "fetch", "--no-tags", "origin", "refs/pull/7/head")
    shell.reply("git", "rev-parse", "FETCH_HEAD", text=HEAD + "\n")
    shell.api(f"repos/o/r/actions/runs?head_sha={HEAD}&per_page=100", {"workflow_runs": []})
    for tail in ("timeline", "reactions", "comments"):
        shell.pages(f"repos/o/r/issues/7/{tail}")
    shell.pages("repos/o/r/pulls/7/reviews")
    seen = []
    monkeypatch.setattr(digest, "merge_adds_content", lambda sha, base, cwd=None: seen.append((sha, base)) or False)
    verified = []
    monkeypatch.setattr(digest, "verify_oracle_reviews", lambda repo, reviews: verified.append(repo) or tuple(reviews))
    queries = []

    def graphql(command):
        queries.append(command)
        return graphql_threads([])

    shell.graphql = graphql
    digest.collect_pull("o/r", MERGED)
    assert seen == [(OLD, f"{MERGED}~1")]
    assert verified == ["o/r"]
    assert "p=7" in queries[0]


def test_the_predicate_is_evaluated_against_the_parent_manifest_in_the_given_checkout(shell):
    checkout = Path("/checkout")
    seen = {}
    shell.reply("git", "show", f"{HEAD}~1:.github/soundness-paths.txt", text="MANIFEST")
    shell.reply("git", "show", f"{HEAD}~1:_project/scripts/soundness_paths.py", text="PREDICATE")

    def driver(command, input_text, cwd):
        scripts = Path(command[3])
        seen["scripts"] = scripts.parts[-2:]
        seen["manifest"] = (scripts.parents[1] / ".github/soundness-paths.txt").read_text(encoding="utf-8")
        seen["predicate"] = (scripts / "soundness_paths.py").read_text(encoding="utf-8")
        seen["input"] = json.loads(input_text)
        seen["cwd"] = cwd
        return json.dumps(["b"])

    shell.handlers.append(((sys.executable, "-c", digest.PREDICATE_DRIVER), driver))
    assert digest.soundness_files(HEAD, ["a", "b"], cwd=checkout) == ["b"]
    assert seen == {
        "scripts": ("_project", "scripts"),
        "manifest": "MANIFEST",
        "predicate": "PREDICATE",
        "input": ["a", "b"],
        "cwd": checkout,
    }
    assert {cwd for key, cwd in shell.calls if key[0] == "git"} == {checkout}


@pytest.mark.parametrize("failing", ["overrides", "red", "withdrawals"])
def test_a_failing_supplement_keeps_the_unreadable_pull_notice(wired, capsys, failing):
    wired.window = ([evidence()], [digest.UnreadablePull(9, HEAD, "bad")])
    setattr(wired, failing, ReadError("section broke"))
    wired.overrides = wired.overrides if failing == "overrides" else [override_item()]
    assert digest.main([]) == 0
    assert "Unreadable pull requests:" in capsys.readouterr().out


def marked_body(decision, state, header=True):
    from oracle_reviewers import protocol

    record = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": decision,
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "small",
        "tier": "small",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
    }
    head = f"### oracle-verdict: {state} for `{HEAD}`\n\n" if header else "Free text.\n\n"
    return head + protocol.encode_marker(record)


def serve_app_status(monkeypatch, state):
    status = {
        "id": 1,
        "context": "oracle-verdict",
        "state": state,
        "description": "d",
        "creator": {"login": ORACLE_BOT, "type": "Bot"},
        "created_at": AFTER,
    }
    monkeypatch.setattr(digest, "_graphql_review_edits", lambda ids: {"PRR_1": (None, False)})
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: [status])


def test_a_marker_decision_contradicting_the_header_is_unreadable_even_when_the_status_matches_the_decision(
    monkeypatch,
):
    serve_app_status(monkeypatch, "success")
    review_ = bare_app_review(body=marked_body("SHIP", "failure"), node_id="PRR_1")
    (checked,) = digest.verify_oracle_reviews("o/r", [review_])
    assert "cannot be read" in checked.unverifiable


@pytest.mark.parametrize("status", ["success", "failure"])
def test_a_review_with_neither_marker_nor_header_is_unreadable_whatever_the_status_says(monkeypatch, status):
    serve_app_status(monkeypatch, status)
    review_ = bare_app_review(body="Free text, no verdict.", node_id="PRR_1")
    (checked,) = digest.verify_oracle_reviews("o/r", [review_])
    assert "cannot be read" in checked.unverifiable


def test_a_marker_with_no_header_agreeing_with_the_status_verifies(monkeypatch):
    serve_app_status(monkeypatch, "success")
    review_ = bare_app_review(body=marked_body("SHIP", "success", header=False), node_id="PRR_1")
    (checked,) = digest.verify_oracle_reviews("o/r", [review_])
    assert checked.unverifiable == ""
