from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CACHE_ENV = "BENCHBOX_OMP_CACHE"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?")
CHUNK_BYTES = 1 << 20
DOWNLOAD_SECONDS = 600
USER_AGENT = "benchbox-oracle/1.0"
VERSION_SECONDS = 60
SYSTEMS = {"darwin": "darwin", "linux": "linux"}
MACHINES = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}

Fetch = Callable[[str, Path], None]


class PinError(Exception):
    pass


@dataclass(frozen=True)
class Build:
    url: str
    sha256: str


@dataclass(frozen=True)
class Pin:
    version: str
    builds: Mapping[str, Build]


def parse_pin(data: Any) -> Pin:
    section = data.get("omp") if isinstance(data, Mapping) else None
    if not isinstance(section, Mapping):
        raise PinError("the omp pin (the omp section of the reviewer policy) is missing")
    version = section.get("version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise PinError("the omp pin has no valid version")
    builds_data = section.get("builds")
    if not isinstance(builds_data, Mapping) or not builds_data:
        raise PinError("the omp pin lists no platform builds")
    builds: dict[str, Build] = {}
    for name, entry in builds_data.items():
        url = entry.get("url") if isinstance(entry, Mapping) else None
        digest = entry.get("sha256") if isinstance(entry, Mapping) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise PinError(f"the omp pin for {name} needs an https url")
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            raise PinError(f"the omp pin for {name} needs a lowercase hex sha256")
        builds[str(name)] = Build(url, digest)
    return Pin(version, builds)


def pin_from_text(text: str) -> Pin:
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PinError("the reviewer policy is not valid YAML") from exc
    return parse_pin(data)


def current_platform() -> str:
    system = SYSTEMS.get(platform.system().lower())
    machine = MACHINES.get(platform.machine().lower())
    if system is None or machine is None:
        return f"{platform.system().lower()}-{platform.machine().lower()}"
    return f"{system}-{machine}"


def cache_root() -> Path:
    override = os.environ.get(CACHE_ENV)
    return Path(override) if override else Path.home() / ".cache" / "benchbox" / "omp"


def binary_path(pin: Pin, target: str, root: Path | None = None) -> Path:
    return (root or cache_root()) / pin.version / target / "omp"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    if not url.startswith("https://"):
        raise PinError("omp is downloaded over https only")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_SECONDS) as response, destination.open("wb") as handle:
        while chunk := response.read(CHUNK_BYTES):
            handle.write(chunk)


def reported_version(binary: Path) -> str:
    try:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, check=False, timeout=VERSION_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PinError(f"omp --version failed: {type(exc).__name__}") from exc
    match = VERSION_PATTERN.search(result.stdout + result.stderr)
    if result.returncode != 0 or match is None:
        raise PinError("omp --version gave no version")
    return match.group(0)


def select_build(pin: Pin, target: str) -> Build:
    build = pin.builds.get(target)
    if build is None:
        raise PinError(f"no omp build is pinned for {target}; pinned platforms: {', '.join(sorted(pin.builds))}")
    return build


def verified(binary: Path, build: Build) -> bool:
    return binary.is_file() and os.access(binary, os.X_OK) and sha256_of(binary) == build.sha256


def install(pin: Pin, target: str | None = None, root: Path | None = None, fetch: Fetch | None = None) -> Path:
    target = target or current_platform()
    build = select_build(pin, target)
    binary = binary_path(pin, target, root)
    if verified(binary, build):
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="omp-download-", dir=binary.parent)
    os.close(handle)
    candidate = Path(name)
    try:
        try:
            (fetch or download)(build.url, candidate)
        except (OSError, ValueError) as exc:
            raise PinError(f"the omp download failed: {type(exc).__name__}") from exc
        if sha256_of(candidate) != build.sha256:
            raise PinError(f"the omp download for {target} does not match the pinned sha256; refusing to install")
        candidate.chmod(0o755)
        reported = reported_version(candidate)
        if reported != pin.version:
            raise PinError(f"the pinned omp build reports {reported}, expected {pin.version}")
        os.replace(candidate, binary)
    finally:
        candidate.unlink(missing_ok=True)
    return binary


def check_legacy_pin(pin: Pin) -> None:
    names = ("OMP_URL", "OMP_SHA256", "OMP_VERSION")
    if not any(name in os.environ for name in names):
        return
    build = select_build(pin, "linux-x64")
    expected = (build.url, build.sha256, pin.version)
    if tuple(os.environ.get(name) for name in names) != expected:
        raise PinError("the legacy workflow omp pin differs from the reviewer policy pin")


def command_install(args: argparse.Namespace) -> int:
    try:
        text = sys.stdin.read() if args.policy == "-" else Path(args.policy).read_text(encoding="utf-8")
        pin = pin_from_text(text)
        check_legacy_pin(pin)
        binary = install(pin)
    except (OSError, PinError) as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        return 1
    print(binary)
    return 0
