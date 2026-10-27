from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities.posix_shell import run_posix_shell

from .conftest import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HOST_SCRIPT = REPO_ROOT / "_project" / "scripts" / "oracle_gateway_host.sh"
TOKENS = {"oracle-ci": "i" * 48, "oracle-local": "l" * 48, "oracle-cloud": "c" * 48}
TOOLS = ("omp", "curl", "launchctl", "tailscale", "gh", "uv")
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _entry(token: str) -> dict:
    return {
        "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
        "models": ["anthropic/model-a", "openai-codex/model-b"],
        "accounts": {"anthropic": ["email:a@example.com"], "openai-codex": ["email:o@example.com"]},
    }


@pytest.fixture
def host(tmp_path: Path) -> dict:
    if shutil.which("jq") is None or shutil.which("bash") is None:
        pytest.skip("the gateway host script needs bash and jq")
    gateway_dir = tmp_path / "gateway"
    gateway_dir.mkdir()
    calls = tmp_path / "tool-calls.log"
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for tool in TOOLS:
        stub = stubs / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 1\n', encoding="utf-8")
        stub.chmod(0o755)
    clients = {name: _entry(token) for name, token in TOKENS.items()}
    clients_file = gateway_dir / "clients.json"
    clients_file.write_text(json.dumps(clients, indent=2) + "\n", encoding="utf-8")
    clients_file.chmod(0o600)
    (gateway_dir / "client.token").write_text(TOKENS["oracle-ci"], encoding="utf-8")
    (gateway_dir / "local-client.token").write_text(TOKENS["oracle-local"], encoding="utf-8")
    (gateway_dir / "cloud-client.token").write_text(TOKENS["oracle-cloud"], encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "GATEWAY_DIR": str(gateway_dir),
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "OMP": str(stubs / "omp"),
    }
    return {"dir": gateway_dir, "clients": clients, "calls": calls, "env": env}


def _revoke(host: dict, name: str) -> subprocess.CompletedProcess[str]:
    return run_posix_shell(
        f"{shlex.quote(str(HOST_SCRIPT))} revoke {shlex.quote(name)}",
        env=host["env"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_revoking_one_client_leaves_the_others_byte_identical_and_calls_no_service(host: dict) -> None:
    result = _revoke(host, "oracle-local")
    assert result.returncode == 0, result.stderr
    remaining = {name: entry for name, entry in host["clients"].items() if name != "oracle-local"}
    clients_file = host["dir"] / "clients.json"
    assert clients_file.read_text(encoding="utf-8") == json.dumps(remaining, indent=2) + "\n"
    assert (clients_file.stat().st_mode & 0o777) == 0o600
    assert not (host["dir"] / "local-client.token").exists()
    assert (host["dir"] / "client.token").read_text(encoding="utf-8") == TOKENS["oracle-ci"]
    assert (host["dir"] / "cloud-client.token").read_text(encoding="utf-8") == TOKENS["oracle-cloud"]
    assert not host["calls"].exists()
    assert not [path for path in host["dir"].iterdir() if path.name.startswith(".clients.")]
    assert TOKENS["oracle-local"] not in result.stdout + result.stderr


def test_revoking_a_client_whose_token_file_is_already_gone_still_removes_its_entry(host: dict) -> None:
    (host["dir"] / "cloud-client.token").unlink()
    assert _revoke(host, "oracle-cloud").returncode == 0
    assert list(json.loads((host["dir"] / "clients.json").read_text(encoding="utf-8"))) == ["oracle-ci", "oracle-local"]


def test_revoking_refuses_oracle_ci_and_unknown_names_and_changes_nothing(host: dict) -> None:
    before = (host["dir"] / "clients.json").read_bytes()
    outside = host["dir"].parent / "x-client.token"
    outside.write_text("keep", encoding="utf-8")
    for name in ("oracle-ci", "oracle-missing", "local", "", "oracle-../x", "oracle-local/../../x"):
        assert _revoke(host, name).returncode != 0
    assert (host["dir"] / "clients.json").read_bytes() == before
    assert (host["dir"] / "client.token").exists()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert not host["calls"].exists()


def test_a_failed_revoke_leaves_the_clients_file_and_token_untouched(host: dict) -> None:
    clients_file = host["dir"] / "clients.json"
    clients_file.write_text("not json", encoding="utf-8")
    assert _revoke(host, "oracle-local").returncode != 0
    assert clients_file.read_text(encoding="utf-8") == "not json"
    assert (host["dir"] / "local-client.token").exists()
    assert not [path for path in host["dir"].iterdir() if path.name.startswith(".clients.")]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **GIT_IDENTITY}, check=True, capture_output=True)


def _write_config(root: Path, model: str) -> None:
    config = root / ".github" / "oracle-omp-config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"modelRoles:\n  default: {model}\nretry:\n  fallbackChains: {{}}\n",
        encoding="utf-8",
    )


