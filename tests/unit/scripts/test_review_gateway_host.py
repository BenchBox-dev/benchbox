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
import yaml

from tests.utilities.posix_shell import run_posix_shell

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "_project" / "scripts"
HOST_RELATIVE = "_project/scripts/review_gateway_host.sh"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX gateway host script"),
    pytest.mark.skipif(
        not (REPO_ROOT / HOST_RELATIVE).is_file(), reason="_project gateway host script not in this tree"
    ),
]
BOOTSTRAP_RELATIVE = "_project/scripts/theseus_bootstrap.py"
TOKENS = {"review-ci": "i" * 48, "review-local": "l" * 48, "review-cloud": "c" * 48}
PROVIDERS = ("anthropic", "openai-codex", "google-antigravity")
MODELS = ["anthropic/from-develop", "openai-codex/model-b", "google-antigravity/model-c"]
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
WHEEL_PATH = "/_assets/github.com/joeharris76/theseus/releases/download/v0.1.6/theseus_reviewer-0.1.6-py3-none-any.whl"
RETAINED_PATH = WHEEL_PATH.replace(".whl", ".retained.whl")

FAKE_THESEUS = r"""
import hashlib
import json
import os
import re
import sys

NO_LOGIN = "no logged-in broker account"
OWNER = "excluded by owner"


def parse(args):
    options, named, excluded = {}, [], []
    index = 1
    while index < len(args):
        flag = args[index]
        if flag == "--json":
            options[flag] = True
            index += 1
            continue
        value = args[index + 1]
        if flag == "--client":
            named.append(value)
        elif flag == "--exclude-provider":
            excluded.append(value)
        else:
            options[flag] = value
        index += 2
    return options, named, excluded


def config_models(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    found = []
    for match in re.findall(r"([a-z][a-z0-9-]*/[A-Za-z0-9._-]+)", text):
        if match not in found:
            found.append(match)
    return found


def accounts_by_provider(path):
    with open(path, encoding="utf-8") as handle:
        listed = json.load(handle).get("accounts", [])
    result = {}
    for entry in listed:
        result.setdefault(entry["provider"], []).append(entry.get("identityKey", ""))
    return result


def split(models):
    routed = []
    for model in models:
        provider = model.split("/", 1)[0]
        if provider not in routed:
            routed.append(provider)
    return routed


def gateway_clients(args):
    options, named, excluded = parse(args)
    models = config_models(options["--omp-config"])
    routed = split(models)
    present = accounts_by_provider(options["--accounts"]) if "--accounts" in options else {p: ["k"] for p in routed}
    omitted = {}
    for provider in routed:
        if provider in excluded:
            omitted[provider] = OWNER
        elif provider not in present:
            omitted[provider] = NO_LOGIN
    granted = [p for p in routed if p not in omitted]
    report = {
        "granted": granted,
        "excluded": {
            p: {"reason": reason, "models": [m for m in models if m.startswith(p + "/")]}
            for p, reason in omitted.items()
        },
    }
    if "--report" in options:
        with open(options["--report"], "w", encoding="utf-8") as handle:
            json.dump(report, handle)
    if not granted:
        print("no configured provider has a broker login", file=sys.stderr)
        return 1
    for provider, details in report["excluded"].items():
        print(f"excluded: {provider} ({details['reason']}): {', '.join(details['models'])}")
    tokens = {"review-ci": options["--token-file"]}
    for item in named:
        name, path = item.split("=", 1)
        tokens[name] = path
    granted_models = [m for m in models if m.split("/", 1)[0] in granted]
    clients = {}
    for name, path in tokens.items():
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        clients[name] = {
            "tokenSha256": digest,
            "models": granted_models,
            "accounts": {p: present[p] for p in granted},
        }
    with open(options["--out"], "w", encoding="utf-8") as handle:
        json.dump(clients, handle, indent=2)
        handle.write("\n")
    os.chmod(options["--out"], 0o600)
    return 0


def served_models(options, models):
    path = os.environ.get("SERVED_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    if "--clients" in options:
        with open(options["--clients"], encoding="utf-8") as handle:
            return json.load(handle)["review-ci"]["models"]
    return models


def gateway_providers(args):
    options, _, excluded = parse(args)
    models = config_models(options["--omp-config"])
    routed = split(models)
    present = accounts_by_provider(options["--accounts"]) if "--accounts" in options else None
    pinned = None
    if "--clients" in options:
        with open(options["--clients"], encoding="utf-8") as handle:
            pinned = json.load(handle)["review-ci"].get("accounts", {})
    error = os.environ.get("FAKE_SERVED_ERROR") if "--served-url" in options else None
    served = split(served_models(options, models)) if "--served-url" in options and not error else None
    providers = {}
    for provider in routed:
        logged = None if present is None else provider in present
        stale = None
        if present is not None and pinned is not None and provider in pinned and logged:
            stale = sorted(pinned[provider]) != sorted(present[provider])
        owner = provider in excluded
        granted = not owner and logged is not False
        is_served = None if served is None else provider in served
        reason = ""
        if owner:
            reason = OWNER
        elif logged is False:
            reason = NO_LOGIN
        elif stale:
            reason = "stale pin"
        elif granted and is_served is False:
            reason = "granted but not served"
        providers[provider] = {
            "routed": True,
            "logged_in": logged,
            "stale_pin": stale,
            "excluded_by_owner": owner,
            "granted": granted,
            "served": is_served,
            "reason": reason,
        }
    broken = error is not None or not any(p["granted"] for p in providers.values())
    degraded = any(p["reason"] and not p["excluded_by_owner"] for p in providers.values()) or bool(os.environ.get("FAKE_FORCE_DEGRADED"))
    state = "broken" if broken else "degraded" if degraded else "healthy"
    print(json.dumps({"state": state, "served_error": error, "providers": providers, "roles": {}}))
    return {"healthy": 0, "degraded": 3, "broken": 1}[state]


def main():
    args = sys.argv[1:]
    log = os.environ.get("FAKE_THESEUS_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(args) + "\n")
    command = args[0]
    if command == "install-omp":
        with open(args[2], encoding="utf-8") as handle:
            for line in handle.read().splitlines():
                if line.startswith("omp_binary:"):
                    print(line.split(":", 1)[1].strip())
                    return 0
        print("no omp build is pinned for this platform", file=sys.stderr)
        return 1
    if command == "gateway-clients":
        return gateway_clients(args)
    if command == "gateway-providers":
        return gateway_providers(args)
    return 2
"""


