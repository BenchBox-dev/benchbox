from __future__ import annotations

import contextlib
import email
import email.message
import hashlib
import http.server
import io
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.response
from pathlib import Path

import pytest
import yaml

from _project.scripts.oracle_reviewers import cli, omp_pin

from .conftest import POLICY_PATH

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCRIPT = "#!/bin/sh\necho omp/1.2.3\n"
URL = "https://downloads.example.invalid/omp-linux-x64"


def _pin(version: str = "1.2.3", target: str = "linux-x64") -> omp_pin.Pin:
    digest = hashlib.sha256(SCRIPT.encode()).hexdigest()
    return omp_pin.Pin(version, {target: omp_pin.Build(URL, digest)})


class Fetcher:
    def __init__(self, content: str = SCRIPT) -> None:
        self.content = content
        self.urls: list[str] = []

    def __call__(self, url: str, destination: Path) -> None:
        self.urls.append(url)
        destination.write_text(self.content, encoding="utf-8")


def test_the_committed_policy_pins_every_published_platform_with_https_urls_and_checksums() -> None:
    pin = omp_pin.pin_from_text(POLICY_PATH.read_text(encoding="utf-8"))
    assert {"linux-x64", "linux-arm64", "darwin-arm64"} <= set(pin.builds)
    for build in pin.builds.values():
        assert build.url.startswith("https://build.stencil.so/d/omp/")
        assert len(build.sha256) == 64


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.pop("omp"),
        lambda data: data["omp"].update(version="latest"),
        lambda data: data["omp"].update(builds={}),
        lambda data: data["omp"]["builds"]["linux-x64"].update(url="http://insecure.example.invalid/omp"),
        lambda data: data["omp"]["builds"]["linux-x64"].update(sha256="ABC"),
        lambda data: data["omp"]["builds"].update({"../evil-x64": data["omp"]["builds"]["linux-x64"]}),
        lambda data: data["omp"]["builds"].update({"linux/x64": data["omp"]["builds"]["linux-x64"]}),
        lambda data: data["omp"]["builds"].update(
            {"linux-x64\nORACLE_OMP_BINARY=/tmp/x": data["omp"]["builds"]["linux-x64"]}
        ),
        lambda data: data["omp"]["builds"].update({"Linux-X64": data["omp"]["builds"]["linux-x64"]}),
        lambda data: data["omp"]["builds"].update({"linux": data["omp"]["builds"]["linux-x64"]}),
        lambda data: data["omp"]["builds"].update({7: data["omp"]["builds"]["linux-x64"]}),
    ],
)
def test_a_malformed_pin_is_rejected(mutation) -> None:
    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    mutation(data)
    with pytest.raises(omp_pin.PinError):
        omp_pin.pin_from_text(yaml.safe_dump(data))


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "darwin-arm64"),
        ("Linux", "x86_64", "linux-x64"),
        ("Linux", "aarch64", "linux-arm64"),
    ],
)
def test_the_platform_key_follows_the_pin_naming(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, expected: str
) -> None:
    monkeypatch.setattr(omp_pin.platform, "system", lambda: system)
    monkeypatch.setattr(omp_pin.platform, "machine", lambda: machine)
    assert omp_pin.current_platform() == expected


def test_install_downloads_verifies_and_places_the_binary_in_the_versioned_cache(tmp_path: Path) -> None:
    fetch = Fetcher()
    binary = omp_pin.install(_pin(), "linux-x64", tmp_path, fetch)
    assert binary == tmp_path / "1.2.3" / "linux-x64" / "omp"
    assert binary.read_text(encoding="utf-8") == SCRIPT
    assert fetch.urls == [URL]
    assert binary.stat().st_mode & 0o111
    assert [path.name for path in binary.parent.iterdir()] == ["omp"]


def test_install_reuses_a_verified_binary_without_downloading(tmp_path: Path) -> None:
    omp_pin.install(_pin(), "linux-x64", tmp_path, Fetcher())
    fetch = Fetcher()
    omp_pin.install(_pin(), "linux-x64", tmp_path, fetch)
    assert fetch.urls == []


def test_install_replaces_a_cached_binary_that_no_longer_matches_the_pin(tmp_path: Path) -> None:
    binary = omp_pin.install(_pin(), "linux-x64", tmp_path, Fetcher())
    binary.write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
    fetch = Fetcher()
    assert omp_pin.install(_pin(), "linux-x64", tmp_path, fetch) == binary
    assert fetch.urls == [URL] and binary.read_text(encoding="utf-8") == SCRIPT


