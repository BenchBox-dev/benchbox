from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import release_artifact_consumer as consumer, release_line_create
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
    line_head = git(maintenance_source, "rev-parse", "HEAD")
    summary = consumer.admit(maintenance_source, "v0.4.2", output, metadata["api"], merged_pulls(line_head))
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
        consumer.admit(
            maintenance_source,
            "v0.4.2",
            output,
            metadata["api"],
            merged_pulls(git(maintenance_source, "rev-parse", "HEAD")),
        )
    assert not output.exists()


def test_admission_refuses_a_maintenance_commit_without_a_trunk_run(
    tmp_path, monkeypatch, maintenance_source, distributions, metadata
):
    bind_admission_fixture(maintenance_source, distributions, metadata, tmp_path, monkeypatch)
    metadata["runs"] = []
    output = tmp_path / "admitted"
    with pytest.raises(ValueError, match=r"no trunk producer on release/0\.4\.x"):
        consumer.admit(
            maintenance_source,
            "v0.4.2",
            output,
            metadata["api"],
            merged_pulls(git(maintenance_source, "rev-parse", "HEAD")),
        )
    assert not output.exists()


def test_admission_refuses_a_maintenance_tag_that_is_not_a_merged_pull_request(
    tmp_path, monkeypatch, maintenance_source, distributions, metadata
):
    metadata["run"]["head_branch"] = LINE
    bind_admission_fixture(maintenance_source, distributions, metadata, tmp_path, monkeypatch)
    output = tmp_path / "admitted"
    with pytest.raises(ValueError, match="does not point at a pull request merged into release/0.4.x"):
        consumer.admit(maintenance_source, "v0.4.2", output, metadata["api"], merged_pulls())
    assert not output.exists()


def merged_pulls(*commits: str, branch: str = LINE, repository: str = consumer.REPOSITORY):
    merged = set(commits)

    def pulls(path: str) -> list[dict]:
        sha = path.split("/")[1]
        if sha not in merged:
            return []
        return [
            {
                "merge_commit_sha": sha,
                "merged_at": "2026-11-10T00:00:00Z",
                "base": {"ref": branch, "repo": {"full_name": repository}},
            }
        ]

    return pulls


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


@pytest.fixture
def created_line(tmp_path):
    root = tmp_path / "created"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    write(root, ".github/review.yml", "weak policy\n")
    git(root, "add", ".github/review.yml")
    git(root, "commit", "-q", "-m", "Early reviewer policy")
    shas = {"before_tag": git(root, "rev-parse", "HEAD")}
    write(root, ".github/oracle.yml", "legacy\n")
    write(root, ".github/workflows/oracle-gate.yml", "legacy gate\n")
    write(root, ".github/review.yml", "tag policy\n")
    git(root, "add", ".github")
    shas["released"] = commit_version(root, "0.4.1")
    tag(root, "v0.4.1")
    git(root, "rm", "-q", ".github/oracle.yml", ".github/workflows/oracle-gate.yml")
    write(root, ".github/review.yml", "reviewed policy\n")
    write(root, ".github/workflows/review-gate.yml", "reviewed gate\n")
    git(root, "add", ".github")
    git(root, "commit", "-q", "-m", "Rename the reviewer policy")
    shas["reviewed"] = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "side")
    write(root, ".github/review.yml", "side policy\n")
    git(root, "add", ".github/review.yml")
    git(root, "commit", "-q", "-m", "Side policy")
    shas["side"] = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "develop")
    git(root, "merge", "-q", "--no-ff", "-s", "ours", "-m", "Record side history", "side")
    shas["tip"] = commit_version(root, "0.5.0")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "checkout", "-q", "-b", LINE, shas["released"])
    for name, sha in shas.items():
        git(root, "update-ref", f"refs/fixture/{name}", sha)
    return root


def fixture_sha(root: Path, name: str) -> str:
    return git(root, "rev-parse", f"refs/fixture/{name}")


