from __future__ import annotations

import contextlib
import hashlib
import io
import os
import subprocess
import sys
import urllib.request
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


def test_the_download_sends_an_explicit_user_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> contextlib.AbstractContextManager[io.BytesIO]:
        seen.append(request)
        return contextlib.closing(io.BytesIO(b"binary-bytes"))

    monkeypatch.setattr(omp_pin.urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / "omp"
    omp_pin.download("https://downloads.example.invalid/omp-linux-x64", destination)
    assert destination.read_bytes() == b"binary-bytes"
    assert len(seen) == 1
    user_agent = seen[0].get_header("User-agent") or seen[0].get_header("User-Agent")
    assert user_agent == omp_pin.USER_AGENT
    assert "Python-urllib" not in user_agent


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


@pytest.mark.parametrize("name", ["OMP_URL", "OMP_SHA256", "OMP_VERSION"])
def test_the_install_command_refuses_a_legacy_pin_mismatch_before_download(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pin = omp_pin.pin_from_text(POLICY_PATH.read_text(encoding="utf-8"))
    build = pin.builds["linux-x64"]
    for key, value in zip(("OMP_URL", "OMP_SHA256", "OMP_VERSION"), (build.url, build.sha256, pin.version)):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(name, "different")
    fetch = Fetcher()
    monkeypatch.setattr(omp_pin, "download", fetch)
    assert cli.main(["install-omp", "--policy", str(POLICY_PATH)]) == 1
    assert "legacy workflow omp pin differs" in capsys.readouterr().err
    assert fetch.urls == []


def test_the_matching_legacy_pin_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    pin = omp_pin.pin_from_text(POLICY_PATH.read_text(encoding="utf-8"))
    build = pin.builds["linux-x64"]
    for key, value in zip(("OMP_URL", "OMP_SHA256", "OMP_VERSION"), (build.url, build.sha256, pin.version)):
        monkeypatch.setenv(key, value)
    omp_pin.check_legacy_pin(pin)


@pytest.mark.parametrize("command", ["select", "scan-disk", "attempts", "finalize"])
def test_bare_python_cli_imports_without_pyyaml(command: str) -> None:
    root = POLICY_PATH.parents[1]
    env = {**os.environ, "PYTHONPATH": str(root)}
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
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