def test_install_refuses_a_checksum_mismatch_before_the_file_is_executable(tmp_path: Path) -> None:
    seen: list[int] = []

    def fetch(url: str, destination: Path) -> None:
        destination.write_text("#!/bin/sh\necho omp/1.2.3\n# swapped\n", encoding="utf-8")
        seen.append(destination.stat().st_mode & 0o111)

    with pytest.raises(omp_pin.PinError, match="does not match the pinned sha256"):
        omp_pin.install(_pin(), "linux-x64", tmp_path, fetch)
    assert seen == [0]
    assert list((tmp_path / "1.2.3" / "linux-x64").iterdir()) == []


def test_install_refuses_a_platform_with_no_pin_and_names_it(tmp_path: Path) -> None:
    fetch = Fetcher()
    with pytest.raises(omp_pin.PinError, match="no omp build is pinned for darwin-x64"):
        omp_pin.install(_pin(), "darwin-x64", tmp_path, fetch)
    assert fetch.urls == [] and list(tmp_path.iterdir()) == []


def test_install_refuses_a_build_that_reports_another_version(tmp_path: Path) -> None:
    with pytest.raises(omp_pin.PinError, match="reports 1.2.3, expected 9.9.9"):
        omp_pin.install(_pin(version="9.9.9"), "linux-x64", tmp_path, Fetcher())
    assert list((tmp_path / "9.9.9" / "linux-x64").iterdir()) == []


def test_install_reports_a_failed_download(tmp_path: Path) -> None:
    def fetch(url: str, destination: Path) -> None:
        raise OSError("offline")

    with pytest.raises(omp_pin.PinError, match="download failed"):
        omp_pin.install(_pin(), "linux-x64", tmp_path, fetch)


class _Server:
    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str]] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.requests.append((self.path, self.headers.get("User-Agent", "")))
                status, headers, body = owner.routes.get(self.path, (404, {}, b""))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch):
    servers: list[_Server] = []

    def start(routes: dict[str, tuple[int, dict[str, str], bytes]]) -> _Server:
        server = _Server(routes)
        servers.append(server)
        monkeypatch.setenv(omp_pin.MIRROR_ENV, server.origin)
        return server

    yield start
    for server in servers:
        server.close()


def test_the_download_sends_an_explicit_user_agent(tmp_path: Path, served) -> None:
    server = served({"/omp-linux-x64": (200, {}, b"binary-bytes")})
    destination = tmp_path / "omp"
    omp_pin.download(URL, destination)
    assert destination.read_bytes() == b"binary-bytes"
    assert [path for path, _ in server.requests] == ["/omp-linux-x64"]
    user_agent = server.requests[0][1]
    assert user_agent == omp_pin.USER_AGENT
    assert "Python-urllib" not in user_agent


def test_a_download_refuses_a_redirect_to_plain_http(tmp_path: Path, served) -> None:
    target = "http://downloads.example.invalid/omp-linux-x64"
    server = served({"/omp-linux-x64": (302, {"Location": target}, b"")})
    with pytest.raises(OSError, match="non-https"):
        omp_pin.download(URL, tmp_path / "omp")
    assert [path for path, _ in server.requests] == ["/omp-linux-x64"]
    assert not (tmp_path / "omp").exists()


def test_a_download_follows_a_redirect_that_stays_on_the_permitted_scheme(tmp_path: Path, served) -> None:
    server = served({})
    server.routes["/omp-linux-x64"] = (302, {"Location": f"{server.origin}/moved"}, b"")
    server.routes["/moved"] = (200, {}, b"binary-bytes")
    omp_pin.download(URL, tmp_path / "omp")
    assert (tmp_path / "omp").read_bytes() == b"binary-bytes"
    assert [path for path, _ in server.requests] == ["/omp-linux-x64", "/moved"]