def create(
    root: Path, source: str | None, *, policy: str | None = "reviewed policy\n", workflows: bool = True, edit=None
) -> str:
    git(root, "rm", "-q", ".github/oracle.yml")
    if policy is not None:
        write(root, ".github/review.yml", policy)
        git(root, "add", ".github/review.yml")
    if workflows:
        git(root, "rm", "-q", ".github/workflows/oracle-gate.yml")
        write(root, ".github/workflows/review-gate.yml", "reviewed gate\n")
        git(root, "add", ".github/workflows/review-gate.yml")
    if edit:
        edit(root)
    message = "Carry develop's reviewer files"
    if source:
        message += f"\n\nDevelop-Source: {source}"
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def land(root: Path, version: str) -> str:
    commit = commit_version(root, version)
    git(root, "update-ref", f"refs/remotes/origin/{LINE}", "HEAD")
    return commit


@pytest.mark.parametrize("source", ["reviewed", "tip"])
def test_a_line_created_from_one_develop_snapshot_is_admitted(created_line, source):
    create(created_line, fixture_sha(created_line, source))
    fixed = land(created_line, "0.4.2")
    consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(fixed))


def test_a_line_created_at_the_bare_tag_needs_only_merged_pull_requests(created_line):
    first = land(created_line, "0.4.2rc1")
    second = land(created_line, "0.4.2rc2")
    fixed = land(created_line, "0.4.2")
    consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(first, second, fixed))
    with pytest.raises(ValueError, match=f"{LINE} commit {second} is neither a pull request merged into it"):
        consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(first, fixed))


@pytest.mark.parametrize(
    ("policy", "edit", "expected"),
    [
        ("unreviewed policy\n", None, r"sets \.github/review\.yml, which differs from develop at"),
        ("weak policy\n", None, r"sets \.github/review\.yml, which differs from develop at"),
        (
            "reviewed policy\n",
            lambda root: git(root, "update-index", "--chmod=+x", ".github/review.yml"),
            r"sets \.github/review\.yml, which differs from develop at",
        ),
        (
            "reviewed policy\n",
            lambda root: git(root, "rm", "-q", "scripts/verify_distribution_binaries.py"),
            r"deletes scripts/verify_distribution_binaries\.py, which differs from develop at",
        ),
    ],
    ids=["unreviewed-content", "rolled-back-content", "changed-mode", "unreviewed-deletion"],
)
def test_a_creation_commit_that_departs_from_its_develop_snapshot_is_refused(created_line, policy, edit, expected):
    create(created_line, fixture_sha(created_line, "tip"), policy=policy, edit=edit)
    fixed = land(created_line, "0.4.2")
    with pytest.raises(ValueError, match=expected):
        consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(fixed))


@pytest.mark.parametrize(
    ("policy", "workflows", "edit", "missing"),
    [
        (None, True, None, ".github/review.yml"),
        ("reviewed policy\n", False, None, ".github/workflows/oracle-gate.yml"),
        (
            "reviewed policy\n",
            False,
            lambda root: git(root, "rm", "-q", ".github/workflows/oracle-gate.yml"),
            ".github/workflows/review-gate.yml",
        ),
    ],
    ids=["policy-not-carried", "legacy-workflow-kept", "review-workflow-missing"],
)
def test_a_creation_commit_that_carries_only_part_of_the_reviewer_files_is_refused(
    created_line, policy, workflows, edit, missing
):
    create(created_line, fixture_sha(created_line, "tip"), policy=policy, workflows=workflows, edit=edit)
    fixed = land(created_line, "0.4.2")
    with pytest.raises(ValueError, match=f"creation does not carry {re.escape(missing)} as develop has it"):
        consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(fixed))


def recreate_on_patch_tag(root: Path, source: str | None) -> tuple[str, str]:
    create(root, fixture_sha(root, "tip"))
    old_fixed = land(root, "0.4.2")
    tag(root, "v0.4.2")
    git(root, "update-ref", "-d", f"refs/remotes/origin/{LINE}")
    git(root, "checkout", "-q", "-B", LINE, old_fixed)
    message = "Recreate the line"
    if source:
        message += f"\n\nDevelop-Source: {source}"
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return old_fixed, land(root, "0.4.3")


def test_a_line_recreated_from_a_patch_tag_of_an_earlier_line_is_admitted(created_line):
    old_fixed, fixed = recreate_on_patch_tag(created_line, fixture_sha(created_line, "tip"))
    consumer.verify_maintenance_line(created_line, "v0.4.3", fixed, LINE, merged_pulls(old_fixed, fixed))


