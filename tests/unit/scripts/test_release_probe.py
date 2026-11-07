from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location("release_probe_under_test", SCRIPTS / "release_probe.py")
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)

REPOSITORY = "owner/repo"
RUN_ID = 4242
CANDIDATE_VERSION = "0.5.0"
LOCK = (
    'version = 1\n\n[[package]]\nname = "benchbox"\nversion = "{v}"\nsource = {{ editable = "." }}\n\n'
    '[[package]]\nname = "other"\nversion = "0.5.0"\n'
)
WORKFLOW = "name: Release\njobs:\n" + "".join(f"  uat-stage{n}:\n    runs-on: ubuntu-latest\n" for n in (1, 2, 3))
GUARDED_FILES = (
    "tests/uat/file.txt",
    "tests/utilities/file.txt",
    "tests/__init__.py",
    "docker/file.txt",
    ".github/actions/release-uat-env/file.txt",
    "Makefile",
    "scripts/uat-bring-up/uat_bring_up.py",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    ).stdout.strip()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _members(version: str) -> tuple[dict[str, bytes], dict[str, bytes]]:
    init = f'"""benchbox"""\n\n__version__ = "{version}"\n'.encode()
    metadata = f"Metadata-Version: 2.4\nName: benchbox\nVersion: {version}\n\nCurrent release: v{version}.\n".encode()
    marker = f"# Versions\n\nCurrent release: `v{version}`.\n".encode()
    wheel = {
        "benchbox/__init__.py": init,
        "benchbox/core.py": b"VALUE = 1\n",
        "benchbox/utils/VERSION_MANAGEMENT.md": marker,
        f"benchbox-{version}.dist-info/METADATA": metadata,
        f"benchbox-{version}.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        f"benchbox-{version}.dist-info/RECORD": b"benchbox/core.py,sha256=abc,1\n",
    }
    sdist = {
        "PKG-INFO": metadata,
        "benchbox.egg-info/PKG-INFO": metadata,
        "benchbox.egg-info/SOURCES.txt": b"benchbox/core.py\n",
        "pyproject.toml": f'[project]\nname = "benchbox"\nversion = "{version}"\n'.encode(),
        "README.md": f"# BenchBox\n\nCurrent release: v{version}.\n".encode(),
        "CHANGELOG.md": f"changelog {version}\n".encode(),
        "benchbox/__init__.py": init,
        "benchbox/core.py": b"VALUE = 1\n",
        "benchbox/utils/VERSION_MANAGEMENT.md": marker,
    }
    return wheel, sdist


def _write_dist(directory: Path, version: str, *, with_sums: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    wheel, sdist = _members(version)
    with zipfile.ZipFile(directory / f"benchbox-{version}-py3-none-any.whl", "w") as archive:
        for name, data in wheel.items():
            archive.writestr(name, data)
    with tarfile.open(directory / f"benchbox-{version}.tar.gz", "w:gz") as archive:
        for name, data in sdist.items():
            info = tarfile.TarInfo(f"benchbox-{version}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    if with_sums:
        names = sorted(path.name for path in directory.iterdir())
        lines = "".join(f"{hashlib.sha256((directory / n).read_bytes()).hexdigest()}  {n}\n" for n in names)
        (directory / "SHA256SUMS").write_text(lines, encoding="utf-8")
    return directory


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "develop")
    files = {
        "pyproject.toml": f'[project]\nname = "benchbox"\nversion = "{CANDIDATE_VERSION}"\n',
        "uv.lock": LOCK.format(v=CANDIDATE_VERSION),
        "benchbox/__init__.py": f'__version__ = "{CANDIDATE_VERSION}"\n',
        "README.md": f"Current release: v{CANDIDATE_VERSION}.\n",
        "docs/README.md": f"Current release: v{CANDIDATE_VERSION}.\n",
        "benchbox/utils/VERSION_MANAGEMENT.md": f"Current release: `v{CANDIDATE_VERSION}`.\n",
        ".github/workflows/release-v2.yml": WORKFLOW,
        **dict.fromkeys(GUARDED_FILES, "one\n"),
    }
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    (path / "scripts").mkdir(exist_ok=True)
    shutil.copy(SCRIPTS / "update_version.py", path / "scripts" / "update_version.py")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")
    return path


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("event", "probe_input", "tag", "ref_type", "mode"),
    [
        ("push", "", "v0.5.0", "tag", "real"),
        ("push", "", "v0.5.0rc1", "tag", "real"),
        ("workflow_dispatch", "none", "v0.5.0", "tag", "real"),
        ("workflow_dispatch", "none", "v0.5.1", "branch", "real"),
        ("workflow_dispatch", "reuse", probe.PROBE_TAG, "branch", "probe"),
        ("workflow_dispatch", "fallback", probe.PROBE_TAG, "branch", "probe"),
    ],
)
def test_classification_accepts_real_releases_and_branch_probes(event, probe_input, tag, ref_type, mode):
    assert probe.classify_run(event, probe_input, tag, ref_type) == (mode, [])


