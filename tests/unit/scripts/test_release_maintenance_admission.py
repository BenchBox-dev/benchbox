from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import release_artifact_consumer as consumer
from tests.unit.scripts.test_release_artifact_consumer import SHA, distributions as distributions, metadata as metadata
from tests.unit.scripts.test_release_artifact_execution import bind_admission_fixture, git

pytestmark = [pytest.mark.unit, pytest.mark.medium]

LINE = "release/0.4.x"
ADMISSION_RECEIPT_KEYS = {
    "tag",
    "tag_object",
    "head_sha",
    "version",
    "producer",
    "artifact_id",
    "artifact_digest",
    "expires_at",
}


def commit_version(root: Path, version: str) -> str:
    (root / "pyproject.toml").write_text(f'[project]\nname = "benchbox"\nversion = "{version}"\n')
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/verify_distribution_binaries.py").write_text("import sys\nsys.exit(0)\n")
    (root / "benchbox/utils").mkdir(parents=True, exist_ok=True)
    (root / "benchbox/utils/binary_manifest.py").write_text("")
    git(root, "add", "pyproject.toml", "scripts/verify_distribution_binaries.py", "benchbox/utils/binary_manifest.py")
    git(root, "commit", "-m", f"Version {version}")
    return git(root, "rev-parse", "HEAD")


def tag(root: Path, name: str) -> None:
    git(root, "tag", "-a", name, "-m", name)


@pytest.fixture
def maintenance_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    commit_version(root, "0.4.1")
    tag(root, "v0.4.1")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "branch", LINE)
    commit_version(root, "0.5.0")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "checkout", "-q", LINE)
    commit_version(root, "0.4.2")
    git(root, "update-ref", f"refs/remotes/origin/{LINE}", "HEAD")
    tag(root, "v0.4.2")
    return root


def test_tag_on_the_maintenance_branch_resolves_to_that_line(maintenance_source):
    source = consumer.resolve_tag(maintenance_source, "v0.4.2")
    assert consumer.protected_line(maintenance_source, "v0.4.2", source["head_sha"]) == LINE


def test_resolution_does_not_add_the_line_to_the_admission_identity(maintenance_source):
    assert set(consumer.resolve_tag(maintenance_source, "v0.4.2")) == {"tag", "tag_object", "head_sha", "version"}


def test_develop_is_the_line_when_no_maintenance_branch_exists(maintenance_source):
    git(maintenance_source, "checkout", "-q", "develop")
    tag(maintenance_source, "v0.5.0")
    source = consumer.resolve_tag(maintenance_source, "v0.5.0")
    assert consumer.protected_line(maintenance_source, "v0.5.0", source["head_sha"]) == "develop"


def test_a_develop_only_commit_is_refused_once_the_maintenance_branch_exists(maintenance_source):
    git(maintenance_source, "checkout", "-q", "develop")
    commit_version(maintenance_source, "0.4.3")
    git(maintenance_source, "update-ref", "refs/remotes/origin/develop", "HEAD")
    tag(maintenance_source, "v0.4.3")
    with pytest.raises(ValueError, match=r"release/0\.4\.x exists, so v0\.4\.3 must be tagged on it"):
        consumer.resolve_tag(maintenance_source, "v0.4.3")


def test_a_tag_for_another_minor_on_the_maintenance_branch_is_refused(maintenance_source):
    commit = git(maintenance_source, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match=r"neither develop nor release/0\.5\.x"):
        consumer.protected_line(maintenance_source, "v0.5.1", commit)


def test_a_commit_on_both_lines_is_refused(maintenance_source):
    shared = git(maintenance_source, "rev-parse", "v0.4.1^{commit}")
    with pytest.raises(ValueError, match="both develop and release/0.4.x"):
        consumer.protected_line(maintenance_source, "v0.4.2", shared)


def test_a_commit_on_no_protected_line_is_refused(maintenance_source):
    git(maintenance_source, "checkout", "-q", "-b", "feature")
    commit = commit_version(maintenance_source, "0.4.3")
    with pytest.raises(ValueError, match="neither develop nor release/0.4.x"):
        consumer.protected_line(maintenance_source, "v0.4.3", commit)


def test_the_line_command_names_the_branch_the_tag_belongs_to(maintenance_source, capsys):
    assert consumer.main(["line", "--source", str(maintenance_source), "--tag", "v0.4.2"]) == 0
    assert capsys.readouterr().out.strip() == LINE


def test_the_line_command_refuses_a_tag_off_every_line(maintenance_source, capsys):
    git(maintenance_source, "checkout", "-q", "develop")
    commit_version(maintenance_source, "0.4.3")
    git(maintenance_source, "update-ref", "refs/remotes/origin/develop", "HEAD")
    tag(maintenance_source, "v0.4.3")
    with pytest.raises(SystemExit) as error:
        consumer.main(["line", "--source", str(maintenance_source), "--tag", "v0.4.3"])
    assert error.value.code == 1
    assert "must be tagged on it" in capsys.readouterr().err


def test_producer_runs_are_listed_for_the_requested_branch(metadata):
    metadata["run"]["head_branch"] = LINE
    consumer.select_producer(SHA, metadata["api"], branch=LINE)
    listing = [path for path in metadata["calls"] if path.startswith("actions/workflows/")]
    assert listing
    for path in listing:
        assert "branch=release%2F0.4.x" in path
        assert parse_qs(urlsplit(path).query)["branch"] == [LINE]