def test_a_recreated_line_still_checks_its_new_creation_commit(created_line):
    old_fixed, fixed = recreate_on_patch_tag(created_line, None)
    with pytest.raises(ValueError, match="must name one full develop commit in a Develop-Source trailer"):
        consumer.verify_maintenance_line(created_line, "v0.4.3", fixed, LINE, merged_pulls(old_fixed, fixed))


def test_a_creation_commit_without_a_develop_source_is_refused(created_line):
    create(created_line, None)
    fixed = land(created_line, "0.4.2")
    with pytest.raises(ValueError, match="must name one full develop commit in a Develop-Source trailer"):
        consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(fixed))


@pytest.mark.parametrize(
    ("source", "policy"),
    [("before_tag", "weak policy\n"), ("side", "side policy\n"), ("released", "tag policy\n")],
    ids=["older-than-the-tag", "side-branch", "the-tag-itself"],
)
def test_a_creation_source_off_develops_first_parent_history_after_the_tag_is_refused(created_line, source, policy):
    create(created_line, fixture_sha(created_line, source), policy=policy)
    fixed = land(created_line, "0.4.2")
    with pytest.raises(ValueError, match="is not a first-parent develop commit newer than the line's tag"):
        consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(fixed))


@pytest.mark.parametrize(
    "pulls",
    [
        lambda commit: merged_pulls(commit, branch="develop"),
        lambda commit: merged_pulls(commit, repository="someone/fork"),
    ],
    ids=["other-base", "other-repository"],
)
def test_a_pull_request_merged_elsewhere_does_not_count(created_line, pulls):
    create(created_line, fixture_sha(created_line, "tip"))
    fixed = land(created_line, "0.4.2")
    with pytest.raises(ValueError, match="does not point at a pull request merged into"):
        consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, pulls(fixed))


@pytest.mark.parametrize("base_tag", [None, "v0.4.1rc1", "v0.3.9"])
def test_a_line_that_does_not_start_from_a_final_tag_of_its_minor_is_refused(tmp_path, base_tag):
    root = tmp_path / "untagged"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    start = commit_version(root, "0.4.1")
    if base_tag:
        tag(root, base_tag)
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "checkout", "-q", "-b", LINE, start)
    fixed = land(root, "0.4.2")
    with pytest.raises(ValueError, match="does not start from a final release tag of its minor"):
        consumer.verify_maintenance_line(root, "v0.4.2", fixed, LINE, merged_pulls(fixed))


def test_a_merge_commit_on_the_line_is_refused(created_line):
    create(created_line, fixture_sha(created_line, "tip"))
    git(created_line, "checkout", "-q", "-b", "line-side", "HEAD")
    write(created_line, "side.txt", "side\n")
    git(created_line, "add", "side.txt")
    git(created_line, "commit", "-q", "-m", "Side history")
    git(created_line, "checkout", "-q", LINE)
    git(created_line, "merge", "-q", "--no-ff", "-m", "Merge side", "line-side")
    merge = git(created_line, "rev-parse", "HEAD")
    fixed = land(created_line, "0.4.2")
    with pytest.raises(ValueError, match=f"{LINE} commit {merge} is not a single-parent commit"):
        consumer.verify_maintenance_line(created_line, "v0.4.2", fixed, LINE, merged_pulls(merge, fixed))


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


def no_fetch(root, tag, **options):
    return None


def create_with_script(root: Path, tag: str = "v0.4.1") -> str:
    branch, commit = release_line_create.create_line(root, tag, fetch=no_fetch)
    assert branch == LINE
    return commit


def test_a_created_line_is_verified_from_develop_without_any_tag(created_line):
    creation = create(created_line, fixture_sha(created_line, "tip"))
    consumer.verify_line(created_line, LINE, creation, merged_pulls())
    fixed = land(created_line, "0.4.2")
    consumer.verify_line(created_line, LINE, fixed, merged_pulls(fixed))


@pytest.mark.parametrize(
    ("source", "policy", "expected"),
    [
        (None, "reviewed policy\n", "must name one full develop commit in a Develop-Source trailer"),
        ("tip", "unreviewed policy\n", r"sets \.github/review\.yml, which differs from develop at"),
        ("before_tag", "weak policy\n", "is not a first-parent develop commit newer than the line's tag"),
    ],
)
def test_verify_line_refuses_a_bad_creation_commit_before_any_tag_exists(created_line, source, policy, expected):
    creation = create(created_line, fixture_sha(created_line, source) if source else None, policy=policy)
    with pytest.raises(ValueError, match=expected):
        consumer.verify_line(created_line, LINE, creation, merged_pulls())