@pytest.mark.parametrize(
    ("event", "probe_input", "tag", "ref_type"),
    [
        ("push", "", probe.PROBE_TAG, "tag"),
        ("workflow_dispatch", "none", probe.PROBE_TAG, "branch"),
        ("workflow_dispatch", "reuse", "v0.5.0", "branch"),
        ("workflow_dispatch", "fallback", "v0.5.0rc1", "branch"),
        ("workflow_dispatch", "reuse", "v0.5.0", "tag"),
        ("workflow_dispatch", "reuse", probe.PROBE_TAG, "tag"),
        ("workflow_dispatch", "reuse", probe.PROBE_TAG, ""),
        ("workflow_dispatch", "publish", probe.PROBE_TAG, "branch"),
        ("workflow_dispatch", "", "v0.5.0", "branch"),
        ("pull_request", "reuse", probe.PROBE_TAG, "branch"),
        ("workflow_call", "none", "v0.5.0", "tag"),
        ("push", "reuse", probe.PROBE_TAG, "branch"),
    ],
)
def test_classification_refuses_every_probe_of_a_real_tag_and_every_unknown_combination(
    event, probe_input, tag, ref_type
):
    mode, problems = probe.classify_run(event, probe_input, tag, ref_type)
    assert problems
    assert mode in {"refused", "real", "probe"}


def test_the_guard_command_exits_nonzero_for_a_probe_aimed_at_a_real_tag(capsys):
    argv = ["guard", "--event", "workflow_dispatch", "--probe", "reuse", "--tag", "v0.5.0", "--ref-type", "branch"]
    assert probe.main(argv) == 1
    assert "synthetic tag" in capsys.readouterr().err


def test_a_push_of_the_probe_tag_cannot_release_it(capsys):
    assert probe.main(["guard", "--event", "push", "--tag", probe.PROBE_TAG, "--ref-type", "tag"]) == 1
    assert "cannot be released" in capsys.readouterr().err


def test_the_version_bump_changes_the_release_markers_and_only_the_benchbox_lock_entry(repo):
    probe.bump_version(repo)
    assert f'version = "{probe.PROBE_VERSION}"' in _read(repo / "pyproject.toml")
    assert probe.PROBE_VERSION in _read(repo / "benchbox" / "__init__.py")
    lock = _read(repo / "uv.lock")
    assert lock == LOCK.format(v=probe.PROBE_VERSION)
    assert 'name = "other"\nversion = "0.5.0"' in lock


@pytest.fixture
def admitted_inputs(tmp_path):
    dist = _write_dist(tmp_path / "dist", probe.PROBE_VERSION, with_sums=True)
    environ = {"GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_REPOSITORY_ID": "77"}
    return dist, environ


def test_probe_admission_builds_a_directory_the_release_verifier_accepts(tmp_path, admitted_inputs):
    dist, environ = admitted_inputs
    head_sha = "a" * 40
    output = tmp_path / "admitted"
    probe.build_admitted(dist, output, probe.PROBE_TAG, head_sha, environ)
    admitted = probe.load_sibling("release_admitted_dist")
    receipt, payloads = admitted.verify_admitted(output, probe.PROBE_TAG, head_sha)
    assert receipt["producer"]["artifact_name"] == f"dist-{head_sha}-attempt-2"
    assert sorted(payloads) == sorted(p.name for p in dist.iterdir() if p.name != "SHA256SUMS")


@pytest.mark.parametrize("tag", ["v0.5.0", "v0.5.0rc1", "v0.0.1rc0", "v0.0.0rc1"])
def test_probe_admission_refuses_any_tag_but_the_synthetic_one(tmp_path, admitted_inputs, tag):
    dist, environ = admitted_inputs
    with pytest.raises(ValueError, match="synthetic"):
        probe.build_admitted(dist, tmp_path / "admitted", tag, "a" * 40, environ)
    assert not (tmp_path / "admitted").exists()


def test_probe_preparation_refuses_a_real_tag_before_touching_the_checkout(tmp_path, repo):
    before = _read(repo / "pyproject.toml")
    with pytest.raises(ValueError, match="synthetic"):
        probe.prepare(repo, tmp_path / "work", "v0.5.0", _head(repo))
    assert _read(repo / "pyproject.toml") == before
    assert not (tmp_path / "work").exists()


def _canary(repo: Path, **overrides) -> int:
    head = _head(repo)
    fake = probe.FakeGitHub(REPOSITORY, head, RUN_ID, **overrides)
    return probe.run_canary(head, REPOSITORY, repo, fake)


def test_the_probe_canary_check_passes_for_a_fresh_green_canary_on_an_ancestor(repo):
    assert _canary(repo) == 0


