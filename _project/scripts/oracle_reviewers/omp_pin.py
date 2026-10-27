from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BINARY_ENV = "ORACLE_OMP_BINARY"
CACHE_ENV = "BENCHBOX_OMP_CACHE"
MIRROR_ENV = "BENCHBOX_OMP_DOWNLOAD_MIRROR"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?")
CHUNK_BYTES = 1 << 20
DOWNLOAD_SECONDS = 600
DOWNLOAD_READ_SECONDS = 30
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


def _loopback(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in ("http", "https") and parts.hostname in LOOPBACK_HOSTS


def mirror() -> str | None:
    value = os.environ.get(MIRROR_ENV, "")
    if not value:
        return None
    if not _loopback(value):
        raise PinError(f"{MIRROR_ENV} must name a loopback http(s) origin")
    return value.rstrip("/")


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_loopback: bool = False) -> None:
        super().__init__()
        self.allow_loopback = allow_loopback

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        permitted = newurl.lower().startswith("https://") or (self.allow_loopback and _loopback(newurl))
        if not permitted:
            raise urllib.error.URLError(f"the omp download redirected to a non-https url ({code})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener() -> urllib.request.OpenerDirector:
    if mirror() is None:
        return urllib.request.build_opener(HttpsOnlyRedirectHandler())
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), HttpsOnlyRedirectHandler(allow_loopback=True))


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return build_opener().open(request, timeout=timeout)


def _stream(
    request: urllib.request.Request,
    destination: Path,
    deadline_seconds: float = DOWNLOAD_SECONDS,
    read_seconds: float = DOWNLOAD_READ_SECONDS,
) -> None:
    deadline = time.monotonic() + deadline_seconds
    with _open(request, read_seconds) as response, destination.open("wb") as handle:
        while chunk := response.read1(CHUNK_BYTES):
            handle.write(chunk)
            if time.monotonic() > deadline:
                raise TimeoutError(f"the omp download exceeded {deadline_seconds:g} seconds")


def download(url: str, destination: Path) -> None:
    if not url.startswith("https://"):
        raise PinError("omp is downloaded over https only")
    origin = mirror()
    if origin is not None:
        parts = urllib.parse.urlsplit(url)
        url = origin + urllib.parse.urlunsplit(("", "", parts.path, parts.query, ""))
    _stream(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), destination)


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


def configured_binary() -> Path | None:
    value = os.environ.get(BINARY_ENV, "")
    return Path(value) if value else None


def required_binary() -> Path:
    binary = configured_binary()
    if binary is None or not binary.is_file() or not os.access(binary, os.X_OK):
        raise PinError(f"the pinned omp is not installed; {BINARY_ENV} does not name an executable")
    return binary


def command_install(args: argparse.Namespace) -> int:
    try:
        text = sys.stdin.read() if args.policy == "-" else Path(args.policy).read_text(encoding="utf-8")
        binary = install(pin_from_text(text))
    except (OSError, PinError) as exc:
        print(f"oracle-verdict: error: {exc}", file=sys.stderr)
        return 1
    print(binary)
    return 0
