from __future__ import annotations

import importlib
import os
import subprocess
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
REFRESH = "c" * 40
MERGED = "e" * 40
PUSHED = "2026-10-02T10:00:00Z"
BEFORE = "2026-10-02T09:00:00Z"
AFTER = "2026-10-02T11:00:00Z"
MERGED_AT = "2026-10-02T12:00:00Z"
LATER = "2026-10-02T13:00:00Z"
AFTER_REFRESH = "2026-10-02T11:30:00Z"
CONNECTOR = "chatgpt-codex-connector[bot]"


def commit(sha, arrived_at, refresh=False):
    return digest.PullCommit(sha, arrived_at, refresh)


def review(login, sha, submitted_at=AFTER, state="COMMENTED", user_type="", body=""):
    return digest.Review(login, sha, submitted_at, state, user_type, body)


def evidence(**overrides):
    fields = {
        "number": 7,
        "author": "dev",
        "merged_at": MERGED_AT,
        "commits": (commit(OLD, BEFORE), commit(HEAD, PUSHED)),
    }
    fields.update(overrides)
    return digest.PullEvidence(**fields)


def refreshed(**overrides):
    commits = (commit(OLD, BEFORE), commit(HEAD, PUSHED), commit(REFRESH, AFTER, refresh=True))
    return evidence(commits=commits, **overrides)


def test_connector_review_of_the_last_content_commit_is_a_signal():
    pull = evidence(reviews=(review(CONNECTOR, HEAD),))
    assert digest.review_signals(pull) == ("connector-review",)


def test_connector_review_of_an_earlier_commit_is_not_a_signal():
    pull = evidence(reviews=(review(CONNECTOR, OLD),))
    assert digest.review_signals(pull) == ()


def test_a_connector_review_stays_valid_after_a_refresh_merge():
    pull = refreshed(reviews=(review(CONNECTOR, HEAD),))
    assert digest.review_signals(pull) == ("connector-review",)


def test_a_pending_connector_review_is_not_a_signal():
    pull = evidence(reviews=(review(CONNECTOR, HEAD, state="PENDING"),))
    assert digest.review_signals(pull) == ()


def test_signals_that_arrive_after_the_merge_are_not_counted():
    pull = evidence(
        reviews=(review(CONNECTOR, HEAD, submitted_at=LATER),),
        reactions=(digest.Reaction(CONNECTOR, "+1", LATER),),
        comments=(digest.Comment("dev", "Reviewer: codex", LATER),),
    )
    assert digest.review_signals(pull) == ()


def test_a_connector_review_from_another_account_is_not_a_signal():
    pull = evidence(reviews=(review("dev", HEAD),))
    assert digest.review_signals(pull) == ()


def test_connector_approval_must_follow_the_last_content_commit():
    stale = evidence(reactions=(digest.Reaction(CONNECTOR, "+1", BEFORE),))
    fresh = evidence(reactions=(digest.Reaction(CONNECTOR, "+1", AFTER),))
    assert digest.review_signals(stale) == ()
    assert digest.review_signals(fresh) == ("connector-approval",)


def test_a_refresh_merge_after_the_approval_keeps_the_signal():
    pull = refreshed(reactions=(digest.Reaction(CONNECTOR, "+1", "2026-10-02T10:30:00Z"),))
    assert digest.review_signals(pull) == ("connector-approval",)


def test_a_content_commit_after_the_signals_drops_them():
    pull = evidence(
        commits=(commit(OLD, BEFORE), commit(HEAD, AFTER)),
        reviews=(review(CONNECTOR, OLD),),
        reactions=(digest.Reaction(CONNECTOR, "+1", PUSHED),),
        comments=(digest.Comment("dev", "Reviewer: codex", PUSHED),),
    )
    assert digest.review_signals(pull) == ()


def test_an_eyes_reaction_alone_is_not_a_signal():
    pull = evidence(reactions=(digest.Reaction(CONNECTOR, "eyes", AFTER),))
    assert digest.review_signals(pull) == ()


def test_a_thumbs_up_from_anyone_else_is_not_a_signal():
    pull = evidence(reactions=(digest.Reaction("dev", "+1", AFTER),))
    assert digest.review_signals(pull) == ()


def test_a_posted_external_review_after_the_last_content_commit_is_a_signal():
    pull = evidence(comments=(digest.Comment("joeharris76", "Reviewer: Agy\n\nNo findings.", AFTER, "User"),))
    assert digest.review_signals(pull) == ("external-review:agy",)


def test_a_posted_review_from_before_the_last_content_commit_is_not_a_signal():
    pull = evidence(comments=(digest.Comment("joeharris76", "Reviewer: codex", BEFORE, "User"),))
    assert digest.review_signals(pull) == ()


@pytest.mark.parametrize(
    "body",
    [
        "**Reviewer:** Codex CLI, read-only",
        "- Reviewer: muse",
        "Soundness review: external agy, plan mode",
        "External reviewer: codex",
    ],
)
def test_common_review_headings_are_recognised(body):
    pull = evidence(comments=(digest.Comment("joeharris76", body, AFTER, "User"),))
    assert len(digest.review_signals(pull)) == 1


def test_a_comment_naming_an_unapproved_reviewer_is_not_a_signal():
    pull = evidence(comments=(digest.Comment("joeharris76", "Reviewer: my own judgement", AFTER, "User"),))
    assert digest.review_signals(pull) == ()


@pytest.mark.parametrize(
    "comment",
    [
        digest.Comment("dev", "Reviewer: codex", AFTER, "User"),
        digest.Comment("joeharris76", "Reviewer: codex", AFTER, "Bot"),
        digest.Comment("joeharris76", "Reviewer: codex", AFTER),
    ],
    ids=["other-author", "bot-account", "no-type"],
)
def test_an_external_review_counts_only_from_a_listed_attester(comment):
    assert digest.review_signals(evidence(comments=(comment,))) == ()


def standin(sha, login="joeharris76", at=AFTER, prefix="", user_type="User", updated_at=None):
    return digest.Comment(login, f"{prefix}Stand-in oracle review: APPROVE {sha}", at, user_type, updated_at)


def test_a_standin_approval_of_the_merged_head_from_a_listed_attester_is_a_signal():
    assert digest.review_signals(evidence(comments=(standin(HEAD),))) == ("stand-in",)


def test_an_unedited_standin_with_a_matching_updated_at_is_a_signal():
    assert digest.review_signals(evidence(comments=(standin(HEAD, updated_at=AFTER),))) == ("stand-in",)


def test_a_standin_approval_must_name_the_merged_head_after_a_refresh_merge():
    assert digest.review_signals(refreshed(comments=(standin(REFRESH, at=AFTER_REFRESH),))) == ("stand-in",)
    assert digest.review_signals(refreshed(comments=(standin(HEAD, at=AFTER_REFRESH),))) == ()


def test_a_standin_approval_is_not_timed_against_the_head_arrival():
    assert digest.review_signals(evidence(comments=(standin(HEAD, at=PUSHED),))) == ("stand-in",)


def test_a_standin_approval_posted_before_a_retarget_is_not_a_signal():
    comments = (standin(HEAD, at=AFTER),)
    assert digest.review_signals(evidence(comments=comments, base_changed_at=AFTER)) == ()
    assert digest.review_signals(evidence(comments=comments, base_changed_at=PUSHED)) == ("stand-in",)


def test_a_standin_approval_posted_after_the_merge_is_not_a_signal():
    assert digest.review_signals(evidence(comments=(standin(HEAD, at=LATER),))) == ()


@pytest.mark.parametrize(
    "comment",
    [
        standin(OLD),
        standin(HEAD, login="dev"),
        digest.Comment("joeharris76", f"```\nStand-in oracle review: APPROVE {HEAD}\n```", AFTER, "User"),
        standin(HEAD, prefix="Looks fine. "),
        standin(HEAD, user_type="Bot"),
        standin(HEAD, user_type=""),
        standin(HEAD, updated_at=LATER),
    ],
    ids=["wrong-sha", "non-attester", "fenced", "mid-line", "bot", "no-type", "edited"],
)
def test_a_standin_approval_that_does_not_qualify_is_not_a_signal(comment):
    assert digest.review_signals(evidence(comments=(comment,))) == ()


def test_a_thread_started_before_a_refresh_merge_is_still_counted():
    threads = (digest.Thread(resolved=True, started_by=CONNECTOR, first_comment_sha=HEAD),)
    assert digest.unreviewed_thread_count(refreshed(threads=threads)) == 1


def test_a_reviewer_thread_resolved_on_the_final_content_is_counted():
    threads = (
        digest.Thread(resolved=True, started_by=CONNECTOR, first_comment_sha=HEAD),
        digest.Thread(resolved=True, started_by=CONNECTOR, first_comment_sha=OLD),
        digest.Thread(resolved=False, started_by=CONNECTOR, first_comment_sha=HEAD),
        digest.Thread(resolved=True, started_by="dev", first_comment_sha=HEAD),
    )
    assert digest.unreviewed_thread_count(evidence(threads=threads)) == 1


def test_a_commit_with_no_pull_request_needs_attention():
    entry = digest.classify("c" * 40, "direct push", ["AGENTS.md"], None)
    assert entry.needs_attention
    assert entry.reasons == ("no merged pull request",)


def test_a_reviewed_commit_with_no_flagged_thread_needs_none():
    pull = evidence(reviews=(review(CONNECTOR, HEAD),))
    entry = digest.classify("c" * 40, "change", ["AGENTS.md"], pull)
    assert not entry.needs_attention
    assert entry.reasons == ()


def test_a_reviewed_commit_with_a_flagged_thread_needs_attention():
    pull = evidence(
        reviews=(review(CONNECTOR, HEAD),),
        threads=(digest.Thread(resolved=True, started_by=CONNECTOR, first_comment_sha=HEAD),),
    )
    entry = digest.classify("c" * 40, "change", ["AGENTS.md"], pull)
    assert entry.needs_attention
    assert "resolved with no later commit" in entry.reasons[0]


def test_render_lists_each_commit_with_its_signal():
    pull = evidence(reviews=(review(CONNECTOR, HEAD),))
    entries = [
        digest.classify("c" * 40, "reviewed", ["AGENTS.md"], pull),
        digest.classify("d" * 40, "unreviewed", ["AGENTS.md"], evidence(number=8)),
    ]
    text = digest.render(entries, since="1" * 40, until="2" * 40)
    assert "| `ccccccccc` reviewed | #7 | connector-review | - |" in text
    assert "| `ddddddddd` unreviewed | #8 | none | no completed review after the last content commit |" in text


def test_render_reports_an_empty_range():
    assert digest.render([], since="1" * 40, until="2" * 40).endswith("None.")


GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def git(repo, *args, check=True):
    result = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def write(repo, name, text):
    (repo / name).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "f", "a\nb\nc\n")
    write(tmp_path, "g", "x\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def make_branches(repo):
    git(repo, "checkout", "-qb", "feat")
    write(repo, "f", "a\nb\nc\nfeat\n")
    git(repo, "commit", "-qam", "feature")
    git(repo, "checkout", "-q", "main")
    write(repo, "f", "top\na\nb\nc\n")
    git(repo, "commit", "-qam", "base moves")


def test_a_mechanical_merge_of_the_base_adds_no_content(repo):
    make_branches(repo)
    git(repo, "checkout", "-q", "feat")
    git(repo, "merge", "-q", "--no-edit", "main")
    assert digest.merge_adds_content(git(repo, "rev-parse", "HEAD"), "main", cwd=repo) is False


def test_a_mechanical_merge_of_two_branches_off_the_base_adds_content(repo):
    for name in ("one", "two"):
        git(repo, "checkout", "-qb", name, "main")
        write(repo, name, name)
        git(repo, "add", name)
        git(repo, "commit", "-qm", name)
    git(repo, "merge", "-q", "--no-edit", "one")
    assert digest.merge_adds_content(git(repo, "rev-parse", "HEAD"), "main", cwd=repo) is True


def test_a_merge_with_an_extra_edit_adds_content(repo):
    make_branches(repo)
    git(repo, "checkout", "-q", "feat")
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "main")
    write(repo, "f", "top\na\nb\nc\nfeat\nsneaked in\n")
    git(repo, "commit", "-qam", "merge main")
    assert digest.merge_adds_content(git(repo, "rev-parse", "HEAD"), "main", cwd=repo) is True


def test_a_merge_resolved_by_hand_adds_content(repo):
    git(repo, "checkout", "-qb", "left")
    write(repo, "g", "left\n")
    git(repo, "commit", "-qam", "left")
    git(repo, "checkout", "-q", "main")
    write(repo, "g", "main\n")
    git(repo, "commit", "-qam", "main")
    git(repo, "checkout", "-q", "left")
    git(repo, "merge", "--no-edit", "main", check=False)
    write(repo, "g", "resolved\n")
    git(repo, "commit", "-qam", "resolve")
    assert digest.merge_adds_content(git(repo, "rev-parse", "HEAD"), "main", cwd=repo) is True


