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
digest = importlib.import_module("_project.scripts.soundness_merge_digest")

LINE = "release/0.5.x"
DEVELOP_HEAD = "a" * 40
LINE_HEAD = "c" * 40
DEVELOP_CHECKPOINT = "1" * 40
LINE_CHECKPOINT = "2" * 40
FORK_POINT = "3" * 40
CREATION = "4" * 40
MERGED = "e" * 40


@pytest.fixture(autouse=True)
def _develop_branch(monkeypatch):
    monkeypatch.setattr(digest, "_BRANCH", "develop")


class Shell:
    def __init__(self):
        self.calls = []
        self.replies = {}

    def __call__(self, args, *, input_text=None, cwd=None):
        key = tuple(args)
        self.calls.append(key)
        if key not in self.replies:
            raise AssertionError(f"unexpected command {list(key)}")
        reply = self.replies[key]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def shell(monkeypatch):
    fake = Shell()
    monkeypatch.setattr(digest, "run", fake)
    return fake


def label_url(label):
    return f"repos/o/r/issues?labels={label}&state=all&per_page=100"


def pages(shell, label, *issues):
    shell.replies[("gh", "api", "--paginate", "--slurp", label_url(label))] = json.dumps([list(issues)])


def line_marker(branch):
    return f"<!-- soundness-digest-branch: {branch} -->"


def line_issue(number, branch, sha):
    return {"number": number, "body": f"{line_marker(branch)}\n<!-- soundness-digest-checkpoint: {sha} -->\n\nreport"}


def test_a_release_line_reads_its_own_state_issue_and_never_the_develop_one(shell):
    pages(
        shell,
        "soundness-merge-digest-line",
        line_issue(8, "release/0.4.x", "9" * 40),
        line_issue(9, LINE, LINE_CHECKPOINT),
    )
    digest.set_branch(LINE)
    assert digest.read_checkpoint("o/r") == LINE_CHECKPOINT
    assert all("labels=soundness-merge-digest&" not in part for key in shell.calls for part in key)


def test_the_develop_checkpoint_still_reads_the_develop_label(shell):
    body = f"<!-- soundness-digest-checkpoint: {DEVELOP_CHECKPOINT} -->"
    pages(shell, "soundness-merge-digest", {"number": 4, "body": body})
    digest.set_branch("develop")
    assert digest.read_checkpoint("o/r") == DEVELOP_CHECKPOINT


def test_a_release_line_without_a_state_issue_has_no_checkpoint(shell):
    pages(shell, "soundness-merge-digest-line", line_issue(8, "release/0.4.x", "9" * 40))
    digest.set_branch(LINE)
    assert digest.read_checkpoint("o/r") is None


def test_two_state_issues_for_one_line_are_refused(shell):
    pages(
        shell,
        "soundness-merge-digest-line",
        line_issue(8, LINE, LINE_CHECKPOINT),
        line_issue(9, LINE, LINE_CHECKPOINT),
    )
    digest.set_branch(LINE)
    with pytest.raises(digest.ReadError):
        digest.read_checkpoint("o/r")


def test_the_first_line_checkpoint_creates_a_line_issue_carrying_the_branch_marker(shell):
    pages(shell, "soundness-merge-digest-line")
    body = f"{line_marker(LINE)}\n<!-- soundness-digest-checkpoint: {LINE_HEAD} -->\n\nthe report\n"
    shell.replies[
        ("gh", "label", "create", "soundness-merge-digest-line", "--repo", "o/r", "--force", "--color", "0e8a16")
    ] = ""
    shell.replies[
        (
            "gh",
            "issue",
            "create",
            "--repo",
            "o/r",
            "--title",
            f"Soundness merge digest: {LINE}",
            "--label",
            "soundness-merge-digest-line",
            "--body",
            body,
        )
    ] = ""
    digest.set_branch(LINE)
    digest.write_checkpoint("o/r", LINE_HEAD, "the report")
    assert shell.calls[-1][:3] == ("gh", "issue", "create")


def test_a_line_checkpoint_edits_only_that_lines_issue(shell):
    pages(
        shell,
        "soundness-merge-digest-line",
        line_issue(8, "release/0.4.x", "9" * 40),
        line_issue(9, LINE, LINE_CHECKPOINT),
    )
    body = f"{line_marker(LINE)}\n<!-- soundness-digest-checkpoint: {LINE_HEAD} -->\n\nthe report\n"
    shell.replies[("gh", "issue", "edit", "9", "--repo", "o/r", "--body", body)] = ""
    digest.set_branch(LINE)
    digest.write_checkpoint("o/r", LINE_HEAD, "the report")
    assert shell.calls[-1] == ("gh", "issue", "edit", "9", "--repo", "o/r", "--body", body)
    assert all(key[:3] != ("gh", "label", "create") for key in shell.calls)