def test_verify_line_still_refuses_a_commit_that_is_neither_merged_nor_a_creation(created_line):
    creation = create(created_line, fixture_sha(created_line, "tip"))
    stray = commit_version(created_line, "0.4.2")
    with pytest.raises(ValueError, match=f"commit {stray} is neither a pull request merged into it"):
        consumer.verify_line(created_line, LINE, stray, merged_pulls(creation))


def test_verify_line_refuses_a_line_that_does_not_start_from_a_final_tag(tmp_path):
    root = tmp_path / "untagged"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    commit_version(root, "0.4.1rc1")
    tag(root, "v0.4.1rc1")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "checkout", "-q", "-b", LINE)
    tip = commit_version(root, "0.4.2")
    with pytest.raises(ValueError, match="does not start from a final release tag of its minor"):
        consumer.verify_line(root, LINE, tip, merged_pulls(tip))


def test_the_verify_line_command_fetches_and_checks_the_hosted_tip(created_line, monkeypatch, capsys):
    create(created_line, fixture_sha(created_line, "tip"))
    fixed = land(created_line, "0.4.2")
    fetched = []
    monkeypatch.setattr(consumer, "fetch_hosted_refs", lambda source, tag, **options: fetched.append((tag, options)))
    monkeypatch.setattr(consumer, "github_json_list", merged_pulls(fixed))
    assert consumer.main(["verify-line", "--source", str(created_line), "--branch", LINE]) == 0
    assert capsys.readouterr().out.strip() == f"{LINE} {fixed} verified"
    assert fetched == [(None, {"prune": True})]


def test_the_verify_line_command_exits_nonzero_for_a_bad_creation_commit(created_line, monkeypatch, capsys):
    create(created_line, None)
    fixed = land(created_line, "0.4.2")
    monkeypatch.setattr(consumer, "fetch_hosted_refs", lambda source, tag, **options: None)
    monkeypatch.setattr(consumer, "github_json_list", merged_pulls(fixed))
    with pytest.raises(SystemExit) as exit_info:
        consumer.main(["verify-line", "--source", str(created_line), "--branch", LINE])
    assert exit_info.value.code == 1
    assert "Develop-Source trailer" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("branch", "tip", "message"),
    [
        ("release/0.4", None, "invalid maintenance branch"),
        (LINE, None, "does not exist on the hosted repository"),
        (LINE, "abc", "must be a full commit SHA"),
        (LINE, "0" * 40, "is not a commit in the checkout"),
    ],
)
def test_the_line_tip_must_name_a_commit_of_a_maintenance_branch(created_line, branch, tip, message):
    with pytest.raises(ValueError, match=message):
        consumer.resolve_line_tip(created_line, branch, tip)


def test_the_creation_script_builds_a_commit_that_verify_line_accepts(created_line):
    commit = create_with_script(created_line)
    consumer.verify_line(created_line, LINE, commit, merged_pulls())
    develop = fixture_sha(created_line, "tip")
    assert git(created_line, "log", "-1", "--format=%(trailers:key=Develop-Source,valueonly)", commit) == develop
    assert git(created_line, "rev-parse", f"{commit}^") == fixture_sha(created_line, "released")
    for path in consumer.creation_review_paths(created_line, commit, develop):
        assert git(created_line, "ls-tree", commit, "--", path) == git(created_line, "ls-tree", develop, "--", path)


def test_the_creation_script_leaves_the_checkout_and_index_untouched(created_line):
    before = git(created_line, "rev-parse", "HEAD")
    create_with_script(created_line)
    assert git(created_line, "rev-parse", "HEAD") == before
    assert git(created_line, "status", "--porcelain", "--untracked-files=all") == ""


def test_the_creation_script_uses_the_source_from_the_fetched_develop(created_line):
    def advance(root, tag, **options):
        write(root, "advanced.txt", "later\n")
        git(root, "add", "advanced.txt")
        git(root, "commit", "-q", "-m", "Later develop work")
        git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
        git(root, "checkout", "-q", LINE)

    git(created_line, "checkout", "-q", "develop")
    _, commit = release_line_create.create_line(created_line, "v0.4.1", fetch=advance)
    fresh = git(created_line, "rev-parse", "refs/remotes/origin/develop")
    assert fresh != fixture_sha(created_line, "tip")
    assert git(created_line, "log", "-1", "--format=%(trailers:key=Develop-Source,valueonly)", commit) == fresh


@pytest.mark.parametrize(
    ("tag_name", "message"),
    [("v0.4.1rc1", "is not a final release"), ("0.4.1", "invalid version tag")],
)
def test_the_creation_script_needs_a_final_tag(created_line, tag_name, message):
    with pytest.raises(ValueError, match=message):
        release_line_create.create_line(created_line, tag_name, fetch=no_fetch)


def test_the_creation_script_refuses_a_line_that_already_exists(created_line):
    git(created_line, "update-ref", f"refs/remotes/origin/{LINE}", "HEAD")
    with pytest.raises(ValueError, match=f"{LINE} already exists"):
        release_line_create.create_line(created_line, "v0.4.1", fetch=no_fetch)


def test_the_creation_script_refuses_a_develop_that_has_not_moved_past_the_tag(created_line):
    git(created_line, "update-ref", "refs/remotes/origin/develop", fixture_sha(created_line, "released"))
    with pytest.raises(ValueError, match="is not a first-parent develop commit newer than the line's tag"):
        release_line_create.create_line(created_line, "v0.4.1", fetch=no_fetch)


def test_the_creation_script_pushes_only_when_asked(created_line, tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(created_line, "remote", "add", "origin", str(remote))
    assert release_line_create.main(["--source", str(created_line), "--tag", "v0.4.1"], no_fetch) == 0
    assert git(remote, "branch", "--list") == ""
    assert release_line_create.main(["--source", str(created_line), "--tag", "v0.4.1", "--push"], no_fetch) == 0
    assert git(remote, "rev-parse", f"refs/heads/{LINE}")


def test_a_line_recreated_from_a_patch_tag_cannot_roll_back_to_an_older_develop_source(created_line):
    old_fixed, fixed = recreate_on_patch_tag(created_line, fixture_sha(created_line, "reviewed"))
    previous = fixture_sha(created_line, "tip")
    older = fixture_sha(created_line, "reviewed")
    with pytest.raises(ValueError, match=f"creation source {older} is older than {previous}"):
        consumer.verify_maintenance_line(created_line, "v0.4.3", fixed, LINE, merged_pulls(old_fixed, fixed))


def test_a_line_recreated_from_a_newer_develop_source_is_admitted(created_line):
    root = created_line
    create(root, fixture_sha(root, "reviewed"))
    old_fixed = land(root, "0.4.2")
    tag(root, "v0.4.2")
    git(root, "update-ref", "-d", f"refs/remotes/origin/{LINE}")
    git(root, "checkout", "-q", "-B", LINE, old_fixed)
    git(root, "commit", "-q", "--allow-empty", "-m", f"Recreate the line\n\nDevelop-Source: {fixture_sha(root, 'tip')}")
    fixed = land(root, "0.4.3")
    consumer.verify_maintenance_line(root, "v0.4.3", fixed, LINE, merged_pulls(old_fixed, fixed))


def test_the_guide_scripts_creation_instead_of_listing_the_reviewer_files():
    guide = (Path(__file__).resolve().parents[3] / "docs/internal/release-guide.md").read_text(encoding="utf-8")
    assert "scripts/release_line_create.py" in guide
    assert "verify-line" in guide
    for path in consumer.CREATION_REVIEW_PATHS:
        assert path not in guide


def test_a_pull_request_commit_naming_an_older_source_does_not_lower_the_recreation_floor(created_line):
    root = created_line
    create(root, fixture_sha(root, "tip"))
    git(root, "commit", "-q", "--allow-empty", "-m", f"Fix\n\nDevelop-Source: {fixture_sha(root, 'reviewed')}")
    merged = land(root, "0.4.2")
    tag(root, "v0.4.2")
    git(root, "update-ref", "-d", f"refs/remotes/origin/{LINE}")
    git(root, "checkout", "-q", "-B", LINE, merged)
    git(root, "commit", "-q", "--allow-empty", "-m", f"Recreate\n\nDevelop-Source: {fixture_sha(root, 'reviewed')}")
    fixed = land(root, "0.4.3")
    with pytest.raises(ValueError, match="is older than"):
        consumer.verify_maintenance_line(root, "v0.4.3", fixed, LINE, merged_pulls(merged, fixed))