def test_an_octopus_merge_adds_content(repo):
    for name in ("one", "two"):
        git(repo, "checkout", "-qb", name, "main")
        write(repo, name, name)
        git(repo, "add", name)
        git(repo, "commit", "-qm", name)
    git(repo, "checkout", "-q", "main")
    write(repo, "g", "main\n")
    git(repo, "commit", "-qam", "main moves")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "one", "two")
    head = git(repo, "rev-parse", "HEAD")
    assert len(git(repo, "rev-list", "--parents", "-n1", head).split()) == 4
    assert digest.merge_adds_content(head, "main", cwd=repo) is True


def test_a_merge_of_unrelated_histories_is_a_read_error_not_added_content(repo):
    git(repo, "checkout", "-q", "--orphan", "other")
    git(repo, "commit", "-qm", "other root", "--allow-empty")
    main, other = git(repo, "rev-parse", "main"), git(repo, "rev-parse", "other")
    tree = git(repo, "rev-parse", "other^{tree}")
    merge = git(repo, "commit-tree", tree, "-p", main, "-p", other, "-m", "join")
    with pytest.raises(digest.ReadError, match="merge-tree"):
        digest.merge_adds_content(merge, "main", cwd=repo)


def test_an_unknown_commit_is_an_error_not_a_non_ancestor(repo):
    with pytest.raises(digest.ReadError):
        digest.is_ancestor("f" * 40, "main", cwd=repo)


def test_ancestry_is_reported_in_both_directions(repo):
    base = git(repo, "rev-parse", "main")
    write(repo, "f", "changed\n")
    git(repo, "commit", "-qam", "next")
    assert digest.is_ancestor(base, "main", cwd=repo) is True
    assert digest.is_ancestor("main", base, cwd=repo) is False


def test_a_rename_reports_both_the_old_and_the_new_path(repo):
    git(repo, "mv", "f", "renamed")
    git(repo, "commit", "-qm", "rename")
    assert set(digest.commit_files(git(repo, "rev-parse", "HEAD"), cwd=repo)) == {"f", "renamed"}


def seed_predicate(repo, extra_rules):
    (repo / ".github").mkdir(exist_ok=True)
    (repo / "_project/scripts").mkdir(parents=True, exist_ok=True)
    manifest = (ROOT / ".github/soundness-paths.txt").read_text(encoding="utf-8")
    write(repo, ".github/soundness-paths.txt", manifest + extra_rules)
    write(
        repo, "_project/scripts/soundness_paths.py", (ROOT / "_project/scripts/soundness_paths.py").read_text("utf-8")
    )


def test_a_commit_is_judged_by_the_manifest_of_its_first_parent(repo):
    seed_predicate(repo, "file\tsecret.py\n")
    write(repo, "secret.py", "1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "protect")
    write(repo, "secret.py", "2\n")
    git(repo, "commit", "-qam", "edit protected file")
    edit = git(repo, "rev-parse", "HEAD")
    seed_predicate(repo, "")
    git(repo, "commit", "-qam", "drop the rule")
    write(repo, "secret.py", "3\n")
    git(repo, "commit", "-qam", "edit after the rule is gone")
    assert digest.soundness_files(edit, ["secret.py"], cwd=repo) == ["secret.py"]
    assert digest.soundness_files(git(repo, "rev-parse", "HEAD"), ["secret.py"], cwd=repo) == []


def test_a_commit_that_removes_a_rule_and_edits_the_path_it_covered_is_still_flagged(repo):
    seed_predicate(repo, "file\tsecret.py\n")
    write(repo, "secret.py", "1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "protect")
    seed_predicate(repo, "")
    write(repo, "secret.py", "2\n")
    git(repo, "commit", "-qam", "drop the rule and edit")
    changed = digest.commit_files(git(repo, "rev-parse", "HEAD"), cwd=repo)
    assert "secret.py" in digest.soundness_files(git(repo, "rev-parse", "HEAD"), changed, cwd=repo)


def test_a_rule_added_later_does_not_apply_to_earlier_commits(repo):
    seed_predicate(repo, "")
    write(repo, "late.py", "1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    write(repo, "late.py", "2\n")
    git(repo, "commit", "-qam", "edit before the rule")
    edit = git(repo, "rev-parse", "HEAD")
    seed_predicate(repo, "file\tlate.py\n")
    git(repo, "commit", "-qam", "add the rule")
    assert digest.soundness_files(edit, ["late.py"], cwd=repo) == []


@pytest.mark.parametrize("path", sorted(digest.GOVERNANCE_PATHS))
def test_the_governance_files_stay_protected_when_the_manifest_does_not_list_them(repo, path):
    seed_predicate(repo, "")
    manifest = repo / ".github/soundness-paths.txt"
    kept = [line for line in manifest.read_text("utf-8").splitlines() if not line.endswith(path)]
    manifest.write_text("\n".join(kept) + "\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    write(repo, "unrelated.py", "1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "next")
    sha = git(repo, "rev-parse", "HEAD")
    assert digest.soundness_files(sha, [path, "unrelated.py"], cwd=repo) == [path]


def test_a_commit_with_no_changed_files_reads_nothing(repo):
    assert digest.soundness_files(git(repo, "rev-parse", "HEAD"), [], cwd=repo) == []


def test_a_parent_without_the_predicate_is_a_read_error(repo):
    write(repo, "h", "1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "next")
    with pytest.raises(digest.ReadError):
        digest.soundness_files(git(repo, "rev-parse", "HEAD"), ["h"], cwd=repo)


def pull_stub(monkeypatch, *, pulls):
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: pulls)


def listed_pull(**overrides):
    fields = {"number": 7, "merge_commit_sha": MERGED, "merged_at": AFTER, "base": {"ref": "develop"}}
    fields.update(overrides)
    return fields


def test_the_pull_request_merged_into_develop_as_the_commit_is_found(monkeypatch):
    pull_stub(monkeypatch, pulls=[listed_pull(number=3, merge_commit_sha="f" * 40), listed_pull()])
    assert digest.merged_pull_number("o/r", MERGED) == 7


@pytest.mark.parametrize(
    "override",
    [{"merged_at": None}, {"base": {"ref": "release"}}, {"merge_commit_sha": "f" * 40}],
)
def test_a_pull_request_that_did_not_merge_this_commit_into_develop_is_ignored(monkeypatch, override):
    pull_stub(monkeypatch, pulls=[listed_pull(**override)])
    assert digest.merged_pull_number("o/r", MERGED) is None


def test_two_pull_requests_for_one_merge_commit_are_refused(monkeypatch):
    pull_stub(monkeypatch, pulls=[listed_pull(number=7), listed_pull(number=8)])
    with pytest.raises(digest.ReadError):
        digest.merged_pull_number("o/r", MERGED)


def api_commit(sha, committed_at, parents=1):
    return {"sha": sha, "commit": {"committer": {"date": committed_at}}, "parents": [{"sha": "p"}] * parents}


FIRST_RUN = "2026-10-02T10:05:00Z"
SECOND_RUN = "2026-10-02T10:20:00Z"


def runs_response(*runs):
    entries = []
    for event, at, *numbers in runs:
        entry = {"event": event, "created_at": at, "pull_requests": [{"number": n} for n in (numbers or [7])]}
        entries.append(entry)
    return {"workflow_runs": entries}


def commit_list(monkeypatch, commits, *, fetch_head=HEAD, runs=()):
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: commits)

    def fake_json(*args):
        endpoint = args[1]
        if isinstance(runs, dict):
            return runs_response(*runs.get(endpoint.split("head_sha=")[1].split("&")[0], ()))
        return runs_response(*runs)

    monkeypatch.setattr(digest, "gh_json", fake_json)
    monkeypatch.setattr(digest, "merge_adds_content", lambda sha, base, cwd=None: sha == OLD)
    calls = []

    def fake_run(args, **_):
        calls.append(list(args))
        return fetch_head + "\n" if args[:3] == ["git", "rev-parse", "FETCH_HEAD"] else ""

    monkeypatch.setattr(digest, "run", fake_run)
    return calls


def pull_of(head, commits):
    return {
        "commits": commits,
        "head": {"sha": head, "ref": "feature", "repo": {"full_name": "o/r"}},
        "merged_at": MERGED_AT,
        "created_at": "2026-10-02T08:00:00Z",
    }


def run_record(**overrides):
    record = {
        "event": "pull_request",
        "created_at": PUSHED,
        "pull_requests": [],
        "head_branch": "feature",
        "head_repository": {"full_name": "o/r"},
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"pull_requests": [{"number": 7}]}, True),
        ({"pull_requests": [{"number": 3}]}, False),
        ({"pull_requests": [], "head_branch": "feature"}, True),
        ({"pull_requests": [], "head_branch": "elsewhere"}, False),
        ({"pull_requests": [], "head_repository": {"full_name": "fork/r"}}, False),
        ({"pull_requests": [], "head_repository": None}, False),
        ({"pull_requests": [{"number": 7}], "created_at": "2026-10-01T00:00:00Z"}, False),
        ({"event": "push", "pull_requests": [{"number": 7}]}, False),
        ({"event": "pull_request_target"}, True),
    ],
)
def test_a_run_counts_only_when_this_pull_request_started_it(override, expected):
    assert digest.belongs_to_pull(run_record(**override), 7, pull_of(HEAD, 1)) is expected


def test_a_pull_request_whose_head_repository_is_gone_matches_no_unlisted_run():
    pull = pull_of(HEAD, 1)
    pull["head"]["repo"] = None
    assert digest.belongs_to_pull(run_record(), 7, pull) is False


def test_commits_are_read_with_refresh_merges_marked(monkeypatch):
    commits = [api_commit(OLD, BEFORE, 2), api_commit(REFRESH, PUSHED, 2), api_commit(HEAD, AFTER)]
    calls = commit_list(monkeypatch, commits)
    pull = pull_of(HEAD, 3)
    result = digest.collect_commits("o/r", 7, pull, "base")
    assert [c.is_refresh for c in result] == [False, True, False]
    assert ["git", "fetch", "--no-tags", "origin", "refs/pull/7/head"] in calls


def test_a_commit_list_without_merges_fetches_nothing(monkeypatch):
    calls = commit_list(monkeypatch, [api_commit(HEAD, AFTER)])
    digest.collect_commits("o/r", 7, pull_of(HEAD, 1), "base")
    assert calls == []


def test_a_pull_head_that_moved_during_the_read_is_refused(monkeypatch):
    commits = [api_commit(OLD, BEFORE, 2), api_commit(HEAD, AFTER)]
    commit_list(monkeypatch, commits, fetch_head="f" * 40)
    with pytest.raises(digest.ReadError):
        digest.collect_commits("o/r", 7, pull_of(HEAD, 2), "base")


def test_the_last_content_commit_is_dated_by_the_first_pull_request_run(monkeypatch):
    commit_list(
        monkeypatch,
        [api_commit(OLD, BEFORE), api_commit(HEAD, "2000-01-01T00:00:00Z")],
        runs=[("pull_request", SECOND_RUN), ("push", "2026-10-02T08:00:00Z"), ("pull_request", FIRST_RUN)],
    )
    result = digest.collect_commits("o/r", 7, pull_of(HEAD, 2), "base")
    assert [c.arrived_at for c in result] == [BEFORE, FIRST_RUN]


def test_a_backdated_commit_cannot_make_an_old_reaction_current(monkeypatch):
    commit_list(
        monkeypatch,
        [api_commit(HEAD, "2000-01-01T00:00:00Z")],
        runs=[("pull_request", "2026-10-02T11:30:00Z")],
    )
    commits = digest.collect_commits("o/r", 7, pull_of(HEAD, 1), "base")
    pull = evidence(commits=commits, reactions=(digest.Reaction(CONNECTOR, "+1", AFTER),))
    assert digest.review_signals(pull) == ()


def test_a_run_for_another_pull_request_does_not_date_the_commit(monkeypatch):
    commit_list(monkeypatch, [api_commit(HEAD, "2000-01-01T00:00:00Z")], runs=[("pull_request", BEFORE, 3)])
    result = digest.collect_commits("o/r", 7, pull_of(HEAD, 1), "base")
    assert [c.arrived_at for c in result] == [MERGED_AT]


def test_a_content_commit_with_no_run_takes_the_first_run_of_a_later_commit(monkeypatch):
    commits = [api_commit(HEAD, "2000-01-01T00:00:00Z"), api_commit(REFRESH, "2000-01-01T00:00:00Z", 2)]
    commit_list(monkeypatch, commits, fetch_head=REFRESH, runs={REFRESH: [("pull_request", SECOND_RUN)]})
    result = digest.collect_commits("o/r", 7, pull_of(REFRESH, 2), "base")
    assert [(c.sha, c.is_refresh) for c in result] == [(HEAD, False), (REFRESH, True)]
    assert result[0].arrived_at == SECOND_RUN


def test_a_skipped_run_on_the_last_content_commit_cannot_make_an_old_review_current(monkeypatch):
    commits = [api_commit(HEAD, "2000-01-01T00:00:00Z"), api_commit(REFRESH, "2000-01-01T00:00:00Z", 2)]
    commit_list(monkeypatch, commits, fetch_head=REFRESH, runs={REFRESH: [("pull_request", MERGED_AT)]})
    result = digest.collect_commits("o/r", 7, pull_of(REFRESH, 2), "base")
    pull = evidence(commits=result, reactions=(digest.Reaction(CONNECTOR, "+1", AFTER),))
    assert result[0].arrived_at == MERGED_AT
    assert digest.review_signals(pull) == ()


