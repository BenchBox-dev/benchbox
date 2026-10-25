from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import queue
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY = "BenchBox-dev/BenchBox"
WORKFLOW = ".github/workflows/trunk.yml"
PRODUCER_BRANCH = "develop"
PRODUCER_EVENT = "push"
JOB_NAME = "dist-artifact"
PRODUCER_RECEIPT = "producer-receipt.json"
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60.0
PRODUCER_WAIT_SECONDS = 60.0
PRODUCER_POLL_SECONDS = 3.0
Api = Callable[[str], dict[str, Any]]

_CHILD_ENVIRONMENT_KEYS = frozenset({"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT"})
_GIT_ENVIRONMENT = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
TAG_PATTERN = r"v[0-9][A-Za-z0-9.+-]*"
_DENIED_TRANSPORTS = ("ext", "file", "git", "http", "ssh")
_GH_ENVIRONMENT_KEYS = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "HOME",
        "XDG_CONFIG_HOME",
        "GH_CONFIG_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
        "all_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_CA_BUNDLE",
        "REQUESTS_CA_BUNDLE",
    }
)


def _child_environment(**extra: str) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key in _CHILD_ENVIRONMENT_KEYS}
    environment.update(extra)
    return environment


def _gh_environment() -> dict[str, str]:
    environment = _child_environment(GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
    environment.update({key: value for key, value in os.environ.items() if key in _GH_ENVIRONMENT_KEYS})
    return environment


def _run_git(root: Path, *args: str, **options: Any) -> subprocess.CompletedProcess[Any]:
    command = [
        "git",
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "protocol.allow=never",
        *[item for name in _DENIED_TRANSPORTS for item in ("-c", f"protocol.{name}.allow=never")],
        "-c",
        "protocol.https.allow=always",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        "-C",
        str(root),
        *args,
    ]
    return subprocess.run(command, env=_child_environment(**_GIT_ENVIRONMENT), **options)


def _require_private_parent(parent: Path) -> None:
    if not hasattr(os, "getuid"):
        return
    info = parent.stat()
    writable_by_others = bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    _require(
        info.st_uid in {os.getuid(), 0} and (not writable_by_others or bool(info.st_mode & stat.S_ISVTX)),
        "output parent directory is writable by other users",
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_repository(name: Any) -> bool:
    return isinstance(name, str) and name.casefold() == REPOSITORY.casefold()


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _timestamp(value: Any) -> datetime:
    _require(isinstance(value, str), "missing API timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "API timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def github_json(path: str) -> dict[str, Any]:
    endpoint = f"repos/{REPOSITORY}" + (f"/{path}" if path else "")
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com", endpoint],
        capture_output=True,
        check=True,
        timeout=60,
        env=_gh_environment(),
    )
    value = json.loads(result.stdout, object_pairs_hook=_object)
    _require(isinstance(value, dict), "API response is not an object")
    return value


def _pages(api: Api, path: str, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, 101):
        payload = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        count = payload.get("total_count")
        part = payload.get(key)
        _require(type(count) is int and count >= 0, "missing API pagination count")
        if not isinstance(part, list) or len(part) > 100:
            raise ValueError("malformed API page")
        _require(all(isinstance(row, dict) and _positive(row.get("id")) for row in part), "malformed API row")
        if total is None:
            total = count
        _require(total == count, "API population changed during pagination")
        rows.extend(part)
        if len(part) < 100:
            _require(len(rows) == total, "incomplete API pagination")
            _require(len({row["id"] for row in rows}) == len(rows), "duplicate API rows")
            return rows
    raise ValueError("API pagination limit exceeded")


def _run_identity(run: dict[str, Any], sha: str, repository_id: int) -> None:
    _require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "invalid candidate SHA")
    _require(_positive(repository_id), "invalid repository ID")
    _require(run.get("head_sha") == sha and run.get("event") == PRODUCER_EVENT, "wrong producer SHA or event")
    _require(run.get("head_branch") == PRODUCER_BRANCH, "wrong producer branch")
    _require(run.get("path") == WORKFLOW, "wrong producer workflow")
    for field in ("repository", "head_repository"):
        repository = run.get(field)
        _require(isinstance(repository, dict), "missing producer repository")
        _require(
            repository.get("id") == repository_id and _is_repository(repository.get("full_name")),
            "wrong producer repository",
        )
    _require(_positive(run.get("id")) and _positive(run.get("run_attempt")), "invalid producer run identity")


def select_producer(sha: str, api: Api = github_json) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    repository = api("")
    _require(_is_repository(repository.get("full_name")), "wrong repository")
    runs = _pages(
        api,
        f"actions/workflows/trunk.yml/runs?head_sha={sha}&event={PRODUCER_EVENT}&branch={PRODUCER_BRANCH}",
        "workflow_runs",
    )
    _require(bool(runs), "no trunk producer on develop")
    for run in runs:
        _run_identity(run, sha, repository.get("id"))
        _timestamp(run.get("created_at"))
        _timestamp(run.get("run_started_at") or run["created_at"])
    latest = max(runs, key=lambda run: (_timestamp(run["created_at"]), run["id"]))
    for other in runs:
        if other["id"] == latest["id"]:
            continue
        _require(other.get("status") == "completed", "another producer run for this SHA is still pending")
        _require(
            _timestamp(other.get("run_started_at") or other["created_at"])
            <= _timestamp(latest.get("run_started_at") or latest["created_at"]),
            "an older producer run has a newer attempt",
        )
    run = api(f"actions/runs/{latest['id']}")
    _run_identity(run, sha, repository["id"])
    _require(run["id"] == latest["id"] and run["run_attempt"] == latest["run_attempt"], "producer attempt changed")
    _require(
        run.get("status") == "completed" and run.get("conclusion") == "success", "latest producer is not successful"
    )
    attempt = run["run_attempt"]
    jobs = _pages(api, f"actions/runs/{run['id']}/attempts/{attempt}/jobs", "jobs")
    producers = [job for job in jobs if job.get("name") == JOB_NAME]
    _require(len(producers) == 1, "missing or ambiguous distribution producer job")
    job = producers[0]
    _require(
        job.get("run_id") == run["id"] and job.get("run_attempt") == attempt and job.get("head_sha") == sha,
        "wrong producer job identity",
    )
    _require(job.get("status") == "completed" and job.get("conclusion") == "success", "producer job is not successful")
    name = f"dist-{sha}-attempt-{attempt}"
    artifacts = _pages(api, f"actions/runs/{run['id']}/artifacts", "artifacts")
    matches = [artifact for artifact in artifacts if artifact.get("name") == name]
    _require(len(matches) == 1, "missing or ambiguous attempt-bound artifact")
    artifact = api(f"actions/artifacts/{matches[0]['id']}")
    _require(artifact == matches[0], "artifact metadata changed")
    _require(
        _positive(artifact.get("size_in_bytes")) and artifact["size_in_bytes"] <= MAX_PAYLOAD_BYTES,
        "invalid artifact size",
    )
    origin = artifact.get("workflow_run", {})
    _require(
        origin.get("id") == run["id"]
        and origin.get("head_sha") == sha
        and origin.get("repository_id") == repository["id"]
        and origin.get("head_repository_id") == repository["id"],
        "wrong artifact producer",
    )
    _require(artifact.get("expired") is False, "expired artifact")
    expiry = _timestamp(artifact.get("expires_at"))
    _require(expiry > datetime.now(timezone.utc), "artifact expiration is not in the future")
    _require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact.get("digest", "")))), "missing artifact digest")
    return run, job, artifact


