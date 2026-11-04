from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.utilities.posix_shell import run_posix_shell

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "_project" / "scripts"
HOST_RELATIVE = "_project/scripts/oracle_gateway_host.sh"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX gateway host script"),
    pytest.mark.skipif(
        not (REPO_ROOT / HOST_RELATIVE).is_file(), reason="_project gateway host script not in this tree"
    ),
]
BOOTSTRAP_RELATIVE = "_project/scripts/theseus_bootstrap.py"
TOKENS = {"oracle-ci": "i" * 48, "oracle-local": "l" * 48, "oracle-cloud": "c" * 48}
MODELS = ["anthropic/model-a", "openai-codex/model-b"]
PROVIDERS = ("anthropic", "openai-codex", "muse-code", "google-antigravity")
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
WHEEL_PATH = "/_assets/github.com/joeharris76/theseus/releases/download/v0.1.6/theseus_reviewer-0.1.6-py3-none-any.whl"
RETAINED_PATH = WHEEL_PATH.replace(".whl", ".retained.whl")

FAKE_THESEUS = """\
import hashlib
import json
import os
import sys


def main():
    args = sys.argv[1:]
    log = os.environ.get("FAKE_THESEUS_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(args) + "\\n")
    command, options, named = args[0], {}, []
    index = 1
    while index < len(args):
        if args[index] == "--client":
            named.append(args[index + 1])
        else:
            options[args[index]] = args[index + 1]
        index += 2
    if command == "install-omp":
        with open(options["--policy"], encoding="utf-8") as handle:
            for line in handle.read().splitlines():
                if line.startswith("omp_binary:"):
                    print(line.split(":", 1)[1].strip())
                    return 0
        print("no omp build is pinned for this platform", file=sys.stderr)
        return 1
    if command == "gateway-clients":
        with open(options["--omp-config"], encoding="utf-8") as handle:
            default = next(line.split(":", 1)[1].strip() for line in handle.read().splitlines() if line.startswith("default:"))
        tokens = {"oracle-ci": options["--token-file"]}
        for item in named:
            name, path = item.split("=", 1)
            tokens[name] = path
        clients = {}
        for name, path in tokens.items():
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            clients[name] = {"tokenSha256": digest, "models": [default, "openai-codex/model-b"]}
        with open(options["--out"], "w", encoding="utf-8") as handle:
            json.dump(clients, handle, indent=2)
            handle.write("\\n")
        os.chmod(options["--out"], 0o600)
        return 0
    return 2
"""


def _fake_omp(providers: tuple[str, ...] = PROVIDERS, clients: bool = True) -> str:
    accounts = ",".join(f'{{"provider":"{provider}"}}' for provider in providers)
    flag = "--clients=<value>" if clients else "--bind=<value>"
    return (
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *--version*) echo "omp/1.2.3";;\n'
        f'  *"serve --help"*) echo "{flag}";;\n'
        '  *"auth-broker status"*) echo \'{"ok":true}\';;\n'
        f'  *"usage accounts"*) echo \'{{"accounts":[{accounts}]}}\';;\n'
        '  *) echo "omp/1.2.3";;\n'
        "esac\n"
    )


FAKE_OMP = _fake_omp()

CURL_STUB = """#!/bin/sh
IFS= read -r header || true
token=${header#Authorization: Bearer }
for url; do :; done
case "$*" in
  *http_code*)
    case "$url" in
      */v1/usage) printf 403;;
      *) printf 200;;
    esac;;
  *)
    case "$url" in
      */v1/models)
        if [ -n "${CURL_ODD_TOKEN:-}" ] && [ "$token" = "$CURL_ODD_TOKEN" ]; then
          echo '{"data":[{"id":"x/other"},{"id":"openai-codex/model-b"}]}'
        else
          echo '{"data":[{"id":"anthropic/from-develop"},{"id":"openai-codex/model-b"}]}'
        fi;;
    esac;;
esac
"""


def _executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _wheel() -> bytes:
    dist = "theseus_reviewer-0.1.6.dist-info"
    files = {
        "theseus_reviewer/__init__.py": b"",
        "theseus_reviewer/cli.py": FAKE_THESEUS.encode(),
        f"{dist}/METADATA": b"Metadata-Version: 2.1\nName: theseus-reviewer\nVersion: 0.1.6\n",
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist}/entry_points.txt": b"[console_scripts]\ntheseus = theseus_reviewer.cli:main\n",
    }
    rows = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        rows.append(f"{name},sha256={digest},{len(content)}")
    rows.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(rows) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