def test_a_commit_with_no_pull_request_run_at_all_is_dated_by_the_merge(monkeypatch):
    commit_list(monkeypatch, [api_commit(HEAD, AFTER)], runs=[("push", BEFORE)])
    result = digest.collect_commits("o/r", 7, pull_of(HEAD, 1), "base")
    assert [c.arrived_at for c in result] == [MERGED_AT]


def test_the_cutoff_is_the_last_content_commit_alone():
    pull = evidence(commits=(commit(OLD, LATER), commit(HEAD, BEFORE)))
    assert pull.content_cutoff == BEFORE


@pytest.mark.parametrize(
    ("commits", "pull"),
    [
        ([], pull_of(HEAD, 0)),
        ([api_commit(HEAD, AFTER)], pull_of(HEAD, 2)),
        ([api_commit(OLD, AFTER)], pull_of(HEAD, 1)),
    ],
)
def test_an_empty_short_or_mismatched_commit_list_is_refused(monkeypatch, commits, pull):
    commit_list(monkeypatch, commits)
    with pytest.raises(digest.ReadError):
        digest.collect_commits("o/r", 7, pull, "base")


def test_a_commit_list_at_the_api_limit_is_refused(monkeypatch):
    commits = [api_commit(f"{i:040x}", BEFORE) for i in range(digest.MAX_PULL_COMMITS)]
    commit_list(monkeypatch, commits)
    with pytest.raises(digest.ReadError):
        digest.collect_commits("o/r", 7, pull_of(commits[-1]["sha"], len(commits)), "base")


def test_pages_of_a_paginated_read_are_flattened(monkeypatch):
    pages = '[[{"n": 1}, {"n": 2}], [{"n": 3}]]'
    monkeypatch.setattr(digest, "run", lambda args, **_: pages)
    assert digest.gh_pages("repos/o/r/issues") == [{"n": 1}, {"n": 2}, {"n": 3}]


def graphql_threads(nodes, *, more=False):
    return {
        "data": {"repository": {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": more}, "nodes": nodes}}}}
    }


def thread_node(resolved, login, sha, *, last_edited_at=None, includes_created_edit=False):
    comment = {
        "author": {"login": login},
        "originalCommit": {"oid": sha},
        "lastEditedAt": last_edited_at,
        "includesCreatedEdit": includes_created_edit,
    }
    return {"isResolved": resolved, "comments": {"nodes": [comment]}}


def test_review_threads_are_parsed_from_the_graphql_response(monkeypatch):
    nodes = [
        thread_node(True, "reviewer", HEAD),
        {"isResolved": False, "comments": {"nodes": [{"author": None, "originalCommit": None}]}},
    ]
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads(nodes))
    threads = digest.collect_threads("o/r", 7)
    assert threads == (digest.Thread(True, "reviewer", HEAD), digest.Thread(False, "", ""))


def test_a_thread_with_no_comments_is_skipped(monkeypatch):
    nodes = [{"isResolved": True, "comments": {"nodes": []}}, thread_node(True, "reviewer", HEAD)]
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads(nodes))
    assert digest.collect_threads("o/r", 7) == (digest.Thread(True, "reviewer", HEAD),)


def test_more_threads_than_one_page_stops_the_read(monkeypatch):
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads([], more=True))
    with pytest.raises(digest.ReadError):
        digest.collect_threads("o/r", 7)


def test_a_malformed_thread_response_stops_the_read(monkeypatch):
    monkeypatch.setattr(digest, "gh_json", lambda *args: {"data": None})
    with pytest.raises(digest.ReadError):
        digest.collect_threads("o/r", 7)


def test_a_damaged_state_issue_is_refused(monkeypatch):
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: [{"number": 4, "body": "marker deleted"}])
    with pytest.raises(digest.ReadError):
        digest.read_checkpoint("o/r")


def test_two_state_issues_are_refused(monkeypatch):
    body = f"<!-- soundness-digest-checkpoint: {HEAD} -->"
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: [{"number": 4, "body": body}, {"number": 5, "body": body}])
    with pytest.raises(digest.ReadError):
        digest.read_checkpoint("o/r")


def test_no_state_issue_means_no_checkpoint(monkeypatch):
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: [])
    assert digest.read_checkpoint("o/r") is None


def test_the_stored_checkpoint_is_read_from_the_marker(monkeypatch):
    body = f"<!-- soundness-digest-checkpoint: {HEAD} -->\n\nreport"
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: [{"number": 4, "body": body}])
    assert digest.read_checkpoint("o/r") == HEAD


def test_gap_issues_are_not_repeated_across_pages_of_existing_issues(monkeypatch):
    created = []
    known = [{"body": f"<!-- soundness-gap:{'d' * 40} -->"}] * 150
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: known)
    monkeypatch.setattr(digest, "run", lambda args, **_: created.append(list(args)) or "")
    seen = digest.classify("d" * 40, "seen", ["AGENTS.md"], evidence(number=8))
    fresh = digest.classify("f" * 40, "new", ["AGENTS.md"], evidence(number=9))
    assert digest.open_gap_issues("o/r", [seen, fresh]) == ["f" * 40]
    assert sum(1 for args in created if args[:3] == ["gh", "issue", "create"]) == 1


ON_HISTORY = "1" * 40
OFF_HISTORY = "9" * 40


def fake_git(args, **_):
    if args[:2] == ["git", "rev-list"]:
        return f"{HEAD}\n{ON_HISTORY}\n"
    if args[:3] == ["git", "rev-parse", "--verify"]:
        return args[3].removesuffix("^{commit}") + "\n"
    return HEAD + "\n"


@pytest.fixture
def stubbed(monkeypatch):
    calls = {"issues": [], "checkpoint": [], "closed": [], "closures": []}
    unreviewed = digest.classify("d" * 40, "unreviewed", ["AGENTS.md"], evidence(number=8))
    monkeypatch.setattr(digest, "run", fake_git)
    monkeypatch.setattr(digest, "read_checkpoint", lambda repo: ON_HISTORY)
    monkeypatch.setattr(digest, "collect_entries", lambda repo, ref, since: [unreviewed])
    monkeypatch.setattr(digest, "collect_window_pulls", lambda repo, ref, since: ([], []))
    monkeypatch.setattr(digest, "fetch_trunk_runs", lambda repo: [])
    monkeypatch.setattr(digest, "open_gap_issues", lambda repo, entries: calls["issues"].append(entries) or ["d" * 40])
    monkeypatch.setattr(digest, "write_checkpoint", lambda repo, sha, report: calls["checkpoint"].append(sha))
    monkeypatch.setattr(digest, "gap_closures", lambda repo, entries: calls["closures"])
    monkeypatch.setattr(digest, "close_gap_issues", lambda repo, closures: calls["closed"].append(list(closures)))
    return calls


def test_a_checkpoint_off_the_first_parent_history_is_refused(stubbed, monkeypatch):
    monkeypatch.setattr(digest, "read_checkpoint", lambda repo: OFF_HISTORY)
    assert digest.main(["--apply"]) == 2
    assert stubbed["issues"] == []
    assert stubbed["checkpoint"] == []


def test_an_explicit_since_off_the_first_parent_history_is_refused(stubbed):
    assert digest.main(["--apply", "--since", OFF_HISTORY]) == 2
    assert stubbed["checkpoint"] == []


def test_apply_opens_issues_and_then_advances_the_checkpoint(stubbed):
    assert digest.main(["--apply"]) == 0
    assert len(stubbed["issues"]) == 1
    assert stubbed["checkpoint"] == [HEAD]


def test_a_dry_run_changes_nothing(stubbed):
    assert digest.main([]) == 0
    assert stubbed["issues"] == []
    assert stubbed["checkpoint"] == []


@pytest.mark.parametrize("error", [digest.ReadError("boom"), KeyError("head"), ValueError("bad json")])
def test_a_failed_or_malformed_read_does_not_advance_the_checkpoint(stubbed, monkeypatch, error):
    def broken(repo, ref, since):
        raise error

    monkeypatch.setattr(digest, "collect_entries", broken)
    assert digest.main(["--apply"]) == 2
    assert stubbed["issues"] == []
    assert stubbed["checkpoint"] == []


def test_a_failure_while_opening_issues_does_not_advance_the_checkpoint(stubbed, monkeypatch):
    def broken(repo, entries):
        raise digest.ReadError("issue create failed")

    monkeypatch.setattr(digest, "open_gap_issues", broken)
    assert digest.main(["--apply"]) == 2
    assert stubbed["checkpoint"] == []


def test_apply_closes_resolved_gap_issues_before_advancing_the_checkpoint(stubbed):
    closure = digest.Closure(30, SHA_3030, "no longer needs attention (stand-in)", "comment")
    stubbed["closures"].append(closure)
    assert digest.main(["--apply"]) == 0
    assert stubbed["closed"] == [[closure]]
    assert stubbed["checkpoint"] == [HEAD]


def test_a_dry_run_lists_the_issues_it_would_close_and_closes_none(stubbed, capsys):
    stubbed["closures"].append(digest.Closure(30, SHA_3030, "no longer needs attention (stand-in)", "comment"))
    assert digest.main([]) == 0
    assert "- #30 (`fffffffff`): no longer needs attention (stand-in)" in capsys.readouterr().out
    assert stubbed["closed"] == []
    assert stubbed["checkpoint"] == []


def test_a_failure_while_closing_issues_does_not_advance_the_checkpoint(stubbed, monkeypatch):
    def broken(repo, closures):
        raise digest.ReadError("issue close failed")

    monkeypatch.setattr(digest, "close_gap_issues", broken)
    assert digest.main(["--apply"]) == 2
    assert stubbed["checkpoint"] == []


def test_a_missing_checkpoint_is_not_replaced_by_a_normal_run(stubbed, monkeypatch):
    monkeypatch.setattr(digest, "read_checkpoint", lambda repo: None)
    assert digest.main(["--apply"]) == 2
    assert stubbed["checkpoint"] == []
    assert stubbed["issues"] == []


def test_bootstrap_records_the_head_when_no_checkpoint_exists(stubbed, monkeypatch):
    monkeypatch.setattr(digest, "read_checkpoint", lambda repo: None)
    assert digest.main(["--apply", "--bootstrap"]) == 0
    assert stubbed["checkpoint"] == [HEAD]
    assert stubbed["issues"] == []


def test_bootstrap_refuses_to_skip_over_a_stored_checkpoint(stubbed):
    assert digest.main(["--apply", "--bootstrap"]) == 2
    assert stubbed["checkpoint"] == []


def test_bootstrap_without_apply_changes_nothing(stubbed, monkeypatch):
    monkeypatch.setattr(digest, "read_checkpoint", lambda repo: None)
    assert digest.main(["--bootstrap"]) == 2
    assert stubbed["checkpoint"] == []


def test_a_damaged_state_issue_stops_a_run(stubbed, monkeypatch):
    def damaged(repo):
        raise digest.ReadError("no marker")

    monkeypatch.setattr(digest, "read_checkpoint", damaged)
    assert digest.main(["--apply"]) == 2
    assert stubbed["checkpoint"] == []


def test_an_explicit_since_repairs_a_damaged_state_issue(stubbed, monkeypatch):
    def damaged(repo):
        raise AssertionError("the stored checkpoint must not be read")

    monkeypatch.setattr(digest, "read_checkpoint", damaged)
    assert digest.main(["--apply", "--since", ON_HISTORY]) == 0
    assert stubbed["checkpoint"] == [HEAD]


def test_commit_listing_follows_first_parents_in_order(monkeypatch):
    seen = {}

    def fake_run(args, **_):
        seen["args"] = list(args)
        return "x\ny\n"

    monkeypatch.setattr(digest, "run", fake_run)
    assert digest.merged_commits("origin/develop", "abc") == ["x", "y"]
    assert seen["args"][:5] == ["git", "log", "--first-parent", "--reverse", "--format=%H"]
    assert seen["args"][-1] == "abc..origin/develop"


def test_collect_pull_carries_the_comment_author_type_and_update_time(monkeypatch):
    rest = {
        "reviews": [],
        "reactions": [],
        "timeline": [{"event": "labeled", "created_at": BEFORE}, {"event": "base_ref_changed", "created_at": AFTER}],
        "comments": [
            {
                "user": {"login": "joeharris76", "type": "User"},
                "body": "text",
                "created_at": AFTER,
                "updated_at": LATER,
            }
        ],
    }
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 7)
    monkeypatch.setattr(digest, "gh_json", lambda *args: {"user": {"login": "dev"}, "merged_at": MERGED_AT})
    monkeypatch.setattr(digest, "collect_commits", lambda *args, **kwargs: ())
    monkeypatch.setattr(digest, "collect_threads", lambda repo, number: ())
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: rest[endpoint.rsplit("/", 1)[1]])
    pull = digest.collect_pull("o/r", MERGED)
    assert pull.comments == (digest.Comment("joeharris76", "text", AFTER, "User", LATER),)
    assert pull.base_changed_at == AFTER


ORACLE_PATH = ".github/workflows/oracle-review.yml@refs/pull/7/merge"