def _checkout_on_a_feature_branch(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "develop")
    shutil.copytree(
        REPO_ROOT / "_project" / "scripts",
        origin / "_project" / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(REPO_ROOT / ".github", origin / ".github")
    _write_config(origin, "anthropic/from-develop")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "develop")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    _git(checkout, "switch", "-q", "-c", "feature")
    _write_config(checkout, "anthropic/from-feature")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "feature")
    return origin, checkout


def _export_develop(checkout: Path, destination: Path) -> subprocess.CompletedProcess[str]:
    script = checkout / "_project" / "scripts" / "oracle_gateway_host.sh"
    return run_posix_shell(
        f'bash -c \'. "$0"; export_develop "$1"\' {shlex.quote(str(script))} {shlex.quote(str(destination))}',
        env={**os.environ, **GIT_IDENTITY},
        capture_output=True,
        text=True,
        check=False,
    )


def test_gateway_clients_are_generated_from_origin_develop_not_the_checked_out_branch(tmp_path: Path) -> None:
    _, checkout = _checkout_on_a_feature_branch(tmp_path)
    exported = tmp_path / "exported"
    exported.mkdir()
    result = _export_develop(checkout, exported)
    assert result.returncode == 0, result.stderr
    token_file = tmp_path / "client.token"
    token_file.write_text(TOKENS["oracle-ci"], encoding="utf-8")
    out = tmp_path / "clients.json"
    generated = subprocess.run(
        [
            sys.executable,
            "-m",
            "_project.scripts.oracle_reviewers.cli",
            "gateway-clients",
            "--token-file",
            str(token_file),
            "--out",
            str(out),
        ],
        cwd=exported,
        env={**os.environ, "PYTHONPATH": str(exported)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["oracle-ci"]["models"] == ["anthropic/from-develop"]
    assert (checkout / ".github" / "oracle-omp-config.yml").read_text(encoding="utf-8").count("from-feature") == 1


def test_export_follows_a_develop_that_moved_since_the_last_fetch(tmp_path: Path) -> None:
    origin, checkout = _checkout_on_a_feature_branch(tmp_path)
    _write_config(origin, "anthropic/newer-develop")
    _git(origin, "commit", "-q", "-a", "-m", "move develop")
    exported = tmp_path / "exported"
    exported.mkdir()
    assert _export_develop(checkout, exported).returncode == 0
    assert "newer-develop" in (exported / ".github" / "oracle-omp-config.yml").read_text(encoding="utf-8")


def test_export_fails_without_origin_develop(tmp_path: Path) -> None:
    origin, checkout = _checkout_on_a_feature_branch(tmp_path)
    _git(origin, "branch", "-m", "develop", "trunk")
    exported = tmp_path / "exported"
    exported.mkdir()
    result = _export_develop(checkout, exported)
    assert result.returncode != 0
    assert "cannot fetch origin/develop" in result.stderr
    assert list(exported.iterdir()) == []


def _require_valid_token_files(host: dict) -> subprocess.CompletedProcess[str]:
    return run_posix_shell(
        f"bash -c '. \"$0\"; require_valid_token_files' {shlex.quote(str(HOST_SCRIPT))}",
        env=host["env"],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("suffix", ["Ops", "my_ci", "a--b", "-x", "x-"])
def test_a_token_file_with_a_non_matching_name_is_refused_by_name(host: dict, suffix: str) -> None:
    (host["dir"] / f"{suffix}-client.token").write_text("t" * 48, encoding="utf-8")
    result = _require_valid_token_files(host)
    assert result.returncode != 0
    assert f"{suffix}-client.token" in result.stderr
    assert "t" * 48 not in result.stdout + result.stderr


def test_a_token_file_that_derives_a_second_oracle_ci_is_refused(host: dict) -> None:
    (host["dir"] / "ci-client.token").write_text("t" * 48, encoding="utf-8")
    result = _require_valid_token_files(host)
    assert result.returncode != 0
    assert "ci-client.token" in result.stderr and "second oracle-ci" in result.stderr
    assert "t" * 48 not in result.stdout + result.stderr


def test_the_existing_token_file_names_are_accepted(host: dict) -> None:
    assert _require_valid_token_files(host).returncode == 0


@pytest.mark.parametrize("suffix", ["local", "my-ci", "ci2", "Ops", "my_ci", "a--b", "x-", "a.b"])
def test_revoke_and_the_token_file_check_accept_the_same_names(host: dict, suffix: str) -> None:
    name = f"oracle-{suffix}"
    clients_file = host["dir"] / "clients.json"
    clients = json.loads(clients_file.read_text(encoding="utf-8"))
    clients[name] = _entry("z" * 48)
    clients_file.write_text(json.dumps(clients, indent=2) + "\n", encoding="utf-8")
    token_file = host["dir"] / f"{suffix}-client.token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("z" * 48, encoding="utf-8")
    checked = _require_valid_token_files(host).returncode == 0
    revoked = _revoke(host, name).returncode == 0
    assert checked == revoked
    assert revoked == (suffix in {"local", "my-ci", "ci2"})