@pytest.mark.parametrize("target", ["http://127.0.0.1:9/omp", "http://downloads.example.invalid/omp"])
def test_without_a_mirror_a_redirect_must_stay_on_https(monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    monkeypatch.delenv(omp_pin.MIRROR_ENV, raising=False)
    handler = omp_pin.build_opener().handlers
    redirect = next(item for item in handler if isinstance(item, omp_pin.HttpsOnlyRedirectHandler))
    request = urllib.request.Request(URL)
    with pytest.raises(OSError, match="non-https"):
        redirect.redirect_request(request, io.BytesIO(), 302, "Found", email.message.Message(), target)
    followed = redirect.redirect_request(
        request, io.BytesIO(), 302, "Found", email.message.Message(), "https://mirror.example.invalid/omp"
    )
    assert followed is not None
    assert followed.full_url == "https://mirror.example.invalid/omp"


def test_the_mirror_refuses_a_host_that_is_not_loopback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(omp_pin.MIRROR_ENV, "https://downloads.example.invalid")
    with pytest.raises(omp_pin.PinError, match="loopback"):
        omp_pin.download(URL, tmp_path / "omp")


def test_a_corrupted_served_download_fails_the_pinned_checksum(tmp_path: Path, served) -> None:
    served({"/omp-linux-x64": (200, {}, b"#!/bin/sh\necho omp/1.2.3\n# tampered\n")})
    with pytest.raises(omp_pin.PinError, match="does not match the pinned sha256"):
        omp_pin.install(_pin(), "linux-x64", tmp_path, omp_pin.download)


def test_a_served_download_installs_through_the_real_fetch(tmp_path: Path, served) -> None:
    served({"/omp-linux-x64": (200, {}, SCRIPT.encode())})
    binary = omp_pin.install(_pin(), "linux-x64", tmp_path, omp_pin.download)
    assert binary.read_text(encoding="utf-8") == SCRIPT


def test_a_server_trickling_bytes_cannot_outlast_the_deadline(tmp_path: Path) -> None:
    class Trickling(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(10_000_000))
            self.end_headers()
            with contextlib.suppress(OSError):
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.2)

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trickling)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/omp")
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="exceeded"):
            omp_pin._stream(request, tmp_path / "omp", deadline_seconds=1.0, read_seconds=5.0)
        with pytest.raises(omp_pin.PinError, match="download failed"):
            omp_pin.install(
                _pin(),
                "linux-x64",
                tmp_path / "cache",
                lambda url, destination: omp_pin._stream(request, destination, deadline_seconds=1.0, read_seconds=5.0),
            )
    finally:
        server.shutdown()
        server.server_close()
    assert time.monotonic() - started < 4.0


def test_the_cache_root_defaults_under_home_and_honours_the_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(omp_pin.CACHE_ENV, raising=False)
    assert omp_pin.cache_root() == tmp_path / ".cache" / "benchbox" / "omp"
    monkeypatch.setenv(omp_pin.CACHE_ENV, str(tmp_path / "elsewhere"))
    assert omp_pin.cache_root() == tmp_path / "elsewhere"


