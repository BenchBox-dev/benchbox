from __future__ import annotations

import pytest

from scripts import release_artifact_consumer as consumer
from tests.unit.scripts.test_release_artifact_execution import git
from tests.unit.scripts.test_release_releasable import TrunkHistory

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture
def develop(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    commits: dict[str, str] = {}
    for name in ("a", "b", "c", "d"):
        (root / "file.txt").write_text(name)
        git(root, "add", "file.txt")
        git(root, "commit", "-q", "-m", name)
        commits[name] = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "side", commits["a"])
    (root / "file.txt").write_text("side")
    git(root, "commit", "-q", "-a", "-m", "side")
    commits["side"] = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "develop")
    return root, commits


@pytest.fixture
def replaced(develop):
    root, commits = develop
    history = TrunkHistory()
    history.push(commits["a"])
    history.push(commits["c"])
    history.push(commits["side"])
    history.push(commits["d"])
    return root, commits, history


def test_replaced_commit_names_the_nearest_tested_descendant_and_ancestor(replaced):
    root, commits, history = replaced
    found = consumer.nearest_tested_commits(root, commits["b"], history.api, "develop")
    assert found == {"descendant": commits["c"], "ancestor": commits["a"]}


def test_a_tested_commit_without_a_dist_is_not_named(replaced):
    root, commits, history = replaced
    history.push(commits["c"], dist=False)
    history.runs[1]["conclusion"] = "failure"
    found = consumer.nearest_tested_commits(root, commits["b"], history.api, "develop")
    assert found == {"descendant": commits["d"], "ancestor": commits["a"]}


def test_admission_refuses_a_replaced_commit_and_names_the_nearest_tested_commit(replaced, monkeypatch, tmp_path):
    root, commits, history = replaced
    source = {"head_sha": commits["b"], "version": "0.4.2"}
    monkeypatch.setattr(consumer, "resolve_tag", lambda *_: source)
    monkeypatch.setattr(consumer, "protected_line", lambda *_: "develop")
    output = tmp_path / "admitted"
    with pytest.raises(ValueError, match="no trunk producer on develop") as error:
        consumer.admit(root, "v0.4.2", output, history.api)
    message = str(error.value)
    assert commits["b"] in message
    assert f"nearest tested descendant on develop: {commits['c']}" in message
    assert f"nearest tested ancestor on develop: {commits['a']}" in message
    assert not output.exists()


def test_a_failing_lookup_still_refuses_the_replaced_commit(replaced, monkeypatch, tmp_path):
    root, commits, history = replaced

    def failing(path: str):
        if "status=success" in path:
            raise ValueError("API unavailable")
        return history.api(path)

    monkeypatch.setattr(consumer, "resolve_tag", lambda *_: {"head_sha": commits["b"], "version": "0.4.2"})
    monkeypatch.setattr(consumer, "protected_line", lambda *_: "develop")
    with pytest.raises(ValueError, match=r"no trunk producer on develop.*could not be determined \(API unavailable\)"):
        consumer.admit(root, "v0.4.2", tmp_path / "admitted", failing)
