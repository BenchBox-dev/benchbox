from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities.paths import REPO_ROOT
from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


BOOTSTRAP = REPO_ROOT / "scripts" / "cloud_bootstrap.sh"
VERIFY = REPO_ROOT / "scripts" / "cloud_verify.sh"

CLOUD_ENV = {
    "BENCHBOX_EPHEMERAL_CLONE": "1",
    "BENCHBOX_GIT_NAME": "Joe Harris",
    "BENCHBOX_GIT_EMAIL": "joeharris76@gmail.com",
}


def _run(script: Path, *args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    skip_without_posix_shell()
    command = " ".join(shlex.quote(part) for part in ["sh", script.as_posix(), *args])
    return run_posix_shell(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=False)


def _clone(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Seed", "-c", "user.email=seed@example.com"]
        + ["commit", "-q", "--allow-empty", "-m", "seed"],
        check=True,
    )
    return repo


def _env(**overrides: str | None) -> dict[str, str]:
    env = {**os.environ, **CLOUD_ENV}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _local_identity(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get-regexp", r"^user\."],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def test_bootstrap_refuses_without_ephemeral_declaration(tmp_path: Path) -> None:
    repo = _clone(tmp_path)

    result = _run(BOOTSTRAP, cwd=repo, env=_env(BENCHBOX_EPHEMERAL_CLONE=None))

    assert result.returncode == 1
    assert "BENCHBOX_EPHEMERAL_CLONE=1 is not set" in result.stderr
    assert _local_identity(repo) == ""


def test_bootstrap_refuses_a_clone_with_linked_worktrees(tmp_path: Path) -> None:
    repo = _clone(tmp_path)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tmp_path / "linked")], check=True)

    result = _run(BOOTSTRAP, cwd=repo, env=_env())

    assert result.returncode == 1
    assert "2 registered worktrees" in result.stderr
    assert _local_identity(repo) == ""


@pytest.mark.parametrize("missing", ["BENCHBOX_GIT_NAME", "BENCHBOX_GIT_EMAIL"])
def test_bootstrap_refuses_without_a_human_author(tmp_path: Path, missing: str) -> None:
    repo = _clone(tmp_path)

    result = _run(BOOTSTRAP, cwd=repo, env=_env(**{missing: None}))

    assert result.returncode == 1
    assert "BENCHBOX_GIT_NAME and BENCHBOX_GIT_EMAIL" in result.stderr
    assert _local_identity(repo) == ""


def _path_with_node(tmp_path: Path, version: str) -> str:
    bin_dir = tmp_path / "node-bin"
    bin_dir.mkdir()
    for name, body in {"node": f"echo {version}\n", "uv": "exit 0\n"}.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        stub.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


@pytest.mark.parametrize("version", ["v22.22.0", "v22.22.1", "v23.11.0", "v24.14.0", "v25.9.0"])
def test_bootstrap_refuses_a_node_too_old_for_the_markdownlint_hook(tmp_path: Path, version: str) -> None:
    repo = _clone(tmp_path)

    result = _run(BOOTSTRAP, cwd=repo, env=_env(PATH=_path_with_node(tmp_path, version)))

    assert result.returncode == 1
    assert f"node {version[1:]} cannot install the markdownlint hook" in result.stderr
    assert _local_identity(repo) == ""


@pytest.mark.parametrize("version", ["v22.22.2", "v22.23.0", "v24.15.0", "v26.0.0"])
def test_bootstrap_accepts_a_node_the_markdownlint_hook_supports(tmp_path: Path, version: str) -> None:
    repo = _clone(tmp_path)

    result = _run(BOOTSTRAP, cwd=repo, env=_env(PATH=_path_with_node(tmp_path, version)))

    assert "cannot install the markdownlint hook" not in result.stderr


def test_verify_rejects_an_unknown_level(tmp_path: Path) -> None:
    repo = _clone(tmp_path)

    result = _run(VERIFY, "everything", cwd=repo, env=_env())

    assert result.returncode == 2
    assert "LEVEL must be static or tests" in result.stderr