def test_a_develop_producer_does_not_satisfy_a_maintenance_tag(metadata):
    with pytest.raises(ValueError, match="wrong producer branch"):
        consumer.select_producer(SHA, metadata["api"], branch=LINE)


def test_a_maintenance_producer_does_not_satisfy_a_develop_tag(metadata):
    metadata["run"]["head_branch"] = LINE
    with pytest.raises(ValueError, match="wrong producer branch"):
        consumer.select_producer(SHA, metadata["api"])


def test_a_maintenance_commit_without_any_trunk_run_is_refused(metadata):
    metadata["runs"] = []
    with pytest.raises(ValueError, match=r"no trunk producer on release/0\.4\.x"):
        consumer.select_producer(SHA, metadata["api"], branch=LINE)


def test_a_maintenance_commit_whose_trunk_run_failed_is_refused(metadata):
    metadata["run"]["head_branch"] = LINE
    metadata["run"]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="latest producer is not successful"):
        consumer.select_producer(SHA, metadata["api"], branch=LINE)


@pytest.mark.parametrize("branch", ["feature/x", "release", "release/0.4", "release/0.4.x/extra", "release/a.b.x", ""])
def test_only_develop_and_maintenance_branches_can_produce(metadata, branch):
    with pytest.raises(ValueError, match="invalid producer branch"):
        consumer.select_producer(SHA, metadata["api"], branch=branch)


def test_the_producer_receipt_accepts_a_maintenance_run_and_refuses_other_branches(distributions, metadata):
    metadata["run"]["head_branch"] = LINE
    assert consumer.producer_receipt(distributions, metadata["run"], metadata["job"])["head_sha"] == SHA
    metadata["run"]["head_branch"] = "feature/x"
    with pytest.raises(ValueError, match="wrong producer branch"):
        consumer.producer_receipt(distributions, metadata["run"], metadata["job"])


def test_admission_succeeds_for_a_tag_on_the_maintenance_branch(
    tmp_path, monkeypatch, maintenance_source, distributions, metadata
):
    metadata["run"]["head_branch"] = LINE
    bind_admission_fixture(maintenance_source, distributions, metadata, tmp_path, monkeypatch)
    output = tmp_path / "admitted"
    summary = consumer.admit(maintenance_source, "v0.4.2", output, metadata["api"])
    assert summary["tag"] == "v0.4.2" and summary["version"] == "0.4.2"
    receipt = json.loads((output / "admission-receipt.json").read_text(encoding="utf-8"))
    assert set(receipt) == ADMISSION_RECEIPT_KEYS
    assert any("branch=release%2F0.4.x" in path for path in metadata["calls"])


def test_admission_refuses_a_maintenance_tag_whose_trunk_run_was_on_develop(
    tmp_path, monkeypatch, maintenance_source, distributions, metadata
):
    bind_admission_fixture(maintenance_source, distributions, metadata, tmp_path, monkeypatch)
    output = tmp_path / "admitted"
    with pytest.raises(ValueError, match="wrong producer branch"):
        consumer.admit(maintenance_source, "v0.4.2", output, metadata["api"])
    assert not output.exists()


def test_admission_refuses_a_maintenance_commit_without_a_trunk_run(
    tmp_path, monkeypatch, maintenance_source, distributions, metadata
):
    bind_admission_fixture(maintenance_source, distributions, metadata, tmp_path, monkeypatch)
    metadata["runs"] = []
    output = tmp_path / "admitted"
    with pytest.raises(ValueError, match=r"no trunk producer on release/0\.4\.x"):
        consumer.admit(maintenance_source, "v0.4.2", output, metadata["api"])
    assert not output.exists()


def hosted_repository(tmp_path: Path, *, maintenance: bool) -> Path:
    hosted = tmp_path / "hosted"
    hosted.mkdir()
    git(hosted, "init", "-b", "develop")
    git(hosted, "config", "user.name", "Test Fixture")
    git(hosted, "config", "user.email", "fixture@example.invalid")
    commit_version(hosted, "0.4.1")
    tag(hosted, "v0.4.1")
    if maintenance:
        git(hosted, "branch", LINE)
    return hosted


def fetch_into_fresh_checkout(tmp_path: Path, hosted: Path) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-b", "develop")
    subprocess.run(
        ["git", "-C", str(checkout), "fetch", "-q", "--no-tags", str(hosted), *consumer.hosted_refspecs("v0.4.1")],
        check=True,
    )
    return checkout


def test_the_hosted_fetch_brings_in_maintenance_branches(tmp_path):
    checkout = fetch_into_fresh_checkout(tmp_path, hosted_repository(tmp_path, maintenance=True))
    assert git(checkout, "rev-parse", f"refs/remotes/origin/{LINE}") == git(
        checkout, "rev-parse", "refs/remotes/origin/develop"
    )


def test_the_hosted_fetch_succeeds_when_no_maintenance_branch_exists(tmp_path):
    checkout = fetch_into_fresh_checkout(tmp_path, hosted_repository(tmp_path, maintenance=False))
    assert git(checkout, "rev-parse", "refs/remotes/origin/develop")
    assert (
        subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{LINE}"],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )
