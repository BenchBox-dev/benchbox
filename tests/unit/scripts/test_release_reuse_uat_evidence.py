from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SPEC = importlib.util.spec_from_file_location(
    "release_reuse_uat_evidence", Path(__file__).resolve().parents[3] / "scripts" / "release_reuse_uat_evidence.py"
)
assert SPEC is not None and SPEC.loader is not None
reuse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reuse)

RC = "0.5.0rc4"
FINAL = "0.5.0"


def _init(version: str) -> bytes:
    return f'"""benchbox"""\n\n__version__ = "{version}"\n\nVALUE = 1\n'.encode()


def _metadata(version: str) -> bytes:
    return (
        f"Metadata-Version: 2.4\nName: benchbox\nVersion: {version}\n\nIntro\n\nCurrent release: v{version}.\n\nMore\n"
    ).encode()


def _wheel_members(version: str) -> dict[str, bytes]:
    return {
        "benchbox/__init__.py": _init(version),
        "benchbox/core.py": b"def run():\n    return 1\n",
        "benchbox/utils/VERSION_MANAGEMENT.md": f"# Versions\n\nCurrent release: `v{version}`.\n".encode(),
        f"benchbox-{version}.dist-info/METADATA": _metadata(version),
        f"benchbox-{version}.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        f"benchbox-{version}.dist-info/RECORD": f"benchbox/core.py,sha256=abc,{len(version)}\n".encode(),
    }


def _sdist_members(version: str) -> dict[str, bytes]:
    return {
        "PKG-INFO": _metadata(version),
        "benchbox.egg-info/PKG-INFO": _metadata(version),
        "benchbox.egg-info/SOURCES.txt": b"benchbox/core.py\n",
        "pyproject.toml": f'[project]\nname = "benchbox"\nversion = "{version}"\n'.encode(),
        "README.md": f"# BenchBox\n\nCurrent release: v{version}.\n".encode(),
        "CHANGELOG.md": f"changelog for {version}\n".encode(),
        "benchbox/__init__.py": _init(version),
        "benchbox/core.py": b"def run():\n    return 1\n",
        "benchbox/utils/VERSION_MANAGEMENT.md": f"# Versions\n\nCurrent release: `v{version}`.\n".encode(),
    }