def _clone_with_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = _clone(tmp_path)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "HEAD:refs/heads/develop"], check=True)
    return repo


def _stub_tools(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_log = tmp_path / "make.log"
    stubs = {
        "make": f"printf '%s\\n' \"$*\" >>{shlex.quote(make_log.as_posix())}\n",
        "uv": (
            '[ "$1" = run ] && shift\n'
            '[ "$1" = -- ] && shift\n'
            f'[ "$1" = python ] && shift && exec {shlex.quote(sys.executable)} "$@"\n'
            'exec "$@"\n'
        ),
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        stub.chmod(0o755)
    return bin_dir, make_log


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_verify_passes_the_merge_base_as_a_full_commit_sha(tmp_path: Path) -> None:
    repo = _clone_with_origin(tmp_path)
    bin_dir, make_log = _stub_tools(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(
        repo,
        "-c",
        "user.name=Seed",
        "-c",
        "user.email=seed@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "ahead",
    )
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/develop")
    _git(repo, "reset", "-q", "--hard", base)
    env = _env(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", CLOUD_VERIFY_LOG_DIR=str(tmp_path / "logs"), BASE_REF=None
    )

    result = _run(VERIFY, "static", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    calls = make_log.read_text(encoding="utf-8").splitlines()
    assert f"comment-policy-check BASE_REF={base}" in calls
    assert f"duplicate-check-delta BASE_REF={base}" in calls


def test_verify_refuses_a_base_ref_that_is_not_a_commit(tmp_path: Path) -> None:
    repo = _clone_with_origin(tmp_path)
    bin_dir, make_log = _stub_tools(tmp_path)
    env = _env(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        CLOUD_VERIFY_LOG_DIR=str(tmp_path / "logs"),
        BASE_REF="origin/no-such-branch",
    )

    result = _run(VERIFY, "static", cwd=repo, env=env)

    assert result.returncode == 1
    assert "cannot find a merge base between BASE_REF origin/no-such-branch" in result.stderr
    assert not make_log.exists()


GATEWAY = REPO_ROOT / "scripts" / "cloud_gateway.sh"
POLICY_FILE = ".github/oracle-reviewers.yml"

FAKE_KEY = "tskey-auth-FAKEKEY0000"
FAKE_TOKEN = "fake-cloud-token-0000"
FAKE_URL = "https://gateway-fake.example.invalid:4000"
FAKE_OMP = "#!/bin/sh\necho omp/1.2.3\n"
FAKE_OMP_URL = "https://downloads.example.invalid/omp"

STUB_SITECUSTOMIZE = """
import contextlib
import io
import os
import urllib.request

def _urlopen(url, timeout=None):
    target = url.full_url if hasattr(url, 'full_url') else str(url)
    with open(os.environ["STUB_DIR"] + "/downloads.log", "a") as log:
        log.write(target + "\\n")
    return contextlib.closing(io.BytesIO(os.environ["STUB_OMP_CONTENT"].encode()))

urllib.request.urlopen = _urlopen
"""

STUB_TAILSCALED = """#!{python}
import os, sys, time
sock = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--socket=")][0]
with open(os.environ["STUB_DIR"] + "/tailscaled.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
open(sock, "w").close()
time.sleep(600)
"""

STUB_TAILSCALE = """#!/bin/sh
printf '%s\\n' "$*" >>"$STUB_DIR/tailscale.log"
case " $* " in
  *" status "*)
    if [ -f "$STUB_DIR/joined" ]; then echo '{"BackendState": "Running"}'; else echo '{"BackendState": "NeedsLogin"}'; fi
    ;;
  *" up "*)
    for arg in "$@"; do
      case "$arg" in --auth-key=file:*) keyfile=${arg#--auth-key=file:} ;; esac
    done
    [ "$(cat "$keyfile")" = "$FAKE_KEY" ] && echo key-ok >>"$STUB_DIR/tailscale.log"
    [ "${STUB_JOIN_FAILS:-0}" = 1 ] && exit 1
    touch "$STUB_DIR/joined"
    ;;
esac
"""

STUB_CURL = """#!/bin/sh
printf '%s\\n' "$*" >>"$STUB_DIR/curl.log"
out=""
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift ;;
    -K) cat >/dev/null; shift ;;
    http*) url=$1 ;;
  esac
  shift
done
case "$url" in
  */healthz) printf '%s' "${STUB_HEALTH:-200}" ;;
  */v1/models) printf '%s' "${STUB_MODELS:-200}" ;;
  *) printf '%s' "$STUB_OMP_CONTENT" >"$out" ;;
esac
"""

STUB_UNAME = """#!/bin/sh
case "$1" in -m) echo x86_64 ;; *) echo Linux ;; esac
"""

STUB_MAKE = "#!/bin/sh\nexit 0\n"
STUB_NODE = "#!/bin/sh\necho v24.15.0\n"
STUB_UV = """#!/bin/sh
case " $* " in *" --no-project "*) ;; *) exit 0 ;; esac
while [ $# -gt 0 ] && [ "$1" != -- ]; do shift; done
[ $# -gt 0 ] || exit 0
shift
[ "$1" = python ] && shift
exec {python} "$@"
"""


def _policy_text(url: str = FAKE_OMP_URL, content: str = FAKE_OMP) -> str:
    import hashlib

    import yaml

    from _project.scripts.oracle_reviewers.omp_pin import current_platform

    digest = hashlib.sha256(content.encode()).hexdigest()
    pin = {"version": "1.2.3", "builds": {current_platform(): {"url": url, "sha256": digest}}}
    return yaml.safe_dump({"omp": pin})


def _git_in(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Seed", "-c", "user.email=seed@example.com", *args], check=True
    )


def _gateway_clone(tmp_path: Path, *, develop_policy: str | None = None, branch_policy: str | None = None) -> Path:
    repo = _clone(tmp_path)
    policy = repo / POLICY_FILE
    policy.parent.mkdir(parents=True)
    policy.write_text(_policy_text() if develop_policy is None else develop_policy, encoding="utf-8")
    _git_in(repo, "add", POLICY_FILE)
    _git_in(repo, "commit", "-q", "-m", "policy")
    _git_in(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    if branch_policy is not None:
        policy.write_text(branch_policy, encoding="utf-8")
        _git_in(repo, "commit", "-q", "-am", "branch policy")
    return repo


def _gateway_stubs(tmp_path: Path) -> tuple[str, Path]:
    stub_dir = tmp_path / "stubs"
    bin_dir = stub_dir / "bin"
    bin_dir.mkdir(parents=True)
    bodies = {
        "tailscaled": STUB_TAILSCALED.format(python=sys.executable),
        "tailscale": STUB_TAILSCALE,
        "curl": STUB_CURL,
        "uname": STUB_UNAME,
        "make": STUB_MAKE,
        "node": STUB_NODE,
        "uv": STUB_UV.format(python=sys.executable),
    }
    for name, body in bodies.items():
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    (stub_dir / "site").mkdir()
    (stub_dir / "site" / "sitecustomize.py").write_text(STUB_SITECUSTOMIZE, encoding="utf-8")
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}", stub_dir


def _gateway_env(tmp_path: Path, *, credentials: bool = True, **overrides: str | None) -> dict[str, str]:
    path, stub_dir = _gateway_stubs(tmp_path) if not (tmp_path / "stubs").exists() else (None, tmp_path / "stubs")
    if path is None:
        path = f"{stub_dir / 'bin'}{os.pathsep}{os.environ['PATH']}"
    values: dict[str, str | None] = {
        "PATH": path,
        "STUB_DIR": str(stub_dir),
        "STUB_OMP_CONTENT": FAKE_OMP,
        "FAKE_KEY": FAKE_KEY,
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": f"{stub_dir / 'site'}{os.pathsep}{REPO_ROOT}",
        "BENCHBOX_GATEWAY_PROXY_PORT": "1055",
        "TS_AUTHKEY": FAKE_KEY if credentials else None,
        "ORACLE_GATEWAY_URL": FAKE_URL if credentials else None,
        "ORACLE_CLOUD_GATEWAY_TOKEN": FAKE_TOKEN if credentials else None,
    }
    values.update(overrides)
    return _env(**values)


def _stop(repo: Path, env: dict[str, str]) -> None:
    _run(GATEWAY, "stop", cwd=repo, env=env)


def _stub_lines(tmp_path: Path, name: str) -> list[str]:
    log = tmp_path / "stubs" / name
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _assert_no_secrets(*texts: str) -> None:
    for text in texts:
        for secret in (FAKE_KEY, FAKE_TOKEN, FAKE_URL, "gateway-fake.example.invalid"):
            assert secret not in text


def _installed_omp(tmp_path: Path) -> Path:
    from _project.scripts.oracle_reviewers.omp_pin import current_platform

    return tmp_path / "home" / ".cache" / "benchbox" / "omp" / "1.2.3" / current_platform() / "omp"


def _status(repo: Path) -> str:
    return (repo / ".git" / "benchbox-cloud-gateway.status").read_text(encoding="utf-8")


def test_gateway_without_credentials_reports_unavailable_and_succeeds(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path, credentials=False)

    result = _run(GATEWAY, cwd=repo, env=env)

    assert result.returncode == 0
    assert "gateway unavailable" in result.stdout
    assert "gateway=unavailable" in _status(repo)
    assert _stub_lines(tmp_path, "tailscale.log") == []
    assert not _installed_omp(tmp_path).exists()


def test_gateway_joins_userspace_verifies_and_installs_omp_without_printing_secrets(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path)
    try:
        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "gateway reachable" in result.stdout
        assert "gateway=reachable" in _status(repo)
        assert "gateway_proxy=http://127.0.0.1:1055" in _status(repo)
        daemon_args = _stub_lines(tmp_path, "tailscaled.log")
        assert len(daemon_args) == 1
        assert "--tun=userspace-networking" in daemon_args[0]
        assert "--state=mem:" in daemon_args[0]
        assert "--outbound-http-proxy-listen=127.0.0.1:1055" in daemon_args[0]
        tailscale_log = _stub_lines(tmp_path, "tailscale.log")
        assert any(" up " in f" {line} " for line in tailscale_log)
        assert "key-ok" in tailscale_log
        assert _installed_omp(tmp_path).read_text(encoding="utf-8") == FAKE_OMP
        assert _installed_omp(tmp_path).stat().st_mode & 0o111
        assert _stub_lines(tmp_path, "downloads.log") == [FAKE_OMP_URL]
        assert "omp=installed" in _status(repo)
        _assert_no_secrets(result.stdout, result.stderr)
        assert FAKE_TOKEN not in "\n".join(_stub_lines(tmp_path, "downloads.log"))
        _assert_no_secrets(_status(repo), "\n".join(tailscale_log))
    finally:
        _stop(repo, env)


def test_gateway_records_unreachable_gateway_and_leaves_no_daemon(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path, STUB_MODELS="401")

    result = _run(GATEWAY, cwd=repo, env=env)

    assert result.returncode == 0
    assert "gateway unavailable" in result.stdout
    assert "gateway=unavailable" in _status(repo)
    assert not (repo / ".git" / "benchbox-cloud-gateway").exists()
    _assert_no_secrets(result.stdout, result.stderr, _status(repo))


def test_gateway_failed_join_cleans_up_the_daemon(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path, STUB_JOIN_FAILS="1")

    result = _run(GATEWAY, cwd=repo, env=env)

    assert result.returncode == 1
    assert "gateway=unavailable" in _status(repo)
    assert not (repo / ".git" / "benchbox-cloud-gateway").exists()
    _assert_no_secrets(result.stdout, result.stderr)


def test_gateway_refuses_an_omp_download_with_a_bad_checksum(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path, STUB_OMP_CONTENT="#!/bin/sh\necho omp/1.2.3\n# tampered\n")
    try:
        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 1
        assert "does not match the pinned sha256" in result.stdout
        assert not _installed_omp(tmp_path).exists()
        assert "omp=not-installed" in _status(repo)
    finally:
        _stop(repo, env)


def test_gateway_reads_the_pin_from_origin_develop_not_from_the_branch(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path, branch_policy=_policy_text(url="https://branch.example.invalid/omp"))
    env = _gateway_env(tmp_path)
    try:
        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _stub_lines(tmp_path, "downloads.log") == [FAKE_OMP_URL]
        assert "omp=installed" in _status(repo)
    finally:
        _stop(repo, env)


def test_gateway_does_not_install_omp_when_origin_develop_has_no_policy(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    _git_in(repo, "update-ref", "-d", "refs/remotes/origin/develop")
    env = _gateway_env(tmp_path)
    try:
        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 1
        assert "origin/develop:.github/oracle-reviewers.yml is unreadable" in result.stdout
        assert "omp=not-installed" in _status(repo)
        assert _stub_lines(tmp_path, "downloads.log") == []
    finally:
        _stop(repo, env)


def test_gateway_refuses_a_platform_with_no_pin_and_names_it(tmp_path: Path) -> None:
    import yaml

    data = yaml.safe_load(_policy_text())
    data["omp"]["builds"] = {"plan9-mips": next(iter(data["omp"]["builds"].values()))}
    repo = _gateway_clone(tmp_path, develop_policy=yaml.safe_dump(data))
    env = _gateway_env(tmp_path)
    try:
        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 1
        assert "no omp build is pinned for" in result.stdout
        assert "omp=not-installed" in _status(repo)
        assert _stub_lines(tmp_path, "downloads.log") == []
    finally:
        _stop(repo, env)


def test_bootstrap_does_not_record_omp_installed_when_the_install_fails(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "cloud_gateway.sh").write_text(GATEWAY.read_text(encoding="utf-8"), encoding="utf-8")
    env = _gateway_env(tmp_path, STUB_OMP_CONTENT="#!/bin/sh\necho omp/1.2.3\n# tampered\n")
    try:
        result = _run(BOOTSTRAP, cwd=repo, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "omp=installed" not in result.stdout
        assert "omp=not-installed" in result.stdout
    finally:
        _stop(repo, env)


def test_gateway_ignores_a_stored_pid_that_belongs_to_another_process(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path)
    bystander = subprocess.Popen(["sleep", "600"])
    try:
        state = repo / ".git" / "benchbox-cloud-gateway"
        state.mkdir()
        (state / "tailscaled.pid").write_text(str(bystander.pid), encoding="utf-8")
        (state / "tailscaled.sock").write_text("", encoding="utf-8")

        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert len(_stub_lines(tmp_path, "tailscaled.log")) == 1
        assert bystander.poll() is None
        _stop(repo, env)
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()
        _stop(repo, env)


def test_gateway_second_run_reuses_the_daemon_and_the_install(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path)
    try:
        first = _run(GATEWAY, cwd=repo, env=env)
        downloads = _stub_lines(tmp_path, "downloads.log")
        second = _run(GATEWAY, cwd=repo, env=env)

        assert first.returncode == second.returncode == 0
        assert len(_stub_lines(tmp_path, "tailscaled.log")) == 1
        assert len([line for line in _stub_lines(tmp_path, "tailscale.log") if " up " in f" {line} "]) == 1
        assert downloads == [FAKE_OMP_URL]
        assert _stub_lines(tmp_path, "downloads.log") == downloads
    finally:
        _stop(repo, env)


def test_gateway_socket_path_stays_short_for_a_long_git_dir(tmp_path: Path) -> None:
    deep = tmp_path / ("a" * 60) / ("b" * 60)
    deep.mkdir(parents=True)
    repo = _gateway_clone(deep)
    env = _gateway_env(tmp_path)
    try:
        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "gateway=reachable" in _status(repo)
        daemon_args = _stub_lines(tmp_path, "tailscaled.log")[0].split()
        socket_arg = next(a for a in daemon_args if a.startswith("--socket=")).split("=", 1)[1]
        assert len(socket_arg.encode()) <= 104
        assert len(str(repo / ".git" / "benchbox-cloud-gateway" / "tailscaled.sock")) > 104
        assert Path(socket_arg).exists()
    finally:
        _stop(repo, env)
    assert not Path(socket_arg).exists()


@pytest.mark.parametrize("temp_root", ["unset", "long"])
def test_gateway_socket_root_falls_back_to_tmp_when_the_temp_dir_is_unusable(tmp_path: Path, temp_root: str) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path, TMPDIR=None if temp_root == "unset" else "/tmp/" + "x" * 80)
    try:
        result = _run(GATEWAY, cwd=repo, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "gateway=reachable" in _status(repo)
        daemon_args = _stub_lines(tmp_path, "tailscaled.log")[0].split()
        assert next(a for a in daemon_args if a.startswith("--socket=")).startswith("--socket=/tmp/bbcg-")
    finally:
        _stop(repo, env)


def test_gateway_uses_a_separate_socket_per_checkout(tmp_path: Path) -> None:
    sockets = []
    for name in ("one", "two"):
        workspace = tmp_path / name
        workspace.mkdir()
        repo = _gateway_clone(workspace)
        env = _gateway_env(tmp_path)
        try:
            _run(GATEWAY, cwd=repo, env=env)
            sockets.append(
                next(a for a in _stub_lines(tmp_path, "tailscaled.log")[-1].split() if a.startswith("--socket="))
            )
        finally:
            _stop(repo, env)
    assert sockets[0] != sockets[1]


def test_gateway_stop_ends_the_daemon(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    env = _gateway_env(tmp_path)
    _run(GATEWAY, cwd=repo, env=env)
    pid = int((repo / ".git" / "benchbox-cloud-gateway" / "tailscaled.pid").read_text(encoding="utf-8"))

    _stop(repo, env)

    assert not (repo / ".git" / "benchbox-cloud-gateway").exists()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_bootstrap_without_credentials_still_succeeds_and_reports_the_gateway(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "cloud_gateway.sh").write_text(GATEWAY.read_text(encoding="utf-8"), encoding="utf-8")
    env = _gateway_env(tmp_path, credentials=False)

    result = _run(BOOTSTRAP, cwd=repo, env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "cloud-bootstrap: OK" in result.stdout
    assert "gateway=unavailable" in result.stdout


def test_bootstrap_with_credentials_records_the_gateway_in_the_marker(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "cloud_gateway.sh").write_text(GATEWAY.read_text(encoding="utf-8"), encoding="utf-8")
    env = _gateway_env(tmp_path)
    try:
        result = _run(BOOTSTRAP, cwd=repo, env=env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "gateway=reachable" in result.stdout
        assert "omp=installed" in result.stdout
        _assert_no_secrets(result.stdout, result.stderr)
    finally:
        _stop(repo, env)


def test_bootstrap_survives_a_failed_gateway_setup(tmp_path: Path) -> None:
    repo = _gateway_clone(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "cloud_gateway.sh").write_text(GATEWAY.read_text(encoding="utf-8"), encoding="utf-8")
    env = _gateway_env(tmp_path, STUB_JOIN_FAILS="1")

    result = _run(BOOTSTRAP, cwd=repo, env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "the oracle gateway setup reported a problem" in result.stderr
    assert "gateway=unavailable" in result.stdout