def _unrelated_commit(repo: Path) -> str:
    _git(repo, "checkout", "-q", "--orphan", "unrelated")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-q", "-m", "unrelated")
    sha = _head(repo)
    _git(repo, "checkout", "-q", "-f", "develop")
    return sha


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"canary_age_hours": 49.0}, "stale"),
        ({"canary_conclusion": "failure"}, "red"),
        ({"canary_commit": "unrelated"}, "non-ancestor"),
    ],
)
def test_the_probe_runs_the_real_canary_freshness_check_so_a_stale_red_or_unrelated_canary_refuses(
    repo, capsys, overrides, detail
):
    if overrides.get("canary_commit") == "unrelated":
        overrides = {"canary_commit": _unrelated_commit(repo)}
    assert _canary(repo, **overrides) == 1
    assert detail in capsys.readouterr().err


@pytest.fixture
def decision(tmp_path, repo):
    candidate = _write_dist(tmp_path / "candidate", CANDIDATE_VERSION)
    final = _write_dist(tmp_path / "final", probe.PROBE_VERSION)

    def run(variant: str) -> tuple[int, dict[str, str], str]:
        output = tmp_path / f"out-{variant}"
        head = _head(repo)
        status = probe.run_decide(
            variant,
            probe.PROBE_TAG,
            head,
            REPOSITORY,
            candidate,
            final,
            tmp_path / f"work-{variant}",
            repo,
            output,
            RUN_ID,
        )
        return status, probe._read_outputs(output), head

    return run


def test_the_reuse_variant_reuses_the_synthetic_trunk_run_through_the_real_decision(decision):
    status, outputs, head = decision("reuse")
    assert status == 0
    assert outputs["reuse"] == "true"
    assert outputs["source_kind"] == "trunk"
    assert outputs["source_sha"] == head
    assert outputs["source_run_id"] == str(RUN_ID)
    assert outputs["source_tag"] == f"trunk-{head}"


def test_the_fallback_variant_finds_no_trunk_evidence_and_runs_the_full_uat_stages(decision):
    status, outputs, _ = decision("fallback")
    assert status == 0
    assert outputs == {"reuse": "false"}


def test_the_reuse_variant_fails_when_the_final_distribution_differs_from_the_candidate(tmp_path, decision):
    (tmp_path / "final" / f"benchbox-{probe.PROBE_VERSION}.tar.gz").write_bytes(b"not an sdist")
    status, outputs, _ = decision("reuse")
    assert status == 1
    assert outputs["reuse"] == "false"


@pytest.mark.parametrize(
    ("tag", "variant"), [("v0.5.0", "reuse"), ("v0.5.0", "fallback"), (probe.PROBE_TAG, "publish")]
)
def test_decision_refuses_a_real_tag_and_an_unknown_variant(tmp_path, repo, capsys, tag, variant):
    status = probe.run_decide(
        variant, tag, _head(repo), REPOSITORY, tmp_path, tmp_path, tmp_path / "work", repo, tmp_path / "out", RUN_ID
    )
    assert status == 1
    assert "probe decides only" in capsys.readouterr().err


def test_decision_refuses_a_checkout_that_is_not_the_probed_commit(tmp_path, repo, capsys):
    status = probe.run_decide(
        "reuse", probe.PROBE_TAG, "c" * 40, REPOSITORY, tmp_path, tmp_path, tmp_path / "w", repo, tmp_path / "o", 1
    )
    assert status == 1
    assert "not the probed commit" in capsys.readouterr().err


def test_the_fake_github_has_no_route_to_anything_it_was_not_given():
    fake = probe.FakeGitHub(REPOSITORY, "a" * 40, RUN_ID)
    with pytest.raises(ValueError, match="no route"):
        fake.gh_json(f"repos/{REPOSITORY}/pulls")
    with pytest.raises(ValueError, match="no artifact"):
        fake.download_artifact(REPOSITORY, 1)


def test_the_expected_decision_flags_a_reuse_variant_that_did_not_reuse():
    head = "a" * 40
    assert probe.expected_decision_problems("reuse", {"reuse": "false"}, RUN_ID, head)
    assert probe.expected_decision_problems("fallback", {"reuse": "true", "source_kind": "trunk"}, RUN_ID, head)
    assert not probe.expected_decision_problems("fallback", {"reuse": "false"}, RUN_ID, head)


def test_probe_clock_helpers_use_utc():
    stamp = probe._utc_stamp(dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC))
    assert stamp == "2026-11-06T12:00:00Z"


def test_the_probe_json_receipt_names_the_synthetic_tag_only(tmp_path, admitted_inputs):
    dist, environ = admitted_inputs
    output = tmp_path / "admitted"
    probe.build_admitted(dist, output, probe.PROBE_TAG, "a" * 40, environ)
    receipt = json.loads(_read(output / "admission-receipt.json"))
    assert receipt["tag"] == probe.PROBE_TAG
    assert receipt["version"] == probe.PROBE_VERSION