WHEEL = _wheel()
WHEEL_SHA256 = hashlib.sha256(WHEEL).hexdigest()


def _lock(sha256: str = WHEEL_SHA256) -> str:
    commit = "a" * 40
    return json.dumps(
        {
            "schema": 1,
            "package": "theseus-reviewer",
            "version": "0.1.6",
            "repository": "joeharris76/theseus",
            "source_commit": commit,
            "source_sha256": "1" * 64,
            "delta_sha256": "2" * 64,
            "artifact": {
                "sha256": sha256,
                "urls": [
                    f"https://github.com{WHEEL_PATH.removeprefix('/_assets/github.com')}",
                    f"https://github.com{RETAINED_PATH.removeprefix('/_assets/github.com')}",
                ],
            },
            "dependencies": [],
            "workflow_sha": commit,
        },
        indent=2,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **GIT_IDENTITY}, check=True, capture_output=True)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _policy(omp: Path | None) -> str:
    return "version: 1\n" + (f"omp_binary: {omp}\n" if omp else "")


def _config(model: str) -> str:
    return f"default: {model}\n"


def _entry(token: str) -> dict:
    return {
        "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
        "models": MODELS,
        "accounts": {"anthropic": ["email:a@example.com"], "openai-codex": ["email:o@example.com"]},
    }