def _fake_omp(providers: tuple[str, ...] = PROVIDERS, clients: bool = True, probe_fails: bool = False) -> str:
    accounts = ",".join(
        f'{{"provider":"{provider}","identityKey":"email:{provider}@example.com"}}' for provider in providers
    )
    flag = "--clients=<value>" if clients else "--bind=<value>"
    probe = 'echo "401 from anthropic"; exit 1' if probe_fails else "echo ok"
    return (
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *--version*) echo "omp/1.2.3";;\n'
        f'  *"serve --help"*) echo "{flag}";;\n'
        '  *"auth-broker status"*) echo \'{"ok":true}\';;\n'
        f'  *"usage accounts"*) echo \'{{"accounts":[{accounts}]}}\';;\n'
        f'  *"auth-gateway check"*) {probe};;\n'
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
        elif [ -n "${SERVED_FILE:-}" ] && [ -f "$SERVED_FILE" ]; then
          ids=$(sed 's/.*/{"id":"&"}/' "$SERVED_FILE" | paste -sd, -)
          echo "{\\"data\\":[$ids]}"
        else
          jq -c '{data: [."review-ci".models[] | {id: .}]}' "$GATEWAY_DIR/clients.json"
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


def _config(model: str, extra_chain: str = "") -> str:
    return (
        f"modelRoles:\n  default: {model}:high\nretry:\n  fallbackChains:\n    {model}:\n"
        "      - openai-codex/model-b:medium\n      - google-antigravity/model-c:medium\n"
        f"{extra_chain}"
    )