def oracle_run(at, event="synchronize"):
    return {
        "path": ORACLE_PATH,
        "created_at": at,
        "display_title": f"feat: change ({event})",
        "event": "pull_request",
        "pull_requests": [{"number": 7}],
    }


def collected_pull(monkeypatch, commits, head, head_runs, comments, *, runs_error=None):
    pull = {**pull_of(head, len(commits)), "user": {"login": "dev"}}

    def fake_json(*args):
        endpoint = args[1]
        if endpoint.endswith("/pulls/7"):
            return pull
        return {"workflow_runs": head_runs}

    def fake_pages(endpoint):
        if endpoint.endswith("/pulls/7/commits"):
            return commits
        if "actions/runs?head_sha=" in endpoint:
            if runs_error:
                raise runs_error
            return [{"workflow_runs": head_runs}]
        if endpoint.endswith("/comments"):
            return comments
        return []

    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 7)
    monkeypatch.setattr(digest, "gh_json", fake_json)
    monkeypatch.setattr(digest, "gh_pages", fake_pages)
    monkeypatch.setattr(digest, "collect_threads", lambda repo, number: ())
    monkeypatch.setattr(digest, "merge_adds_content", lambda sha, base, cwd=None: False)
    monkeypatch.setattr(digest, "run", lambda args, **_: head + "\n")
    return digest.collect_pull("o/r", MERGED)


def rest_comment(sha, at):
    return {
        "user": {"login": "joeharris76", "type": "User"},
        "body": f"Stand-in oracle review: APPROVE {sha}",
        "created_at": at,
        "updated_at": at,
    }


@pytest.mark.parametrize("approved_at", ["2026-10-02T10:05:00Z", "2026-10-02T10:10:00Z", "2026-10-02T10:15:00Z"])
def test_a_standin_for_a_refresh_head_is_not_timed_against_its_run(monkeypatch, approved_at):
    commits = [api_commit(OLD, BEFORE), api_commit(REFRESH, "2026-10-02T10:00:00Z", 2)]
    pull = collected_pull(
        monkeypatch,
        commits,
        REFRESH,
        [oracle_run("2026-10-02T10:10:00Z", "opened")],
        [rest_comment(REFRESH, approved_at)],
    )
    assert pull.commits[-1].is_refresh
    assert digest.review_signals(pull) == ("stand-in",)


@pytest.mark.parametrize("approved_at", ["2026-10-02T10:30:00Z", "2026-10-02T11:30:00Z"])
def test_a_standin_survives_the_head_moving_away_and_back(monkeypatch, approved_at):
    commits = [api_commit(OLD, BEFORE), api_commit(HEAD, "2026-10-02T09:30:00Z")]
    runs = [oracle_run("2026-10-02T10:00:00Z", "opened"), oracle_run("2026-10-02T11:00:00Z")]
    pull = collected_pull(monkeypatch, commits, HEAD, runs, [rest_comment(HEAD, approved_at)])
    assert digest.review_signals(pull) == ("stand-in",)


def test_the_digest_no_longer_reads_head_workflow_runs(monkeypatch):
    commits = [api_commit(OLD, BEFORE), api_commit(HEAD, "2026-10-02T09:30:00Z")]
    pull = collected_pull(
        monkeypatch, commits, HEAD, [], [rest_comment(HEAD, AFTER)], runs_error=digest.ReadError("runs failed")
    )
    assert digest.review_signals(pull) == ("stand-in",)


ORACLE_BOT = "benchbox-oracle[bot]"


def oracle_verdict(sha, state="success", context="oracle-verdict"):
    return f"### {context}: {state} for `{sha}`\n\nDetails."


def test_an_oracle_success_review_of_the_merged_content_is_a_signal():
    pull = evidence(reviews=(review(ORACLE_BOT, HEAD, user_type="Bot", body=oracle_verdict(HEAD)),))
    assert digest.review_signals(pull) == ("oracle-review",)


@pytest.mark.parametrize(
    "oracle_review",
    [
        review(ORACLE_BOT, HEAD, user_type="Bot", body=oracle_verdict(HEAD, "failure")),
        review(ORACLE_BOT, HEAD, user_type="Bot", body=oracle_verdict(OLD)),
        review(ORACLE_BOT, OLD, user_type="Bot", body=oracle_verdict(OLD)),
        review("benchbox-oracle", HEAD, user_type="User", body=oracle_verdict(HEAD)),
        review(ORACLE_BOT, HEAD, LATER, user_type="Bot", body=oracle_verdict(HEAD)),
    ],
    ids=["failure", "other-sha", "older-commit", "user-account", "after-merge"],
)
def test_an_oracle_review_that_does_not_qualify_is_not_a_signal(oracle_review):
    assert digest.review_signals(evidence(reviews=(oracle_review,))) == ()


def test_only_the_latest_oracle_verdict_after_a_retarget_counts():
    success = review(ORACLE_BOT, HEAD, PUSHED, user_type="Bot", body=oracle_verdict(HEAD))
    failure = review(ORACLE_BOT, HEAD, AFTER, user_type="Bot", body=oracle_verdict(HEAD, "failure"))
    assert digest.review_signals(evidence(reviews=(success, failure))) == ()
    assert digest.review_signals(evidence(reviews=(failure, success), base_changed_at=AFTER)) == ()
    later_success = review(ORACLE_BOT, HEAD, AFTER, user_type="Bot", body=oracle_verdict(HEAD))
    assert digest.review_signals(evidence(reviews=(later_success,), base_changed_at=PUSHED)) == ("oracle-review",)


def test_a_dismissed_or_pending_oracle_review_does_not_count():
    for state in ("DISMISSED", "PENDING"):
        dismissed = review(ORACLE_BOT, HEAD, state=state, user_type="Bot", body=oracle_verdict(HEAD))
        assert digest.review_signals(evidence(reviews=(dismissed,))) == ()


def test_a_standin_must_follow_the_latest_oracle_verdict_on_the_merged_head():
    failure = review(ORACLE_BOT, HEAD, AFTER, user_type="Bot", body=oracle_verdict(HEAD, "failure"))
    early = standin(HEAD, at=PUSHED)
    assert digest.review_signals(evidence(reviews=(failure,), comments=(early,))) == ()
    late = standin(HEAD, at=AFTER_REFRESH)
    assert digest.review_signals(evidence(reviews=(failure,), comments=(late,))) == ("stand-in",)


def test_connector_signals_count_only_for_merges_before_the_cut_over():
    signals = {"reviews": (review(CONNECTOR, HEAD),), "reactions": (digest.Reaction(CONNECTOR, "+1", AFTER),)}
    assert digest.review_signals(evidence(merged_at="2026-10-09T02:00:00Z", **signals)) == ()
    assert digest.review_signals(evidence(**signals)) == ("connector-review", "connector-approval")


@pytest.mark.parametrize("context", ["oracle-verdict", "oracle-review-shadow"], ids=["current", "legacy"])
def test_the_digest_reads_both_first_lines(context):
    success = evidence(reviews=(review(ORACLE_BOT, HEAD, user_type="Bot", body=oracle_verdict(HEAD, context=context)),))
    failure = evidence(
        reviews=(review(ORACLE_BOT, HEAD, user_type="Bot", body=oracle_verdict(HEAD, "failure", context)),)
    )
    assert digest.review_signals(success) == ("oracle-review",)
    assert digest.review_signals(failure) == ()


def test_the_digest_keeps_reading_the_legacy_first_line_for_merged_history():
    legacy = oracle_verdict(HEAD, context="oracle-review-shadow")
    assert digest.ORACLE_VERDICT_LINE.match(legacy) is not None
    pull = evidence(reviews=(review(ORACLE_BOT, HEAD, user_type="Bot", body=legacy),))
    assert digest.review_signals(pull) == ("oracle-review",)


STANDIN_AT = "2026-10-15T00:09:40Z"
PENDING_AT = "2026-10-15T06:01:21Z"
MERGED_THE_NEXT_MORNING = "2026-10-15T11:36:00Z"
PUSHED_THE_NIGHT_BEFORE = "2026-10-14T20:00:00Z"


def merged_pull(**overrides):
    return evidence(merged_at=MERGED_THE_NEXT_MORNING, commits=(commit(HEAD, PUSHED_THE_NIGHT_BEFORE),), **overrides)


def test_a_standin_survives_a_later_oracle_review_that_carries_no_verdict():
    pending = review(ORACLE_BOT, HEAD, PENDING_AT, user_type="Bot", body=oracle_verdict(HEAD, "pending"))
    pull = merged_pull(reviews=(pending,), comments=(standin(HEAD, at=STANDIN_AT),))
    entry = digest.classify("f" * 40, "change", ["AGENTS.md"], pull)
    assert entry.signals == ("stand-in",)
    assert not entry.needs_attention


def test_a_decisive_failure_after_the_standin_voids_it():
    failure = review(ORACLE_BOT, HEAD, PENDING_AT, user_type="Bot", body=oracle_verdict(HEAD, "failure"))
    pull = merged_pull(reviews=(failure,), comments=(standin(HEAD, at=STANDIN_AT),))
    entry = digest.classify("f" * 40, "change", ["AGENTS.md"], pull)
    assert entry.signals == ()
    assert entry.needs_attention


T_A, T_B, T_C, T_D = (f"2026-10-02T10:{minute}:00Z" for minute in (10, 20, 30, 40))
TIMELINES = {
    "no-signal": ([], None, None, False),
    "standin-only": ([], T_C, None, True),
    "pending-after-standin": ([("pending", T_D)], T_B, None, True),
    "pending-only": ([("pending", T_B)], None, None, False),
    "failure-after-standin": ([("failure", T_D)], T_B, None, False),
    "failure-before-standin": ([("failure", T_A)], T_B, None, True),
    "failure-then-pending-after-standin": ([("failure", T_A), ("pending", T_D)], T_B, None, True),
    "success-before-standin": ([("success", T_A)], T_B, None, True),
    "success-after-standin": ([("success", T_D)], T_B, None, True),
    "failure-after-success-and-standin": ([("success", T_A), ("failure", T_C)], T_B, None, False),
    "retarget-after-standin": ([], T_B, T_C, False),
    "retarget-before-standin": ([], T_C, T_A, True),
    "review-before-retarget": ([("success", T_A)], None, T_B, False),
}


def gate_accepts(verdicts, standin_at, base_change):
    checker = sys.modules["oracle_review_check"]
    run_uuid = "1" * 32
    from _project.scripts.oracle_reviewers import protocol

    record = {
        "v": protocol.MARKER_VERSION,
        "cycle": 1,
        "round": 1,
        "kind": protocol.FIRST,
        "decision": protocol.SHIP,
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "large",
        "tier": "large",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
        "run_uuid": run_uuid,
    }
    statuses = [
        {
            "id": index,
            "context": "oracle-verdict",
            "state": state,
            "description": f"large: {state} [run {run_uuid}]" if state == "success" else "",
            "login": ORACLE_BOT,
            "user_type": "Bot",
            "created_at": at,
        }
        for index, (state, at) in enumerate(verdicts)
    ]
    comments = []
    if standin_at:
        comments.append(
            {
                "login": "joeharris76",
                "user_type": "User",
                "body": f"Stand-in oracle review: APPROVE {HEAD}",
                "created_at": standin_at,
                "updated_at": standin_at,
            }
        )
    receipt = {
        "login": ORACLE_BOT,
        "user_type": "Bot",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": T_A,
        "body": f"{oracle_verdict(HEAD, 'success')}\n\n{protocol.encode_marker(record)}",
        "last_edited_at": None,
        "includes_created_edit": False,
    }
    status, _ = checker.decide(
        HEAD, ["AGENTS.md"], statuses, lambda _files: True, base_change, comments, reviews=[receipt]
    )
    return status == checker.PASS


def digest_accepts(verdicts, standin_at, base_change):
    reviews = tuple(
        review(ORACLE_BOT, HEAD, at, user_type="Bot", body=oracle_verdict(HEAD, state)) for state, at in verdicts
    )
    comments = (standin(HEAD, at=standin_at),) if standin_at else ()
    pull = evidence(reviews=reviews, comments=comments, base_changed_at=base_change or "")
    return bool({"oracle-review", "stand-in"} & set(digest.review_signals(pull)))


@pytest.mark.parametrize("timeline", TIMELINES.values(), ids=TIMELINES.keys())
def test_the_digest_and_the_gate_agree_on_the_standin_window(timeline):
    verdicts, standin_at, base_change, expected = timeline
    assert gate_accepts(verdicts, standin_at, base_change) is expected
    assert digest_accepts(verdicts, standin_at, base_change) is expected


def oracle_thread(severity, started_by="benchbox-oracle"):
    return digest.Thread(resolved=True, started_by=started_by, first_comment_sha=HEAD, severity=severity)


