from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import release_artifact_consumer as consumer
from tests.unit.scripts.test_release_artifact_consumer import (
    ROOT,
    distributions as distributions,
    metadata as metadata,
    write_zip,
    zip_members,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-C",
            str(root),
            *args,
        ],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


@pytest.fixture
def tagged_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "pyproject.toml").write_text('[project]\nname = "benchbox"\nversion = "0.4.2"\n')
    git(root, "add", "pyproject.toml")
    git(root, "commit", "-m", "Tagged package")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "tag", "-a", "v0.4.2", "-m", "Release")
    return root


def test_real_annotated_tag_on_develop_resolves_exact_commit(tagged_source):
    source = consumer.resolve_tag(tagged_source, "v0.4.2")
    assert source["head_sha"] == git(tagged_source, "rev-parse", "HEAD")
    assert source["tag_object"] != source["head_sha"]
    assert source["version"] == "0.4.2"


@pytest.mark.parametrize("case", ["tracked", "untracked", "staged"])
def test_dirty_tagged_source_is_not_trusted(tagged_source, case):
    if case == "tracked":
        (tagged_source / "pyproject.toml").write_text("changed source")
    else:
        (tagged_source / "injected.py").write_text("untrusted verifier bytes")
        if case == "staged":
            git(tagged_source, "add", "injected.py")
    with pytest.raises(ValueError, match="checkout is dirty"):
        consumer.resolve_tag(tagged_source, "v0.4.2")


@pytest.mark.parametrize("case", ["lightweight", "off_develop", "version", "checkout", "branch"])
def test_real_invalid_tag_or_checkout_refuses_admission(tagged_source, case):
    tag = "v0.4.2"
    if case == "lightweight":
        git(tagged_source, "tag", "-d", tag)
        git(tagged_source, "tag", tag)
    elif case in {"off_develop", "checkout"}:
        (tagged_source / "later.txt").write_text("Later source")
        git(tagged_source, "add", "later.txt")
        git(tagged_source, "commit", "-m", "Later source")
        if case == "off_develop":
            git(tagged_source, "tag", "-d", tag)
            git(tagged_source, "tag", "-a", tag, "-m", "Off develop")
    elif case == "version":
        tag = "v0.4.3"
        git(tagged_source, "tag", "-a", tag, "-m", "Wrong version")
    else:
        tag = "develop"
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        consumer.resolve_tag(tagged_source, tag)