def _entry(token: str) -> dict:
    return {
        "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
        "models": MODELS,
        "accounts": {provider: [f"email:{provider}@example.com"] for provider in PROVIDERS},
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
    shutil.copy2(SCRIPTS / "review_gateway_host.sh", _prepare(origin, HOST_RELATIVE))
    shutil.copy2(SCRIPTS / "theseus_bootstrap.py", _prepare(origin, BOOTSTRAP_RELATIVE))
    _write(origin, ".github/theseus-lock.json", _lock())
    _write(origin, ".github/review.yml", _policy(good_omp))
    _write(origin, ".github/review-omp-config.yml", _config("anthropic/from-develop"))
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "develop")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    _git(checkout, "switch", "-q", "-c", "feature")
    _write(checkout, ".github/review.yml", _policy(decoy_omp))
    _write(checkout, ".github/review-omp-config.yml", _config("anthropic/from-feature"))
    _write(checkout, ".github/theseus-lock.json", "not a lock")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "feature")
    gateway_dir = tmp_path / "gateway"
    gateway_dir.mkdir()
    clients = {name: _entry(token) for name, token in TOKENS.items()}
    clients_file = gateway_dir / "clients.json"
    clients_file.write_text(json.dumps(clients, indent=2) + "\n", encoding="utf-8")
    clients_file.chmod(0o600)
    (gateway_dir / "client.token").write_text(TOKENS["review-ci"], encoding="utf-8")
    (gateway_dir / "local-client.token").write_text(TOKENS["review-local"], encoding="utf-8")
    (gateway_dir / "cloud-client.token").write_text(TOKENS["review-cloud"], encoding="utf-8")
    home = tmp_path / "home"
    (home / ".omp").mkdir(parents=True)
    (home / ".omp" / "auth-broker.token").write_text("broker-token", encoding="utf-8")
    calls = tmp_path / "tool-calls.log"
    stubs = tmp_path / "stubs"
    for tool in ("gh", "omp"):
        _executable(stubs / tool, f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 1\n')
    _executable(stubs / "curl", CURL_STUB)
    _executable(stubs / "osascript", f'#!/bin/sh\necho "$*" >> "{tmp_path}/notifications.log"\n')
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
        "REVIEW_PROMOTION_MIRROR": mirror["url"].decode(),
        "FAKE_THESEUS_LOG": str(tmp_path / "theseus-calls.log"),
        "SERVED_FILE": str(tmp_path / "served.txt"),
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
        "stubs": stubs,
        "served": tmp_path / "served.txt",
        "notifications": tmp_path / "notifications.log",
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
    result = _revoke(host, "review-local")
    assert result.returncode == 0, result.stderr
    remaining = {name: entry for name, entry in host["clients"].items() if name != "review-local"}
    clients_file = host["dir"] / "clients.json"
    assert clients_file.read_text(encoding="utf-8") == json.dumps(remaining, indent=2) + "\n"
    assert (clients_file.stat().st_mode & 0o777) == 0o600
    assert not (host["dir"] / "local-client.token").exists()
    assert (host["dir"] / "client.token").read_text(encoding="utf-8") == TOKENS["review-ci"]
    assert (host["dir"] / "cloud-client.token").read_text(encoding="utf-8") == TOKENS["review-cloud"]
    assert not host["calls"].exists()
    assert not [path for path in host["dir"].iterdir() if path.name.startswith(".clients.")]
    assert TOKENS["review-local"] not in result.stdout + result.stderr


def test_revoking_a_client_whose_token_file_is_already_gone_still_removes_its_entry(host: dict) -> None:
    (host["dir"] / "cloud-client.token").unlink()
    assert _revoke(host, "review-cloud").returncode == 0
    assert list(json.loads((host["dir"] / "clients.json").read_text(encoding="utf-8"))) == ["review-ci", "review-local"]


def test_revoking_refuses_review_ci_and_unknown_names_and_changes_nothing(host: dict) -> None:
    before = (host["dir"] / "clients.json").read_bytes()
    outside = host["dir"].parent / "x-client.token"
    outside.write_text("keep", encoding="utf-8")
    for name in ("review-ci", "review-missing", "local", "", "review-../x", "review-local/../../x"):
        assert _revoke(host, name).returncode != 0
    assert (host["dir"] / "clients.json").read_bytes() == before
    assert (host["dir"] / "client.token").exists()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert not host["calls"].exists()


def test_a_failed_revoke_leaves_the_clients_file_and_token_untouched(host: dict) -> None:
    clients_file = host["dir"] / "clients.json"
    clients_file.write_text("not json", encoding="utf-8")
    assert _revoke(host, "review-local").returncode != 0
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


def test_a_token_file_that_derives_a_second_review_ci_is_refused(host: dict) -> None:
    (host["dir"] / "ci-client.token").write_text("t" * 48, encoding="utf-8")
    result = _require_valid_token_files(host)
    assert result.returncode != 0
    assert "ci-client.token" in result.stderr and "second review-ci" in result.stderr
    assert "t" * 48 not in result.stdout + result.stderr


def test_the_existing_token_file_names_are_accepted(host: dict) -> None:
    assert _require_valid_token_files(host).returncode == 0


@pytest.mark.parametrize("suffix", ["local", "my-ci", "ci2", "Ops", "my_ci", "a--b", "x-", "a.b"])
def test_revoke_and_the_token_file_check_accept_the_same_names(host: dict, suffix: str) -> None:
    name = f"review-{suffix}"
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
    host["clients"]["review-cloud"]["tokenSha256"] = host["clients"]["review-local"]["tokenSha256"]
    (host["dir"] / "clients.json").write_text(json.dumps(host["clients"]), encoding="utf-8")
    result = _source(host, "check_clients_file")
    assert result.returncode != 0
    assert "review-cloud" in result.stderr
    assert TOKENS["review-cloud"] not in result.stdout + result.stderr


def test_the_clients_check_refuses_a_client_that_is_missing_or_unlisted(host: dict) -> None:
    del host["clients"]["review-cloud"]
    (host["dir"] / "clients.json").write_text(json.dumps(host["clients"]), encoding="utf-8")
    result = _source(host, "check_clients_file")
    assert result.returncode != 0
    assert "token files are for" in result.stderr


def test_the_clients_check_refuses_models_that_differ_from_review_ci(host: dict) -> None:
    host["clients"]["review-local"]["models"] = ["anthropic/model-a"]
    (host["dir"] / "clients.json").write_text(json.dumps(host["clients"]), encoding="utf-8")
    result = _source(host, "check_clients_file")
    assert result.returncode != 0
    assert "review-local" in result.stderr and "models that differ from review-ci's" in result.stderr


def test_the_live_scope_check_passes_when_every_client_sees_the_same_models(host: dict) -> None:
    result = _source(host, 'check_gateway_scope "http://127.0.0.1:4000"')
    assert result.returncode == 0, result.stderr
    assert "review-local models=3/3 usage=403" in result.stdout


def test_the_live_scope_check_refuses_a_client_that_sees_different_models(host: dict) -> None:
    env = {**host["env"], "CURL_ODD_TOKEN": TOKENS["review-cloud"]}
    result = _source(host, 'check_gateway_scope "http://127.0.0.1:4000"', env)
    assert result.returncode != 0
    assert "lists different models for review-cloud than for review-ci" in result.stderr


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
    _develop(host, {".github/review.yml": _policy(other)})
    result = _resolved(host)
    assert result.returncode == 0, result.stderr
    assert f"resolved={other}" in result.stdout.splitlines()


def test_the_host_refuses_when_develop_pins_no_omp_and_does_not_fall_back_to_a_path_omp(host: dict) -> None:
    _develop(host, {".github/review.yml": _policy(None)})
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
    assert "from-develop" in (destination / ".github" / "review-omp-config.yml").read_text(encoding="utf-8")
    assert (destination / ".github" / "theseus-lock.json").read_text(encoding="utf-8") == _lock()
    theseus = next(line for line in result.stdout.splitlines() if line.startswith("theseus=")).removeprefix("theseus=")
    assert Path(theseus).is_file() and Path(theseus).stat().st_mode & 0o111
    assert Path(theseus).is_relative_to(host["tmp"] / "theseus-cache")


def test_export_follows_a_develop_that_moved_since_the_last_fetch(host: dict) -> None:
    _develop(host, {".github/review-omp-config.yml": _config("anthropic/newer-develop")})
    destination = host["tmp"] / "exported"
    destination.mkdir()
    assert _source(host, f"export_develop {shlex.quote(str(destination))}").returncode == 0
    assert "newer-develop" in (destination / ".github" / "review-omp-config.yml").read_text(encoding="utf-8")


def test_gateway_clients_are_generated_from_origin_develop_not_the_checked_out_branch(host: dict) -> None:
    result = _script(host, "gateway")
    assert result.returncode == 0, result.stderr
    assert "gateway: published at https://host.example.ts.net:4000" in result.stdout
    clients = json.loads((host["dir"] / "clients.json").read_text(encoding="utf-8"))
    assert sorted(clients) == ["review-ci", "review-cloud", "review-local"]
    for name, token in TOKENS.items():
        assert clients[name]["models"] == MODELS
        assert clients[name]["tokenSha256"] == hashlib.sha256(token.encode()).hexdigest()
    invocations = [json.loads(line) for line in host["theseus_log"].read_text(encoding="utf-8").splitlines()]
    generated = next(args for args in invocations if args[0] == "gateway-clients")
    config = Path(generated[generated.index("--omp-config") + 1])
    assert not config.is_relative_to(host["checkout"])
    assert config.name == "review-omp-config.yml"
    assert generated[generated.index("--out") + 1] == str(host["dir"] / "clients.json")
    assert (host["checkout"] / ".github" / "review-omp-config.yml").read_text(encoding="utf-8") == _config(
        "anthropic/from-feature"
    )
    run_file = (host["dir"] / "run.sh").read_text(encoding="utf-8")
    assert f'exec "{host["good_omp"]}" auth-gateway serve' in run_file
    assert str(host["decoy_omp"]) not in run_file
    assert "launchctl bootstrap" in host["calls"].read_text(encoding="utf-8")
    assert not [path for path in host["dir"].iterdir() if path.name == ".accounts.json"]


def test_gateway_clients_follow_a_develop_config_that_changed_after_the_checkout(host: dict) -> None:
    _develop(host, {".github/review-omp-config.yml": _config("anthropic/newer-develop")})
    assert _script(host, "gateway").returncode == 0
    clients = json.loads((host["dir"] / "clients.json").read_text(encoding="utf-8"))
    assert clients["review-ci"]["models"][0] == "anthropic/newer-develop"


FAKE_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "secret list") printf 'ORACLE_GATEWAY_TOKEN\\nORACLE_GATEWAY_URL\\n';;
  "issue list") [ -f "$GH_STATE/open" ] && cat "$GH_STATE/open";;
  "issue create")
    [ -z "${GH_FAIL_CREATE:-}" ] || exit 1
    echo 7 > "$GH_STATE/open"
    echo https://github.com/o/r/issues/7;;
  "issue close") rm -f "$GH_STATE/open";;