def gap_body_for(**overrides):
    entry = digest.classify("f" * 40, "change", ["AGENTS.md"], evidence(**overrides))
    return digest.gap_issue_body(entry)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"reviews": (review(CONNECTOR, HEAD),), "threads": (oracle_thread("High"),)},
            "Reason class: override of an oracle High finding.",
        ),
        (
            {"reviews": (review(CONNECTOR, HEAD),), "threads": (oracle_thread("Critical"),)},
            "Reason class: override of an oracle High finding.",
        ),
        (
            {"reviews": (review(CONNECTOR, HEAD),), "threads": (oracle_thread("Medium"), oracle_thread("Low"))},
            "Reason class: deferred finding.",
        ),
        (
            {"reviews": (review(CONNECTOR, HEAD),), "threads": (oracle_thread("", started_by=CONNECTOR),)},
            "Reason class: deferred finding.",
        ),
        (
            {"reviews": (review(CONNECTOR, HEAD),), "threads": (oracle_thread("High", started_by="someone"),)},
            "Reason class: deferred finding.",
        ),
        ({}, "Reason class: no review signal."),
        (
            {"threads": (oracle_thread("High"), oracle_thread("Medium"))},
            "Reason class: override of an oracle High finding; deferred finding; no review signal.",
        ),
    ],
    ids=["high", "critical", "medium-low", "human-thread", "high-from-a-human", "no-signal", "all-three"],
)
def test_the_gap_issue_body_states_the_reason_class(overrides, expected):
    assert expected in gap_body_for(**overrides)


def test_a_gap_issue_for_a_commit_with_no_pull_request_states_no_review_signal():
    entry = digest.classify("c" * 40, "direct push", ["AGENTS.md"], None)
    assert "Reason class: no review signal." in digest.gap_issue_body(entry)


def test_a_thread_records_the_severity_the_oracle_titled_its_finding_with(monkeypatch):
    node = thread_node(True, "benchbox-oracle", HEAD)
    node["comments"]["nodes"][0]["body"] = "**High**: the title\n\ndetail"
    plain = thread_node(True, "reviewer", HEAD)
    plain["comments"]["nodes"][0]["body"] = "Consider **High** performance"
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads([node, plain]))
    assert [thread.severity for thread in digest.collect_threads("o/r", 7)] == ["High", ""]


SHA_3030 = "f" * 40
ATTESTER = {"login": "joeharris76", "type": "User"}
CLEAN_REVIEW = "External review: codex\n\nVerified clean: no findings on the merged diff."
NO_SIGNAL_BODY = (
    f"<!-- soundness-gap:{SHA_3030} -->\n"
    f"Commit `{SHA_3030}` (#2915) changed paths on the soundness list and merged to develop.\n\n"
    "Why it is listed: no completed review after the last content commit.\n\n"
    "Soundness-path files:\n- `scripts/check_comment_policy.py`\n\n"
    "Next step: run an external adversarial review of the commit (codex, muse or agy), post it as a "
    "comment, and either record a clean result here or open a fix or revert pull request."
)


def gap_issue(number, state="open", body=NO_SIGNAL_BODY):
    return {"number": number, "state": state, "body": body}


def external_review(body=CLEAN_REVIEW, user=ATTESTER):
    return {"user": user, "body": body, "html_url": "https://example.test/comment/1"}


@pytest.fixture
def gap_env(monkeypatch):
    env = {"issues": [], "entries": {}, "comments": {}, "fetched": [], "ran": [], "commented": []}

    def fetch_entry(repo, sha):
        env["fetched"].append(sha)
        return env["entries"].get(sha)

    def issue_comments(endpoint):
        env["commented"].append(endpoint)
        return env["comments"].get(endpoint, [])

    monkeypatch.setattr(digest, "list_gap_issues", lambda repo: env["issues"])
    monkeypatch.setattr(digest, "collect_entry", fetch_entry)
    monkeypatch.setattr(digest, "gh_pages", issue_comments)
    monkeypatch.setattr(digest, "run", lambda args, **_: env["ran"].append(list(args)) or "")
    return env


def reviewed_entry(**overrides):
    pull = merged_pull(comments=(standin(HEAD, at=STANDIN_AT),), **overrides)
    return digest.classify(SHA_3030, "change", ["AGENTS.md"], pull)


def unreviewed_entry(**overrides):
    return digest.classify(SHA_3030, "change", ["AGENTS.md"], evidence(number=8, **overrides))


def thread_body(severity="Medium", reviewed=True):
    threads = (oracle_thread(severity),)
    if reviewed:
        entry = reviewed_entry(threads=threads)
    else:
        entry = digest.classify(SHA_3030, "change", ["AGENTS.md"], evidence(number=8, threads=threads))
    return digest.gap_issue_body(entry)


def test_a_no_signal_issue_in_the_shape_already_open_is_closed_once_a_signal_appears(gap_env):
    gap_env["issues"] = [gap_issue(30)]
    gap_env["entries"][SHA_3030] = reviewed_entry()
    closures = digest.gap_closures("o/r", [])
    assert [(closure.number, closure.sha) for closure in closures] == [(30, SHA_3030)]
    assert closures[0].comment == (
        f"<!-- soundness-gap-closed:{SHA_3030} -->\n"
        f"Closing: the issue was opened because `{SHA_3030}` (#7) had no review signal, and the digest now "
        "finds one.\n\n"
        "Review signal: stand-in."
    )
    digest.close_gap_issues("o/r", closures)
    assert gap_env["ran"] == [
        ["gh", "issue", "close", "30", "--repo", "o/r", "--reason", "completed", "--comment", closures[0].comment]
    ]


def test_an_issue_the_digest_opens_for_a_missing_signal_is_closed_once_a_signal_appears(gap_env):
    gap_env["issues"] = [gap_issue(30, body=digest.gap_issue_body(unreviewed_entry()))]
    gap_env["entries"][SHA_3030] = reviewed_entry()
    assert [closure.number for closure in digest.gap_closures("o/r", [])] == [30]


def test_a_no_signal_issue_stays_open_while_the_entry_has_no_signal(gap_env):
    gap_env["issues"] = [gap_issue(33)]
    gap_env["entries"][SHA_3030] = unreviewed_entry()
    assert digest.gap_closures("o/r", []) == []
    digest.close_gap_issues("o/r", [])
    assert gap_env["ran"] == []


def test_a_no_signal_issue_closes_on_the_signal_whatever_the_threads_now_say(gap_env):
    gap_env["issues"] = [gap_issue(30)]
    gap_env["entries"][SHA_3030] = reviewed_entry(threads=(oracle_thread("High"),))
    assert [closure.number for closure in digest.gap_closures("o/r", [])] == [30]


@pytest.mark.parametrize("severity", ["High", "Medium"], ids=["override", "deferred"])
@pytest.mark.parametrize("live_threads", [(), ("unresolved",)], ids=["deleted", "unresolved"])
def test_a_thread_issue_is_never_closed_by_changing_its_threads(gap_env, severity, live_threads):
    gap_env["issues"] = [gap_issue(40, body=thread_body(severity))]
    threads = tuple(
        digest.Thread(resolved=False, started_by="benchbox-oracle", first_comment_sha=HEAD, severity=severity)
        for _ in live_threads
    )
    gap_env["entries"][SHA_3030] = reviewed_entry(threads=threads)
    assert digest.gap_closures("o/r", []) == []


def test_a_mixed_reason_issue_is_not_closed_by_re_evaluation(gap_env):
    body = thread_body(reviewed=False)
    assert "no completed review after the last content commit; 1 reviewer thread(s)" in body
    gap_env["issues"] = [gap_issue(41, body=body)]
    gap_env["entries"][SHA_3030] = reviewed_entry()
    assert digest.gap_closures("o/r", []) == []


@pytest.mark.parametrize(
    "body",
    [
        f"<!-- soundness-gap:{SHA_3030} -->\nno reason given",
        f"<!-- soundness-gap:{SHA_3030} -->\nWhy it is listed: x",
    ],
    ids=["no-reason", "unterminated"],
)
def test_an_issue_with_an_unparseable_reason_is_not_closed_by_re_evaluation(gap_env, body):
    gap_env["issues"] = [gap_issue(42, body=body)]
    gap_env["entries"][SHA_3030] = reviewed_entry()
    assert digest.gap_closures("o/r", []) == []


def test_an_issue_with_no_merged_pull_request_is_not_closed_by_re_evaluation(gap_env):
    body = digest.gap_issue_body(digest.classify(SHA_3030, "direct push", ["AGENTS.md"], None))
    gap_env["issues"] = [gap_issue(43, body=body)]
    gap_env["entries"][SHA_3030] = reviewed_entry()
    assert digest.gap_closures("o/r", []) == []


@pytest.mark.parametrize("severity", ["High", "Medium"], ids=["override", "deferred"])
def test_an_attester_review_closes_a_thread_issue(gap_env, severity):
    gap_env["issues"] = [gap_issue(44, body=thread_body(severity))]
    gap_env["comments"]["repos/o/r/issues/44/comments"] = [external_review()]
    assert [closure.number for closure in digest.gap_closures("o/r", [])] == [44]
    assert gap_env["fetched"] == []


def test_an_attester_external_review_on_the_issue_closes_it(gap_env):
    gap_env["issues"] = [gap_issue(31)]
    gap_env["entries"][SHA_3030] = unreviewed_entry()
    gap_env["comments"]["repos/o/r/issues/31/comments"] = [external_review()]
    closures = digest.gap_closures("o/r", [])
    assert [closure.number for closure in closures] == [31]
    assert closures[0].summary == "external review by codex from joeharris76"
    assert "https://example.test/comment/1" in closures[0].comment
    assert f"<!-- soundness-gap-closed:{SHA_3030} -->" in closures[0].comment


@pytest.mark.parametrize(
    "comment",
    [
        external_review(user={"login": "dev", "type": "User"}),
        external_review(user={"login": "joeharris76", "type": "Bot"}),
        external_review(user={"login": "chatgpt-codex-connector[bot]", "type": "Bot"}),
        external_review(body="Looks fine to me."),
        external_review(body="External review: gemini\n\nVerified clean."),
        external_review(body="I agree with the\nExternal review: codex\n\nVerified clean."),
        external_review(body="Verified clean.\n\nExternal review: codex"),
        external_review(body="Reviewer: codex (queued)\n\nVerified clean."),
        external_review(body="Soundness review: codex\n\nVerified clean."),
        external_review(body="External review: codex\n\nNo findings."),
        external_review(body="External review: codex\n\n- **High**: a defect\n\nVerified clean."),
        external_review(body="External review: codex\n\n**Low**: a nit\n\nVerified clean."),
        external_review(body="External review: codex\n\nNot Verified clean yet."),
    ],
    ids=[
        "non-attester",
        "attester-bot",
        "connector",
        "no-marker",
        "unlisted-reviewer",
        "heading-not-first",
        "heading-last",
        "reviewer-queued",
        "soundness-review-form",
        "no-clean-line",
        "high-finding",
        "low-finding",
        "clean-not-line-start",
    ],
)
def test_a_comment_that_does_not_qualify_never_closes_a_gap_issue(gap_env, comment):
    gap_env["issues"] = [gap_issue(32)]
    gap_env["entries"][SHA_3030] = unreviewed_entry()
    gap_env["comments"]["repos/o/r/issues/32/comments"] = [comment]
    assert digest.gap_closures("o/r", []) == []


def test_the_clean_form_may_follow_leading_whitespace_and_name_any_listed_reviewer(gap_env):
    gap_env["issues"] = [gap_issue(45)]
    gap_env["entries"][SHA_3030] = unreviewed_entry()
    gap_env["comments"]["repos/o/r/issues/45/comments"] = [
        external_review(body="\n External review: agy\nVerified clean")
    ]
    assert [closure.summary for closure in digest.gap_closures("o/r", [])] == [
        "external review by agy from joeharris76"
    ]


def test_a_closed_gap_issue_is_never_read_or_modified(gap_env):
    gap_env["issues"] = [gap_issue(34, state="closed")]
    gap_env["entries"][SHA_3030] = reviewed_entry()
    gap_env["comments"]["repos/o/r/issues/34/comments"] = [external_review()]
    assert digest.gap_closures("o/r", [reviewed_entry()]) == []
    assert gap_env["fetched"] == []
    assert gap_env["commented"] == []
    assert gap_env["ran"] == []


def test_an_issue_without_the_marker_or_with_an_unresolvable_commit_is_left_alone(gap_env):
    gap_env["issues"] = [{"number": 35, "state": "open", "body": "written by hand"}, gap_issue(36)]
    assert digest.gap_closures("o/r", []) == []
    assert gap_env["fetched"] == [SHA_3030]


def test_an_entry_from_the_run_is_reused_instead_of_read_again(gap_env):
    gap_env["issues"] = [gap_issue(37)]
    assert [closure.number for closure in digest.gap_closures("o/r", [reviewed_entry()])] == [37]
    assert gap_env["fetched"] == []


def test_the_closure_list_names_each_issue():
    closure = digest.Closure(30, SHA_3030, "review signal found (stand-in)", "comment")
    assert (
        digest.render_closures([closure])
        == "\nGap issues to close:\n- #30 (`fffffffff`): review signal found (stand-in)"
    )
    assert digest.render_closures([]) == "\nGap issues to close:\nNone."


@pytest.mark.parametrize("body", ["External review: agy\n\nVerified clean.", "External review: codex"])
def test_the_attested_review_form_is_not_a_pull_request_comment_signal(body):
    comment = digest.Comment("joeharris76", body, AFTER, "User")
    assert digest.review_signals(evidence(comments=(comment,))) == ()