def test_release_candidate_uses_existing_version_normalization(tagged_source):
    (tagged_source / "pyproject.toml").write_text('[project]\nname = "benchbox"\nversion = "0.4.2rc1"\n')
    git(tagged_source, "add", "pyproject.toml")
    git(tagged_source, "commit", "-m", "Candidate version")
    git(tagged_source, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(tagged_source, "tag", "-a", "v0.4.2-rc.1", "-m", "Candidate")
    assert consumer.resolve_tag(tagged_source, "v0.4.2-rc.1")["version"] == "0.4.2rc1"


def install_gh(tmp_path, monkeypatch, responses=None, archive=None):
    executable = tmp_path / "gh"
    executable.write_text(
        f"#!{sys.executable}\nimport json,sys,pathlib\n"
        f"responses={responses!r}\narchive={str(archive)!r}\n"
        "assert sys.argv[1:4] == ['api', '--hostname', 'github.com'], sys.argv\n"
        "assert len(sys.argv) == 5, sys.argv\n"
        "endpoint=sys.argv[4]\n"
        "if endpoint.endswith('/zip'):\n"
        " sys.stdout.buffer.write(pathlib.Path(archive).read_bytes())\n"
        "else:\n"
        " print(json.dumps(responses[endpoint]))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))


@pytest.mark.parametrize("attempt", ["2", "3"])
def test_producer_command_uses_real_isolated_process_and_attempt_api(
    tmp_path, monkeypatch, distributions, metadata, attempt
):
    receipt_path = distributions / consumer.PRODUCER_RECEIPT
    receipt_path.unlink()
    metadata["run"].update(status="in_progress", conclusion=None)
    metadata["job"].update(status="in_progress", conclusion=None)
    prefix = f"repos/{consumer.REPOSITORY}/"
    responses = {
        prefix + "actions/runs/7": metadata["run"],
        prefix + "actions/runs/7/attempts/2/jobs?per_page=100&page=1": {"total_count": 1, "jobs": [metadata["job"]]},
    }
    install_gh(tmp_path, monkeypatch, responses=responses)
    for key, value in {
        "GITHUB_RUN_ID": "7",
        "GITHUB_RUN_ATTEMPT": attempt,
        "GITHUB_SHA": metadata["run"]["head_sha"],
        "GITHUB_REPOSITORY_ID": "1135770337",
    }.items():
        monkeypatch.setenv(key, value)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(ROOT / "scripts/release_artifact_consumer.py"),
            "producer",
            "--dist",
            str(distributions),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    if attempt == "3":
        assert result.returncode != 0 and "workflow context differs" in result.stderr
        assert not receipt_path.exists()
    else:
        assert result.returncode == 0, result.stderr
        receipt = json.loads(receipt_path.read_text())
        assert (receipt["run_id"], receipt["run_attempt"], receipt["job_id"]) == (7, 2, 9)
        for name, info in receipt["files"].items():
            assert info["sha256"] == hashlib.sha256((distributions / name).read_bytes()).hexdigest()


def test_missing_actual_binary_verifier_fails_without_admission_output(
    tmp_path, monkeypatch, tagged_source, distributions, metadata
):
    sha = git(tagged_source, "rev-parse", "HEAD")
    metadata["run"]["head_sha"] = sha
    metadata["job"]["head_sha"] = sha
    metadata["artifact"]["workflow_run"]["head_sha"] = sha
    metadata["artifact"]["name"] = f"dist-{sha}-attempt-2"
    receipt = consumer.producer_receipt(distributions, metadata["run"], metadata["job"])
    (distributions / consumer.PRODUCER_RECEIPT).write_text(json.dumps(receipt))
    archive = tmp_path / "download.zip"
    digest = write_zip(archive, zip_members(distributions))
    metadata["artifact"].update(digest)
    install_gh(tmp_path, monkeypatch, archive=archive)
    output = tmp_path / "admitted"
    with pytest.raises(ValueError, match="lacks required distribution binary verifier"):
        consumer.admit(tagged_source, "v0.4.2", output, metadata["api"])
    assert not output.exists()
    assert not list(tmp_path.glob("release-admission-*"))


def test_oversized_download_stream_refused(tmp_path, monkeypatch):
    archive = tmp_path / "large.zip"
    archive.write_bytes(b"x" * 64)
    install_gh(tmp_path, monkeypatch, archive=archive)
    monkeypatch.setattr(consumer, "MAX_PAYLOAD_BYTES", 16)
    target = tmp_path / "download.zip"
    with pytest.raises(ValueError, match="size limit"):
        consumer._download(11, target)


def test_existing_output_is_never_overwritten(tmp_path, tagged_source, metadata):
    sha = git(tagged_source, "rev-parse", "HEAD")
    metadata["run"]["head_sha"] = sha
    metadata["job"]["head_sha"] = sha
    metadata["artifact"]["workflow_run"]["head_sha"] = sha
    metadata["artifact"]["name"] = f"dist-{sha}-attempt-2"
    metadata["artifact"]["digest"] = "sha256:" + "b" * 64
    output = tmp_path / "admitted"
    output.mkdir()
    (output / "keep").write_text("prior")
    with pytest.raises(ValueError, match="output already exists"):
        consumer.admit(tagged_source, "v0.4.2", output, metadata["api"])
    assert (output / "keep").read_text() == "prior"


@pytest.mark.parametrize("transport", ["metadata", "download"])
def test_nondefault_gh_host_does_not_change_api_authority(tmp_path, monkeypatch, transport):
    monkeypatch.setenv("GH_HOST", "attacker.invalid")
    prefix = f"repos/{consumer.REPOSITORY}"
    archive = tmp_path / "payload"
    archive.write_bytes(b"trusted host bytes")
    install_gh(tmp_path, monkeypatch, responses={prefix: {"authority": "github.com"}}, archive=archive)
    if transport == "metadata":
        assert consumer.github_json("") == {"authority": "github.com"}
    else:
        target = tmp_path / "download.zip"
        consumer._download(11, target)
        assert target.read_bytes() == archive.read_bytes()


def bind_admission_fixture(tagged_source, distributions, metadata, tmp_path, monkeypatch):
    sha = git(tagged_source, "rev-parse", "HEAD")
    metadata["run"]["head_sha"] = sha
    metadata["job"]["head_sha"] = sha
    metadata["artifact"]["workflow_run"]["head_sha"] = sha
    metadata["artifact"]["name"] = f"dist-{sha}-attempt-2"
    receipt = consumer.producer_receipt(distributions, metadata["run"], metadata["job"])
    (distributions / consumer.PRODUCER_RECEIPT).write_text(json.dumps(receipt))
    archive = tmp_path / "input.zip"
    metadata["artifact"].update(write_zip(archive, zip_members(distributions)))
    install_gh(tmp_path, monkeypatch, archive=archive)


def commit_rejection_probe(root, script):
    verifier = root / "scripts/verify_distribution_binaries.py"
    verifier.parent.mkdir()
    verifier.write_text(script)
    binaries = root / "benchbox/_binaries"
    binaries.mkdir(parents=True)
    (binaries / "sentinel").write_text("committed rejection data")
    git(root, "add", "scripts/verify_distribution_binaries.py", "benchbox/_binaries/sentinel")
    git(root, "commit", "-m", "Rejecting execution boundary sentinel")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "tag", "-d", "v0.4.2")
    git(root, "tag", "-a", "v0.4.2", "-m", "Rejecting fixture")
    return verifier


def test_mutated_then_restored_worktree_cannot_replace_committed_verifier(
    tmp_path, monkeypatch, tagged_source, distributions, metadata
):
    original = "import sys\nsys.exit(23)\n"
    verifier = commit_rejection_probe(tagged_source, original)
    bind_admission_fixture(tagged_source, distributions, metadata, tmp_path, monkeypatch)
    real_run = subprocess.run
    seen = []

    def interpose(argv, **kwargs):
        if any(str(value).endswith("verify_distribution_binaries.py") for value in argv):
            seen.append(argv)
            verifier.write_text("import sys\nsys.exit(0)\n")
            try:
                return real_run(argv, **kwargs)
            finally:
                verifier.write_text(original)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(consumer.subprocess, "run", interpose)
    output = tmp_path / "admitted"
    with pytest.raises(subprocess.CalledProcessError) as error:
        consumer.admit(tagged_source, "v0.4.2", output, metadata["api"])
    assert error.value.returncode == 23
    assert seen and "-I" in seen[0]
    assert verifier.read_text() == original
    assert not output.exists() and not list(tmp_path.glob("release-admission-*"))


def test_python_environment_cannot_turn_rejecting_verifier_into_success(
    tmp_path, monkeypatch, tagged_source, distributions, metadata
):
    commit_rejection_probe(
        tagged_source,
        "import os,sys\nsys.exit(0 if any(k.startswith('PYTHON') for k in os.environ) else 23)\n",
    )
    bind_admission_fixture(tagged_source, distributions, metadata, tmp_path, monkeypatch)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "poison"))
    monkeypatch.setenv("PYTHONSTARTUP", str(tmp_path / "startup.py"))
    output = tmp_path / "admitted"
    with pytest.raises(subprocess.CalledProcessError) as error:
        consumer.admit(tagged_source, "v0.4.2", output, metadata["api"])
    assert error.value.returncode == 23
    assert not output.exists() and not list(tmp_path.glob("release-admission-*"))