def test_a_release_merge_is_matched_to_pull_requests_into_that_line(monkeypatch):
    pulls = [
        {"number": 3, "merge_commit_sha": MERGED, "merged_at": "t", "base": {"ref": "develop"}},
        {"number": 7, "merge_commit_sha": MERGED, "merged_at": "t", "base": {"ref": LINE}},
    ]
    monkeypatch.setattr(digest, "gh_pages", lambda endpoint: pulls)
    digest.set_branch(LINE)
    assert digest.merged_pull_number("o/r", MERGED) == 7
    digest.set_branch("develop")
    assert digest.merged_pull_number("o/r", MERGED) == 3


def test_trunk_runs_are_read_for_the_audited_line(shell):
    url = "repos/o/r/actions/workflows/trunk.yml/runs?branch=release%2F0.5.x&event=push&status=completed&per_page=100&page=1"
    shell.replies[("gh", "api", url)] = json.dumps({"workflow_runs": [{"id": 1}]})
    digest.set_branch(LINE)
    assert [run["id"] for run in digest.fetch_trunk_runs("o/r")] == [1]


@pytest.mark.parametrize("branch", ["feature/x", "release", "release/", "release/0.5.x/extra", "main", "release/.x"])
def test_a_branch_that_is_neither_develop_nor_a_release_line_is_refused(branch):
    with pytest.raises(digest.ReadError):
        digest.set_branch(branch)


@pytest.mark.parametrize("branch", ["develop", "release/0.5.x", "release/1.0.x"])
def test_develop_and_release_lines_are_accepted(branch):
    digest.set_branch(branch)
    assert branch == digest._BRANCH


def test_the_first_commit_after_the_fork_point_is_where_a_new_line_starts(shell):
    shell.replies[("git", "merge-base", "origin/develop", LINE_HEAD)] = FORK_POINT + "\n"
    shell.replies[("git", "rev-list", "--first-parent", "--reverse", f"{FORK_POINT}..{LINE_HEAD}")] = (
        f"{CREATION}\n{MERGED}\n{LINE_HEAD}\n"
    )
    assert digest.line_origin(LINE_HEAD) == CREATION


def test_a_line_with_no_commits_of_its_own_starts_at_the_fork_point(shell):
    shell.replies[("git", "merge-base", "origin/develop", FORK_POINT)] = FORK_POINT + "\n"
    shell.replies[("git", "rev-list", "--first-parent", "--reverse", f"{FORK_POINT}..{FORK_POINT}")] = ""
    assert digest.line_origin(FORK_POINT) == FORK_POINT


class Wired:
    def __init__(self, monkeypatch):
        self.stored = {"develop": DEVELOP_CHECKPOINT, LINE: None}
        self.written = []
        self.windows = []
        self.git = {
            ("git", "rev-parse", "origin/develop"): DEVELOP_HEAD + "\n",
            ("git", "rev-parse", f"origin/{LINE}"): LINE_HEAD + "\n",
            ("git", "rev-parse", "--verify", f"{DEVELOP_CHECKPOINT}^{{commit}}"): DEVELOP_CHECKPOINT + "\n",
            ("git", "rev-parse", "--verify", f"{LINE_CHECKPOINT}^{{commit}}"): LINE_CHECKPOINT + "\n",
            ("git", "rev-parse", "--verify", f"{CREATION}^{{commit}}"): CREATION + "\n",
            ("git", "rev-list", "--first-parent", DEVELOP_HEAD): f"{DEVELOP_HEAD}\n{DEVELOP_CHECKPOINT}\n",
            ("git", "rev-list", "--first-parent", LINE_HEAD): f"{LINE_HEAD}\n{LINE_CHECKPOINT}\n{CREATION}\n",
            ("git", "merge-base", "origin/develop", LINE_HEAD): FORK_POINT + "\n",
            ("git", "rev-list", "--first-parent", "--reverse", f"{FORK_POINT}..{LINE_HEAD}"): (
                f"{CREATION}\n{LINE_CHECKPOINT}\n{LINE_HEAD}\n"
            ),
        }
        self.verified = []
        self.verify_error = None
        self.engine_loads = []
        monkeypatch.setattr(digest, "load_engine", self.load_engine)
        monkeypatch.setattr(digest, "verify_new_line", self.verify_new_line)
        monkeypatch.setattr(digest, "run", self.run)
        monkeypatch.setattr(digest, "read_checkpoint", lambda repo: self.stored[digest._BRANCH])
        monkeypatch.setattr(digest, "write_checkpoint", self.write_checkpoint)
        monkeypatch.setattr(digest, "collect_entries", self.collect_entries)
        monkeypatch.setattr(digest, "collect_window_pulls", lambda repo, ref, since: ([], []))
        monkeypatch.setattr(digest, "fetch_trunk_runs", lambda repo: [])
        monkeypatch.setattr(digest, "gap_closures", lambda repo, entries: [])
        monkeypatch.setattr(digest, "open_gap_issues", lambda repo, entries: [])
        monkeypatch.setattr(digest, "close_gap_issues", lambda repo, closures: None)

    def run(self, args, **_):
        key = tuple(args)
        if key[:3] == ("git", "cat-file", "-e"):
            return ""
        if key not in self.git:
            raise AssertionError(f"unexpected command {list(key)}")
        return self.git[key]

    def load_engine(self, root, ref):
        self.engine_loads.append((digest._BRANCH, ref))

    def verify_new_line(self, until):
        if self.verify_error is not None:
            raise self.verify_error
        self.verified.append(until)

    def collect_entries(self, repo, ref, since):
        self.windows.append((digest._BRANCH, ref, since))
        return []

    def write_checkpoint(self, repo, sha, report):
        self.written.append((digest._BRANCH, sha))
        self.stored[digest._BRANCH] = sha