def _write_dist(
    directory: Path,
    version: str,
    wheel: dict[str, bytes] | None = None,
    sdist: dict[str, bytes] | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(directory / f"benchbox-{version}-py3-none-any.whl", "w") as archive:
        for name, data in (wheel if wheel is not None else _wheel_members(version)).items():
            archive.writestr(name, data)
    with tarfile.open(directory / f"benchbox-{version}.tar.gz", "w:gz") as archive:
        for name, data in (sdist if sdist is not None else _sdist_members(version)).items():
            info = tarfile.TarInfo(f"benchbox-{version}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return directory


def _differences(tmp_path: Path, wheel: dict | None = None, sdist: dict | None = None) -> list[str]:
    rc = _write_dist(tmp_path / "rc", RC)
    final = _write_dist(tmp_path / "final", FINAL, wheel, sdist)
    return reuse.dist_differences(rc, final, RC, FINAL)


def test_version_only_differences_are_equivalent(tmp_path):
    assert _differences(tmp_path) == []


def test_record_content_is_ignored_but_the_file_must_exist(tmp_path):
    wheel = _wheel_members(FINAL)
    wheel[f"benchbox-{FINAL}.dist-info/RECORD"] = b"entirely different\n"
    assert _differences(tmp_path, wheel=wheel) == []


def test_missing_record_is_a_difference(tmp_path):
    wheel = _wheel_members(FINAL)
    del wheel[f"benchbox-{FINAL}.dist-info/RECORD"]
    assert any("RECORD" in item for item in _differences(tmp_path, wheel=wheel))


def test_a_changed_source_byte_fails(tmp_path):
    wheel = _wheel_members(FINAL)
    wheel["benchbox/core.py"] = b"def run():\n    return 2\n"
    assert _differences(tmp_path, wheel=wheel) == ["wheel: benchbox/core.py differs beyond the version references"]


def test_an_extra_file_fails(tmp_path):
    wheel = _wheel_members(FINAL)
    wheel["benchbox/extra.py"] = b"x = 1\n"
    assert _differences(tmp_path, wheel=wheel) == ["wheel: benchbox/extra.py exists only in the final distribution"]


def test_a_missing_file_fails(tmp_path):
    wheel = _wheel_members(FINAL)
    del wheel["benchbox/core.py"]
    assert _differences(tmp_path, wheel=wheel) == ["wheel: benchbox/core.py exists only in the release candidate"]


def test_an_extra_line_next_to_the_version_assignment_fails(tmp_path):
    wheel = _wheel_members(FINAL)
    wheel["benchbox/__init__.py"] = _init(FINAL) + b"EXTRA = 1\n"
    assert any("__init__.py" in item for item in _differences(tmp_path, wheel=wheel))


def test_a_version_assignment_that_is_not_the_final_version_fails(tmp_path):
    wheel = _wheel_members(FINAL)
    wheel["benchbox/__init__.py"] = _init(RC)
    assert any("__init__.py" in item for item in _differences(tmp_path, wheel=wheel))


def test_changed_metadata_beyond_the_version_fails(tmp_path):
    wheel = _wheel_members(FINAL)
    wheel[f"benchbox-{FINAL}.dist-info/METADATA"] = _metadata(FINAL) + b"Requires-Dist: extra\n"
    assert any("METADATA" in item for item in _differences(tmp_path, wheel=wheel))


def test_a_dist_info_file_other_than_record_must_be_identical(tmp_path):
    wheel = _wheel_members(FINAL)
    wheel[f"benchbox-{FINAL}.dist-info/WHEEL"] = b"Wheel-Version: 1.1\n"
    assert any("WHEEL" in item for item in _differences(tmp_path, wheel=wheel))


def test_a_version_reference_in_an_unlisted_member_fails(tmp_path):
    wheel = _wheel_members(RC)
    wheel["benchbox/core.py"] = f"VERSION = '{RC}'\n".encode()
    rc = _write_dist(tmp_path / "rc", RC, wheel=wheel)
    wheel_final = _wheel_members(FINAL)
    wheel_final["benchbox/core.py"] = f"VERSION = '{FINAL}'\n".encode()
    final = _write_dist(tmp_path / "final", FINAL, wheel=wheel_final)
    assert any("core.py" in item for item in reuse.dist_differences(rc, final, RC, FINAL))


def test_sdist_changelog_may_differ_but_other_members_may_not(tmp_path):
    assert _differences(tmp_path) == []
    sdist = _sdist_members(FINAL)
    sdist["benchbox/core.py"] = b"def run():\n    return 3\n"
    assert _differences(tmp_path / "second", sdist=sdist) == [
        "sdist: benchbox/core.py differs beyond the version references"
    ]


def test_sdist_pyproject_change_beyond_the_version_fails(tmp_path):
    sdist = _sdist_members(FINAL)
    sdist["pyproject.toml"] = f'[project]\nname = "benchbox"\nversion = "{FINAL}"\ndependencies = ["x"]\n'.encode()
    assert any("pyproject.toml" in item for item in _differences(tmp_path, sdist=sdist))


def test_sdist_outside_its_root_is_rejected(tmp_path):
    rc = _write_dist(tmp_path / "rc", RC)
    final = _write_dist(tmp_path / "final", FINAL)
    with tarfile.open(final / f"benchbox-{FINAL}.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("elsewhere/file")
        info.size = 0
        archive.addfile(info, io.BytesIO(b""))
    with pytest.raises(ValueError, match="outside"):
        reuse.dist_differences(rc, final, RC, FINAL)


def test_sdist_symlink_members_are_rejected(tmp_path):
    rc = _write_dist(tmp_path / "rc", RC)
    final = _write_dist(tmp_path / "final", FINAL)
    with tarfile.open(final / f"benchbox-{FINAL}.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo(f"benchbox-{FINAL}/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    with pytest.raises(ValueError, match="regular"):
        reuse.dist_differences(rc, final, RC, FINAL)


def test_a_directory_needs_exactly_one_wheel_and_one_sdist(tmp_path):
    directory = _write_dist(tmp_path / "rc", RC)
    (directory / "benchbox-0.0.0-py3-none-any.whl").write_bytes(b"")
    with pytest.raises(ValueError, match="exactly one"):
        reuse.distribution_digest(directory)


def test_equivalent_command_exit_codes(tmp_path, capsys):
    rc = _write_dist(tmp_path / "rc", RC)
    final = _write_dist(tmp_path / "final", FINAL)
    argv = [
        "equivalent",
        "--rc-dist",
        str(rc),
        "--final-dist",
        str(final),
        "--rc-version",
        RC,
        "--final-version",
        FINAL,
    ]
    assert reuse.main(argv) == 0
    wheel = _wheel_members(FINAL)
    wheel["benchbox/core.py"] = b"changed\n"
    _write_dist(tmp_path / "final", FINAL, wheel=wheel)
    assert reuse.main(argv) == 1
    assert "DIFFERENT" in capsys.readouterr().err


def test_distribution_digest_matches_the_sha256sum_file_digest(tmp_path):
    directory = _write_dist(tmp_path / "dist", RC)
    wheel, sdist = reuse.distribution_files(directory)
    text = "".join(f"{reuse.sha256_hex(path.read_bytes())}  {path.name}\n" for path in (wheel, sdist))
    assert reuse.distribution_digest(directory) == reuse.sha256_hex(text.encode())


def test_newest_release_candidate_is_numeric_and_same_version_only():
    tags = ["v0.5.0rc2", "v0.5.0rc10", "v0.5.0rc9", "v0.4.9rc99", "v0.5.0", "v0.5.0a1", "v0.5.00rc1"]
    assert reuse.newest_release_candidate("v0.5.0", tags) == "v0.5.0rc10"
    assert reuse.newest_release_candidate("v0.5.0", ["v0.4.0rc1"]) is None
    assert reuse.newest_release_candidate("v0.5.0rc4", tags) is None


GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_IDENTITY}
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8", env=env
    ).stdout.strip()


LOCK = 'version = 1\n\n[[package]]\nname = "benchbox"\nversion = "{v}"\nsource = {{ editable = "." }}\n\n[[package]]\nname = "other"\nversion = "1.0"\n'


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


WORKFLOW = """\
name: Release
env:
  HEAVY: "1.5"
jobs:
  admit:
    runs-on: ubuntu-latest
  uat-stage1:
    needs: admit
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - shard: sqlite
            platforms: sqlite
    steps:
      - run: make uat-sweep
        env:
          BENCHBOX_HEAVY_MEMORY_BUDGET_GIB: '13'
  uat-stage2:
    needs: [admit, uat-stage1]
    if: ${{ always() }}
    runs-on: ubuntu-latest
    steps:
      - run: make uat-sweep
        env:
          CLICKHOUSE_MEMORY_LIMIT: 12g
  uat-stage3:
    needs: [admit, uat-stage2]
    runs-on: ubuntu-latest
    steps:
      - run: make uat-sweep
  release-uat:
    needs: [uat-stage3]
    steps:
      - run: gate
"""
GUARDED_FILES = (
    "tests/uat/file.txt",
    "tests/utilities/file.txt",
    "tests/__init__.py",
    "docker/file.txt",
    ".github/actions/release-uat-env/file.txt",
    "Makefile",
    "make/uat.mk",
    "scripts/uat-bring-up/uat_bring_up.py",
)
WORKFLOW_FILE = ".github/workflows/release-v2.yml"


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    for name in GUARDED_FILES:
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text("one\n")
    (path / WORKFLOW_FILE).parent.mkdir(parents=True, exist_ok=True)
    (path / WORKFLOW_FILE).write_text(WORKFLOW)
    (path / "uv.lock").write_text(LOCK.format(v=RC))
    return path


def test_tree_check_passes_when_only_the_benchbox_lock_version_changes(repo):
    rc_sha = _commit(repo, "rc")
    (repo / "uv.lock").write_text(LOCK.format(v=FINAL))
    (repo / "other.txt").write_text("unguarded change\n")
    assert reuse.tree_differences(rc_sha, _commit(repo, "final"), RC, FINAL, repo) == []


@pytest.mark.parametrize("path", GUARDED_FILES)
def test_tree_check_fails_when_a_guarded_path_changes(repo, path):
    rc_sha = _commit(repo, "rc")
    (repo / path).write_text("two\n")
    differences = reuse.tree_differences(rc_sha, _commit(repo, "final"), RC, RC, repo)
    assert any(path in item for item in differences)


def test_tree_check_fails_when_a_new_guarded_file_appears(repo):
    rc_sha = _commit(repo, "rc")
    (repo / "docker" / "new.yml").write_text("x\n")
    assert reuse.tree_differences(rc_sha, _commit(repo, "final"), RC, RC, repo)


def _final_workflow(repo: Path, text: str) -> list[str]:
    rc_sha = _commit(repo, "rc")
    (repo / WORKFLOW_FILE).write_text(text)
    return reuse.tree_differences(rc_sha, _commit(repo, "final"), RC, RC, repo)


def test_workflow_check_ignores_scheduling_keys_and_other_jobs(repo):
    changed = (
        WORKFLOW.replace(
            "needs: admit\n    runs-on", "needs: [admit, uat-reuse]\n    if: ${{ !cancelled() }}\n    runs-on", 1
        )
        .replace("needs: [admit, uat-stage1]\n    if: ${{ always() }}", "needs: [admit, uat-reuse, uat-stage1]")
        .replace("steps:\n      - run: gate", "steps:\n      - run: other gate")
        + "  uat-reuse:\n    needs: admit\n"
    )
    assert _final_workflow(repo, changed) == []


@pytest.mark.parametrize(
    ("old", "new", "job"),
    [
        ("BENCHBOX_HEAVY_MEMORY_BUDGET_GIB: '13'", "BENCHBOX_HEAVY_MEMORY_BUDGET_GIB: '14'", "uat-stage1"),
        ("CLICKHOUSE_MEMORY_LIMIT: 12g", "CLICKHOUSE_MEMORY_LIMIT: 8g", "uat-stage2"),
        ("platforms: sqlite", "platforms: sqlite,duckdb", "uat-stage1"),
        (
            "      - run: make uat-sweep\n  release-uat",
            "      - run: make uat-sweep\n      - run: extra\n  release-uat",
            "uat-stage3",
        ),
    ],
)
def test_workflow_check_fails_when_a_stage_job_changes_beyond_scheduling(repo, old, new, job):
    assert old in WORKFLOW
    differences = _final_workflow(repo, WORKFLOW.replace(old, new, 1))
    assert differences == [f"{WORKFLOW_FILE}: {job} differs beyond needs and if"]


def test_workflow_check_fails_when_workflow_env_changes(repo):
    assert _final_workflow(repo, WORKFLOW.replace('HEAVY: "1.5"', 'HEAVY: "2"')) == [
        f"{WORKFLOW_FILE}: workflow-level env differs"
    ]


def test_workflow_check_fails_when_a_stage_job_is_removed(repo):
    stripped = WORKFLOW.replace(
        "  uat-stage3:\n    needs: [admit, uat-stage2]\n    runs-on: ubuntu-latest\n    steps:\n      - run: make uat-sweep\n",
        "",
    )
    assert _final_workflow(repo, stripped) == [f"{WORKFLOW_FILE}: uat-stage3 is missing"]


def _live_workflow() -> str:
    return (Path(__file__).resolve().parents[3] / WORKFLOW_FILE).read_text(encoding="utf-8")


def test_live_workflow_stage_jobs_equal_their_chained_form_apart_from_scheduling():
    import yaml

    live = yaml.safe_load(_live_workflow())
    chained = yaml.safe_load(_live_workflow())
    chained["jobs"]["uat-stage1"]["needs"] = "admit"
    chained["jobs"]["uat-stage1"].pop("if", None)
    chained["jobs"]["uat-stage2"]["needs"] = ["admit", "uat-stage1"]
    chained["jobs"]["uat-stage3"]["needs"] = ["admit", "uat-stage2"]
    for name in ("uat-stage2", "uat-stage3"):
        chained["jobs"][name]["if"] = "${{ !cancelled() && needs.admit.result == 'success' }}"
    assert live["jobs"]["uat-stage1"]["needs"] != chained["jobs"]["uat-stage1"]["needs"]
    assert reuse._stage_job_definitions(yaml.safe_dump(live)) == reuse._stage_job_definitions(yaml.safe_dump(chained))


@pytest.mark.parametrize("job", reuse.STAGE_JOBS)
def test_live_workflow_stage_jobs_fail_the_check_when_env_or_matrix_changes(job):
    import yaml

    base = reuse._stage_job_definitions(_live_workflow())
    for mutate in (
        lambda j: j["strategy"]["matrix"]["include"].pop(),
        lambda j: j["steps"][-3].setdefault("env", {}).update(EXTRA="1"),
        lambda j: j.update({"timeout-minutes": j["timeout-minutes"] - 1}),
    ):
        workflow = yaml.safe_load(_live_workflow())
        mutate(workflow["jobs"][job])
        assert reuse._stage_job_definitions(yaml.safe_dump(workflow)) != base


def test_tree_check_fails_when_the_lock_changes_beyond_the_benchbox_version(repo):
    rc_sha = _commit(repo, "rc")
    (repo / "uv.lock").write_text(LOCK.format(v=FINAL).replace('version = "1.0"', 'version = "1.1"'))
    assert reuse.tree_differences(rc_sha, _commit(repo, "final"), RC, FINAL, repo) == [
        "uv.lock differs beyond the benchbox package version line"
    ]


def test_tree_check_fails_when_the_lock_benchbox_version_is_not_the_release_version(repo):
    rc_sha = _commit(repo, "rc")
    (repo / "uv.lock").write_text(LOCK.format(v="0.5.1"))
    assert reuse.tree_differences(rc_sha, _commit(repo, "final"), RC, FINAL, repo) == [
        "uv.lock has no single benchbox package version line matching its release"
    ]


def test_gate_evidence_must_be_green_for_the_rc_commit_and_digest():
    good = {"verdict": "green", "source_commit_sha": "a" * 40, "distribution_digest": "d" * 64}
    assert reuse.gate_evidence_problems(good, "a" * 40, "d" * 64) == []
    assert reuse.gate_evidence_problems({**good, "verdict": "red"}, "a" * 40, "d" * 64)
    assert reuse.gate_evidence_problems(good, "b" * 40, "d" * 64)
    assert reuse.gate_evidence_problems(good, "a" * 40, "e" * 64)
    assert reuse.gate_evidence_problems({"verdict": "green"}, "a" * 40, "d" * 64)


def test_verify_gate_command_writes_a_receipt_only_for_matching_evidence(tmp_path):
    summary = tmp_path / "summary.json"
    receipt = tmp_path / "receipt.json"
    summary.write_text(json.dumps({"verdict": "green", "source_commit_sha": "a" * 40, "distribution_digest": "d" * 64}))
    argv = [
        "verify-gate",
        "--summary",
        str(summary),
        "--rc-tag",
        "v0.5.0rc4",
        "--rc-run-id",
        "7",
        "--rc-sha",
        "a" * 40,
        "--rc-digest",
        "d" * 64,
        "--receipt",
        str(receipt),
    ]
    assert reuse.main(argv) == 0
    assert json.loads(receipt.read_text(encoding="utf-8"))["reused_from_run_id"] == "7"
    receipt.unlink()
    summary.write_text(json.dumps({"verdict": "green", "source_commit_sha": "b" * 40, "distribution_digest": "d" * 64}))
    assert reuse.main(argv) == 1
    assert not receipt.exists()


class FakeGitHub:
    def __init__(self, rc_sha: str, rc_dist: Path, admitted_available: bool = True, job_conclusion: str = "success"):
        self.rc_sha = rc_sha
        self.rc_dist = rc_dist
        self.admitted_available = admitted_available
        self.job_conclusion = job_conclusion
        self.digest = reuse.distribution_digest(rc_dist)
        self.dist_text = self._dist_digest_text()
        self.summary = {
            "verdict": "green",
            "source_commit_sha": rc_sha,
            "distribution_digest": reuse.sha256_hex(self.dist_text),
        }

    def _dist_digest_text(self) -> bytes:
        wheel, sdist = reuse.distribution_files(self.rc_dist)
        return "".join(f"{reuse.sha256_hex(p.read_bytes())}  {p.name}\n" for p in (wheel, sdist)).encode()

    def json(self, *args: str):
        endpoint = args[0]
        if "/workflows/release-v2.yml/runs" in endpoint:
            return {
                "workflow_runs": [
                    {
                        "id": 99,
                        "run_number": 5,
                        "run_attempt": 1,
                        "head_sha": self.rc_sha,
                        "head_branch": "v0.5.0rc4",
                        "event": "push",
                        "status": "completed",
                    },
                    {
                        "id": 11,
                        "run_number": 2,
                        "run_attempt": 1,
                        "head_sha": self.rc_sha,
                        "head_branch": "v0.5.0rc4",
                        "event": "push",
                        "status": "completed",
                    },
                ]
            }
        if endpoint.endswith("/jobs?filter=latest&per_page=100"):
            return {"jobs": [{"name": "release UAT gate", "conclusion": self.job_conclusion}]}
        if "/runs/99/artifacts" in endpoint:
            names = [f"release-uat-v0.5.0rc4-stage{n}-shard" for n in (1, 2, 3)] + ["release-uat-v0.5.0rc4"]
            if self.admitted_available:
                names.append("admitted-v0.5.0rc4")
            return {
                "artifacts": [{"id": index, "name": name, "expired": False} for index, name in enumerate(names, 100)]
            }
        if "/actions/artifacts?name=dist-" in endpoint:
            if "attempt-1&" in endpoint:
                return {"artifacts": [{"id": 500, "name": "dist", "expired": False}]}
            return {"artifacts": []}
        raise AssertionError(endpoint)

    def download(self, artifact_id: int) -> dict[str, bytes]:
        wheel, sdist = reuse.distribution_files(self.rc_dist)
        if artifact_id == 103:
            return {"dist-digest.txt": self.dist_text, "uat-gate-summary.json": json.dumps(self.summary).encode()}
        if artifact_id in (104, 500):
            return {wheel.name: wheel.read_bytes(), sdist.name: sdist.read_bytes(), "SHA256SUMS": b""}
        raise AssertionError(artifact_id)


@pytest.fixture
def scenario(tmp_path, repo, monkeypatch):
    rc_sha = _commit(repo, "rc")
    _git(repo, "tag", "-a", "v0.5.0rc4", "-m", "rc4", rc_sha)
    (repo / "uv.lock").write_text(LOCK.format(v=FINAL))
    head = _commit(repo, "final")
    rc_dist = _write_dist(tmp_path / "rc-source", RC)
    final_dist = _write_dist(tmp_path / "final-dist", FINAL)
    fake = FakeGitHub(rc_sha, rc_dist)
    monkeypatch.setattr(reuse, "_gh_json", fake.json)
    monkeypatch.setattr(reuse, "_download_artifact", lambda repository, artifact_id: fake.download(artifact_id))

    def run(final_dist_path: Path = final_dist) -> tuple[bool, dict[str, str], reuse.Report]:
        args = argparse.Namespace(
            tag="v0.5.0",
            head_sha=head,
            github_repository="owner/repo",
            final_dist=str(final_dist_path),
            work=str(tmp_path / "work"),
            repo_dir=str(repo),
        )
        report = reuse.Report()
        outputs: dict[str, str] = {}
        return reuse.decide(args, report, outputs), outputs, report

    return run, fake, tmp_path


def test_decide_reuses_when_every_condition_holds(scenario):
    run, fake, _ = scenario
    reused, outputs, report = run()
    assert reused
    assert outputs["rc_tag"] == "v0.5.0rc4"
    assert outputs["rc_run_id"] == "99"
    assert outputs["rc_dist_digest"] == reuse.sha256_hex(fake.dist_text)
    assert [ok for _, ok, _ in report.rows] == [True] * len(report.rows)
    conditions = {condition for condition, _, _ in report.rows}
    assert {"(a) release candidate run", "(b) ancestry", "(d) UAT inputs", "(c) distribution equivalence"} <= conditions


def test_decide_uses_the_newest_run_only(scenario):
    run, fake, _ = scenario
    fake.job_conclusion = "failure"
    reused, _, report = run()
    assert not reused
    assert report.rows[-1][0] == "(a) release candidate run"


def test_decide_falls_back_to_the_trunk_dist_when_the_admitted_artifact_expired(scenario):
    run, fake, _ = scenario
    fake.admitted_available = False
    reused, _, report = run()
    assert reused
    assert any("dist-" in detail for _, _, detail in report.rows)


def test_decide_refuses_when_the_distribution_differs(scenario):
    run, _, tmp_path = scenario
    wheel = _wheel_members(FINAL)
    wheel["benchbox/core.py"] = b"tampered\n"
    reused, _, report = run(_write_dist(tmp_path / "tampered", FINAL, wheel=wheel))
    assert not reused
    assert report.rows[-1][0] == "(c) distribution equivalence" and not report.rows[-1][1]


def test_decide_refuses_when_the_rc_dist_does_not_match_the_admitted_digest(scenario):
    run, fake, _ = scenario
    fake.dist_text = b"0" * 64 + b"  other\n"
    fake.summary["distribution_digest"] = reuse.sha256_hex(fake.dist_text)
    reused, _, report = run()
    assert not reused
    assert report.rows[-1][0] == "(c) release candidate distribution"


def test_decide_refuses_when_the_evidence_source_commit_differs(scenario):
    run, fake, _ = scenario
    fake.summary["source_commit_sha"] = "f" * 40
    reused, _, report = run()
    assert not reused
    assert report.rows[-1][0] == "(a) release candidate gate evidence"


def test_decide_refuses_when_the_rc_commit_is_not_an_ancestor(scenario, repo):
    run, fake, _ = scenario
    _git(repo, "checkout", "-q", "--orphan", "other")
    (repo / "x").write_text("x")
    other = _commit(repo, "unrelated")
    _git(repo, "tag", "-f", "-a", "v0.5.0rc4", "-m", "rc4", other)
    fake.rc_sha = other
    fake.summary["source_commit_sha"] = other
    reused, _, report = run()
    assert not reused
    assert report.rows[-1][0] == "(b) ancestry"


def test_decide_refuses_when_there_is_no_release_candidate(scenario, repo):
    run, _, _ = scenario
    _git(repo, "tag", "-d", "v0.5.0rc4")
    reused, _, report = run()
    assert not reused
    assert report.rows[-1][0] == "release candidate"


def test_decide_command_fails_closed_on_unexpected_errors(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(reuse, "decide", explode)
    output = tmp_path / "out"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = reuse.main(
        [
            "decide",
            "--tag",
            "v0.5.0",
            "--head-sha",
            "a" * 40,
            "--github-repository",
            "o/r",
            "--final-dist",
            str(tmp_path),
            "--work",
            str(tmp_path),
            "--github-output",
            str(output),
        ]
    )
    assert code == 0
    assert output.read_text(encoding="utf-8") == "reuse=false\n"
    assert "run the full UAT stages" in summary.read_text(encoding="utf-8")