def test_the_install_command_prints_the_binary_path_for_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pin = _pin(target=omp_pin.current_platform())
    policy = tmp_path / "policy.yml"
    policy.write_text(
        yaml.safe_dump(
            {
                "omp": {
                    "version": pin.version,
                    "builds": {name: {"url": b.url, "sha256": b.sha256} for name, b in pin.builds.items()},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(omp_pin.CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.setattr(omp_pin, "download", Fetcher())
    assert cli.main(["install-omp", "--policy", str(policy)]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == str(tmp_path / "cache" / "1.2.3" / omp_pin.current_platform() / "omp")


def test_the_install_command_fails_with_the_refusal_when_the_platform_is_not_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "policy.yml"
    policy.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(omp_pin, "current_platform", lambda: "plan9-mips")
    monkeypatch.setenv(omp_pin.CACHE_ENV, str(tmp_path / "cache"))
    assert cli.main(["install-omp", "--policy", str(policy)]) == 1
    assert "no omp build is pinned for plan9-mips" in capsys.readouterr().err


def test_required_binary_never_falls_back_to_path_omp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decoy = tmp_path / "omp"
    decoy.write_text("#!/bin/sh\n", encoding="utf-8")
    decoy.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv(omp_pin.BINARY_ENV, raising=False)
    with pytest.raises(omp_pin.PinError, match="pinned omp is not installed"):
        omp_pin.required_binary()
    monkeypatch.setenv(omp_pin.BINARY_ENV, str(tmp_path / "missing"))
    with pytest.raises(omp_pin.PinError):
        omp_pin.required_binary()
    monkeypatch.setenv(omp_pin.BINARY_ENV, sys.executable)
    assert omp_pin.required_binary() == Path(sys.executable)


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_verified_binary_accepts_only_the_pinned_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = _executable(tmp_path / "omp", SCRIPT)
    digest = hashlib.sha256(SCRIPT.encode()).hexdigest()
    monkeypatch.setenv(omp_pin.BINARY_ENV, str(pinned))
    assert omp_pin.verified_binary(digest) == pinned
    _executable(pinned, SCRIPT + "# swapped\n")
    with pytest.raises(omp_pin.PinError, match="does not match the pinned sha256"):
        omp_pin.verified_binary(digest)


@pytest.mark.parametrize("expected", ["", "ABC", "0" * 63])
def test_verified_binary_refuses_when_the_plan_has_no_valid_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected: str
) -> None:
    monkeypatch.setenv(omp_pin.BINARY_ENV, str(_executable(tmp_path / "omp", SCRIPT)))
    with pytest.raises(omp_pin.PinError, match="no pinned omp sha256"):
        omp_pin.verified_binary(expected)


def test_verified_binary_never_falls_back_to_the_path_omp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _executable(tmp_path / "omp", SCRIPT)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv(omp_pin.BINARY_ENV, raising=False)
    with pytest.raises(omp_pin.PinError, match="pinned omp is not installed"):
        omp_pin.verified_binary(hashlib.sha256(SCRIPT.encode()).hexdigest())


@pytest.mark.parametrize("command", ["select", "scan-disk", "attempts", "finalize"])
def test_bare_python_cli_imports_without_pyyaml(command: str) -> None:
    root = POLICY_PATH.parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "MUTANT_UNDER_TEST": ""}
    probe = subprocess.run(
        [sys.executable, "-S", "-c", "import importlib.util; assert importlib.util.find_spec('yaml') is None"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    result = subprocess.run(
        [sys.executable, "-S", "-m", "_project.scripts.oracle_reviewers.cli", command, "--help"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_bare_python_disk_scan_runs_without_pyyaml(tmp_path: Path) -> None:
    root = POLICY_PATH.parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "_project.scripts.oracle_reviewers.cli",
            "scan-disk",
            "--root",
            str(tmp_path),
            "--env",
            "NONEXISTENT_ORACLE_SMOKE_SECRET",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root), "MUTANT_UNDER_TEST": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_a_relative_cache_override_yields_an_absolute_binary_path_that_survives_a_cwd_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(omp_pin.CACHE_ENV, ".cache/oracle-omp")
    pin = _pin(target=omp_pin.current_platform())
    binary = omp_pin.install(pin, fetch=Fetcher())
    scratch = tmp_path / "scratch" / "omp-cwd"
    scratch.mkdir(parents=True)
    monkeypatch.chdir(scratch)
    assert binary.is_absolute()
    assert binary.is_file()
    assert omp_pin.binary_path(pin, omp_pin.current_platform()).is_absolute()
    assert omp_pin.cache_root().is_absolute()


def test_a_relative_binary_override_is_resolved_against_the_launch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(omp_pin.BINARY_ENV, "bin/omp")
    assert omp_pin.configured_binary() == Path(os.getcwd()) / "bin" / "omp"


def test_the_pin_digest_binds_the_version_and_every_platform_url_and_hash() -> None:
    base = omp_pin.Pin(
        "1.2.3",
        {"linux-x64": omp_pin.Build("https://a/x", "a" * 64), "darwin-arm64": omp_pin.Build("https://a/d", "b" * 64)},
    )
    reordered = omp_pin.Pin("1.2.3", dict(reversed(list(base.builds.items()))))
    assert omp_pin.pin_digest(base) == omp_pin.pin_digest(reordered)
    changed = {
        "version": omp_pin.Pin("1.2.4", base.builds),
        "linux hash": omp_pin.Pin("1.2.3", {**base.builds, "linux-x64": omp_pin.Build("https://a/x", "c" * 64)}),
        "linux url": omp_pin.Pin("1.2.3", {**base.builds, "linux-x64": omp_pin.Build("https://evil/x", "a" * 64)}),
        "dropped": omp_pin.Pin("1.2.3", {"darwin-arm64": base.builds["darwin-arm64"]}),
    }
    digests = {omp_pin.pin_digest(pin) for pin in changed.values()} | {omp_pin.pin_digest(base)}
    assert len(digests) == len(changed) + 1
