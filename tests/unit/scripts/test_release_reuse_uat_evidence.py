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
    assert _differences(tmp_path, wheel=wheel) == ["wheel: benchbox/core.py exists only in the candidate distribution"]


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
        "--candidate-dist",
        str(rc),
        "--final-dist",
        str(final),
        "--candidate-version",
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


def test_gate_evidence_must_be_green_for_the_source_commit_and_digest():
    good = {"verdict": "green", "source_commit_sha": "a" * 40, "distribution_digest": "d" * 64}
    assert reuse.gate_evidence_problems(good, "a" * 40, "d" * 64) == []
    assert reuse.gate_evidence_problems({**good, "verdict": "red"}, "a" * 40, "d" * 64)
    assert reuse.gate_evidence_problems(good, "b" * 40, "d" * 64)
    assert reuse.gate_evidence_problems(good, "a" * 40, "e" * 64)
    assert reuse.gate_evidence_problems({"verdict": "green"}, "a" * 40, "d" * 64)


def test_the_candidate_version_is_read_from_the_distribution_file_names(tmp_path):
    directory = _write_dist(tmp_path / "dist", "0.5.1.dev3")
    assert reuse.distribution_version(directory) == "0.5.1.dev3"
    (directory / "benchbox-0.5.1.dev3.tar.gz").rename(directory / "benchbox-0.5.2.tar.gz")
    with pytest.raises(ValueError, match="different versions"):
        reuse.distribution_version(directory)


SOURCE_SHA = "a" * 40
SOURCE_DIGEST = "d" * 64


def _cell(**overrides):
    row = {
        "platform": "singlestore",
        "benchmark": "tpch",
        "scale": 0.1,
        "status": "passed",
        "attempt": 1,
        "retry_signature": None,
        "retry_outcome": None,
        "first_attempt_status": None,
    }
    row.update(overrides)
    return row


RETRY_ABSORBED = {
    "status": "passed",
    "attempt": 2,
    "retry_signature": "timeout",
    "retry_outcome": "retry_passed",
    "first_attempt_status": "timed-out",
}
RETRY_FAILED = {**RETRY_ABSORBED, "status": "failed", "retry_outcome": "retry_failed"}
RETRY_NOT_STARTED = {
    "status": "timed-out",
    "attempt": 1,
    "retry_signature": "timeout",
    "retry_outcome": "skipped_insufficient_time",
}


def _evidence(tmp_path: Path, rows: list[dict], tag: str = f"trunk-{SOURCE_SHA}") -> Path:
    run = tmp_path / "evidence" / f"release-uat-{tag}-stage3-olap" / "run"
    run.mkdir(parents=True)
    (run / "cells.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return tmp_path / "evidence"


def _verify_gate(tmp_path: Path, evidence: Path, verdict: str = "green", tag: str = f"trunk-{SOURCE_SHA}") -> int:
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps({"verdict": verdict, "source_commit_sha": SOURCE_SHA, "distribution_digest": SOURCE_DIGEST})
    )
    return reuse.main(
        [
            "verify-gate",
            "--summary",
            str(summary),
            "--evidence",
            str(evidence),
            "--source-kind",
            "trunk",
            "--source-tag",
            tag,
            "--source-run-id",
            "7",
            "--source-sha",
            SOURCE_SHA,
            "--source-digest",
            SOURCE_DIGEST,
            "--receipt",
            str(tmp_path / "receipt.json"),
        ]
    )


def test_verify_gate_writes_a_receipt_only_for_matching_evidence(tmp_path):
    evidence = _evidence(tmp_path, [_cell()])
    assert _verify_gate(tmp_path, evidence) == 0
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert (receipt["reused_from_kind"], receipt["reused_from_run_id"]) == ("trunk", "7")
    (tmp_path / "receipt.json").unlink()
    assert _verify_gate(tmp_path, evidence, verdict="red") == 1
    assert not (tmp_path / "receipt.json").exists()


def test_verify_gate_accepts_a_cell_whose_infrastructure_failure_passed_on_attempt_two(tmp_path):
    assert _verify_gate(tmp_path, _evidence(tmp_path, [_cell(), _cell(**RETRY_ABSORBED)])) == 0