@pytest.fixture
def wired(monkeypatch):
    return Wired(monkeypatch)


def test_a_release_line_is_audited_from_its_own_checkpoint_not_develops(wired, capsys):
    wired.stored[LINE] = LINE_CHECKPOINT
    assert digest.main(["--apply", "--branch", LINE]) == 0
    assert wired.windows == [(LINE, LINE_HEAD, LINE_CHECKPOINT)]
    assert wired.written == [(LINE, LINE_HEAD)]
    assert capsys.readouterr().out.startswith(f"Soundness-path commits on {LINE} from {LINE_CHECKPOINT[:9]}")


def test_a_release_audit_leaves_the_develop_checkpoint_unchanged(wired):
    wired.stored[LINE] = LINE_CHECKPOINT
    assert digest.main(["--apply", "--branch", LINE]) == 0
    assert wired.stored["develop"] == DEVELOP_CHECKPOINT
    assert digest.main(["--apply"]) == 0
    assert wired.written == [(LINE, LINE_HEAD), ("develop", DEVELOP_HEAD)]
    assert wired.windows[-1] == ("develop", DEVELOP_HEAD, DEVELOP_CHECKPOINT)


def test_the_first_audit_of_a_new_line_verifies_it_and_starts_at_its_creation_commit(wired):
    assert digest.main(["--apply", "--branch", LINE]) == 0
    assert wired.verified == [LINE_HEAD]
    assert wired.windows == [(LINE, LINE_HEAD, CREATION)]
    assert wired.written == [(LINE, LINE_HEAD)]


def test_a_line_that_fails_verification_is_not_audited_or_checkpointed(wired, capsys):
    wired.verify_error = digest.ReadError("line verification failed")
    assert digest.main(["--apply", "--branch", LINE]) == 2
    assert "line verification failed" in capsys.readouterr().err
    assert wired.windows == wired.written == []


def test_a_line_with_a_stored_checkpoint_is_not_reverified(wired):
    wired.stored[LINE] = LINE_CHECKPOINT
    assert digest.main(["--apply", "--branch", LINE]) == 0
    assert wired.verified == []


def test_a_line_is_judged_by_the_develop_policy_and_develop_by_its_own_ref(wired):
    assert digest.main(["--apply", "--branch", LINE]) == 0
    assert digest.main(["--apply"]) == 0
    assert [ref for _, ref in wired.engine_loads] == ["origin/develop", "origin/develop"]


def test_the_creation_check_failure_is_reported_as_a_read_error(monkeypatch, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "release_artifact_consumer.py").write_text(
        "def github_json_list(path):\n    return []\n\n\n"
        "def verify_line(root, branch, tip, pulls):\n    raise ValueError('not a creation commit')\n"
    )
    monkeypatch.setattr(digest, "REPO_ROOT", tmp_path)
    digest.set_branch(LINE)
    with pytest.raises(digest.ReadError, match="not a creation commit"):
        digest.verify_new_line(LINE_HEAD)


def test_a_line_that_passes_the_creation_check_is_accepted(monkeypatch, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "release_artifact_consumer.py").write_text(
        "def github_json_list(path):\n    return []\n\n\ndef verify_line(root, branch, tip, pulls):\n    return None\n"
    )
    monkeypatch.setattr(digest, "REPO_ROOT", tmp_path)
    digest.set_branch(LINE)
    digest.verify_new_line(LINE_HEAD)


def test_develop_still_needs_a_stored_checkpoint(wired, capsys):
    wired.stored["develop"] = None
    assert digest.main(["--apply"]) == 2
    assert "No checkpoint stored" in capsys.readouterr().err
    assert wired.written == []


@pytest.mark.parametrize("branch", ["feature/x", "release", "release/0.5.x/extra"])
def test_an_unknown_branch_fails_closed_before_anything_is_read(wired, capsys, branch):
    assert digest.main(["--apply", "--branch", branch]) == 2
    assert "audits only those branches" in capsys.readouterr().err
    assert wired.windows == wired.written == []


def test_the_ref_defaults_to_the_audited_branch():
    assert digest._parse_args(["--branch", LINE]).ref == f"origin/{LINE}"
    assert digest._parse_args([]).ref == "origin/develop"
    assert digest._parse_args(["--branch", LINE, "--ref", "x"]).ref == "x"