def test_a_reviewer_line_on_a_pull_request_comment_is_still_a_signal():
    comment = digest.Comment("joeharris76", "Reviewer: agy\n\nClean.", AFTER, "User")
    assert digest.review_signals(evidence(comments=(comment,))) == ("external-review:agy",)


INSTRUCTION_AT = "2026-10-02T11:49:22Z"
STANDIN_AT = "2026-10-02T11:49:23Z"


def verdict_with_open(sha, severities=("Medium",)):
    lines = [
        f"### oracle-verdict: failure for `{sha}`",
        "",
        "Decision: **SHIP WITH FIXES**.",
        "",
        "**Still open**",
        "",
    ]
    return "\n".join(lines + [f"- **{severity}** `scripts/plugin.py:13`: D1: some finding" for severity in severities])


def failure_review(sha=HEAD, body=None):
    return review(ORACLE_BOT, sha, user_type="Bot", body=body if body is not None else verdict_with_open(sha))


def instruction(at=INSTRUCTION_AT, body='Owner instruction: "Post the stand in".'):
    return digest.Comment("joeharris76", body, at, "User")


def overridden(severities=("Medium",), at=STANDIN_AT, instruction_at=INSTRUCTION_AT, armed_at=()):
    return evidence(
        reviews=(failure_review(body=verdict_with_open(HEAD, severities)),),
        comments=(instruction(instruction_at), standin(HEAD, at=at)),
        armed_at=armed_at,
    )


def test_a_standin_override_lists_the_attester_gap_and_severity():
    override = digest.standin_overrides([overridden()])[0]
    assert (override.number, override.head, override.attester) == (7, HEAD, "joeharris76")
    assert override.instruction_gap_seconds == 1
    assert override.severities == ("Medium",)


@pytest.mark.parametrize(
    ("listed", "expected"),
    [
        (("Medium",), ("Medium",)),
        (("Low", "Critical", "Medium"), ("Critical", "Medium", "Low")),
        (("High",), ("High",)),
    ],
)
def test_a_standin_override_lists_findings_of_any_severity(listed, expected):
    assert digest.standin_overrides([overridden(severities=listed)])[0].severities == expected


def test_an_unresolved_oracle_thread_counts_as_an_open_finding():
    pull = evidence(
        comments=(standin(HEAD),),
        threads=(digest.Thread(False, "benchbox-oracle", HEAD, "Low"),),
    )
    assert [override.severities for override in digest.standin_overrides([pull])] == [("Low",)]


@pytest.mark.parametrize(
    "pull",
    [
        evidence(reviews=(review(ORACLE_BOT, HEAD, user_type="Bot", body=oracle_verdict(HEAD)),)),
        evidence(comments=(standin(HEAD),)),
        evidence(
            reviews=(failure_review(),),
            comments=(standin(HEAD, login="someone-else"),),
        ),
        evidence(
            reviews=(failure_review(),),
            comments=(standin(OLD),),
        ),
        evidence(
            comments=(standin(HEAD),),
            threads=(digest.Thread(True, "benchbox-oracle", OLD, "Medium"),),
        ),
    ],
    ids=["oracle-success", "no-finding", "non-attester", "wrong-sha", "resolved-unlisted-thread"],
)
def test_a_pass_without_an_open_finding_is_not_an_override(pull):
    assert digest.standin_overrides([pull]) == []


def test_a_standin_overriding_a_do_not_ship_without_severities_is_listed():
    body = f"### oracle-verdict: failure for `{HEAD}`\n\nDecision: **DO NOT SHIP**.\n"
    pull = evidence(reviews=(failure_review(body=body),), comments=(standin(HEAD, at=STANDIN_AT),))
    assert digest.standin_overrides([pull])[0].severities == ("DO NOT SHIP",)


def test_a_standin_overriding_a_decisionless_failure_is_listed():
    pull = evidence(
        reviews=(failure_review(body=oracle_verdict(HEAD, "failure")),), comments=(standin(HEAD, at=STANDIN_AT),)
    )
    assert digest.standin_overrides([pull])[0].severities == ("failure",)


def test_a_standin_without_any_verdict_is_not_an_override():
    assert digest.standin_overrides([evidence(comments=(standin(HEAD, at=STANDIN_AT),))]) == []


def test_a_standin_override_without_an_instruction_marks_the_gap():
    pull = evidence(reviews=(failure_review(),), comments=(standin(HEAD, at=STANDIN_AT),))
    override = digest.standin_overrides([pull])[0]
    assert override.instruction_gap_seconds is None
    assert "no Owner instruction comment" in digest.render_standin_overrides([override])


def test_a_standin_override_uses_the_nearest_preceding_instruction():
    pull = evidence(
        reviews=(failure_review(),),
        comments=(
            instruction(BEFORE, "Owner instruction: arm it."),
            instruction(),
            standin(HEAD, at=STANDIN_AT),
            instruction(LATER),
        ),
    )
    assert digest.standin_overrides([pull])[0].instruction_gap_seconds == 1


@pytest.mark.parametrize(
    ("total_seconds", "expected"),
    [(0, "0s"), (1, "1s"), (61, "1m 1s"), (3600, "1h"), (3661, "1h 1m 1s")],
)
def test_gaps_render_compactly(total_seconds, expected):
    assert digest.format_gap(total_seconds) == expected


def test_still_open_bullets_come_only_from_open_finding_sections():
    body = "\n".join(
        [
            "**Still open**",
            "",
            "- **Medium** `a.py:1`: D1: open",
            "",
            "<details><summary>Not counted</summary>",
            "",
            "- **High** `b.py:2`: D2: hidden",
            "",
            "</details>",
            "",
            "**Reviewers not available**",
            "",
            "- **Low** `c.py:3`: D3: prose",
            "",
            "Withdrawn:",
            "",
            "- **Critical** `d.py:4`: D4: withdrawn",
        ]
    )
    assert digest.still_open_severities(body) == ["Medium"]


def test_render_standin_overrides_lists_each_override():
    text = digest.render_standin_overrides(digest.standin_overrides([overridden()]))
    assert f"| #7 | `{HEAD[:9]}` | joeharris76 | 1s | Medium |" in text
    assert digest.render_standin_overrides([]).endswith("None.")


def trunk_run(run_id, completed_at, conclusion="failure", head="a" * 40, status="completed"):
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "head_sha": head,
        "updated_at": completed_at,
    }


TRUNK_RED = trunk_run(39555893700, "2026-10-21T07:47:46Z")
TRUNK_GREEN = trunk_run(39596269177, "2026-10-21T12:32:24Z", "success", "b" * 40)


def armed_pull(*armed_at):
    return evidence(armed_at=tuple(armed_at))


def red_only(pull, runs):
    red, _ = digest.red_armings("o/r", [pull], runs)
    return red


def test_arming_while_trunk_is_red_is_flagged():
    arming = red_only(armed_pull("2026-10-21T11:41:17Z"), [TRUNK_RED, TRUNK_GREEN])[0]
    assert (arming.number, arming.armed_at, arming.conclusion) == (7, "2026-10-21T11:41:17Z", "failure")
    assert (arming.run_id, arming.run_head, arming.completed_at) == (39555893700, "a" * 9, "2026-10-21T07:47:46Z")


def test_a_run_completed_after_the_arming_does_not_count():
    late_green = trunk_run(9, "2026-10-21T12:32:24Z", "success", "b" * 40)
    assert red_only(armed_pull("2026-10-21T11:41:17Z"), [late_green, TRUNK_RED]) != []
    red, unknown = digest.red_armings("o/r", [armed_pull("2026-10-21T11:41:17Z")], [late_green])
    assert red == []
    assert [item.reason for item in unknown] == [
        "no completed trunk.yml push run on develop finished before the arming"
    ]


def test_no_trunk_history_marks_the_arming_unknown():
    red, unknown = digest.red_armings("o/r", [armed_pull("2026-10-21T11:41:17Z")], [])
    assert red == []
    assert [(item.number, item.armed_at) for item in unknown] == [(7, "2026-10-21T11:41:17Z")]
    assert unknown[0].reason == "no trunk.yml push run on develop existed yet"


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([TRUNK_GREEN], []),
        ([trunk_run(1, "2026-10-21T07:47:46Z", "cancelled")], []),
        ([trunk_run(1, "2026-10-21T07:47:46Z", "timed_out")], ["timed_out"]),
        ([], []),
    ],
    ids=["green", "cancelled", "timed-out", "no-runs"],
)
def test_only_red_trunk_conclusions_are_flagged(runs, expected):
    assert [arming.conclusion for arming in red_only(armed_pull("2026-10-21T11:41:17Z"), runs)] == expected


def test_an_unfinished_trunk_run_is_not_the_newest_completed():
    running = trunk_run(9, "2026-10-21T11:40:00Z", None, "b" * 40, status="in_progress")
    assert red_only(armed_pull("2026-10-21T11:41:17Z"), [running, TRUNK_RED]) != []
    red, unknown = digest.red_armings("o/r", [armed_pull("2026-10-21T11:41:17Z")], [running])
    assert red == []
    assert [item.reason for item in unknown] == [
        "no completed trunk.yml push run on develop finished before the arming"
    ]


def rerun_run(run_id=9, created="2026-10-21T11:30:00Z", updated="2026-10-21T12:30:00Z"):
    return {
        "id": run_id,
        "status": "completed",
        "conclusion": "success",
        "head_sha": "b" * 40,
        "created_at": created,
        "updated_at": updated,
        "run_attempt": 2,
    }


def test_a_rerun_after_the_arming_resolves_to_the_attempt_current_then(monkeypatch):
    first = {"conclusion": "failure", "updated_at": "2026-10-21T11:35:00Z"}
    monkeypatch.setattr(digest, "trunk_attempt", lambda repo, run_id, attempt: first)
    red, unknown = digest.red_armings("o/r", [armed_pull("2026-10-21T11:41:17Z")], [rerun_run(), TRUNK_RED])
    assert unknown == []
    assert [(arming.run_id, arming.conclusion, arming.completed_at) for arming in red] == [
        (9, "failure", "2026-10-21T11:35:00Z")
    ]


def test_a_rerun_whose_first_attempt_postdates_the_arming_is_not_counted(monkeypatch):
    first = {"conclusion": "failure", "updated_at": "2026-10-21T11:50:00Z"}
    monkeypatch.setattr(digest, "trunk_attempt", lambda repo, run_id, attempt: first)
    red, unknown = digest.red_armings("o/r", [armed_pull("2026-10-21T11:41:17Z")], [rerun_run()])
    assert red == []
    assert [item.reason for item in unknown] == [
        "no completed trunk.yml push run on develop finished before the arming"
    ]


def test_an_unresolvable_rerun_marks_the_arming_unknown(monkeypatch):
    def broken(repo, run_id, attempt):
        raise digest.ReadError("attempt gone")

    monkeypatch.setattr(digest, "trunk_attempt", broken)
    red, unknown = digest.red_armings("o/r", [armed_pull("2026-10-21T11:41:17Z")], [rerun_run()])
    assert red == []
    assert [item.reason for item in unknown] == ["trunk run state was unresolvable: attempt gone"]


def test_render_red_armings_names_the_run():
    arming = digest.RedArming(7, "2026-10-21T11:41:17Z", "failure", 39555893700, "a" * 9, "2026-10-21T07:47:46Z")
    text = digest.render_red_armings([arming])
    assert "- #7 armed at 2026-10-21T11:41:17Z" in text
    assert "was failure (run 39555893700, `aaaaaaaaa`, completed 2026-10-21T07:47:46Z)" in text
    assert digest.render_red_armings([]).endswith("None.")


def test_render_red_armings_reports_unknown_trunk_state():
    unknown = digest.UnknownArming(7, "2026-10-21T11:41:17Z", "no completed trunk.yml push run on develop existed yet")
    text = digest.render_red_armings([], [unknown])
    assert (
        "- #7 armed at 2026-10-21T11:41:17Z; trunk state unknown (no completed trunk.yml push run on develop existed yet)."
        in text
    )


def test_collect_pull_records_auto_merge_armings(monkeypatch):
    rest = {
        "reviews": [],
        "reactions": [],
        "timeline": [
            {"event": "labeled", "created_at": BEFORE},
            {"event": "auto_squash_enabled", "created_at": AFTER},
            {"event": "auto_merge_disabled", "created_at": LATER},
        ],
        "comments": [],
    }
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 7)
    monkeypatch.setattr(digest, "gh_json", lambda *args: {"user": {"login": "dev"}, "merged_at": MERGED_AT})
    monkeypatch.setattr(digest, "collect_commits", lambda *args, **kwargs: ())
    monkeypatch.setattr(digest, "collect_threads", lambda repo, number: ())
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: rest[endpoint.rsplit("/", 1)[1]])
    assert digest.collect_pull("o/r", MERGED).armed_at == (AFTER,)