@pytest.mark.parametrize(
    "retried",
    [
        pytest.param(RETRY_FAILED, id="attempt-two-failed"),
        pytest.param(RETRY_NOT_STARTED, id="retry-never-started"),
        pytest.param({**RETRY_ABSORBED, "retry_signature": None}, id="no-infrastructure-signature"),
        pytest.param({**RETRY_ABSORBED, "first_attempt_status": "passed"}, id="attempt-one-had-not-failed"),
        pytest.param({**RETRY_ABSORBED, "status": "failed"}, id="attempt-two-status-failed"),
        pytest.param({**RETRY_ABSORBED, "attempt": 3}, id="third-attempt"),
        pytest.param({"attempt": 2}, id="second-attempt-without-retry-record"),
    ],
)
def test_verify_gate_rejects_every_retry_record_except_a_passed_second_attempt(tmp_path, capsys, retried):
    assert _verify_gate(tmp_path, _evidence(tmp_path, [_cell(**retried)])) == 1
    assert "gate evidence:" in capsys.readouterr().err
    assert not (tmp_path / "receipt.json").exists()


def test_verify_gate_rejects_evidence_without_cell_rows(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _verify_gate(tmp_path, empty) == 1


def test_verify_gate_rejects_an_unreadable_cell_row(tmp_path):
    evidence = _evidence(tmp_path, [_cell()])
    cells = next(evidence.glob("*/run/cells.jsonl"))
    cells.write_text(cells.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    assert _verify_gate(tmp_path, evidence) == 1


ATTEMPT_START = "2026-11-05T05:00:00Z"
BEFORE_ATTEMPT = "2026-11-05T02:10:00Z"
AFTER_ATTEMPT = "2026-11-05T05:00:05Z"


def trunk_jobs(attempt: int = 1, **conclusions: str) -> list[dict]:
    names = {
        "plan": "plan",
        "admit": "admit",
        "stage1": "UAT stage 1 sqlite",
        "stage2": "UAT stage 2 starrocks",
        "stage3": "UAT stage 3 olap",
        "gate": "trunk UAT gate",
        "record": "trunk UAT record",
    }
    return [
        {
            "name": name,
            "conclusion": conclusions.get(key, "success"),
            "run_attempt": attempt,
            "started_at": AFTER_ATTEMPT if attempt > 1 else BEFORE_ATTEMPT,
        }
        for key, name in names.items()
    ]


TRUNK_VERSION = "0.5.1.dev3"
TRUNK_RUNS = "trunk-uat.yml"
RELEASE_RUNS = "release-v2.yml"


class FakeGitHub:
    def __init__(self) -> None:
        self.runs: dict[str, list[dict]] = {}
        self.artifacts: dict[int, list[dict]] = {}
        self.blobs: dict[int, dict[str, bytes]] = {}
        self.jobs: dict[int, list[dict]] = {}
        self.endpoints: list[str] = []
        self._next_artifact = 1000

    def artifact(self, run_id: int, name: str, files: dict[str, bytes], expired: bool = False) -> None:
        self._next_artifact += 1
        self.artifacts.setdefault(run_id, []).append({"id": self._next_artifact, "name": name, "expired": expired})
        self.blobs[self._next_artifact] = files

    def evidence(
        self,
        run_id: int,
        *,
        tag: str,
        sha: str,
        dist: Path,
        gate_name: str,
        admitted_name: str,
        verdict: str = "green",
        summary_sha: str | None = None,
    ) -> str:
        wheel, sdist = reuse.distribution_files(dist)
        text = "".join(f"{reuse.sha256_hex(p.read_bytes())}  {p.name}\n" for p in (wheel, sdist)).encode()
        summary = {
            "verdict": verdict,
            "source_commit_sha": summary_sha or sha,
            "distribution_digest": reuse.sha256_hex(text),
        }
        for number in (1, 2, 3):
            self.artifact(run_id, f"release-uat-{tag}-stage{number}-shard", {})
        self.artifact(
            run_id, gate_name, {"dist-digest.txt": text, "uat-gate-summary.json": json.dumps(summary).encode()}
        )
        self.artifact(
            run_id, admitted_name, {wheel.name: wheel.read_bytes(), sdist.name: sdist.read_bytes(), "SHA256SUMS": b""}
        )
        return reuse.sha256_hex(text)

    def trunk_run(
        self,
        run_id: int,
        sha: str,
        dist: Path,
        *,
        created: str,
        event: str = "schedule",
        branch: str = "develop",
        conclusion: str = "success",
        head_sha: str | None = None,
        attempt: int = 1,
        jobs: list[dict] | None = None,
        **evidence,
    ) -> str:
        self.runs.setdefault(TRUNK_RUNS, []).append(
            {
                "id": run_id,
                "event": event,
                "head_branch": branch,
                "status": "completed",
                "conclusion": conclusion,
                "created_at": created,
                "head_sha": head_sha or sha,
                "run_attempt": attempt,
                "run_started_at": ATTEMPT_START if attempt > 1 else created,
            }
        )
        self.jobs[run_id] = jobs if jobs is not None else trunk_jobs(attempt)
        return self.evidence(
            run_id,
            tag=f"trunk-{sha}",
            sha=sha,
            dist=dist,
            gate_name=f"trunk-uat-gate-{sha}",
            admitted_name=f"admitted-v{reuse.distribution_version(dist)}",
            **evidence,
        )

    def expire(self, run_id: int, prefix: str) -> None:
        hits = [a for a in self.artifacts[run_id] if a["name"].startswith(prefix)]
        assert hits, prefix
        for artifact in hits:
            artifact["expired"] = True

    def json(self, *args: str):
        endpoint = args[0]
        self.endpoints.append(endpoint)
        if "/actions/workflows/" in endpoint and "/runs" in endpoint:
            workflow = endpoint.split("/actions/workflows/")[1].split("/")[0]
            return {"workflow_runs": self.runs.get(workflow, [])}
        if endpoint.endswith("/jobs?filter=latest&per_page=100"):
            return {"jobs": self.jobs[int(endpoint.split("/runs/")[1].split("/")[0])]}
        if "/actions/runs/" in endpoint and "/artifacts" in endpoint:
            return {"artifacts": self.artifacts.get(int(endpoint.split("/runs/")[1].split("/")[0]), [])}
        if "/actions/artifacts?name=dist-" in endpoint:
            return {"artifacts": []}
        raise AssertionError(endpoint)

    def download(self, artifact_id: int) -> dict[str, bytes]:
        return self.blobs[artifact_id]


class World:
    def __init__(self, tmp_path: Path, repo: Path, fake: FakeGitHub) -> None:
        self.tmp_path = tmp_path
        self.repo = repo
        self.fake = fake
        (repo / "uv.lock").write_text(LOCK.format(v=TRUNK_VERSION))
        self.candidate = _commit(repo, "trunk candidate")
        self.head = self.advance(**{"uv.lock": LOCK.format(v=FINAL), "other.txt": "unguarded change\n"})
        self.trunk_dist = _write_dist(tmp_path / "trunk-dist", TRUNK_VERSION)
        self.final_dist = _write_dist(tmp_path / "final-dist", FINAL)

    def advance(self, **files: str) -> str:
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.head = _commit(self.repo, "advance")
        _git(self.repo, "update-ref", "refs/remotes/origin/develop", self.head)
        return self.head

    def decide(self, tag: str = "v0.5.0") -> tuple[bool, dict[str, str], reuse.Report]:
        args = argparse.Namespace(
            tag=tag,
            head_sha=self.head,
            github_repository="owner/repo",
            final_dist=str(self.final_dist),
            work=str(self.tmp_path / "work"),
            repo_dir=str(self.repo),
        )
        report = reuse.Report()
        outputs: dict[str, str] = {}
        return reuse.decide(args, report, outputs), outputs, report


@pytest.fixture
def world(tmp_path, repo, monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(reuse, "_gh_json", fake.json)
    monkeypatch.setattr(reuse, "_download_artifact", lambda repository, artifact_id: fake.download(artifact_id))
    return World(tmp_path, repo, fake)


def _failures(report: reuse.Report) -> str:
    return " | ".join(f"{condition}: {detail}" for condition, ok, detail in report.rows if not ok)


def test_decide_reuses_the_newest_qualifying_trunk_run(world):
    digest = world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z")
    reused, outputs, report = world.decide()
    assert reused
    assert outputs == {
        "source_kind": "trunk",
        "source_tag": f"trunk-{world.candidate}",
        "source_sha": world.candidate,
        "source_run_id": "201",
        "source_dist_digest": digest,
    }
    assert [ok for _, ok, _ in report.rows] == [True] * len(report.rows)
    assert report.rows[-1] == ("reused evidence", True, f"reused trunk UAT run 201 for {world.candidate}")
    assert "reuse earlier UAT evidence" in report.markdown(True)


def test_decide_prefers_the_newest_of_two_qualifying_trunk_runs(world):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-04T02:00:00Z")
    newer = _commit_between(world)
    world.fake.trunk_run(202, newer, world.trunk_dist, created="2026-11-05T02:00:00Z")
    reused, outputs, _ = world.decide()
    assert reused
    assert (outputs["source_run_id"], outputs["source_sha"]) == ("202", newer)


def _commit_between(world: World) -> str:
    _git(world.repo, "checkout", "-q", "-b", "scratch", world.candidate)
    (world.repo / "uv.lock").write_text(LOCK.format(v=TRUNK_VERSION))
    (world.repo / "between.txt").write_text("unguarded\n")
    between = _commit(world.repo, "between")
    _git(world.repo, "checkout", "-q", "-")
    _git(world.repo, "merge", "-q", "--no-edit", "scratch")
    world.head = _git(world.repo, "rev-parse", "HEAD")
    _git(world.repo, "update-ref", "refs/remotes/origin/develop", world.head)
    return between


def test_a_newer_red_run_on_the_same_distribution_decides_over_an_older_green_run(world):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-04T02:00:00Z")
    newer = _commit_between(world)
    world.fake.trunk_run(202, newer, world.trunk_dist, created="2026-11-05T02:00:00Z", conclusion="failure")
    reused, outputs, report = world.decide()
    assert not reused and outputs == {}
    assert "trunk run 202: run: concluded 'failure'" in _failures(report)
    assert "no older run is used" in _failures(report)
    assert not any("trunk run 201" in condition for condition, _, _ in report.rows)


def test_a_newer_run_that_executed_no_cells_does_not_hide_an_older_green_run(world):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-04T02:00:00Z")
    world.fake.runs[TRUNK_RUNS].append(
        {
            "id": 202,
            "event": "schedule",
            "head_branch": "develop",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-05T02:00:00Z",
        }
    )
    reused, outputs, _ = world.decide()
    assert reused and outputs["source_run_id"] == "201"


def test_a_newer_red_run_on_a_commit_that_is_not_an_ancestor_does_not_decide(world):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-04T02:00:00Z")
    unrelated = _git(world.repo, "commit-tree", "-m", "unrelated", "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    world.fake.trunk_run(202, unrelated, world.trunk_dist, created="2026-11-05T02:00:00Z", conclusion="failure")
    reused, outputs, _ = world.decide()
    assert reused and outputs["source_run_id"] == "201"


def _only_run(world: World) -> int:
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z")
    return 201


def _non_ancestor(world: World) -> None:
    other = _git(world.repo, "commit-tree", "-m", "unrelated", "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    world.fake.trunk_run(201, other, world.trunk_dist, created="2026-11-05T02:00:00Z")


def _guarded_change(world: World) -> None:
    _only_run(world)
    world.advance(**{"tests/uat/file.txt": "changed\n"})


def _trunk_workflow_change(world: World) -> None:
    _only_run(world)
    world.advance(**{".github/workflows/trunk-uat.yml": "name: Trunk UAT\n"})


def _stage_job_change(world: World) -> None:
    _only_run(world)
    world.advance(**{WORKFLOW_FILE: WORKFLOW.replace("platforms: sqlite", "platforms: sqlite,duckdb")})


def _lock_change(world: World) -> None:
    _only_run(world)
    world.advance(**{"uv.lock": LOCK.format(v=FINAL).replace('version = "1.0"', 'version = "1.1"')})


def _non_equivalent_wheel(world: World) -> None:
    _only_run(world)
    wheel = _wheel_members(FINAL)
    wheel["benchbox/core.py"] = b"tampered\n"
    world.final_dist = _write_dist(world.tmp_path / "tampered", FINAL, wheel=wheel)


def _expire(prefix: str):
    def mutate(world: World) -> None:
        _only_run(world)
        world.fake.expire(201, prefix)

    return mutate


def _runs_on(branch: str = "develop", event: str = "schedule", conclusion: str = "success"):
    def mutate(world: World) -> None:
        world.fake.trunk_run(
            201,
            world.candidate,
            world.trunk_dist,
            created="2026-11-05T02:00:00Z",
            branch=branch,
            event=event,
            conclusion=conclusion,
        )

    return mutate


def _red_verdict(world: World) -> None:
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", verdict="red")


def _digest_of_another_commit(world: World) -> None:
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", summary_sha="f" * 40)


@pytest.mark.parametrize(
    ("mutate", "named_reason"),
    [
        pytest.param(_non_ancestor, "is not an ancestor of the tagged commit", id="non-ancestor"),
        pytest.param(
            _guarded_change, "guarded trees changed since the evidence commit: tests/uat/file.txt", id="guarded"
        ),
        pytest.param(_trunk_workflow_change, "trunk-uat.yml", id="trunk-workflow"),
        pytest.param(_stage_job_change, "uat-stage1 differs beyond needs and if", id="stage-job"),
        pytest.param(_runs_on(branch="feature/x"), "ran on 'feature/x', not develop", id="non-develop-ref"),
        pytest.param(_runs_on(event="push"), "event 'push' is not schedule or workflow_dispatch", id="push-event"),
        pytest.param(_runs_on(conclusion="cancelled"), "concluded 'cancelled', not 'success'", id="cancelled-run"),
        pytest.param(_lock_change, "uv.lock differs beyond the benchbox package version line", id="lock"),
        pytest.param(
            _non_equivalent_wheel, "wheel: benchbox/core.py differs beyond the version references", id="wheel"
        ),
        pytest.param(_expire("trunk-uat-gate-"), "expired artifact: trunk-uat-gate-", id="expired-gate"),
        pytest.param(_expire("release-uat-trunk-"), "expired artifact: release-uat-trunk-", id="expired-shards"),
        pytest.param(_expire("admitted-"), "expired artifact: admitted-v0.5.1.dev3", id="expired-distribution"),
        pytest.param(_red_verdict, "verdict is 'red'", id="red-verdict"),
        pytest.param(_digest_of_another_commit, "is not {candidate}", id="evidence-of-another-commit"),
    ],
)
def test_decide_names_the_reason_each_trunk_run_is_disqualified_and_runs_the_full_uat(world, mutate, named_reason):
    mutate(world)
    reused, outputs, report = world.decide()
    assert not reused and outputs == {}
    expected = named_reason.replace("{candidate}", world.candidate)
    assert expected in _failures(report)
    assert expected in report.markdown(False)
    assert "run the full UAT stages" in report.markdown(False)


def test_decide_checks_only_a_bounded_number_of_runs_and_says_so(world):
    unrelated = _git(world.repo, "commit-tree", "-m", "unrelated", "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    for run_id in range(300, 300 + reuse.TRUNK_RUN_LIMIT + 3):
        world.fake.trunk_run(
            run_id,
            unrelated,
            world.trunk_dist,
            created=f"2026-11-05T02:{run_id - 300:02d}:00Z",
            conclusion="failure",
        )
    reused, _, report = world.decide()
    assert not reused
    assert f"none of the newest {reuse.TRUNK_RUN_LIMIT} runs qualified" in _failures(report)
    assert sum("concluded 'failure'" in detail for _, _, detail in report.rows) == reuse.TRUNK_RUN_LIMIT


def test_the_latest_attempt_of_a_trunk_run_must_have_every_gate_shard_and_record_job_green(world):
    world.fake.trunk_run(
        201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", jobs=trunk_jobs(stage2="failure")
    )
    reused, outputs, report = world.decide()
    assert not reused and outputs == {}
    assert "trunk run 201: attempt: job 'UAT stage 2 starrocks' concluded 'failure' in the latest attempt" in (
        _failures(report)
    )


@pytest.mark.parametrize("skipped", ["gate", "record", "stage1"])
def test_a_rerun_that_skipped_the_gate_shard_or_record_job_does_not_reuse_the_earlier_attempts_evidence(world, skipped):
    world.fake.trunk_run(
        201,
        world.candidate,
        world.trunk_dist,
        created="2026-11-05T02:00:00Z",
        attempt=2,
        jobs=trunk_jobs(2, **{skipped: "skipped"}),
    )
    reused, outputs, report = world.decide()
    assert not reused and outputs == {}
    assert "trunk run 201: attempt:" in _failures(report)


def test_a_trunk_run_whose_gate_job_is_missing_from_the_latest_attempt_is_not_reused(world):
    jobs = [job for job in trunk_jobs() if job["name"] != "trunk UAT gate"]
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", jobs=jobs)
    reused, _, report = world.decide()
    assert not reused
    assert "no 'trunk UAT gate' job in the latest attempt" in _failures(report)


def test_evidence_kept_from_an_earlier_attempt_cannot_qualify_a_later_attempt(world):
    jobs = trunk_jobs(2)
    jobs[2]["run_attempt"] = 1
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", attempt=2, jobs=jobs)
    reused, _, report = world.decide()
    assert not reused
    assert "ran in attempt 1, not the run's latest attempt 2" in _failures(report)


def test_a_partially_rerun_trunk_run_is_refused_and_names_the_rerun_jobs(world):
    jobs = trunk_jobs(2)
    for kept in jobs:
        if kept["name"] in ("UAT stage 2 starrocks", "UAT stage 3 olap"):
            kept["started_at"] = BEFORE_ATTEMPT
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", attempt=2, jobs=jobs)
    reused, outputs, report = world.decide()
    assert not reused and outputs == {}
    failure = _failures(report)
    assert "trunk run 201: attempt: the run was partially re-run in attempt 2" in failure
    assert "re-run jobs UAT stage 1 sqlite, trunk UAT gate, trunk UAT record" in failure
    assert "UAT stage 2 starrocks, UAT stage 3 olap" in failure


def test_a_rerun_attempt_without_a_run_start_time_is_refused(world):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", attempt=2)
    del world.fake.runs[TRUNK_RUNS][0]["run_started_at"]
    reused, _, report = world.decide()
    assert not reused
    assert "records no usable run_started_at" in _failures(report)


def test_a_trunk_run_whose_every_job_ran_in_the_latest_attempt_is_reused(world):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", attempt=2)
    reused, outputs, _ = world.decide()
    assert reused and outputs["source_run_id"] == "201"


def test_decide_reports_a_trunk_workflow_with_no_completed_run(world):
    reused, _, report = world.decide()
    assert not reused
    assert "no completed trunk-uat.yml run found" in _failures(report)


def test_decide_skips_a_run_that_kept_no_gate_evidence(world):
    world.fake.runs[TRUNK_RUNS] = [
        {
            "id": 201,
            "event": "schedule",
            "head_branch": "develop",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-05T02:00:00Z",
        }
    ]
    reused, _, report = world.decide()
    assert not reused
    assert "no trunk UAT gate artifact; the run skipped, was deferred or kept no evidence" in _failures(report)


def test_a_candidate_that_errors_stops_the_trunk_search_instead_of_using_an_older_run(world, monkeypatch):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-04T02:00:00Z")
    newer = _commit_between(world)
    world.fake.trunk_run(202, newer, world.trunk_dist, created="2026-11-05T02:00:00Z")
    original = world.fake.json

    def flaky(*args: str):
        if "/runs/202/artifacts" in args[0]:
            raise ValueError("gh api failed")
        return original(*args)

    monkeypatch.setattr(reuse, "_gh_json", flaky)
    reused, outputs, report = world.decide()
    assert not reused and outputs == {}
    assert "trunk run 202: evaluation: ValueError: gh api failed" in _failures(report)
    assert "no older run and no release candidate run is used" in _failures(report)


def test_a_candidate_that_errors_also_refuses_the_release_candidate_fallback(rc_world, monkeypatch):
    _git(rc_world.repo, "update-ref", "refs/remotes/origin/develop", rc_world.head)
    rc_world.fake.trunk_run(201, rc_world.rc_sha, rc_world.rc_dist, created="2026-11-05T02:00:00Z")
    original = rc_world.fake.json

    def flaky(*args: str):
        if "/runs/201/artifacts" in args[0]:
            raise ValueError("gh api failed")
        return original(*args)

    monkeypatch.setattr(reuse, "_gh_json", flaky)
    reused, outputs, report = rc_world.decide()
    assert not reused and outputs == {}
    assert not any(condition.startswith("release candidate") for condition, _, _ in report.rows)


def test_trunk_evidence_is_never_used_for_a_commit_that_is_not_on_develop(world):
    _only_run(world)
    other = _git(world.repo, "commit-tree", "-p", world.candidate, "-m", "elsewhere", f"{world.head}^{{tree}}")
    _git(world.repo, "update-ref", "refs/remotes/origin/develop", other)
    reused, _, report = world.decide()
    assert not reused
    assert "not on develop; trunk UAT evidence does not cover it" in _failures(report)
    assert not any("trunk-uat.yml" in endpoint for endpoint in world.fake.endpoints)


def test_the_trunk_workflow_a_run_executed_must_equal_the_one_at_the_tagged_commit(world):
    workflow = world.repo / ".github" / "workflows" / "trunk-uat.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("name: Trunk UAT\n")
    later = _commit(world.repo, "trunk workflow changes after the tag")
    _git(world.repo, "update-ref", "refs/remotes/origin/develop", later)
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z", head_sha=later)
    reused, _, report = world.decide()
    assert not reused
    assert f"the workflow file the run executed, at {later[:10]}, differs from the one at the tagged commit" in (
        _failures(report)
    )


def test_trunk_evidence_is_never_used_when_develop_is_not_fetched(world):
    _only_run(world)
    _git(world.repo, "update-ref", "-d", "refs/remotes/origin/develop")
    reused, _, report = world.decide()
    assert not reused
    assert "is not fetched" in _failures(report)


def test_a_pre_release_tag_reuses_trunk_evidence_but_never_looks_for_release_candidates(world):
    _only_run(world)
    world.final_dist = _write_dist(world.tmp_path / "rc-final", "0.5.0rc5")
    world.advance(**{"uv.lock": LOCK.format(v="0.5.0rc5")})
    reused, outputs, _ = world.decide(tag="v0.5.0rc5")
    assert reused and outputs["source_kind"] == "trunk"
    _git(world.repo, "update-ref", "-d", "refs/remotes/origin/develop")
    reused, _, report = world.decide(tag="v0.5.0rc5")
    assert not reused
    assert not any(condition == "release candidate" for condition, _, _ in report.rows)


def test_decide_rejects_a_tag_that_is_not_a_release_tag(world):
    reused, _, report = world.decide(tag="v0.5")
    assert not reused and "is not a vX.Y.Z" in _failures(report)


RC_TAG = "v0.5.0rc4"


class ReleaseCandidateWorld(World):
    def __init__(self, tmp_path: Path, repo: Path, fake: FakeGitHub) -> None:
        super().__init__(tmp_path, repo, fake)
        _git(repo, "update-ref", "-d", "refs/remotes/origin/develop")
        (repo / "uv.lock").write_text(LOCK.format(v=RC))
        self.rc_sha = _commit(repo, "rc")
        _git(repo, "tag", "-a", RC_TAG, "-m", "rc4", self.rc_sha)
        (repo / "uv.lock").write_text(LOCK.format(v=FINAL))
        self.head = _commit(repo, "final")
        self.rc_dist = _write_dist(tmp_path / "rc-source", RC)
        self.job_conclusion = "success"
        fake.runs[RELEASE_RUNS] = [
            {
                "id": 99,
                "run_number": 5,
                "run_attempt": 1,
                "head_sha": self.rc_sha,
                "head_branch": RC_TAG,
                "event": "push",
                "status": "completed",
            },
            {
                "id": 11,
                "run_number": 2,
                "run_attempt": 1,
                "head_sha": self.rc_sha,
                "head_branch": RC_TAG,
                "event": "push",
                "status": "completed",
            },
        ]
        fake.jobs[99] = [{"name": "release UAT gate", "conclusion": "success"}]
        self.digest = fake.evidence(
            99,
            tag=RC_TAG,
            sha=self.rc_sha,
            dist=self.rc_dist,
            gate_name=f"release-uat-{RC_TAG}",
            admitted_name=f"admitted-{RC_TAG}",
        )


@pytest.fixture
def rc_world(tmp_path, repo, monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(reuse, "_gh_json", fake.json)
    monkeypatch.setattr(reuse, "_download_artifact", lambda repository, artifact_id: fake.download(artifact_id))
    return ReleaseCandidateWorld(tmp_path, repo, fake)


def test_a_final_tag_off_develop_reuses_its_release_candidate_run(rc_world):
    reused, outputs, report = rc_world.decide()
    assert reused
    assert outputs == {
        "source_kind": "release-candidate",
        "source_tag": RC_TAG,
        "source_sha": rc_world.rc_sha,
        "source_run_id": "99",
        "source_dist_digest": rc_world.digest,
    }
    assert "trunk line" in _failures(report)
    assert report.rows[-1][2] == f"reused release candidate run of {RC_TAG} 99 for {rc_world.rc_sha}"


def test_a_red_trunk_run_that_executed_cells_also_refuses_the_release_candidate_fallback(rc_world):
    _git(rc_world.repo, "update-ref", "refs/remotes/origin/develop", rc_world.head)
    rc_world.fake.trunk_run(
        201, rc_world.rc_sha, rc_world.rc_dist, created="2026-11-05T02:00:00Z", conclusion="failure"
    )
    reused, outputs, report = rc_world.decide()
    assert not reused and outputs == {}
    assert "trunk run 201: run: concluded 'failure'" in _failures(report)
    assert "release candidate" not in _failures(report)


def test_the_release_candidate_run_is_still_used_after_a_trunk_run_that_executed_no_cells(rc_world):
    _git(rc_world.repo, "update-ref", "refs/remotes/origin/develop", rc_world.head)
    rc_world.fake.runs[TRUNK_RUNS] = [
        {
            "id": 201,
            "event": "schedule",
            "head_branch": "develop",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-11-05T02:00:00Z",
        }
    ]
    reused, outputs, report = rc_world.decide()
    assert reused and outputs["source_kind"] == "release-candidate"
    assert "trunk run 201: run: concluded 'failure'" in _failures(report)


def test_a_trunk_run_wins_over_the_release_candidate_run(rc_world):
    _git(rc_world.repo, "update-ref", "refs/remotes/origin/develop", rc_world.head)
    rc_world.fake.trunk_run(201, rc_world.rc_sha, rc_world.rc_dist, created="2026-11-05T02:00:00Z")
    reused, outputs, _ = rc_world.decide()
    assert reused and outputs["source_kind"] == "trunk"


def test_decide_uses_the_newest_release_candidate_run_only(rc_world):
    rc_world.fake.jobs[99] = [{"name": "release UAT gate", "conclusion": "failure"}]
    reused, _, report = rc_world.decide()
    assert not reused
    assert "release candidate v0.5.0rc4: run: run 99 release-uat job: failure" in _failures(report)


def test_decide_falls_back_to_the_trunk_dist_artifact_when_the_admitted_artifact_expired(rc_world, monkeypatch):
    rc_world.fake.expire(99, "admitted-")
    wheel, sdist = reuse.distribution_files(rc_world.rc_dist)
    rc_world.fake.blobs[500] = {wheel.name: wheel.read_bytes(), sdist.name: sdist.read_bytes()}
    original = rc_world.fake.json

    def with_dist(*args: str):
        if "/actions/artifacts?name=dist-" in args[0] and "attempt-1&" in args[0]:
            return {"artifacts": [{"id": 500, "name": "dist", "expired": False}]}
        return original(*args)

    monkeypatch.setattr(reuse, "_gh_json", with_dist)
    reused, _, report = rc_world.decide()
    assert reused
    assert any("dist-" in detail for _, _, detail in report.rows)


def test_decide_refuses_when_the_release_candidate_distribution_cannot_be_obtained(rc_world):
    rc_world.fake.expire(99, "admitted-")
    reused, _, report = rc_world.decide()
    assert not reused
    assert "expired artifact: admitted-v0.5.0rc4" in _failures(report)


def test_decide_refuses_when_the_release_candidate_distribution_differs(rc_world):
    wheel = _wheel_members(FINAL)
    wheel["benchbox/core.py"] = b"tampered\n"
    rc_world.final_dist = _write_dist(rc_world.tmp_path / "tampered", FINAL, wheel=wheel)
    reused, _, report = rc_world.decide()
    assert not reused
    assert "distribution equivalence: wheel: benchbox/core.py differs" in _failures(report)


def test_decide_refuses_when_the_release_candidate_evidence_names_another_commit(rc_world):
    for artifact in rc_world.fake.artifacts[99]:
        if artifact["name"] == f"release-uat-{RC_TAG}":
            blob = rc_world.fake.blobs[artifact["id"]]
            summary = json.loads(blob["uat-gate-summary.json"])
            summary["source_commit_sha"] = "f" * 40
            blob["uat-gate-summary.json"] = json.dumps(summary).encode()
    reused, _, report = rc_world.decide()
    assert not reused
    assert "gate evidence: source commit" in _failures(report)


def test_decide_refuses_when_the_release_candidate_commit_is_not_an_ancestor(rc_world):
    _git(rc_world.repo, "checkout", "-q", "--orphan", "other")
    (rc_world.repo / "x").write_text("x")
    other = _commit(rc_world.repo, "unrelated")
    _git(rc_world.repo, "tag", "-f", "-a", RC_TAG, "-m", "rc4", other)
    rc_world.fake.runs[RELEASE_RUNS][0]["head_sha"] = other
    rc_world.fake.runs[RELEASE_RUNS][1]["head_sha"] = other
    reused, _, report = rc_world.decide()
    assert not reused
    assert "ancestry" in _failures(report)


def test_decide_refuses_when_there_is_no_release_candidate(rc_world):
    _git(rc_world.repo, "tag", "-d", RC_TAG)
    reused, _, report = rc_world.decide()
    assert not reused
    assert "no v0.5.0rcN tag exists" in _failures(report)


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


@pytest.mark.parametrize("foreign_first", [False, True])
def test_a_run_holding_evidence_for_an_ancestor_and_a_newer_commit_is_decisive_and_red(world, foreign_first):
    world.fake.trunk_run(201, world.candidate, world.trunk_dist, created="2026-11-04T02:00:00Z")
    world.fake.trunk_run(202, world.candidate, world.trunk_dist, created="2026-11-05T02:00:00Z")
    unrelated = _git(world.repo, "commit-tree", "-m", "unrelated", "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    world.fake.artifact(202, f"release-uat-trunk-{unrelated}-stage1-shard", {})
    if foreign_first:
        world.fake.artifacts[202].insert(0, world.fake.artifacts[202].pop())
    reused, outputs, report = world.decide()
    assert not reused and outputs == {}
    assert "holds evidence for 2 source commits" in _failures(report)
    assert not any("trunk run 201" in condition for condition, _, _ in report.rows)


def test_a_mixed_source_run_also_refuses_the_release_candidate_fallback(rc_world):
    _git(rc_world.repo, "update-ref", "refs/remotes/origin/develop", rc_world.head)
    rc_world.fake.trunk_run(201, rc_world.rc_sha, rc_world.rc_dist, created="2026-11-05T02:00:00Z")
    unrelated = _git(rc_world.repo, "commit-tree", "-m", "unrelated", "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    rc_world.fake.artifact(201, f"release-uat-trunk-{unrelated}-stage1-shard", {})
    reused, outputs, _ = rc_world.decide()
    assert not reused and outputs == {}


def test_a_green_trunk_run_that_fails_a_guarded_input_check_still_allows_the_release_candidate_fallback(rc_world):
    _git(rc_world.repo, "update-ref", "refs/remotes/origin/develop", rc_world.head)
    rc_world.fake.trunk_run(201, rc_world.rc_sha, rc_world.rc_dist, created="2026-11-05T02:00:00Z")
    rc_world.advance(**{"tests/uat/file.txt": "changed\n"})
    reused, outputs, report = rc_world.decide()
    assert "guarded trees changed since the evidence commit" in _failures(report)
    assert any(condition.startswith("release candidate") for condition, _, _ in report.rows)