esac
exit 0
"""


def _fake_gh(host: dict, **extra: str) -> Path:
    state = host["tmp"] / "gh-state"
    state.mkdir(exist_ok=True)
    log = host["tmp"] / "gh.log"
    _executable(host["stubs"] / "gh", FAKE_GH)
    host["env"] = {**host["env"], "GH_LOG": str(log), "GH_STATE": str(state), **extra}
    return log


def _logins(host: dict, providers: tuple[str, ...] | list[str], **options: bool) -> None:
    _executable(host["good_omp"], _fake_omp(tuple(providers), **options))


def _clients(host: dict) -> dict:
    return json.loads((host["dir"] / "clients.json").read_text(encoding="utf-8"))


def _check(host: dict, *args: str) -> subprocess.CompletedProcess[str]:
    _fake_gh(host)
    return _source(host, "check_builds() { :; }; cmd_check " + " ".join(args))


def _monitor(host: dict) -> subprocess.CompletedProcess[str]:
    return _source(host, "check_builds() { :; }; cmd_monitor")


def _deny(host: dict, *providers: str, mode: int = 0o600) -> None:
    path = host["dir"] / "disabled-providers"
    path.write_text("# owner removals\n" + "".join(f"{provider}\n" for provider in providers), encoding="utf-8")
    path.chmod(mode)


@pytest.mark.parametrize("lost", PROVIDERS)
def test_a_provider_without_a_login_is_excluded_and_the_gateway_still_starts(host: dict, lost: str) -> None:
    _logins(host, [provider for provider in PROVIDERS if provider != lost])
    result = _script(host, "gateway")
    assert result.returncode == 0, result.stderr
    assert f"excluded: {lost} (no logged-in broker account)" in result.stdout
    for entry in _clients(host).values():
        assert not [model for model in entry["models"] if model.startswith(f"{lost}/")]
        assert set(entry["accounts"]) == set(PROVIDERS) - {lost}
    calls = host["calls"].read_text(encoding="utf-8")
    assert "launchctl bootstrap" in calls and "tailscale serve --bg" in calls
    checked = _check(host)
    assert checked.returncode == 3, checked.stdout + checked.stderr
    assert f"degraded: {lost}: no logged-in broker account" in checked.stdout


@pytest.mark.parametrize("only", PROVIDERS)
def test_one_logged_in_provider_is_enough_to_serve(host: dict, only: str) -> None:
    _logins(host, [only])
    result = _script(host, "gateway")
    assert result.returncode == 0, result.stderr
    for entry in _clients(host).values():
        assert {model.split("/")[0] for model in entry["models"]} == {only}
    checked = _check(host)
    assert checked.returncode == 3
    for other in set(PROVIDERS) - {only}:
        assert f"degraded: {other}: no logged-in broker account" in checked.stdout


def test_the_gateway_refuses_and_touches_nothing_when_no_provider_has_a_login(host: dict) -> None:
    _logins(host, [])
    before = (host["dir"] / "clients.json").read_bytes()
    result = _script(host, "gateway")
    assert result.returncode != 0
    assert "no configured provider has a broker login" in result.stderr
    assert (host["dir"] / "clients.json").read_bytes() == before
    assert not (host["dir"] / "run.sh").exists()
    assert not host["calls"].exists()
    assert not (host["home"] / "Library").exists()


def test_a_develop_provider_without_a_login_that_the_script_never_heard_of_is_excluded_not_refused(host: dict) -> None:
    extra = "      - unknown-co/model-x:medium\n"
    _develop(host, {".github/review-omp-config.yml": _config("anthropic/from-develop", extra)})
    result = _script(host, "gateway")
    assert result.returncode == 0, result.stderr
    assert "excluded: unknown-co (no logged-in broker account): unknown-co/model-x" in result.stdout
    assert "unknown-co/model-x" not in (host["dir"] / "clients.json").read_text(encoding="utf-8")


def test_the_owner_denylist_excludes_a_logged_in_provider_and_is_written_to_the_gateway_home(host: dict) -> None:
    _deny(host, "openai-codex")
    result = _script(host, "gateway")
    assert result.returncode == 0, result.stderr
    assert "excluded: openai-codex (excluded by owner)" in result.stdout
    for entry in _clients(host).values():
        assert not [model for model in entry["models"] if model.startswith("openai-codex/")]
    config = host["dir"] / "home" / ".omp" / "agent" / "config.yml"
    assert config.read_text(encoding="utf-8") == "disabledProviders:\n  - openai-codex\n"
    assert (config.stat().st_mode & 0o777) == 0o600
    invocations = [json.loads(line) for line in host["theseus_log"].read_text(encoding="utf-8").splitlines()]
    generated = next(args for args in invocations if args[0] == "gateway-clients")
    assert generated[generated.index("--exclude-provider") :][:2] == ["--exclude-provider", "openai-codex"]
    checked = _check(host)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "notice: openai-codex removed by owner; develop still names openai-codex" in checked.stdout


def test_without_a_denylist_the_gateway_home_lists_no_disabled_provider(host: dict) -> None:
    assert _script(host, "gateway").returncode == 0
    config = host["dir"] / "home" / ".omp" / "agent" / "config.yml"
    assert config.read_text(encoding="utf-8") == "disabledProviders: []\n"


@pytest.mark.parametrize(
    ("content", "mode", "message"),
    [("openai-codex\n", 0o644, "chmod 600"), ("Bad Name\n", 0o600, "one lowercase provider id per line")],
)
def test_an_unsafe_or_malformed_denylist_refuses_before_anything_is_written(
    host: dict, content: str, mode: int, message: str
) -> None:
    path = host["dir"] / "disabled-providers"
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    before = (host["dir"] / "clients.json").read_bytes()
    result = _script(host, "gateway")
    assert result.returncode != 0
    assert message in result.stderr
    assert (host["dir"] / "clients.json").read_bytes() == before
    assert not host["calls"].exists()


def test_a_stale_account_pin_is_reported_by_check_and_repinned_by_gateway(host: dict) -> None:
    clients = _clients(host)
    clients["review-ci"]["accounts"]["anthropic"] = ["email:old@example.com"]
    (host["dir"] / "clients.json").write_text(json.dumps(clients), encoding="utf-8")
    checked = _check(host)
    assert checked.returncode == 3, checked.stdout + checked.stderr
    assert "degraded: anthropic: stale pin" in checked.stdout
    assert _script(host, "gateway").returncode == 0
    assert _clients(host)["review-ci"]["accounts"]["anthropic"] == ["email:anthropic@example.com"]
    assert _check(host).returncode == 0


def test_check_passes_when_every_provider_is_granted_and_served(host: dict) -> None:
    checked = _check(host)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "usage probes: ok" in checked.stdout
    assert "secrets: ORACLE_GATEWAY_TOKEN and ORACLE_GATEWAY_URL are set" in checked.stdout


def test_check_names_a_granted_provider_the_gateway_does_not_serve(host: dict) -> None:
    host["served"].write_text("anthropic/from-develop\nopenai-codex/model-b\n", encoding="utf-8")
    checked = _check(host)
    assert checked.returncode == 3, checked.stdout + checked.stderr
    assert "degraded: google-antigravity: granted but not served" in checked.stdout
    assert "lists 2 of the 3 granted models" in checked.stdout


def test_check_fails_when_the_gateway_serves_a_model_the_clients_file_does_not_grant(host: dict) -> None:
    host["served"].write_text("\n".join([*MODELS, "x/unlisted"]) + "\n", encoding="utf-8")
    checked = _check(host)
    assert checked.returncode == 1
    assert "models the clients file does not grant: x/unlisted" in checked.stderr


def test_check_is_broken_when_the_served_set_cannot_be_read(host: dict) -> None:
    _fake_gh(host)
    env = {**host["env"], "FAKE_SERVED_ERROR": "gateway unreachable"}
    checked = _source(host, "check_builds() { :; }; cmd_check", env)
    assert checked.returncode == 1
    assert "gateway unreachable" in checked.stderr


def test_check_reports_a_failed_usage_probe_as_degraded(host: dict) -> None:
    _logins(host, PROVIDERS, probe_fails=True)
    checked = _check(host)
    assert checked.returncode == 3, checked.stdout + checked.stderr
    assert "degraded: usage probe: omp auth-gateway check failed" in checked.stdout
    assert "401 from anthropic" not in checked.stdout


def test_a_degraded_exit_with_no_provider_reason_is_still_degraded(host: dict) -> None:
    _fake_gh(host)
    env = {**host["env"], "FAKE_FORCE_DEGRADED": "1"}
    checked = _source(host, "check_builds() { :; }; cmd_check", env)
    assert checked.returncode == 3, checked.stdout + checked.stderr
    assert "degraded: gateway-providers reports a degraded state with no provider reason" in checked.stdout


def test_check_json_gives_the_state_and_each_message(host: dict) -> None:
    _logins(host, PROVIDERS[:-1])
    checked = _check(host, "--json")
    assert checked.returncode == 3, checked.stdout + checked.stderr
    report = json.loads(checked.stdout)
    assert report["state"] == "degraded"
    assert f"degraded: {PROVIDERS[-1]}: no logged-in broker account" in report["degraded"]
    _logins(host, PROVIDERS)
    healthy = json.loads(_check(host, "--json").stdout)
    assert healthy == {"state": "healthy", "degraded": [], "notices": []}


def test_the_broker_command_advises_a_login_for_each_missing_provider(host: dict) -> None:
    _logins(host, PROVIDERS[:-1])
    result = _script(host, "broker")
    assert result.returncode == 0, result.stderr
    assert f"auth-broker login {PROVIDERS[-1]}" in result.stdout
    assert f"auth-broker login {PROVIDERS[0]}" not in result.stdout


def test_the_monitor_alerts_once_per_change_and_closes_the_issue_on_recovery(host: dict) -> None:
    log = _fake_gh(host)
    first = _monitor(host)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "unchanged" in first.stdout
    assert not log.exists() and not host["notifications"].exists()

    _logins(host, PROVIDERS[1:])
    degraded = _monitor(host)
    assert degraded.returncode == 0, degraded.stdout + degraded.stderr
    gh_calls = log.read_text(encoding="utf-8")
    assert "label create review-gateway-health" in gh_calls
    assert "issue create" in gh_calls and "--title Review gateway degraded" in gh_calls
    assert len(host["notifications"].read_text(encoding="utf-8").splitlines()) == 1

    before = log.read_text(encoding="utf-8")
    assert "unchanged" in _monitor(host).stdout
    assert log.read_text(encoding="utf-8") == before
    assert len(host["notifications"].read_text(encoding="utf-8").splitlines()) == 1

    _logins(host, PROVIDERS[2:])
    assert _monitor(host).returncode == 0
    assert "issue edit 7" in log.read_text(encoding="utf-8")
    assert len(host["notifications"].read_text(encoding="utf-8").splitlines()) == 2

    _logins(host, PROVIDERS)
    recovered = _monitor(host)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "issue close 7" in log.read_text(encoding="utf-8")
    notifications = host["notifications"].read_text(encoding="utf-8").splitlines()
    assert len(notifications) == 3 and "recovered" in notifications[-1]
    assert (host["dir"] / "monitor.state").read_text(encoding="utf-8") == "healthy\n"


def test_a_monitor_run_whose_issue_cannot_be_filed_records_nothing_so_the_next_run_retries(host: dict) -> None:
    _fake_gh(host, GH_FAIL_CREATE="1")
    _logins(host, PROVIDERS[1:])
    result = _monitor(host)
    assert result.returncode != 0
    assert not (host["dir"] / "monitor.state").exists()
    assert len(host["notifications"].read_text(encoding="utf-8").splitlines()) == 1


def test_the_issue_body_carries_no_gateway_url_or_home_path(host: dict) -> None:
    log = _fake_gh(host)
    host["served"].write_text("anthropic/from-develop\nopenai-codex/model-b\n", encoding="utf-8")
    assert _monitor(host).returncode == 0
    gh_calls = log.read_text(encoding="utf-8")
    assert "issue create" in gh_calls and "lists 2 of the 3 granted models" in gh_calls
    assert "ts.net" not in gh_calls and "127.0.0.1" not in gh_calls and str(host["home"]) not in gh_calls


def test_check_refuses_an_unsafe_denylist_instead_of_skipping_it(host: dict) -> None:
    _deny(host, "openai-codex", mode=0o644)
    checked = _check(host)
    assert checked.returncode == 1
    assert "chmod 600" in checked.stderr


def test_a_deliberate_removal_is_a_notice_and_does_not_alert(host: dict) -> None:
    log = _fake_gh(host)
    _deny(host, "openai-codex")
    result = _monitor(host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unchanged" in result.stdout
    assert not log.exists() and not host["notifications"].exists()


def test_monitor_install_renders_a_fifteen_minute_agent_that_runs_the_monitor(host: dict) -> None:
    result = _script(host, "monitor-install")
    assert result.returncode == 0, result.stderr
    plist = (host["home"] / "Library" / "LaunchAgents" / "dev.omp.review-gateway-monitor.plist").read_text(
        encoding="utf-8"
    )
    assert "<key>StartInterval</key>\n  <integer>900</integer>" in plist
    assert "<key>RunAtLoad</key>" in plist and "<key>KeepAlive</key>" not in plist
    snapshot = host["dir"] / "monitor-host.sh"
    assert (
        "<string>/usr/bin/env</string>\n"
        f"    <string>GATEWAY_HOST_REPO={host['checkout']}</string>\n"
        f"    <string>/bin/bash</string>\n    <string>{snapshot}</string>\n    <string>monitor</string>"
    ) in plist
    assert str(host["script"]) not in plist
    assert snapshot.read_bytes() == (host["origin"] / HOST_RELATIVE).read_bytes()
    assert (snapshot.stat().st_mode & 0o777) == 0o700
    assert "launchctl bootstrap" in host["calls"].read_text(encoding="utf-8")


def test_the_host_script_names_no_provider_and_holds_no_provider_list() -> None:
    script = (SCRIPTS / "review_gateway_host.sh").read_text(encoding="utf-8")
    config = yaml.safe_load((REPO_ROOT / ".github" / "review-omp-config.yml").read_text(encoding="utf-8"))
    targets = [*config["modelRoles"].values(), *config["retry"]["fallbackChains"]]
    targets += [entry for chain in config["retry"]["fallbackChains"].values() for entry in chain]
    for provider in {target.split("/", 1)[0] for target in targets}:
        assert provider not in script
    assert "PROVIDERS" not in script