def test_snapshot_uses_committed_blob_despite_worktree_and_export_attributes(tmp_path, tagged_source):
    (tagged_source / ".gitattributes").write_text("pyproject.toml export-ignore\n")
    git(tagged_source, "add", ".gitattributes")
    git(tagged_source, "commit", "-m", "Archive exclusion fixture")
    commit = git(tagged_source, "rev-parse", "HEAD")
    original = (tagged_source / "pyproject.toml").read_bytes()
    (tagged_source / "pyproject.toml").write_bytes(b"mutable replacement")
    target = tmp_path / "snapshot"
    target.mkdir()
    consumer._committed_snapshot(tagged_source, commit, target)
    assert (target / "pyproject.toml").read_bytes() == original


def test_snapshot_rejects_committed_verifier_symlink(tmp_path, tagged_source):
    scripts = tagged_source / "scripts"
    scripts.mkdir()
    (scripts / "verify_distribution_binaries.py").symlink_to("../pyproject.toml")
    git(tagged_source, "add", "scripts/verify_distribution_binaries.py")
    git(tagged_source, "commit", "-m", "Symlink rejection fixture")
    with pytest.raises(ValueError, match="unsafe committed snapshot"):
        consumer._committed_snapshot(tagged_source, git(tagged_source, "rev-parse", "HEAD"), tmp_path / "snapshot")