def test_collect_pull_records_a_merge_queue_enqueue_as_an_arming(monkeypatch):
    rest = {
        "reviews": [],
        "reactions": [],
        "timeline": [
            {"event": "labeled", "created_at": BEFORE},
            {"event": "added_to_merge_queue", "created_at": AFTER},
        ],
        "comments": [],
    }
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 7)
    monkeypatch.setattr(digest, "gh_json", lambda *args: {"user": {"login": "dev"}, "merged_at": MERGED_AT})
    monkeypatch.setattr(digest, "collect_commits", lambda *args, **kwargs: ())
    monkeypatch.setattr(digest, "collect_threads", lambda repo, number: ())
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: rest[endpoint.rsplit("/", 1)[1]])
    assert digest.collect_pull("o/r", MERGED).armed_at == (AFTER,)


def test_a_queue_enqueue_while_trunk_is_red_is_flagged(monkeypatch):
    rest = {
        "reviews": [],
        "reactions": [],
        "timeline": [{"event": "added_to_merge_queue", "created_at": "2026-10-21T11:41:17Z"}],
        "comments": [],
    }
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 7)
    monkeypatch.setattr(digest, "gh_json", lambda *args: {"user": {"login": "dev"}, "merged_at": MERGED_AT})
    monkeypatch.setattr(digest, "collect_commits", lambda *args, **kwargs: ())
    monkeypatch.setattr(digest, "collect_threads", lambda repo, number: ())
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: rest[endpoint.rsplit("/", 1)[1]])
    pull = digest.collect_pull("o/r", MERGED)
    arming = red_only(pull, [TRUNK_RED, TRUNK_GREEN])[0]
    assert (arming.number, arming.armed_at, arming.conclusion) == (7, "2026-10-21T11:41:17Z", "failure")


def test_collect_window_pulls_reads_each_merged_pull_request_once(monkeypatch):
    monkeypatch.setattr(digest, "merged_commits", lambda ref, since: ["a" * 40, "b" * 40, "c" * 40])
    numbers = {"a" * 40: 7, "b" * 40: 7, "c" * 40: None}
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: numbers[sha])
    monkeypatch.setattr(digest, "collect_pull", lambda repo, sha: evidence(number=7) if sha == "a" * 40 else None)
    assert digest.collect_window_pulls("o/r", "ref", "since") == ([evidence(number=7)], [])


def test_collect_window_pulls_records_an_unreadable_pull_request(monkeypatch):
    monkeypatch.setattr(digest, "merged_commits", lambda ref, since: ["a" * 40, "b" * 40])
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 7 if sha == "a" * 40 else 8)

    def fake_pull(repo, sha):
        if sha == "a" * 40:
            raise digest.ReadError("threads exploded")
        return evidence(number=8)

    monkeypatch.setattr(digest, "collect_pull", fake_pull)
    pulls, unreadable = digest.collect_window_pulls("o/r", "ref", "since")
    assert pulls == [evidence(number=8)]
    assert [(item.number, item.sha) for item in unreadable] == [(7, "a" * 40)]
    assert "threads exploded" in unreadable[0].reason
    text = digest.render_unreadable(unreadable)
    assert text.startswith("\nUnreadable pull requests: merged PRs the digest could not read")
    assert "- #7: unreadable (threads exploded)." in text
    assert digest.render_unreadable([]) == ""


WITHDRAWN_BODY = "\n".join(
    [
        "### oracle-verdict: failure for `HEAD`",
        "",
        "Decision: **SHIP WITH FIXES**.",
        "",
        "Withdrawn as never defects: D1, D2. Resolve their review threads.",
        "",
        "**Still open from earlier reviews**",
        "",
        "- **Medium** `a.py:1`: D3: open",
        "",
        "**Dispute dispositions**",
        "",
        "- D1 (withdrawn): scaled timeouts are covered by the canary: a.py:2",
        "- D2 (maintained): the bound is still unchecked: a.py:3",
    ]
)


def test_withdrawals_list_the_marked_findings():
    pull = evidence(reviews=(failure_review(body=WITHDRAWN_BODY.replace("HEAD", HEAD)),))
    withdrawal = digest.withdrawn_findings([pull])[0]
    assert withdrawal.number == 7
    assert withdrawal.items == ("D1, D2. Resolve their review threads.",)
    text = digest.render_withdrawals([withdrawal])
    assert f"- #7 (`{HEAD[:9]}`):" in text
    assert "D1, D2. Resolve their review threads." in text


def test_withdrawals_are_skipped_when_no_review_marks_any():
    assert digest.withdrawn_findings([overridden()]) == []
    assert digest.render_withdrawals([]) == ""


def test_a_dry_run_reports_overrides_and_armings(stubbed, monkeypatch, capsys):
    monkeypatch.setattr(
        digest,
        "collect_window_pulls",
        lambda repo, ref, since: ([overridden(armed_at=("2026-10-02T11:50:00Z",))], []),
    )
    monkeypatch.setattr(digest, "fetch_trunk_runs", lambda repo: [trunk_run(1, BEFORE)])
    assert digest.main([]) == 0
    out = capsys.readouterr().out
    assert "| #7 | `aaaaaaaaa` | joeharris76 | 1s | Medium |" in out
    assert "- #7 armed at 2026-10-02T11:50:00Z" in out
    assert "was failure (run 1, `aaaaaaaaa`, completed 2026-10-02T09:00:00Z)" in out


def test_fetch_trunk_runs_paginates_to_a_short_page(monkeypatch):
    seen = []

    def fake_json(*args):
        seen.append(args[1])
        query = dict(part.split("=", 1) for part in args[1].split("?", 1)[1].split("&"))
        if query["page"] == "1":
            return {"workflow_runs": [{"id": index} for index in range(100)]}
        return {"workflow_runs": [{"id": 100}]}

    monkeypatch.setattr(digest, "gh_json", fake_json)
    assert [run["id"] for run in digest.fetch_trunk_runs("o/r")] == list(range(101))
    assert any(endpoint.endswith("page=2") for endpoint in seen)


def test_a_failed_window_listing_marks_every_section_unavailable(stubbed, monkeypatch, capsys):
    def broken(repo, ref, since):
        raise digest.ReadError("window gone")

    monkeypatch.setattr(digest, "collect_window_pulls", broken)
    assert digest.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert "The window's pull requests were unreadable: window gone" in out
    assert "Stand-in overrides are unavailable for this window" in out
    assert "Red-trunk armings are unavailable for this window" in out
    assert "Oracle withdrawals are unavailable for this window" in out
    assert stubbed["checkpoint"] == [HEAD]


def test_partial_coverage_qualifies_a_clean_section(stubbed, monkeypatch, capsys):
    unreadable = digest.UnreadablePull(9, "b" * 40, "threads exploded")
    monkeypatch.setattr(digest, "collect_window_pulls", lambda repo, ref, since: ([overridden()], [unreadable]))
    monkeypatch.setattr(digest, "fetch_trunk_runs", lambda repo: [])
    assert digest.main([]) == 0
    out = capsys.readouterr().out
    assert "Unreadable pull requests" in out
    assert "'None.' above covers only the PRs the digest could read" in out


def test_full_coverage_needs_no_qualifier(stubbed, monkeypatch, capsys):
    monkeypatch.setattr(digest, "collect_window_pulls", lambda repo, ref, since: ([overridden()], []))
    monkeypatch.setattr(digest, "fetch_trunk_runs", lambda repo: [])
    assert digest.main([]) == 0
    out = capsys.readouterr().out
    assert "Unreadable pull requests" not in out
    assert "covers only the PRs" not in out


def test_a_bold_line_that_is_not_a_verdict_section_does_not_close_the_findings():
    body = "\n".join(
        [
            "**Still open**",
            "",
            "- **Medium** `a.py:1`: D1: open",
            "",
            "**Note**",
            "",
            "- **High** `b.py:2`: D2: still open",
        ]
    )
    assert digest.still_open_severities(body) == ["Medium", "High"]


def test_a_withdrawn_mention_inside_text_does_not_close_the_findings():
    body = "\n".join(
        [
            "**Still open**",
            "",
            "- **Medium** `a.py:1`: D1: mentions Withdrawn: handling",
            "",
            "- **Low** `c.py:3`: D3: open",
        ]
    )
    assert digest.still_open_severities(body) == ["Medium", "Low"]


def test_a_known_closed_section_ends_the_findings():
    body = "\n".join(
        [
            "**Still open**",
            "",
            "- **Medium** `a.py:1`: D1: open",
            "",
            "**Reviewers not available**",
            "",
            "- absentee",
        ]
    )
    assert digest.still_open_severities(body) == ["Medium"]


@pytest.mark.parametrize(
    "comment",
    [
        digest.Comment("someone-else", 'Owner instruction: "go"', INSTRUCTION_AT, "User"),
        digest.Comment("joeharris76", 'Owner instruction: "go"', INSTRUCTION_AT, "Bot"),
        digest.Comment("joeharris76", 'Owner instruction: "go"', INSTRUCTION_AT, "User", LATER),
    ],
    ids=["non-attester", "bot-account", "edited"],
)
def test_an_instruction_from_an_untrusted_comment_does_not_count(comment):
    pull = evidence(reviews=(failure_review(),), comments=(comment, standin(HEAD, at=STANDIN_AT)))
    assert digest.standin_overrides([pull])[0].instruction_gap_seconds is None


APP_LOGIN = "benchbox-oracle[bot]"
APP_CREATOR = {"login": APP_LOGIN, "type": "Bot"}


def oracle_record(decision, **over):
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
    record.update(over)
    return record


def oracle_review_body(decision="SHIP", state="success", marker=True, **over):
    from oracle_reviewers import protocol

    body = f"### oracle-verdict: {state} for `{HEAD}`\n\nDecision text.\n"
    return body + ("\n" + protocol.encode_marker(oracle_record(decision, **over)) if marker else "")


def app_review(body, node_id="PRR_1", submitted_at=AFTER):
    return digest.Review(APP_LOGIN, HEAD, submitted_at, "COMMENTED", "Bot", body, node_id)


def app_status(state, description="d", created_at=AFTER, creator=APP_CREATOR, id_=1):
    return {
        "id": id_,
        "context": "oracle-verdict",
        "state": state,
        "description": description,
        "creator": creator,
        "created_at": created_at,
    }


def serve_verification(monkeypatch, *, edited=None, statuses=(), graphql_error=None, status_error=None):
    def graphql(node_ids):
        if graphql_error is not None:
            raise graphql_error
        return {node_id: (None, False) if node_id not in (edited or {}) else edited[node_id] for node_id in node_ids}

    def pages(endpoint):
        if status_error is not None:
            raise status_error
        assert endpoint == f"repos/o/r/commits/{HEAD}/statuses"
        return list(statuses)

    monkeypatch.setattr(digest, "_graphql_review_edits", graphql)
    monkeypatch.setattr(digest, "gh_pages", pages)


def test_an_unedited_review_agreeing_with_the_app_status_counts_as_oracle_evidence(monkeypatch):
    serve_verification(monkeypatch, statuses=[app_status("success")])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body())])
    assert checked.unverifiable == ""
    assert "oracle-review" in digest.review_signals(evidence(reviews=(checked,)))


def test_a_ship_with_fixes_review_agreeing_with_the_app_failure_status_counts(monkeypatch):
    serve_verification(monkeypatch, statuses=[app_status("failure")])
    body = oracle_review_body("SHIP_WITH_FIXES", "failure")
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(body)])
    assert checked.unverifiable == ""
    assert digest.blocking_verdict_decision(evidence(reviews=(checked,))) == "failure"


def test_an_edited_review_is_unverifiable_and_never_oracle_evidence(monkeypatch):
    serve_verification(monkeypatch, edited={"PRR_1": ("2026-10-03T00:00:00Z", False)}, statuses=[app_status("success")])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body())])
    assert "edited" in checked.unverifiable
    pull = evidence(reviews=(checked,))
    assert "oracle-review" not in digest.review_signals(pull)
    assert pull.unverifiable == (f"review on `{HEAD[:9]}`: {checked.unverifiable}",)


def test_a_created_edit_is_unverifiable_too(monkeypatch):
    serve_verification(monkeypatch, edited={"PRR_1": (None, True)}, statuses=[app_status("success")])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body())])
    assert "edited" in checked.unverifiable


@pytest.mark.parametrize(
    ("decision", "state", "status"),
    [("SHIP", "success", "failure"), ("SHIP_WITH_FIXES", "failure", "success")],
)
def test_a_review_disagreeing_with_the_app_status_is_unverifiable(monkeypatch, decision, state, status):
    serve_verification(monkeypatch, statuses=[app_status(status)])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body(decision, state))])
    assert "disagrees" in checked.unverifiable
    assert "oracle-review" not in digest.review_signals(evidence(reviews=(checked,)))


def test_a_marker_decision_contradicting_the_review_header_is_unverifiable(monkeypatch):
    serve_verification(monkeypatch, statuses=[app_status("success")])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body("DO_NOT_SHIP", "success"))])
    assert checked.unverifiable


def test_a_non_app_success_status_never_verifies_a_review(monkeypatch):
    serve_verification(monkeypatch, statuses=[app_status("success", creator={"login": "joe", "type": "User"})])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body())])
    assert "no oracle-verdict status" in checked.unverifiable