def _files(directory: Path) -> dict[str, dict[str, Any]]:
    distributions = [path for path in directory.iterdir() if path.name.endswith((".whl", ".tar.gz"))]
    _require(
        sum(path.name.endswith(".whl") for path in distributions) == 1
        and sum(path.name.endswith(".tar.gz") for path in distributions) == 1,
        "expected one wheel and one sdist",
    )
    result = {}
    for path in distributions:
        _require(path.is_file() and not path.is_symlink(), "distribution is not regular")
        _require(path.stat().st_size <= MAX_PAYLOAD_BYTES, "distribution exceeds size limit")
        result[path.name] = {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def _describe_jobs(jobs: list[dict[str, Any]]) -> str:
    return (
        ", ".join(f"id={job.get('id')} run_attempt={job.get('run_attempt')} status={job.get('status')}" for job in jobs)
        or "none"
    )


def running_producer_job(
    run_id: int,
    attempt: int,
    api: Api = github_json,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wait_seconds: float = PRODUCER_WAIT_SECONDS,
    poll_seconds: float = PRODUCER_POLL_SECONDS,
) -> dict[str, Any]:
    started = clock()
    deadline = started + wait_seconds
    first_lagging: str | None = None
    while True:
        jobs = _pages(api, f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
        matches = [job for job in jobs if job.get("name") == JOB_NAME]
        detail = f"{len(matches)} job(s) named {JOB_NAME}: {_describe_jobs(matches)}"
        _require(len(matches) <= 1, f"producer job is not unique: {detail}")
        if matches and matches[0].get("status") == "in_progress":
            if first_lagging is not None:
                print(
                    f"::notice::Producer job was not listed as running at first; waited {clock() - started:.1f}s "
                    f"for run {run_id} attempt {attempt}. First non-running listing: {first_lagging}"
                )
            return matches[0]
        if first_lagging is None:
            first_lagging = f"{detail} (run_attempt={attempt})"
        _require(
            not matches or matches[0].get("status") in {"queued", "waiting", "pending", "requested"},
            f"producer job is not running: {detail}",
        )
        _require(clock() < deadline, f"producer job did not become running within {wait_seconds:g}s: {detail}")
        sleep(poll_seconds)


def producer_receipt(directory: Path, run: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    _run_identity(run, run["head_sha"], run["repository"]["id"])
    _require(job.get("name") == JOB_NAME and _positive(job.get("id")), "invalid producer job")
    _require(
        job.get("run_id") == run["id"]
        and job.get("run_attempt") == run["run_attempt"]
        and job.get("head_sha") == run["head_sha"],
        "wrong producer job identity",
    )
    return {
        "schema": 1,
        "repository": REPOSITORY,
        "repository_id": run["repository"]["id"],
        "head_repository_id": run["head_repository"]["id"],
        "workflow_path": WORKFLOW,
        "head_sha": run["head_sha"],
        "run_id": run["id"],
        "run_attempt": run["run_attempt"],
        "job_id": job["id"],
        "job_name": JOB_NAME,
        "artifact_name": f"dist-{run['head_sha']}-attempt-{run['run_attempt']}",
        "sha256sums_sha256": hashlib.sha256((directory / "SHA256SUMS").read_bytes()).hexdigest(),
        "files": _files(directory),
    }


def verify_archive(archive: Path, artifact: dict[str, Any]) -> dict[str, bytes]:
    _require(archive.stat().st_size <= MAX_PAYLOAD_BYTES, "archive exceeds size limit")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    _require(artifact.get("digest") == f"sha256:{digest}", "artifact ZIP digest mismatch")
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        _require(len(members) == 4, "expected four artifact members; legacy producers are unsupported")
        _require(sum(member.file_size for member in members) <= MAX_PAYLOAD_BYTES, "payload exceeds size limit")
        names: set[str] = set()
        payloads = {}
        for member in members:
            name = member.filename
            _require(
                member.orig_filename == name
                and PurePosixPath(name).name == name
                and name not in {"", ".", ".."}
                and not any(char in name for char in "\\:\0")
                and name.rstrip(" .") == name,
                "unsafe artifact member",
            )
            _require(name.casefold() not in names, "duplicate artifact member")
            names.add(name.casefold())
            _require(
                not member.is_dir()
                and not member.external_attr & 0x10
                and not member.flag_bits & 1
                and stat.S_IFMT(member.external_attr >> 16) in {0, stat.S_IFREG},
                "nonregular artifact member",
            )
            payloads[name] = bundle.read(member)
    distributions = {name for name in payloads if name.endswith((".whl", ".tar.gz"))}
    _require(
        len(distributions) == 2
        and sum(name.endswith(".whl") for name in distributions) == 1
        and set(payloads) == distributions | {"SHA256SUMS", PRODUCER_RECEIPT},
        "unexpected artifact membership",
    )
    sums: dict[str, str] = {}
    for line in payloads["SHA256SUMS"].decode("ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        _require(match is not None, "malformed SHA256SUMS record")
        assert match is not None
        digest, name = match.groups()
        _require(name not in sums, "duplicate SHA256SUMS record")
        sums[name] = digest
    _require(set(sums) == distributions, "SHA256SUMS membership differs")
    _require(
        all(hashlib.sha256(payloads[name]).hexdigest() == sums[name] for name in sums), "distribution hash mismatch"
    )
    return payloads


def verify_producer_receipt(directory: Path, run: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    receipt = json.loads((directory / PRODUCER_RECEIPT).read_bytes(), object_pairs_hook=_object)
    expected = producer_receipt(directory, run, job)
    _require(
        json.dumps(receipt, sort_keys=True) == json.dumps(expected, sort_keys=True),
        "producer receipt does not bind this attempt and payload",
    )
    return expected


def resolve_tag(root: Path, tag: str, develop_ref: str = "refs/remotes/origin/develop") -> dict[str, str]:
    _require(bool(re.fullmatch(TAG_PATTERN, tag)), "invalid version tag")

    def git(*args: str) -> str:
        return _run_git(root, *args, check=True, text=True, stdout=subprocess.PIPE, timeout=60).stdout.strip()

    ref = f"refs/tags/{tag}"
    _require(git("cat-file", "-t", ref) == "tag", "lightweight tags are unsupported")
    commit = git("rev-parse", f"{ref}^{{commit}}")
    _require(git("rev-parse", "HEAD") == commit, "checkout differs from tagged source")
    _require(not git("status", "--porcelain", "--untracked-files=all"), "tagged source checkout is dirty")
    _run_git(root, "merge-base", "--is-ancestor", commit, develop_ref, check=True, timeout=60)
    spec = importlib.util.spec_from_file_location("_release_version", Path(__file__).with_name("release_flow.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = tomllib.loads(git("show", f"{commit}:pyproject.toml"))["project"]["version"]
    _require(module.normalize_version(tag[1:]) == module.normalize_version(version) is not None, "tag/version mismatch")
    return {"tag": tag, "tag_object": git("rev-parse", ref), "head_sha": commit, "version": version}


def _committed_snapshot(root: Path, commit: str, destination: Path) -> None:
    _require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "invalid snapshot commit")
    entries = _run_git(
        root,
        "ls-tree",
        "-rlz",
        commit,
        "--",
        "benchbox",
        "scripts",
        "pyproject.toml",
        check=True,
        stdout=subprocess.PIPE,
        timeout=60,
    ).stdout
    files = []
    declared_total = 0
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        identity, raw_name = entry.split(b"\t", 1)
        mode, kind, oid, raw_size = identity.split()
        name = raw_name.decode("utf-8")
        path = PurePosixPath(name)
        _require(
            mode in {b"100644", b"100755"}
            and kind == b"blob"
            and not path.is_absolute()
            and all(part not in {".", ".."} for part in path.parts)
            and not any(char in name for char in "\\:\0"),
            "unsafe committed snapshot member",
        )
        declared_size = int(raw_size)
        declared_total += declared_size
        _require(declared_total <= MAX_PAYLOAD_BYTES, "committed snapshot exceeds size limit")
        files.append((name, mode, oid, declared_size))
    _require(len(files) <= 10000, "committed snapshot has too many files")
    with tempfile.TemporaryFile() as batch:
        _run_git(
            root,
            "cat-file",
            "--batch",
            input=b"".join(oid + b"\n" for _, _, oid, _ in files),
            stdout=batch,
            check=True,
            timeout=60,
        )
        batch.seek(0)
        for name, mode, oid, declared_size in files:
            header = batch.readline().split()
            _require(len(header) == 3 and header[:2] == [oid, b"blob"], "snapshot object identity differs")
            size = int(header[2])
            _require(size == declared_size, "snapshot object size differs from its tree entry")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                remaining = size
                while remaining:
                    chunk = batch.read(min(remaining, 1024 * 1024))
                    _require(bool(chunk), "truncated snapshot object")
                    stream.write(chunk)
                    remaining -= len(chunk)
            _require(batch.read(1) == b"\n", "malformed snapshot object boundary")
            target.chmod(0o755 if mode == b"100755" else 0o644)


_VERIFIER_BOOTSTRAP = r"""
import runpy
import sys
import types
from pathlib import Path

snapshot, verifier = Path(sys.argv[1]), sys.argv[2]
for name, relative in (("benchbox", "benchbox"), ("benchbox.utils", "benchbox/utils")):
    stub = types.ModuleType(name)
    stub.__path__ = [str(snapshot / relative)]
    sys.modules[name] = stub
sys.argv = [verifier, *sys.argv[3:]]
runpy.run_path(verifier, run_name="__main__")
"""


def _verify_committed_binaries(root: Path, commit: str, distributions: list[Path]) -> None:
    with tempfile.TemporaryDirectory(prefix="release-source-") as temporary:
        snapshot = Path(temporary)
        _committed_snapshot(root, commit, snapshot)
        verifier = snapshot / "scripts/verify_distribution_binaries.py"
        _require(verifier.is_file(), "tagged source lacks required distribution binary verifier")
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                _VERIFIER_BOOTSTRAP,
                str(snapshot),
                str(verifier),
                *[str(path.resolve()) for path in distributions],
                "--source-root",
                str(snapshot / "benchbox/_binaries"),
            ],
            cwd=snapshot,
            env=_child_environment(),
            check=True,
            timeout=120,
        )


def _publish_output(stage: Path, output: Path) -> None:
    if os.name == "nt":
        os.rename(stage, output)
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        operation = getattr(library, "renamex_np", None)
        _require(operation is not None, "atomic no-replace publication is unsupported")
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(os.fsencode(stage), os.fsencode(output), 0x4)
    elif sys.platform.startswith("linux"):
        operation = getattr(library, "renameat2", None)
        _require(operation is not None, "atomic no-replace publication is unsupported")
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(stage), -100, os.fsencode(output), 1)
    else:
        raise ValueError("atomic no-replace publication is unsupported")
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(output))


def _reap_download(process: subprocess.Popen[bytes], reader: threading.Thread | None, stop: threading.Event) -> None:
    stop.set()
    try:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        finally:
            assert process.stdout is not None
            process.stdout.close()
            if reader is not None and reader.ident is not None:
                reader.join(timeout=0.2)


def _download(artifact_id: int, archive: Path) -> None:
    _require(os.name != "nt", "artifact download needs POSIX process-group ownership; Windows is unsupported")
    spec = importlib.util.spec_from_file_location(
        "_release_clock", Path(__file__).resolve().parents[1] / "benchbox/utils/clock.py"
    )
    assert spec is not None and spec.loader is not None
    clock = importlib.util.module_from_spec(spec)
    previous_clock = sys.modules.get(spec.name)
    sys.modules[spec.name] = clock
    try:
        spec.loader.exec_module(clock)
    finally:
        if previous_clock is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_clock
    started = clock.mono_time()
    chunks: queue.Queue[bytes | Exception] = queue.Queue(maxsize=1)
    stop = threading.Event()
    total = 0
    created = False
    try:
        with archive.open("xb") as stream:
            created = True
            process = subprocess.Popen(
                ["gh", "api", "--hostname", "github.com", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"],
                stdout=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
                env=_gh_environment(),
            )
            assert process.stdout is not None

            def read() -> None:
                assert process.stdout is not None
                while not stop.is_set():
                    try:
                        chunk: bytes | Exception = process.stdout.read(1024 * 1024)
                    except Exception as exc:
                        chunk = exc
                    while not stop.is_set():
                        try:
                            chunks.put(chunk, timeout=0.05)
                            break
                        except queue.Full:
                            continue
                    if not chunk or isinstance(chunk, Exception):
                        return

            reader: threading.Thread | None = None
            try:
                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                while True:
                    remaining = DOWNLOAD_TIMEOUT_SECONDS - clock.elapsed_seconds(started)
                    _require(remaining > 0, "artifact download deadline exceeded")
                    try:
                        chunk = chunks.get(timeout=remaining)
                    except queue.Empty as exc:
                        raise ValueError("artifact download deadline exceeded") from exc
                    if isinstance(chunk, Exception):
                        raise chunk
                    if not chunk:
                        break
                    total += len(chunk)
                    _require(total <= MAX_PAYLOAD_BYTES, "archive exceeds size limit")
                    stream.write(chunk)
                remaining = DOWNLOAD_TIMEOUT_SECONDS - clock.elapsed_seconds(started)
                _require(remaining > 0, "artifact download deadline exceeded")
                _require(process.wait(timeout=remaining) == 0, "artifact download failed")
            finally:
                _reap_download(process, reader, stop)
    except BaseException:
        if created:
            archive.unlink(missing_ok=True)
        raise


def hosted_refspecs(tag: str) -> list[str]:
    _require(bool(re.fullmatch(TAG_PATTERN, tag)), "invalid version tag")
    return ["develop:refs/remotes/origin/develop", f"+refs/tags/{tag}:refs/tags/{tag}"]


def fetch_hosted_refs(source: Path, tag: str) -> None:
    _run_git(
        source,
        "fetch",
        "--no-tags",
        f"https://github.com/{REPOSITORY}.git",
        *hosted_refspecs(tag),
        check=True,
        timeout=600,
    )


def admit(root: Path, tag: str, output: Path, api: Api = github_json) -> dict[str, Any]:
    source = resolve_tag(root, tag)
    run, job, artifact = select_producer(source["head_sha"], api)
    _require(not output.exists(), "output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output = output.parent.resolve(strict=True) / output.name
    _require_private_parent(output.parent)
    with tempfile.TemporaryDirectory(prefix="release-admission-", dir=output.parent) as temporary:
        stage = Path(temporary)
        staged = stage.stat()
        archive = stage / "download.zip"
        _download(artifact["id"], archive)
        payloads = verify_archive(archive, artifact)
        archive.unlink()
        for name, payload in payloads.items():
            (stage / name).write_bytes(payload)
        receipt = verify_producer_receipt(stage, run, job)
        from packaging.utils import parse_sdist_filename, parse_wheel_filename
        from packaging.version import Version

        for name in receipt["files"]:
            package, version = parse_wheel_filename(name)[:2] if name.endswith(".whl") else parse_sdist_filename(name)
            _require(
                package == "benchbox" and version == Version(source["version"]), "distribution package/version mismatch"
            )
        _verify_committed_binaries(root, source["head_sha"], [stage / name for name in receipt["files"]])
        current = select_producer(source["head_sha"], api)
        _require(current == (run, job, artifact), "producer changed during admission")
        _require(resolve_tag(root, tag) == source, "tagged source changed during admission")
        summary = {
            **source,
            "producer": receipt,
            "artifact_id": artifact["id"],
            "artifact_digest": artifact["digest"],
            "expires_at": artifact["expires_at"],
        }
        (stage / "admission-receipt.json").write_text(json.dumps(summary, indent=2) + "\n")
        _require(not output.exists(), "output appeared during admission")
        now = stage.stat()
        _require(
            not stage.is_symlink() and (now.st_dev, now.st_ino) == (staged.st_dev, staged.st_ino),
            "staging directory changed during admission",
        )
        _publish_output(stage, output)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    produce = commands.add_parser("producer")
    produce.add_argument("--dist", type=Path, required=True)
    consume = commands.add_parser("admit")
    consume.add_argument("--source", type=Path, required=True)
    consume.add_argument("--tag", required=True)
    consume.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "producer":
            run_id, attempt = int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
            run = github_json(f"actions/runs/{run_id}")
            _require(
                run["id"] == run_id and run["run_attempt"] == attempt and run["head_sha"] == os.environ["GITHUB_SHA"],
                "workflow context differs from API producer",
            )
            _require(run["repository"]["id"] == int(os.environ["GITHUB_REPOSITORY_ID"]), "repository context differs")
            job = running_producer_job(run_id, attempt)
            receipt = producer_receipt(args.dist, run, job)
            (args.dist / PRODUCER_RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n")
        else:
            fetch_hosted_refs(args.source, args.tag)
            print(json.dumps(admit(args.source.resolve(), args.tag, args.output.absolute()), indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Release artifact admission failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