@pytest.mark.parametrize("kind", ["empty", "nonempty", "file", "symlink"])
def test_atomic_output_never_replaces_an_existing_destination(tmp_path, kind):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "receipt").write_text("new verified output")
    output = tmp_path / "output"
    if kind in {"empty", "nonempty"}:
        output.mkdir()
        if kind == "nonempty":
            (output / "owned").write_text("other actor")
    elif kind == "file":
        output.write_text("other actor")
    else:
        output.symlink_to(tmp_path / "absent", target_is_directory=True)
    with pytest.raises(OSError):
        consumer._publish_output(stage, output)
    assert (stage / "receipt").read_text() == "new verified output"
    assert not (output / "receipt").exists()


def test_atomic_output_race_is_refused_at_actual_rename_boundary(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "receipt").write_text("new output")
    output = tmp_path / "output"
    assert not output.exists()
    if os.name == "nt":
        real_rename = os.rename

        def racing_rename(source, destination):
            output.mkdir()
            return real_rename(source, destination)

        monkeypatch.setattr(consumer.os, "rename", racing_rename)
    else:
        library = consumer.ctypes.CDLL(None, use_errno=True)
        name = "renamex_np" if sys.platform == "darwin" else "renameat2"
        original = getattr(library, name)

        class RacingOperation:
            def __call__(self, *args):
                original.argtypes = self.argtypes
                original.restype = self.restype
                output.mkdir()
                return original(*args)

        class RacingLibrary:
            pass

        wrapper = RacingLibrary()
        setattr(wrapper, name, RacingOperation())
        monkeypatch.setattr(consumer.ctypes, "CDLL", lambda *args, **kwargs: wrapper)
    with pytest.raises(FileExistsError):
        consumer._publish_output(stage, output)
    assert output.is_dir() and list(output.iterdir()) == []
    assert (stage / "receipt").read_text() == "new output"


def test_atomic_output_publishes_whole_directory(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "receipt").write_text("boundary-only fixture, not admission")
    output = tmp_path / "output"
    consumer._publish_output(stage, output)
    assert not stage.exists()
    assert (output / "receipt").read_text() == "boundary-only fixture, not admission"