def test_the_review_is_matched_to_the_status_by_its_run_id(monkeypatch):
    statuses = [
        app_status("success", "ok [run 111]", "2026-10-02T10:00:00Z", id_=1),
        app_status("failure", "later", "2026-10-02T12:00:00Z", id_=2),
    ]
    serve_verification(monkeypatch, statuses=statuses)
    (verified,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body(run_uuid="111"))])
    assert verified.unverifiable == ""
    (other,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body(run_uuid="222"))])
    assert other.unverifiable


def test_a_review_with_no_readable_decision_is_unverifiable(monkeypatch):
    serve_verification(monkeypatch, statuses=[app_status("success")])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review("Free text, no verdict header.")])
    assert checked.unverifiable


def test_unreadable_edit_state_makes_every_oracle_review_unverifiable(monkeypatch):
    from oracle_reviewers import edits

    serve_verification(monkeypatch, graphql_error=edits.EditStateError("GraphQL returned errors"))
    reviews = [app_review(oracle_review_body()), review("joe", HEAD, user_type="User")]
    checked = digest.verify_oracle_reviews("o/r", reviews)
    assert "could not be read" in checked[0].unverifiable and checked[1].unverifiable == ""


def test_a_review_without_a_node_id_is_unverifiable(monkeypatch):
    serve_verification(monkeypatch, statuses=[app_status("success")])
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body(), node_id="")])
    assert checked.unverifiable


def test_unreadable_head_statuses_make_the_review_unverifiable(monkeypatch):
    serve_verification(monkeypatch, status_error=digest.ReadError("boom"))
    (checked,) = digest.verify_oracle_reviews("o/r", [app_review(oracle_review_body())])
    assert "statuses could not be read" in checked.unverifiable


def test_collect_pull_verifies_the_oracle_reviews_it_reads(monkeypatch):
    serve_verification(monkeypatch, edited={"PRR_1": (None, True)}, statuses=[app_status("success")])
    raw = {
        "user": {"login": APP_LOGIN, "type": "Bot"},
        "commit_id": HEAD,
        "submitted_at": AFTER,
        "state": "COMMENTED",
        "body": oracle_review_body(),
        "node_id": "PRR_1",
    }
    rest = {"reviews": [raw], "reactions": [], "timeline": [], "comments": []}
    monkeypatch.setattr(digest, "merged_pull_number", lambda repo, sha: 7)
    monkeypatch.setattr(digest, "gh_json", lambda *args: {"user": {"login": "dev"}, "merged_at": MERGED_AT})
    monkeypatch.setattr(digest, "collect_commits", lambda *args, **kwargs: ())
    monkeypatch.setattr(digest, "collect_threads", lambda repo, number: ())
    pages = digest.gh_pages
    monkeypatch.setattr(
        digest,
        "gh_pages",
        lambda endpoint: (
            rest[endpoint.rsplit("/", 1)[1]] if "/pulls/" in endpoint or "/issues/" in endpoint else pages(endpoint)
        ),
    )
    (collected,) = digest.collect_pull("o/r", MERGED).reviews
    assert collected.node_id == "PRR_1" and "edited" in collected.unverifiable


def comment_node(login, body, **edit):
    node = thread_node(False, login, HEAD, **edit)
    node["comments"]["nodes"][0]["body"] = body
    return node


@pytest.mark.parametrize(
    "edit",
    [{"last_edited_at": "2026-10-03T00:00:00Z"}, {"includes_created_edit": True}],
    ids=["edited-later", "created-edit"],
)
def test_the_severity_of_an_edited_app_first_comment_is_unverifiable(monkeypatch, edit):
    node = comment_node("benchbox-oracle", "**High**: the title", **edit)
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads([node]))
    (thread,) = digest.collect_threads("o/r", 7)
    assert thread.severity == "" and "edited" in thread.unverifiable
    assert digest.is_overridden_finding(thread)
    assert digest.open_finding_severities(evidence(threads=(thread,))) == ()
    assert evidence(threads=(thread,)).unverifiable == (f"review thread: {thread.unverifiable}",)


def test_an_edited_non_app_first_comment_keeps_its_reading(monkeypatch):
    node = comment_node("reviewer", "Consider **High** performance", includes_created_edit=True)
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads([node]))
    (thread,) = digest.collect_threads("o/r", 7)
    assert thread.unverifiable == ""


def test_an_app_first_comment_without_edit_state_is_unverifiable(monkeypatch):
    node = comment_node("benchbox-oracle", "**High**: the title")
    del node["comments"]["nodes"][0]["includesCreatedEdit"]
    monkeypatch.setattr(digest, "gh_json", lambda *args: graphql_threads([node]))
    (thread,) = digest.collect_threads("o/r", 7)
    assert thread.severity == "" and "could not be read" in thread.unverifiable


def test_the_digest_lists_unverifiable_oracle_evidence():
    checked = digest.Review(APP_LOGIN, HEAD, AFTER, "COMMENTED", "Bot", "x", "PRR_1", "it was edited after posting")
    assert digest.render_unverifiable([evidence(reviews=(checked,))]).splitlines()[2:] == [
        f"- #7: review on `{HEAD[:9]}`: it was edited after posting."
    ]
    assert digest.render_unverifiable([evidence()]) == ""


def unverifiable_failure_review(sha=HEAD):
    return digest.Review(
        ORACLE_BOT, sha, AFTER, "COMMENTED", "Bot", verdict_with_open(sha), "PRR_1", "it was edited after posting"
    )


def test_a_standin_over_an_edited_blocking_review_is_still_an_override():
    pull = evidence(reviews=(unverifiable_failure_review(),), comments=(standin(HEAD, at=STANDIN_AT),))
    found = digest.standin_overrides([pull])
    assert [override.severities for override in found] == [(digest.UNVERIFIABLE_FINDING,)]
    assert "unverifiable" in digest.render_standin_overrides(found)


def test_a_standin_over_an_edited_finding_thread_is_still_an_override():
    thread = digest.Thread(False, "benchbox-oracle", HEAD, "", "an oracle finding was edited after posting")
    pull = evidence(threads=(thread,), comments=(standin(HEAD, at=STANDIN_AT),))
    assert [override.severities for override in digest.standin_overrides([pull])] == [(digest.UNVERIFIABLE_FINDING,)]


def test_unverifiable_evidence_adds_to_the_verified_open_findings():
    verified = failure_review(body=verdict_with_open(HEAD, ("High",)))
    pull = evidence(reviews=(verified, unverifiable_failure_review()), comments=(standin(HEAD, at=STANDIN_AT),))
    assert digest.standin_overrides([pull])[0].severities == ("High", digest.UNVERIFIABLE_FINDING)


def test_a_standin_with_no_oracle_evidence_at_all_is_still_not_an_override():
    assert digest.standin_overrides([evidence(comments=(standin(HEAD, at=STANDIN_AT),))]) == []


def test_each_same_head_review_is_matched_to_the_status_nearest_its_submission(monkeypatch):
    statuses = [
        app_status("failure", "round one", "2026-10-02T10:00:00Z", id_=1),
        app_status("success", "round two", "2026-10-02T11:00:00Z", id_=2),
    ]
    serve_verification(monkeypatch, statuses=statuses)
    first = app_review(oracle_review_body("DO_NOT_SHIP", "failure"), "PRR_1", "2026-10-02T10:00:02Z")
    second = app_review(oracle_review_body("SHIP", "success"), "PRR_2", "2026-10-02T11:00:02Z")
    checked = digest.verify_oracle_reviews("o/r", [first, second])
    assert [item.unverifiable for item in checked] == ["", ""]


def test_an_edited_newer_review_voids_an_older_verified_success_signal():
    older = review(ORACLE_BOT, HEAD, submitted_at=PUSHED, user_type="Bot", body=oracle_verdict(HEAD, "success"))
    newer = digest.Review(
        ORACLE_BOT, HEAD, AFTER, "COMMENTED", "Bot", verdict_with_open(HEAD), "PRR_2", "it was edited after posting"
    )
    pull = evidence(reviews=(older, newer), comments=(standin(HEAD, at=STANDIN_AT),))
    assert "oracle-review" not in digest.review_signals(pull)
    assert [override.severities for override in digest.standin_overrides([pull])] == [(digest.UNVERIFIABLE_FINDING,)]


def test_an_unverifiable_review_sets_the_standin_cutoff_whatever_its_header_says():
    headerless = digest.Review(ORACLE_BOT, HEAD, AFTER, "COMMENTED", "Bot", "edited", "PRR_1", "it was edited")
    early = standin(HEAD, at=PUSHED)
    assert digest.standin_comment(evidence(reviews=(headerless,), comments=(early,))) is None


def test_an_edited_finding_thread_on_a_non_content_commit_is_flagged():
    thread = digest.Thread(False, "benchbox-oracle", OLD, "", "an oracle finding was edited after posting")
    pull = refreshed(threads=(thread,), comments=(standin(HEAD, at=STANDIN_AT),))
    assert digest.unverifiable_oracle_evidence(pull)


def test_a_resolved_edited_oracle_thread_stays_an_override_in_the_gap_class():
    thread = digest.Thread(True, "benchbox-oracle", HEAD, "", "an oracle finding was edited after posting")
    assert (
        digest.CLASS_OVERRIDE
        in digest.classify("f" * 40, "change", ["AGENTS.md"], evidence(threads=(thread,))).reason_classes
    )


def adjudication_comment(body="Oracle adjudication: WITHDRAW D2 - the helper keeps the row", at=AFTER, **overrides):
    fields = {"login": "joeharris76", "user_type": "User", "updated_at": None, **overrides}
    return digest.Comment(fields["login"], body, at, fields["user_type"], fields["updated_at"])


def test_the_digest_lists_an_adjudication_with_the_gap_to_its_owner_instruction():
    pull = evidence(comments=(instruction(BEFORE), adjudication_comment()))
    found = digest.adjudication_records([pull])
    assert [(record.number, record.action, record.defect_id) for record in found] == [(7, "WITHDRAW", "D2")]
    assert found[0].instruction_gap_seconds == 7200
    text = digest.render_adjudications(found)
    assert "#7" in text and "WITHDRAW D2" in text and "NO OWNER INSTRUCTION" not in text


def test_the_digest_flags_an_adjudication_without_a_prior_owner_instruction():
    found = digest.adjudication_records([evidence(comments=(adjudication_comment(), instruction(LATER)))])
    assert found[0].instruction_gap_seconds is None
    assert "NO OWNER INSTRUCTION COMMENT" in digest.render_adjudications(found)


@pytest.mark.parametrize(
    "comment",
    [
        adjudication_comment(login="someone"),
        adjudication_comment(user_type="Bot"),
        adjudication_comment(at=LATER),
        adjudication_comment("Oracle adjudication: WITHDRAW D2"),
    ],
    ids=["not an attester", "bot", "after the merge", "malformed"],
)
def test_the_digest_ignores_comments_that_are_not_valid_adjudications(comment):
    assert digest.adjudication_records([evidence(comments=(comment,))]) == []
    assert digest.render_adjudications([]) == ""


def test_an_edited_adjudication_comment_is_still_listed_and_noted():
    found = digest.adjudication_records([evidence(comments=(adjudication_comment(updated_at=LATER),))])
    assert [record.note for record in found] == ["comment edited after posting"]
    assert "comment edited after posting" in digest.render_adjudications(found)


def test_a_ruling_recorded_by_the_oracle_is_listed_when_its_comment_is_gone():
    from oracle_reviewers import protocol

    record = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "carry",
        "decision": "SHIP",
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "",
        "tier": "",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 2,
        "patch_digest": "d" * 32,
        "carried": True,
        "summary": "",
        "adjudicated": [{"id": "D2", "action": "WITHDRAW"}],
    }
    body = f"{oracle_verdict(HEAD, 'success')}\n\n{protocol.encode_marker(record)}"
    pull = evidence(reviews=(review(ORACLE_BOT, HEAD, AFTER, user_type="Bot", body=body),))
    found = digest.adjudication_records([pull])
    assert [(r.action, r.defect_id) for r in found] == [("WITHDRAW", "D2")]
    assert "NO OWNER INSTRUCTION" in digest.render_adjudications(found)
    assert "comment is missing" in digest.render_adjudications(found)


def test_a_recovered_ruling_needs_an_instruction_before_the_review_that_preceded_it():
    from oracle_reviewers import protocol

    base = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": "SHIP_WITH_FIXES",
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "",
        "tier": "",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 2,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
    }
    ruled = {**base, "kind": "carry", "decision": "SHIP", "carried": True}
    ruled["adjudicated"] = [{"id": "D2", "action": "WITHDRAW"}]

    def posted(record, at):
        body = f"{oracle_verdict(HEAD, 'success')}\n\n{protocol.encode_marker(record)}"
        return review(ORACLE_BOT, HEAD, at, user_type="Bot", body=body)

    reviews = (posted(base, "2026-10-02T10:30:00Z"), posted(ruled, AFTER))
    late = digest.adjudication_records([evidence(reviews=reviews, comments=(instruction("2026-10-02T10:45:00Z"),))])
    early = digest.adjudication_records([evidence(reviews=reviews, comments=(instruction("2026-10-02T10:15:00Z"),))])
    assert late[0].instruction_gap_seconds is None
    assert early[0].instruction_gap_seconds is not None