@pytest.fixture
def mirror() -> Iterator[dict[str, bytes]]:
    routes: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = routes.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    routes["url"] = f"http://127.0.0.1:{server.server_address[1]}".encode()
    try:
        yield routes
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def host(tmp_path: Path, mirror: dict[str, bytes]) -> dict:
    if shutil.which("jq") is None or shutil.which("bash") is None or shutil.which("git") is None:
        pytest.skip("the gateway host script needs bash, git and jq")
    mirror[WHEEL_PATH] = WHEEL
    mirror[RETAINED_PATH] = WHEEL
    good_omp = _executable(tmp_path / "bin" / "omp-good", FAKE_OMP)
    decoy_omp = _executable(tmp_path / "bin" / "omp-decoy", FAKE_OMP)
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "develop")
    shutil.copy2(SCRIPTS / "oracle_gateway_host.sh", _prepare(origin, HOST_RELATIVE))
    shutil.copy2(SCRIPTS / "theseus_bootstrap.py", _prepare(origin, BOOTSTRAP_RELATIVE))
    _write(origin, ".github/theseus-lock.json", _lock())
    _write(origin, ".github/oracle-reviewers.yml", _policy(good_omp))
    _write(origin, ".github/oracle-omp-config.yml", _config("anthropic/from-develop"))
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "develop")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    _git(checkout, "switch", "-q", "-c", "feature")
    _write(checkout, ".github/oracle-reviewers.yml", _policy(decoy_omp))
    _write(checkout, ".github/oracle-omp-config.yml", _config("anthropic/from-feature"))
    _write(checkout, ".github/theseus-lock.json", "not a lock")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "feature")
    gateway_dir = tmp_path / "gateway"
    gateway_dir.mkdir()
    clients = {name: _entry(token) for name, token in TOKENS.items()}
    clients_file = gateway_dir / "clients.json"
    clients_file.write_text(json.dumps(clients, indent=2) + "\n", encoding="utf-8")
    clients_file.chmod(0o600)
    (gateway_dir / "client.token").write_text(TOKENS["oracle-ci"], encoding="utf-8")
    (gateway_dir / "local-client.token").write_text(TOKENS["oracle-local"], encoding="utf-8")
    (gateway_dir / "cloud-client.token").write_text(TOKENS["oracle-cloud"], encoding="utf-8")
    home = tmp_path / "home"
    (home / ".omp").mkdir(parents=True)
    (home / ".omp" / "auth-broker.token").write_text("broker-token", encoding="utf-8")
    calls = tmp_path / "tool-calls.log"
    stubs = tmp_path / "stubs"
    for tool in ("gh", "omp"):
        _executable(stubs / tool, f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 1\n')
    _executable(stubs / "curl", CURL_STUB)
    _executable(stubs / "plutil", "#!/bin/sh\nexit 0\n")
    _executable(
        stubs / "launchctl",
        f'#!/bin/sh\necho "launchctl $*" >> "{calls}"\n'
        f'case "$1" in print) [ -e "{tmp_path}/loaded" ] || exit 1; printf "pid = 4242\\n";;'
        f' bootstrap) touch "{tmp_path}/loaded";; esac\n',
    )
    _executable(
        stubs / "tailscale",
        f'#!/bin/sh\ncase "$1" in status) echo \'{{"Self":{{"DNSName":"host.example.ts.net."}}}}\';;'
        f' *) echo "tailscale $*" >> "{calls}";; esac\n',
    )
    env = {
        **os.environ,
        **GIT_IDENTITY,
        "HOME": str(home),
        "GATEWAY_DIR": str(gateway_dir),
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "THESEUS_INSTALL_CACHE": str(tmp_path / "theseus-cache"),
        "THESEUS_OMP_CACHE": str(tmp_path / "omp-cache"),
        "ORACLE_PROMOTION_MIRROR": mirror["url"].decode(),
        "FAKE_THESEUS_LOG": str(tmp_path / "theseus-calls.log"),
    }
    return {
        "dir": gateway_dir,
        "clients": clients,
        "calls": calls,
        "env": env,
        "origin": origin,
        "checkout": checkout,
        "script": checkout / HOST_RELATIVE,
        "good_omp": good_omp,
        "decoy_omp": decoy_omp,
        "mirror": mirror,
        "home": home,
        "theseus_log": tmp_path / "theseus-calls.log",
        "tmp": tmp_path,
    }


def _prepare(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _develop(host: dict, files: dict[str, str] | None = None, remove: tuple[str, ...] = ()) -> None:
    for relative, text in (files or {}).items():
        _write(host["origin"], relative, text)
    for relative in remove:
        (host["origin"] / relative).unlink()
    _git(host["origin"], "add", "-A")
    _git(host["origin"], "commit", "-q", "-m", "move develop")


def _run(host: dict, command: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return run_posix_shell(command, env=env or host["env"], capture_output=True, text=True, check=False)


def _script(host: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(host, " ".join([shlex.quote(str(host["script"])), *map(shlex.quote, args)]))


def _source(host: dict, body: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return _run(host, f"bash -c '. \"$0\"; {body}' {shlex.quote(str(host['script']))}", env)


def _revoke(host: dict, name: str) -> subprocess.CompletedProcess[str]:
    return _script(host, "revoke", name)


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


def _require_valid_token_files(host: dict) -> subprocess.CompletedProcess[str]:
    return _source(host, "require_valid_token_files")


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
    (host["dir"] / f"{suffix}-client.token").write_text("z" * 48, encoding="utf-8")
    checked = _require_valid_token_files(host).returncode == 0
    revoked = _revoke(host, name).returncode == 0
    assert checked == revoked
    assert revoked == (suffix in {"local", "my-ci", "ci2"})


def test_the_clients_check_accepts_each_client_with_its_own_token_hash(host: dict) -> None:
    result = _source(host, "check_clients_file")
    assert result.returncode == 0, result.stderr


def test_the_clients_check_refuses_a_client_whose_recorded_hash_is_not_its_token_files(host: dict) -> None:
    host["clients"]["oracle-cloud"]["tokenSha256"] = host["clients"]["oracle-local"]["tokenSha256"]
    (host["dir"] / "clients.json").write_text(json.dumps(host["clients"]), encoding="utf-8")
    result = _source(host, "check_clients_file")
    assert result.returncode != 0
    assert "oracle-cloud" in result.stderr
    assert TOKENS["oracle-cloud"] not in result.stdout + result.stderr


def test_the_clients_check_refuses_a_client_that_is_missing_or_unlisted(host: dict) -> None:
    del host["clients"]["oracle-cloud"]
    (host["dir"] / "clients.json").write_text(json.dumps(host["clients"]), encoding="utf-8")
    result = _source(host, "check_clients_file")
    assert result.returncode != 0
    assert "token files are for" in result.stderr


def test_the_clients_check_refuses_models_that_differ_from_oracle_ci(host: dict) -> None:
    host["clients"]["oracle-local"]["models"] = ["anthropic/model-a"]
    (host["dir"] / "clients.json").write_text(json.dumps(host["clients"]), encoding="utf-8")
    result = _source(host, "check_clients_file")
    assert result.returncode != 0
    assert "oracle-local" in result.stderr and "models that differ from oracle-ci's" in result.stderr


def test_the_live_scope_check_passes_when_every_client_sees_the_same_models(host: dict) -> None:
    result = _source(host, 'check_gateway_scope "http://127.0.0.1:4000"')
    assert result.returncode == 0, result.stderr
    assert "oracle-local models=2/2 usage=403" in result.stdout


def test_the_live_scope_check_refuses_a_client_that_sees_different_models(host: dict) -> None:
    env = {**host["env"], "CURL_ODD_TOKEN": TOKENS["oracle-cloud"]}
    result = _source(host, 'check_gateway_scope "http://127.0.0.1:4000"', env)
    assert result.returncode != 0
    assert "lists different models for oracle-cloud than for oracle-ci" in result.stderr


def _resolved(host: dict) -> subprocess.CompletedProcess[str]:
    return _source(host, 'require_omp; echo "resolved=$OMP"')


def test_the_host_runs_omp_from_the_develop_pin_never_from_path_or_the_working_tree(host: dict) -> None:
    result = _resolved(host)
    assert result.returncode == 0, result.stderr
    assert f"resolved={host['good_omp']}" in result.stdout.splitlines()
    assert any(line.startswith("omp pin: 1.2.3 sha256=") for line in result.stdout.splitlines())
    assert not host["calls"].exists()
    policy = json.loads(host["theseus_log"].read_text(encoding="utf-8").splitlines()[0])
    assert policy[0] == "install-omp"
    assert not Path(policy[2]).is_relative_to(host["checkout"])


def test_the_pin_follows_a_develop_that_moved_since_the_last_fetch(host: dict) -> None:
    other = _executable(host["tmp"] / "bin" / "omp-newer", FAKE_OMP)
    _develop(host, {".github/oracle-reviewers.yml": _policy(other)})
    result = _resolved(host)
    assert result.returncode == 0, result.stderr
    assert f"resolved={other}" in result.stdout.splitlines()


def test_the_host_refuses_when_develop_pins_no_omp_and_does_not_fall_back_to_a_path_omp(host: dict) -> None:
    _develop(host, {".github/oracle-reviewers.yml": _policy(None)})
    result = _resolved(host)
    assert result.returncode != 0
    assert "resolved=" not in result.stdout
    assert "no omp build is pinned for" in result.stderr
    assert "cannot install the omp build pinned in origin/develop" in result.stderr
    assert not host["calls"].exists()


def test_the_host_refuses_a_pinned_omp_that_is_not_executable(host: dict) -> None:
    host["good_omp"].chmod(0o644)
    result = _resolved(host)
    assert result.returncode != 0
    assert "resolved=" not in result.stdout
    assert "the pinned omp install gave no executable" in result.stderr


def test_the_host_refuses_a_pinned_build_without_the_clients_scope_flag(host: dict) -> None:
    _executable(host["good_omp"], _fake_omp(clients=False))
    result = _resolved(host)
    assert result.returncode != 0
    assert "has no auth-gateway serve --clients" in result.stderr


def _refuse_the_install(host: dict, how: str) -> None:
    if how == "unreachable":
        host["mirror"].pop(WHEEL_PATH)
        host["mirror"].pop(RETAINED_PATH)
    else:
        host["mirror"][WHEEL_PATH] = WHEEL + b"tampered"
        host["mirror"][RETAINED_PATH] = WHEEL + b"tampered"


@pytest.mark.parametrize("how", ["unreachable", "tampered"])
@pytest.mark.parametrize("command", ["broker", "gateway", "check"])
def test_a_refused_theseus_install_stops_the_script_before_any_agent_or_clients_file_is_touched(
    host: dict, command: str, how: str
) -> None:
    _refuse_the_install(host, how)
    clients_file = host["dir"] / "clients.json"
    before = clients_file.read_bytes()
    result = _script(host, command)
    assert result.returncode != 0
    assert "the verified Theseus install of origin/develop was refused" in result.stderr
    assert not host["calls"].exists()
    assert not host["theseus_log"].exists()
    assert not (host["home"] / "Library").exists()
    assert not (host["dir"] / "run.sh").exists()
    assert clients_file.read_bytes() == before


def test_a_develop_without_the_theseus_first_stage_is_refused(host: dict) -> None:
    _develop(host, remove=(BOOTSTRAP_RELATIVE,))
    result = _script(host, "gateway")
    assert result.returncode != 0
    assert "origin/develop has no Theseus first stage" in result.stderr
    assert not host["calls"].exists()
    assert not (host["home"] / "Library").exists()


def test_a_develop_whose_lock_the_first_stage_rejects_is_refused_even_when_the_working_tree_lock_is_good(
    host: dict,
) -> None:
    _develop(host, {".github/theseus-lock.json": "not a lock"})
    _write(host["checkout"], ".github/theseus-lock.json", _lock())
    result = _script(host, "gateway")
    assert result.returncode != 0
    assert "the verified Theseus install of origin/develop was refused" in result.stderr
    assert not host["calls"].exists()


def test_export_fails_without_origin_develop(host: dict) -> None:
    _git(host["origin"], "branch", "-m", "develop", "trunk")
    destination = host["tmp"] / "exported"
    destination.mkdir()
    result = _source(host, f"export_develop {shlex.quote(str(destination))}")
    assert result.returncode != 0
    assert "cannot fetch origin/develop" in result.stderr
    assert list(destination.iterdir()) == []


def test_export_gives_develops_github_tree_not_the_checked_out_branchs(host: dict) -> None:
    destination = host["tmp"] / "exported"
    destination.mkdir()
    result = _source(host, f'export_develop {shlex.quote(str(destination))}; echo "theseus=$THESEUS"')
    assert result.returncode == 0, result.stderr
    assert "from-develop" in (destination / ".github" / "oracle-omp-config.yml").read_text(encoding="utf-8")
    assert (destination / ".github" / "theseus-lock.json").read_text(encoding="utf-8") == _lock()
    theseus = next(line for line in result.stdout.splitlines() if line.startswith("theseus=")).removeprefix("theseus=")
    assert Path(theseus).is_file() and Path(theseus).stat().st_mode & 0o111
    assert Path(theseus).is_relative_to(host["tmp"] / "theseus-cache")


def test_export_follows_a_develop_that_moved_since_the_last_fetch(host: dict) -> None:
    _develop(host, {".github/oracle-omp-config.yml": _config("anthropic/newer-develop")})
    destination = host["tmp"] / "exported"
    destination.mkdir()
    assert _source(host, f"export_develop {shlex.quote(str(destination))}").returncode == 0
    assert "newer-develop" in (destination / ".github" / "oracle-omp-config.yml").read_text(encoding="utf-8")


def test_gateway_clients_are_generated_from_origin_develop_not_the_checked_out_branch(host: dict) -> None:
    result = _script(host, "gateway")
    assert result.returncode == 0, result.stderr
    assert "gateway: published at https://host.example.ts.net:4000" in result.stdout
    clients = json.loads((host["dir"] / "clients.json").read_text(encoding="utf-8"))
    assert sorted(clients) == ["oracle-ci", "oracle-cloud", "oracle-local"]
    for name, token in TOKENS.items():
        assert clients[name]["models"] == ["anthropic/from-develop", "openai-codex/model-b"]
        assert clients[name]["tokenSha256"] == hashlib.sha256(token.encode()).hexdigest()
    invocations = [json.loads(line) for line in host["theseus_log"].read_text(encoding="utf-8").splitlines()]
    generated = next(args for args in invocations if args[0] == "gateway-clients")
    config = Path(generated[generated.index("--omp-config") + 1])
    assert not config.is_relative_to(host["checkout"])
    assert config.name == "oracle-omp-config.yml"
    assert generated[generated.index("--out") + 1] == str(host["dir"] / "clients.json")
    assert (host["checkout"] / ".github" / "oracle-omp-config.yml").read_text(encoding="utf-8") == _config(
        "anthropic/from-feature"
    )
    run_file = (host["dir"] / "run.sh").read_text(encoding="utf-8")
    assert f'exec "{host["good_omp"]}" auth-gateway serve' in run_file
    assert str(host["decoy_omp"]) not in run_file
    assert "launchctl bootstrap" in host["calls"].read_text(encoding="utf-8")
    assert not [path for path in host["dir"].iterdir() if path.name == ".accounts.json"]


def test_gateway_clients_follow_a_develop_config_that_changed_after_the_checkout(host: dict) -> None:
    _develop(host, {".github/oracle-omp-config.yml": _config("anthropic/newer-develop")})
    assert _script(host, "gateway").returncode == 0
    clients = json.loads((host["dir"] / "clients.json").read_text(encoding="utf-8"))
    assert clients["oracle-ci"]["models"][0] == "anthropic/newer-develop"


def test_the_gateway_refuses_before_writing_clients_when_a_broker_login_is_missing(host: dict) -> None:
    _executable(host["good_omp"], _fake_omp(PROVIDERS[:-1]))
    before = (host["dir"] / "clients.json").read_bytes()
    result = _script(host, "gateway")
    assert result.returncode != 0
    assert "no broker login for: google-antigravity" in result.stderr
    assert (host["dir"] / "clients.json").read_bytes() == before
    assert not host["calls"].exists()