@pytest.mark.parametrize("behavior", ["stall", "oversize", "failure"])
def test_download_failure_reaps_real_child_and_removes_partial(tmp_path, monkeypatch, behavior):
    executable = tmp_path / "gh"
    executable.write_text(
        "#!/bin/sh\n"
        '[ "$1 $2 $3" = "api --hostname github.com" ] || exit 9\n'
        + {
            "stall": "sleep 30\n",
            "oversize": "head -c 128 /dev/zero\nsleep 30\n",
            "failure": "printf partial\nexit 4\n",
        }[behavior]
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(consumer, "DOWNLOAD_TIMEOUT_SECONDS", 0.3 if behavior == "stall" else 30.0)
    monkeypatch.setattr(consumer, "MAX_PAYLOAD_BYTES", 16)
    real_popen = subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(consumer.subprocess, "Popen", launch)
    target = tmp_path / "download.zip"
    with pytest.raises(
        ValueError, match={"stall": "deadline", "oversize": "size limit", "failure": "failed"}[behavior]
    ):
        consumer._download(11, target)
    assert children and children[0].returncode is not None
    assert children[0].stdout.closed
    assert not target.exists()


def test_download_refuses_existing_file_without_removing_it(tmp_path):
    archive = tmp_path / "owned.zip"
    archive.write_bytes(b"another actor's file")
    with pytest.raises(FileExistsError):
        consumer._download(11, archive)
    assert archive.read_bytes() == b"another actor's file"


def test_stalled_admission_leaves_no_output_or_partial_stage(
    tmp_path, monkeypatch, tagged_source, distributions, metadata
):
    bind_admission_fixture(tagged_source, distributions, metadata, tmp_path, monkeypatch)
    executable = tmp_path / "gh"
    executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    monkeypatch.setattr(consumer, "DOWNLOAD_TIMEOUT_SECONDS", 0.3)
    real_popen = subprocess.Popen
    children = []

    def launch(argv, **kwargs):
        process = real_popen(argv, **kwargs)
        if argv[0] == "gh":
            children.append(process)
        return process

    monkeypatch.setattr(consumer.subprocess, "Popen", launch)
    output = tmp_path / "admitted"
    with pytest.raises(ValueError, match="deadline"):
        consumer.admit(tagged_source, "v0.4.2", output, metadata["api"])
    assert children and children[0].returncode is not None
    assert not output.exists() and not list(tmp_path.glob("release-admission-*"))


def test_reader_start_failure_reaps_child_and_removes_partial(tmp_path, monkeypatch):
    archive = tmp_path / "input.zip"
    archive.write_bytes(b"download fixture")
    install_gh(tmp_path, monkeypatch, archive=archive)
    real_popen = subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    def fail_start(self):
        raise RuntimeError("reader cannot start")

    monkeypatch.setattr(consumer.subprocess, "Popen", launch)
    monkeypatch.setattr(consumer.threading.Thread, "start", fail_start)
    target = tmp_path / "download.zip"
    with pytest.raises(RuntimeError, match="reader cannot start"):
        consumer._download(11, target)
    assert children and children[0].returncode is not None
    assert children[0].stdout.closed
    assert not target.exists()


def test_transient_git_replacement_cannot_replace_committed_verifier(
    tmp_path, monkeypatch, tagged_source, distributions, metadata
):
    verifier = commit_rejection_probe(tagged_source, "import sys\nsys.exit(23)\n")
    candidate = git(tagged_source, "rev-parse", "HEAD")
    verifier.write_text("import sys\nsys.exit(0)\n")
    git(tagged_source, "add", "scripts/verify_distribution_binaries.py")
    git(tagged_source, "commit", "-m", "Replacement-object attack fixture")
    replacement = git(tagged_source, "rev-parse", "HEAD")
    git(tagged_source, "checkout", candidate)
    bind_admission_fixture(tagged_source, distributions, metadata, tmp_path, monkeypatch)
    snapshot = consumer._committed_snapshot

    def replaced_snapshot(root, commit, destination):
        git(root, "replace", candidate, replacement)
        try:
            return snapshot(root, commit, destination)
        finally:
            git(root, "replace", "-d", candidate)

    monkeypatch.setattr(consumer, "_committed_snapshot", replaced_snapshot)
    output = tmp_path / "admitted"
    with pytest.raises(subprocess.CalledProcessError) as error:
        consumer.admit(tagged_source, "v0.4.2", output, metadata["api"])
    assert error.value.returncode == 23
    assert git(tagged_source, "replace", "-l") == ""
    assert not output.exists() and not list(tmp_path.glob("release-admission-*"))


@pytest.mark.parametrize("parent_exits", [False, True])
def test_inherited_pipe_writer_cannot_hold_download_cleanup(tmp_path, monkeypatch, parent_exits):
    from benchbox.utils.clock import elapsed_seconds, mono_time

    pidfile = tmp_path / "descendant.pid"
    executable = tmp_path / "gh"
    executable.write_text(
        "#!/bin/sh\n"
        '[ "$1 $2 $3" = "api --hostname github.com" ] || exit 9\n'
        "sleep 30 &\n"
        f"echo $! > {pidfile}\n" + ("exit 0\n" if parent_exits else "sleep 30\n")
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    archive = tmp_path / "download.zip"
    supervisor = tmp_path / "supervisor.py"
    supervisor.write_text(
        "import importlib.util,pathlib,sys\n"
        f"spec=importlib.util.spec_from_file_location('consumer',{str(ROOT / 'scripts/release_artifact_consumer.py')!r})\n"
        "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n"
        "module.DOWNLOAD_TIMEOUT_SECONDS=8.0\n"
        f"archive=pathlib.Path({str(archive)!r})\n"
        "try:\n module._download(11,archive)\n"
        "except ValueError as exc:\n assert 'deadline' in str(exc)\n"
        "else:\n raise AssertionError('deadline did not refuse')\n"
        "assert not archive.exists()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", str(supervisor)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=40)
        assert process.returncode == 0, (stdout, stderr)
        assert pidfile.exists(), "the stub gh never started within the download deadline"
        descendant = int(pidfile.read_text())
        started = mono_time()
        while True:
            if os.name == "nt":
                break
            state = subprocess.run(["ps", "-p", str(descendant), "-o", "stat="], capture_output=True, text=True)
            if not state.stdout.strip() or state.stdout.strip().startswith("Z"):
                break
            assert elapsed_seconds(started) < 1, "owned pipe writer survived"
            import threading

            threading.Event().wait(0.02)
        assert not archive.exists()
    finally:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)


def commit_snapshot_files(root: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(root, "add", name)
    git(root, "commit", "-m", "Snapshot boundary fixture")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(root, "tag", "-d", "v0.4.2")
    git(root, "tag", "-a", "v0.4.2", "-m", "Snapshot boundary fixture")
    return git(root, "rev-parse", "HEAD")


VERIFIER_BOUNDARY_PROBE = """\
import sys
from benchbox.utils.binary_manifest import SOURCE

assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode, sys.flags
assert not any("site-packages" in entry for entry in sys.path), sys.path
sys.exit(0 if SOURCE == "committed snapshot" else 23)
"""


def test_verifier_runs_committed_package_without_site_or_package_init(tagged_source):
    commit = commit_snapshot_files(
        tagged_source,
        {
            "scripts/verify_distribution_binaries.py": VERIFIER_BOUNDARY_PROBE,
            "benchbox/__init__.py": "raise RuntimeError('package __init__ must not run')\n",
            "benchbox/utils/__init__.py": "raise RuntimeError('package __init__ must not run')\n",
            "benchbox/utils/binary_manifest.py": 'SOURCE = "committed snapshot"\n',
        },
    )
    consumer._verify_committed_binaries(tagged_source, commit, [])


def test_verifier_rejection_in_committed_module_is_not_masked(tagged_source):
    commit = commit_snapshot_files(
        tagged_source,
        {
            "scripts/verify_distribution_binaries.py": VERIFIER_BOUNDARY_PROBE,
            "benchbox/utils/binary_manifest.py": 'SOURCE = "a different module"\n',
        },
    )
    with pytest.raises(subprocess.CalledProcessError) as error:
        consumer._verify_committed_binaries(tagged_source, commit, [])
    assert error.value.returncode == 23


def test_verifier_and_git_children_inherit_no_credentials(tagged_source, monkeypatch):
    secrets = {
        "GH_TOKEN": "synthetic-gh-token",
        "GITHUB_TOKEN": "synthetic-github-token",
        "GH_ENTERPRISE_TOKEN": "synthetic-enterprise-token",
        "AWS_SECRET_ACCESS_KEY": "synthetic-aws-secret",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "/nonexistent/helper",
    }
    probe = (
        "import os, sys\n"
        "leaked = [k for k in os.environ if k.startswith(('GH_', 'GITHUB_', 'GIT_', 'AWS_')) or 'TOKEN' in k]\n"
        "sys.exit(23 if leaked else 0)\n"
    )
    commit = commit_snapshot_files(
        tagged_source,
        {"scripts/verify_distribution_binaries.py": probe, "benchbox/utils/binary_manifest.py": ""},
    )
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    consumer._verify_committed_binaries(tagged_source, commit, [])
    environment = consumer._child_environment(**consumer._GIT_ENVIRONMENT)
    assert not set(secrets) & set(environment)
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull and environment["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.parametrize("route", ["local-config", "environment"])
def test_git_children_do_not_run_configured_filesystem_monitor(tagged_source, monkeypatch, tmp_path, route):
    marker = tmp_path / "helper-ran"
    helper = tmp_path / "helper.sh"
    helper.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 1\n")
    helper.chmod(0o755)
    if route == "local-config":
        git(tagged_source, "config", "core.fsmonitor", str(helper))
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(helper))
    subprocess.run(["git", "-C", str(tagged_source), "status", "--porcelain"], capture_output=True, check=False)
    assert marker.exists(), "control: configured helper should run under plain git"
    marker.unlink()
    consumer._run_git(tagged_source, "status", "--porcelain", check=True, stdout=subprocess.PIPE, timeout=60)
    assert not marker.exists(), "hardened git must not run the configured filesystem monitor"


def test_snapshot_budget_is_enforced_before_any_blob_is_written(tagged_source, monkeypatch, tmp_path):
    commit = commit_snapshot_files(tagged_source, {"benchbox/payload.bin": "x" * 5000})
    monkeypatch.setattr(consumer, "MAX_PAYLOAD_BYTES", 1000)
    real_run = subprocess.run
    commands = []

    def record(argv, **kwargs):
        commands.append(list(argv))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(consumer.subprocess, "run", record)
    destination = tmp_path / "snapshot"
    destination.mkdir()
    with pytest.raises(ValueError, match="exceeds size limit"):
        consumer._committed_snapshot(tagged_source, commit, destination)
    assert any("ls-tree" in command for command in commands)
    assert not any("cat-file" in command for command in commands), "blob contents were read before the budget check"
    assert not list(destination.rglob("*"))


def test_download_fails_closed_without_posix_process_ownership(monkeypatch, tmp_path):
    import types

    monkeypatch.setattr(consumer, "os", types.SimpleNamespace(name="nt"))
    archive = tmp_path / "artifact.zip"
    with pytest.raises(ValueError, match="Windows is unsupported"):
        consumer._download(1, archive)
    assert not archive.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups; Windows download is unsupported")
def test_cleanup_tolerates_an_exited_unreaped_group_leader():
    import threading

    process = subprocess.Popen(["/bin/sh", "-c", "exit 0"], stdout=subprocess.PIPE, bufsize=0, start_new_session=True)
    for _ in range(500):
        state = subprocess.run(["ps", "-p", str(process.pid), "-o", "stat="], capture_output=True, text=True)
        if state.stdout.strip().startswith("Z"):
            break
        threading.Event().wait(0.01)
    else:
        pytest.fail("the child never became an unreaped zombie")
    consumer._reap_download(process, None, threading.Event())
    assert process.returncode == 0
    assert process.stdout is not None and process.stdout.closed


def test_git_children_do_not_run_repository_local_transport_helpers(tagged_source, tmp_path):
    marker = tmp_path / "ssh-helper-ran"
    helper = tmp_path / "ssh-helper.sh"
    helper.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 1\n")
    helper.chmod(0o755)
    git(tagged_source, "config", "core.sshCommand", str(helper))
    subprocess.run(
        ["git", "-C", str(tagged_source), "ls-remote", "ssh://invalid.example/repository"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert marker.exists(), "control: configured ssh helper should run under plain git"
    marker.unlink()
    result = consumer._run_git(
        tagged_source,
        "ls-remote",
        "ssh://invalid.example/repository",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode != 0
    assert "not allowed" in result.stderr
    assert not marker.exists(), "hardened git must not run a repository-local transport helper"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize(
    "mode, accepted",
    [(0o700, True), (0o755, True), (0o777, False), (0o775, False), (0o1777, True)],
)
def test_output_parent_must_not_let_other_users_swap_entries(tmp_path, mode, accepted):
    parent = tmp_path / "parent"
    parent.mkdir()
    parent.chmod(mode)
    try:
        if accepted:
            consumer._require_private_parent(parent)
        else:
            with pytest.raises(ValueError, match="writable by other users"):
                consumer._require_private_parent(parent)
    finally:
        parent.chmod(0o700)


def test_swapped_staging_directory_is_refused_before_publication(
    tmp_path, monkeypatch, tagged_source, distributions, metadata
):
    commit_snapshot_files(
        tagged_source,
        {
            "scripts/verify_distribution_binaries.py": "import sys\nsys.exit(0)\n",
            "benchbox/utils/binary_manifest.py": "",
        },
    )
    bind_admission_fixture(tagged_source, distributions, metadata, tmp_path, monkeypatch)
    output = tmp_path / "admitted"
    real_resolve_tag = consumer.resolve_tag
    calls = []

    def resolve_then_swap(*args, **kwargs):
        result = real_resolve_tag(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            stage = next(tmp_path.glob("release-admission-*"))
            stage.rename(tmp_path / "moved-away")
            stage.mkdir()
        return result

    monkeypatch.setattr(consumer, "resolve_tag", resolve_then_swap)
    with pytest.raises(ValueError, match="staging directory changed during admission"):
        consumer.admit(tagged_source, "v0.4.2", output, metadata["api"])
    assert not output.exists()


def test_moved_hosted_tag_changes_the_local_tag_so_the_stale_checkout_is_refused(tagged_source, tmp_path):
    first = consumer.resolve_tag(tagged_source, "v0.4.2")
    hosted = tmp_path / "hosted"
    subprocess.run(["git", "clone", "-q", str(tagged_source), str(hosted)], check=True)
    git(hosted, "config", "user.name", "Test Fixture")
    git(hosted, "config", "user.email", "fixture@example.invalid")
    (hosted / "moved.txt").write_text("a different commit\n")
    git(hosted, "add", "moved.txt")
    git(hosted, "commit", "-m", "Commit the hosted tag now points at")
    git(hosted, "tag", "-d", "v0.4.2")
    git(hosted, "tag", "-a", "v0.4.2", "-m", "Moved hosted tag")
    subprocess.run(["git", "-C", str(tagged_source), "fetch", "-q", "--no-tags", str(hosted), "develop"], check=True)
    assert consumer.resolve_tag(tagged_source, "v0.4.2") == first
    subprocess.run(
        ["git", "-C", str(tagged_source), "fetch", "-q", "--no-tags", str(hosted), *consumer.hosted_refspecs("v0.4.2")],
        check=True,
    )
    with pytest.raises(ValueError, match="checkout differs from tagged source"):
        consumer.resolve_tag(tagged_source, "v0.4.2")


@pytest.mark.parametrize("tag", ["", "0.4.2", "v", "v1;touch x", "../v1", "v1 --upload-pack=x", "v1\n"])
def test_hosted_fetch_refuses_a_malformed_tag(tag):
    with pytest.raises(ValueError, match="invalid version tag"):
        consumer.hosted_refspecs(tag)


def test_hosted_fetch_uses_the_fixed_url_and_forces_the_tag(monkeypatch, tmp_path):
    calls = []

    def spy(root, *args, **options):
        calls.append((root, args, options))

    monkeypatch.setattr(consumer, "_run_git", spy)
    consumer.fetch_hosted_refs(tmp_path, "v0.4.2")
    ((root, args, options),) = calls
    assert root == tmp_path
    assert args == (
        "fetch",
        "--no-tags",
        "https://github.com/BenchBox-dev/BenchBox.git",
        "develop:refs/remotes/origin/develop",
        "refs/heads/release/*:refs/remotes/origin/release/*",
        "+refs/tags/v0.4.2:refs/tags/v0.4.2",
    )
    assert options == {"check": True, "timeout": 600}


def test_local_url_rewrite_cannot_reach_an_ext_transport(tagged_source, tmp_path):
    marker = tmp_path / "ext-helper-ran"
    helper = tmp_path / "ext-helper.sh"
    helper.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 1\n")
    helper.chmod(0o755)
    url = "https://github.com/BenchBox-dev/BenchBox.git"
    git(tagged_source, "config", f"url.ext::{helper}.insteadOf", url)
    git(tagged_source, "config", "protocol.ext.allow", "always")
    subprocess.run(["git", "-C", str(tagged_source), "ls-remote", url], capture_output=True, check=False, timeout=60)
    assert marker.exists(), "control: the rewritten ext transport should run under plain git"
    marker.unlink()
    result = consumer._run_git(tagged_source, "ls-remote", url, capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode != 0
    assert not marker.exists(), "hardened git must not run an ext transport named by repository configuration"


def test_gh_children_get_only_authentication_and_network_settings(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "gh-environment.json"
    stub = bin_dir / "gh"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        f"open({str(seen)!r}, 'w').write(json.dumps(sorted(os.environ)))\n"
        "print('{}')\n"
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "synthetic-token")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-aws-secret")
    monkeypatch.setenv("GH_HOST", "evil.example")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    assert consumer.github_json("") == {}
    names = set(json.loads(seen.read_text()))
    assert {"PATH", "GH_TOKEN", "HTTPS_PROXY"} <= names
    assert not {"AWS_SECRET_ACCESS_KEY", "GH_HOST", "GIT_CONFIG_COUNT"} & names


def test_cli_fetches_hosted_refs_before_it_admits(monkeypatch, tmp_path, capsys):
    order = []
    monkeypatch.setattr(consumer, "fetch_hosted_refs", lambda source, tag: order.append(("fetch", source, tag)))

    def record_admission(root, tag, output, *args, **kwargs):
        order.append(("admit", root, tag))
        return {"tag": tag}

    monkeypatch.setattr(consumer, "admit", record_admission)
    argv = ["admit", "--source", str(tmp_path), "--tag", "v0.4.2", "--output", str(tmp_path / "admitted")]
    assert consumer.main(argv) == 0
    assert order == [("fetch", tmp_path, "v0.4.2"), ("admit", tmp_path.resolve(), "v0.4.2")]
    assert '"tag": "v0.4.2"' in capsys.readouterr().out


def test_cli_refuses_to_admit_when_the_hosted_fetch_fails(monkeypatch, tmp_path):
    admitted = []

    def failing_fetch(source, tag):
        raise subprocess.CalledProcessError(128, ["git", "fetch"])

    monkeypatch.setattr(consumer, "fetch_hosted_refs", failing_fetch)
    monkeypatch.setattr(consumer, "admit", lambda *args, **kwargs: admitted.append(args))
    argv = ["admit", "--source", str(tmp_path), "--tag", "v0.4.2", "--output", str(tmp_path / "admitted")]
    with pytest.raises(SystemExit) as error:
        consumer.main(argv)
    assert error.value.code == 1
    assert not admitted
