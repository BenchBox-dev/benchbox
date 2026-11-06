#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import email.parser
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import sysconfig
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_PATH = ".github/theseus-lock.json"
CACHE_ENV = "THESEUS_INSTALL_CACHE"
MIRROR_ENV = "ORACLE_PROMOTION_MIRROR"
SCHEMA = 1
KEY_VERSION = 1
MAX_BYTES = 64 << 20
MAX_FILES = 5000
DOWNLOAD_TIMEOUT_SECONDS = 30
USER_AGENT = "theseus-bootstrap/1.0"
LOCK_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "package",
        "version",
        "repository",
        "source_commit",
        "source_sha256",
        "delta_sha256",
        "artifact",
        "dependencies",
        "workflow_sha",
    }
)
LOCK_OPTIONAL_KEYS = frozenset({"matcher", "bootstrap"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
FORBIDDEN_SUFFIXES = (".pth",)
FORBIDDEN_FOR_PACKAGE = (".so", ".pyd", ".dll", ".dylib")


class BootstrapError(Exception):
    pass


Fetch = Callable[[str], bytes]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BootstrapError(message)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Dependency:
    name: str
    version: str
    sha256: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class Lock:
    package: str
    version: str
    repository: str
    source_commit: str
    artifact_sha256: str
    artifact_urls: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    text_sha256: str


def _https_urls(value: Any, label: str, minimum: int) -> tuple[str, ...]:
    _require(isinstance(value, list) and len(value) >= minimum, f"{label} needs at least {minimum} https url(s)")
    urls = []
    for item in value:
        _require(
            isinstance(item, str) and item.startswith("https://") and not item.isspace(), f"{label} needs https urls"
        )
        urls.append(item)
    _require(len(set(urls)) == len(urls), f"{label} urls must be distinct")
    return tuple(urls)


def parse_lock(raw: bytes) -> Lock:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"the theseus lock is not valid JSON: {exc}") from exc
    _require(isinstance(data, dict), "the theseus lock must be a JSON object")
    _require(
        LOCK_REQUIRED_KEYS <= set(data) <= LOCK_REQUIRED_KEYS | LOCK_OPTIONAL_KEYS,
        "the theseus lock has missing or unexpected keys",
    )
    _require(
        data["schema"] == SCHEMA and not isinstance(data["schema"], bool), f"the theseus lock schema must be {SCHEMA}"
    )
    package, version, repository = data["package"], data["version"], data["repository"]
    commit, workflow = data["source_commit"], data["workflow_sha"]
    _require(isinstance(package, str) and _PACKAGE.fullmatch(package) is not None, "invalid theseus package name")
    _require(isinstance(version, str) and _VERSION.fullmatch(version) is not None, "invalid theseus lock version")
    _require(isinstance(repository, str) and _REPO.fullmatch(repository) is not None, "invalid theseus repository slug")
    _require(isinstance(commit, str) and _COMMIT.fullmatch(commit) is not None, "source_commit must be a full commit")
    _require(workflow == commit, "workflow_sha must equal the reviewed source_commit")
    for key in ("source_sha256", "delta_sha256"):
        _require(isinstance(data[key], str) and _SHA256.fullmatch(data[key]) is not None, f"{key} must be a sha256")
    artifact = data["artifact"]
    _require(isinstance(artifact, dict) and set(artifact) == {"sha256", "urls"}, "the lock artifact is invalid")
    artifact_sha = artifact["sha256"]
    _require(isinstance(artifact_sha, str) and _SHA256.fullmatch(artifact_sha) is not None, "invalid artifact sha256")
    artifact_urls = _https_urls(artifact["urls"], "the theseus artifact", 2)
    _require(isinstance(data["dependencies"], list), "the lock dependencies must be a list")
    dependencies = []
    for item in data["dependencies"]:
        _require(isinstance(item, dict) and set(item) == {"name", "version", "sha256", "urls"}, "invalid dependency")
        name, dep_version, dep_sha = item["name"], item["version"], item["sha256"]
        _require(isinstance(name, str) and _PACKAGE.fullmatch(name) is not None, f"invalid dependency name {name!r}")
        _require(
            isinstance(dep_version, str) and _VERSION.fullmatch(dep_version) is not None, f"invalid version for {name}"
        )
        _require(
            isinstance(dep_sha, str) and _SHA256.fullmatch(dep_sha) is not None, f"missing or invalid sha256 for {name}"
        )
        dependencies.append(Dependency(name, dep_version, dep_sha, _https_urls(item["urls"], f"dependency {name}", 1)))
    names = [normalize(dep.name) for dep in dependencies]
    _require(len(set(names)) == len(names), "the lock lists duplicate dependencies")
    return Lock(
        package=package,
        version=version,
        repository=repository,
        source_commit=commit,
        artifact_sha256=artifact_sha,
        artifact_urls=artifact_urls,
        dependencies=tuple(dependencies),
        text_sha256=sha256_hex(raw),
    )


def read_lock(repo: Path, commit: str, lock_path: str = LOCK_PATH) -> Lock:
    _require(_COMMIT.fullmatch(commit) is not None, "the trusted consumer commit must be a full 40-hex sha")
    parts = lock_path.split("/")
    _require(
        bool(lock_path) and not lock_path.startswith("/") and ".." not in parts and "" not in parts,
        "the lock path must be repository-relative",
    )
    result = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{lock_path}"], capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise BootstrapError(f"cannot read {lock_path} at trusted commit {commit}: {detail}")
    return parse_lock(result.stdout)


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def __init__(self, loopback: bool) -> None:
        self.loopback = loopback

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        parts = urllib.parse.urlsplit(newurl)
        local = self.loopback and parts.scheme == "http" and (parts.hostname or "") in ("127.0.0.1", "localhost", "::1")
        if parts.scheme != "https" and not local:
            raise urllib.error.URLError(f"refusing a redirect to {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _loopback_origin(value: str) -> bool:
    parts = urllib.parse.urlsplit(value)
    return parts.scheme in ("http", "https") and (parts.hostname or "") in ("127.0.0.1", "localhost", "::1")


def default_fetch() -> Fetch:
    mirror = os.environ.get(MIRROR_ENV, "").strip()
    _require(not mirror or _loopback_origin(mirror), f"{MIRROR_ENV} must name a loopback http(s) origin")

    def fetch(url: str) -> bytes:
        _require(url.startswith("https://"), "artifacts are downloaded over https only")
        target = url
        if mirror:
            parts = urllib.parse.urlsplit(url)
            target = f"{mirror.rstrip('/')}/_assets/{(parts.hostname or '').lower()}{parts.path}"
            if parts.query:
                target = f"{target}?{parts.query}"
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}) if mirror else urllib.request.ProxyHandler(), _HttpsOnly(bool(mirror))
        )
        request = urllib.request.Request(target, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                data = response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BootstrapError(f"cannot download {url}: {exc}") from exc
        _require(len(data) <= MAX_BYTES, f"{url} exceeds the maximum artifact size")
        return data

    return fetch


def fetch_verified(label: str, urls: Sequence[str], sha256: str, fetch: Fetch, *, verify_all: bool = False) -> bytes:
    chosen: bytes | None = None
    unreachable: list[str] = []
    for url in urls:
        try:
            raw = fetch(url)
        except (BootstrapError, OSError) as exc:
            unreachable.append(f"{url}: {exc}")
            continue
        actual = sha256_hex(raw)
        _require(actual == sha256, f"{label} from {url} has sha256 {actual}, expected {sha256}")
        if chosen is None:
            chosen = raw
        if not verify_all:
            break
    if verify_all and unreachable:
        raise BootstrapError(f"{label} has unreachable byte sources: {'; '.join(unreachable)}")
    if chosen is None:
        raise BootstrapError(f"{label} has no reachable byte source: {'; '.join(unreachable)}")
    return chosen


def _safe_name(name: str) -> bool:
    parts = name.split("/")
    return bool(name) and not name.startswith("/") and "\\" not in name and all(p not in ("", ".", "..") for p in parts)


def read_wheel(raw: bytes, label: str) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                _require(
                    (info.external_attr >> 16) & 0o170000 != 0o120000, f"{label} contains a symlink {info.filename!r}"
                )
                _require(_safe_name(info.filename), f"{label} contains an invalid path {info.filename!r}")
                _require(info.filename not in entries, f"{label} contains a duplicate entry {info.filename!r}")
                _require(len(entries) < MAX_FILES, f"{label} exceeds the file limit")
                with archive.open(info) as handle:
                    data = handle.read(MAX_BYTES - total + 1)
                total += len(data)
                _require(total <= MAX_BYTES, f"{label} exceeds the size limit")
                entries[info.filename] = data
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError) as exc:
        raise BootstrapError(f"{label} is not a valid wheel: {exc}") from exc
    _require(bool(entries), f"{label} is empty")
    return entries


def _dist_info(entries: Mapping[str, bytes], name: str, version: str, label: str) -> str:
    candidates = {key.split("/", 1)[0] for key in entries if "/" in key and key.split("/", 1)[0].endswith(".dist-info")}
    wanted = re.sub(r"[-_.]+", "_", f"{name}-{version}.dist-info").lower()
    _require(
        len(candidates) == 1 and re.sub(r"[-_.]+", "_", next(iter(candidates))).lower() == wanted,
        f"{label} does not carry exactly the {name} {version} dist-info",
    )
    return f"{next(iter(candidates))}/"


def verify_record(entries: Mapping[str, bytes], dist_info: str, label: str) -> None:
    record_path = f"{dist_info}RECORD"
    for required in ("METADATA", "WHEEL", "RECORD"):
        _require(f"{dist_info}{required}" in entries, f"{label} is missing {dist_info}{required}")
    recorded: dict[str, tuple[str, str]] = {}
    try:
        for row in csv.reader(io.StringIO(entries[record_path].decode("utf-8"))):
            if not row:
                continue
            _require(len(row) == 3, f"{label} has a malformed RECORD row")
            _require(row[0] not in recorded, f"{label} RECORD lists {row[0]!r} twice")
            recorded[row[0]] = (row[1], row[2])
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BootstrapError(f"{label} has an invalid RECORD: {exc}") from exc
    _require(set(recorded) == set(entries), f"{label} RECORD does not list exactly the archive entries")
    for name, content in entries.items():
        digest, size = recorded[name]
        if name == record_path:
            _require(digest == "" and size == "", f"{label} RECORD must not hash itself")
            continue
        expected = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode("ascii").rstrip("=")
        _require(digest == f"sha256={expected}", f"{label} RECORD hash mismatch for {name!r}")
        _require(size == str(len(content)), f"{label} RECORD size mismatch for {name!r}")


def _headers(raw: bytes) -> Any:
    return email.parser.BytesParser().parsebytes(raw, headersonly=True)


@dataclass(frozen=True)
class WheelInfo:
    name: str
    version: str
    requires_python: str
    requires: tuple[str, ...]
    tags: tuple[tuple[str, str, str], ...]
    console_scripts: tuple[tuple[str, str, str], ...]
    dist_info: str


def inspect_wheel(entries: Mapping[str, bytes], name: str, version: str, label: str, *, package: bool) -> WheelInfo:
    dist_info = _dist_info(entries, name, version, label)
    verify_record(entries, dist_info, label)
    for entry in entries:
        lowered = entry.lower()
        _require(not lowered.endswith(FORBIDDEN_SUFFIXES), f"{label} contains a forbidden hook {entry!r}")
        _require(
            not any(segment.endswith(".data") for segment in entry.split("/")[:-1]),
            f"{label} contains a forbidden .data entry {entry!r}",
        )
        _require(
            not (package and lowered.endswith(FORBIDDEN_FOR_PACKAGE)), f"{label} contains a native binary {entry!r}"
        )
    metadata = _headers(entries[f"{dist_info}METADATA"])
    _require(normalize(metadata.get("Name", "")) == normalize(name), f"{label} METADATA names another distribution")
    _require(metadata.get("Version") == version, f"{label} METADATA version differs from the lock")
    wheel = _headers(entries[f"{dist_info}WHEEL"])
    _require(wheel.get("Wheel-Version", "").startswith("1."), f"{label} has an unsupported wheel version")
    tags = []
    for tag in wheel.get_all("Tag") or []:
        parts = tag.split("-")
        _require(len(parts) == 3, f"{label} has a malformed Tag {tag!r}")
        for py in parts[0].split("."):
            for abi in parts[1].split("."):
                for plat in parts[2].split("."):
                    tags.append((py, abi, plat))
    _require(bool(tags), f"{label} declares no tags")
    scripts: list[tuple[str, str, str]] = []
    entry_points = entries.get(f"{dist_info}entry_points.txt")
    if entry_points is not None:
        section = ""
        for line in entry_points.decode("utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if stripped.startswith("["):
                section = stripped.strip("[]").strip()
                continue
            if section == "console_scripts":
                match = re.fullmatch(
                    r"([A-Za-z0-9._-]+)\s*=\s*([A-Za-z0-9_.]+):([A-Za-z0-9_.]+)\s*(?:\[.*\])?", stripped
                )
                _require(match is not None, f"{label} has a malformed console script {stripped!r}")
                assert match is not None
                scripts.append((match.group(1), match.group(2), match.group(3)))
    return WheelInfo(
        name=name,
        version=version,
        requires_python=metadata.get("Requires-Python", ""),
        requires=tuple(metadata.get_all("Requires-Dist") or ()),
        tags=tuple(tags),
        console_scripts=tuple(scripts),
        dist_info=dist_info,
    )


def _release(text: str) -> tuple[int, ...]:
    match = re.match(r"[0-9]+(?:\.[0-9]+)*", text.strip())
    _require(match is not None, f"unsupported version {text!r}")
    assert match is not None
    return tuple(int(part) for part in match.group(0).split("."))


def _pad(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def _compare(actual: str, operator: str, wanted: str) -> bool:
    if operator in ("==", "!=") and wanted.endswith(".*"):
        prefix = _release(wanted[:-2])
        equal = _release(actual)[: len(prefix)] == prefix
        return equal if operator == "==" else not equal
    left, right = _pad(_release(actual), _release(wanted))
    if operator == "===":
        return actual == wanted
    if operator == "~=":
        floor = _release(wanted)
        _require(len(floor) >= 2, f"unsupported compatible-release clause ~={wanted}")
        upper = floor[:-2] + (floor[-2] + 1,)
        a, f = _pad(_release(actual), floor)
        a2, u = _pad(_release(actual), upper)
        return a >= f and a2 < u
    return {
        "==": left == right,
        "!=": left != right,
        ">=": left >= right,
        "<=": left <= right,
        ">": left > right,
        "<": left < right,
    }[operator]


def satisfies(version: str, specifier: str) -> bool:
    clauses = [clause.strip() for clause in specifier.split(",") if clause.strip()]
    for clause in clauses:
        match = re.fullmatch(r"(===|==|!=|~=|>=|<=|>|<)\s*([0-9][^\s]*)", clause)
        _require(match is not None, f"unsupported version specifier {clause!r}")
        assert match is not None
        if not _compare(version, match.group(1), match.group(2)):
            return False
    return True


def marker_environment() -> dict[str, str]:
    implementation = sys.implementation
    version = ".".join(str(part) for part in sys.version_info[:3])
    return {
        "python_version": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "python_full_version": version,
        "os_name": os.name,
        "sys_platform": sys.platform,
        "platform_machine": sysconfig.get_platform().rsplit("-", 1)[-1],
        "platform_system": {"darwin": "Darwin", "linux": "Linux", "win32": "Windows"}.get(sys.platform, sys.platform),
        "platform_python_implementation": {"cpython": "CPython", "pypy": "PyPy"}.get(
            implementation.name, implementation.name
        ),
        "implementation_name": implementation.name,
        "implementation_version": version,
        "extra": "",
    }


_MARKER_TOKEN = re.compile(
    r"\s*(\(|\)|and\b|or\b|not\s+in\b|in\b|===|==|!=|~=|>=|<=|>|<|'[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_.]*)"
)


def evaluate_marker(marker: str, environment: Mapping[str, str]) -> bool:
    tokens: list[str] = []
    position = 0
    text = marker.strip()
    while position < len(text):
        match = _MARKER_TOKEN.match(text, position)
        _require(match is not None, f"unsupported environment marker {marker!r}")
        assert match is not None
        tokens.append(re.sub(r"\s+", " ", match.group(1)))
        position = match.end()
    index = 0

    def peek() -> str | None:
        return tokens[index] if index < len(tokens) else None

    def take() -> str:
        nonlocal index
        _require(index < len(tokens), f"unsupported environment marker {marker!r}")
        index += 1
        return tokens[index - 1]

    def operand() -> tuple[str, bool]:
        token = take()
        if token[0] in "'\"":
            return token[1:-1], False
        _require(token in environment, f"unsupported marker variable {token!r}")
        return environment[token], True

    def atom() -> bool:
        if peek() == "(":
            take()
            value = disjunction()
            _require(take() == ")", f"unsupported environment marker {marker!r}")
            return value
        left, left_variable = operand()
        operator = take()
        right, right_variable = operand()
        if operator in ("in", "not in"):
            return (left in right) if operator == "in" else (left not in right)
        variable_side = left if left_variable else right
        versions = {environment["python_version"], environment["python_full_version"]}
        if variable_side in versions and re.fullmatch(r"[0-9][0-9.]*", left) and re.fullmatch(r"[0-9][0-9.]*", right):
            return _compare(left, operator, right)
        _require(operator in ("==", "!=", "==="), f"unsupported marker comparison {operator!r}")
        return (left == right) if operator != "!=" else (left != right)

    def conjunction() -> bool:
        value = atom()
        while peek() == "and":
            take()
            value = atom() and value
        return value

    def disjunction() -> bool:
        value = conjunction()
        while peek() == "or":
            take()
            value = conjunction() or value
        return value

    result = disjunction()
    _require(index == len(tokens), f"unsupported environment marker {marker!r}")
    return result


_REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(\(?[^;()]*\)?)\s*(?:;\s*(.*))?$")


def required_distributions(info: WheelInfo, environment: Mapping[str, str]) -> dict[str, str]:
    needed: dict[str, str] = {}
    for line in info.requires:
        match = _REQUIREMENT.fullmatch(line.strip())
        _require(match is not None, f"{info.name} has an unsupported requirement {line!r}")
        assert match is not None
        marker = match.group(4)
        if marker and not evaluate_marker(marker, environment):
            continue
        needed[normalize(match.group(1))] = match.group(3).strip().strip("()")
    return needed


def tag_compatible(tags: Sequence[tuple[str, str, str]]) -> bool:
    major, minor = sys.version_info[:2]
    python_tags = {f"py{major}", f"py{major}{minor}", f"cp{major}{minor}"}
    abi_tags = {"none", "abi3", f"cp{major}{minor}"}
    platform = sysconfig.get_platform().replace("-", "_").replace(".", "_").lower()
    system, _, machine = platform.partition("_")
    for py, abi, plat in tags:
        if py not in python_tags or abi not in abi_tags:
            continue
        if plat == "any":
            return True
        lowered = plat.lower()
        if (
            system == "macosx"
            and lowered.startswith("macosx_")
            and (lowered.endswith(machine.rsplit("_", 1)[-1]) or lowered.endswith("universal2"))
        ):
            return True
        if system == "linux" and lowered.startswith(("manylinux", "linux")) and lowered.endswith(machine):
            return True
        if lowered == platform:
            return True
    return False


@dataclass(frozen=True)
class Plan:
    lock: Lock
    wheels: tuple[tuple[str, str, str, tuple[str, ...], bytes], ...]
    infos: tuple[WheelInfo, ...]


def interpreter_identity() -> dict[str, Any]:
    return {
        "implementation": sys.implementation.name,
        "cache_tag": sys.implementation.cache_tag,
        "version": list(sys.version_info[:3]) + [sys.version_info.releaselevel],
        "executable": str(Path(sys.executable).resolve()),
    }


def platform_identity() -> str:
    return sysconfig.get_platform()


def install_key(lock: Lock, interpreter: Mapping[str, Any], platform: str) -> str:
    identity = {
        "v": KEY_VERSION,
        "package": lock.package,
        "version": lock.version,
        "source_commit": lock.source_commit,
        "artifact": lock.artifact_sha256,
        "dependencies": sorted((normalize(d.name), d.version, d.sha256) for d in lock.dependencies),
        "interpreter": dict(interpreter),
        "platform": platform,
    }
    return sha256_hex(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _retained(cache: Path, sha256: str) -> Path:
    return cache / "artifacts" / f"{sha256}.whl"


def _read_retained(retained: Path) -> bytes:
    try:
        return retained.read_bytes()
    except OSError:
        return b""


def _load_artifact(
    cache: Path, label: str, urls: Sequence[str], sha256: str, fetch: Fetch, *, verify_all: bool
) -> bytes:
    retained = _retained(cache, sha256)
    if not verify_all:
        cached = _read_retained(retained)
        if sha256_hex(cached) == sha256:
            return cached
    raw = fetch_verified(label, urls, sha256, fetch, verify_all=verify_all)
    with _file_lock(retained.parent / f".lock-{sha256}"):
        if sha256_hex(_read_retained(retained)) == sha256:
            return raw
        staged = retained.with_name(f".{retained.name}.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            staged.write_bytes(raw)
            os.replace(staged, retained)
        finally:
            staged.unlink(missing_ok=True)
    return raw


def build_plan(lock: Lock, cache: Path, fetch: Fetch, *, verify_all: bool = False) -> Plan:
    environment = marker_environment()
    main_raw = _load_artifact(
        cache, lock.package, lock.artifact_urls, lock.artifact_sha256, fetch, verify_all=verify_all
    )
    main_entries = read_wheel(main_raw, lock.package)
    main_info = inspect_wheel(main_entries, lock.package, lock.version, lock.package, package=True)
    _require(tag_compatible(main_info.tags), f"{lock.package} has no wheel tag compatible with this interpreter")
    if main_info.requires_python:
        _require(
            satisfies(".".join(str(p) for p in sys.version_info[:3]), main_info.requires_python),
            f"{lock.package} requires Python {main_info.requires_python}",
        )
    locked = {normalize(dep.name): dep for dep in lock.dependencies}
    wheels: list[tuple[str, str, str, tuple[str, ...], bytes]] = [
        (lock.package, lock.version, lock.artifact_sha256, lock.artifact_urls, main_raw)
    ]
    infos = [main_info]
    queue = [main_info]
    resolved: dict[str, str] = {normalize(lock.package): lock.version}
    while queue:
        current = queue.pop(0)
        for name, specifier in sorted(required_distributions(current, environment).items()):
            if name in resolved:
                _require(
                    not specifier or satisfies(resolved[name], specifier),
                    f"locked {name} {resolved[name]} does not satisfy {current.name}'s requirement {specifier!r}",
                )
                continue
            dep = locked.get(name)
            _require(dep is not None, f"{current.name} requires {name}, which the lock has no hash for")
            assert dep is not None
            _require(
                not specifier or satisfies(dep.version, specifier),
                f"locked {dep.name} {dep.version} does not satisfy {current.name}'s requirement {specifier!r}",
            )
            raw = _load_artifact(cache, dep.name, dep.urls, dep.sha256, fetch, verify_all=verify_all)
            entries = read_wheel(raw, dep.name)
            info = inspect_wheel(entries, dep.name, dep.version, dep.name, package=False)
            _require(tag_compatible(info.tags), f"locked {dep.name} has no wheel tag compatible with this interpreter")
            if info.requires_python:
                _require(
                    satisfies(".".join(str(p) for p in sys.version_info[:3]), info.requires_python),
                    f"locked {dep.name} requires Python {info.requires_python}",
                )
            resolved[name] = dep.version
            wheels.append((dep.name, dep.version, dep.sha256, dep.urls, raw))
            infos.append(info)
            queue.append(info)
    return Plan(lock, tuple(wheels), tuple(infos))


def launcher_text(python: str, site: Path, module: str, attribute: str) -> str:
    return (
        f"#!{python} -IBS\n"
        "import sys\n"
        "sys.dont_write_bytecode = True\n"
        f"sys.path.insert(0, {str(site)!r})\n"
        f"from {module} import {attribute}\n"
        f"sys.exit({attribute}())\n"
    )


def windows_wrapper_text(python: str, script: str) -> str:
    return f'@"{python}" -IBS "%~dp0{script}" %*\r\n'


def expected_files(plan: Plan, root: Path, interpreter: Mapping[str, Any]) -> dict[str, bytes]:
    site = root / "tree" / "site"
    files: dict[str, bytes] = {}
    for (_, _, _, _, raw), info in zip(plan.wheels, plan.infos, strict=True):
        for name, content in read_wheel(raw, info.name).items():
            target = f"site/{name}"
            _require(target not in files, f"two wheels install {name!r}")
            files[target] = content
    python = str(interpreter["executable"])
    if _windows():
        _require(
            "%" not in python and python.isascii(),
            "the interpreter path contains '%' or non-ASCII characters, which a cmd wrapper cannot carry safely",
        )
    else:
        _require(
            not any(ch.isspace() for ch in python) and len(python) < 120,
            "the interpreter path is unsuitable for a launcher shebang",
        )
    for script, module, attribute in plan.infos[0].console_scripts:
        files[f"bin/{script}"] = launcher_text(python, site, module, attribute).encode("utf-8")
        if _windows():
            files[f"bin/{script}.cmd"] = windows_wrapper_text(python, script).encode("utf-8")
    return files


def _tree(root: Path) -> dict[str, bytes] | None:
    found: dict[str, bytes] = {}
    for current, directories, filenames in os.walk(root, followlinks=False):
        base = Path(current)
        for directory in directories:
            if (base / directory).is_symlink():
                return None
        for filename in filenames:
            path = base / filename
            if path.is_symlink() or not path.is_file():
                return None
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def content_matches(root: Path, expected: Mapping[str, bytes]) -> bool:
    actual = _tree(root)
    return (
        actual is not None and set(actual) == set(expected) and all(actual[name] == expected[name] for name in expected)
    )


def _write_tree(root: Path, expected: Mapping[str, bytes]) -> None:
    for name, content in expected.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if name.startswith("bin/") else 0o644)


MANIFEST = "install.json"


def manifest_text(key: str, lock: Lock, interpreter: Mapping[str, Any], platform: str) -> str:
    return json.dumps(
        {
            "v": KEY_VERSION,
            "key": key,
            "package": lock.package,
            "version": lock.version,
            "source_commit": lock.source_commit,
            "artifact_sha256": lock.artifact_sha256,
            "dependencies": [
                {"name": d.name, "version": d.version, "sha256": d.sha256}
                for d in sorted(lock.dependencies, key=lambda item: normalize(item.name))
            ],
            "interpreter": dict(interpreter),
            "platform": platform,
        },
        indent=2,
        sort_keys=True,
    )


def _default_cache() -> Path:
    configured = os.environ.get(CACHE_ENV, "").strip()
    return Path(configured) if configured else Path.home() / ".cache" / "theseus" / "installs"


@dataclass(frozen=True)
class Installed:
    executable: Path
    root: Path
    key: str
    reconstructed: bool


def install(
    lock: Lock,
    cache: Path | None = None,
    fetch: Fetch | None = None,
    *,
    entry: str | None = None,
    verify_all: bool = False,
    reinstall: bool = False,
) -> Installed:
    cache = (cache or _default_cache()).resolve()
    fetch = fetch or default_fetch()
    interpreter, platform = interpreter_identity(), platform_identity()
    key = install_key(lock, interpreter, platform)
    root = cache / "installs" / key
    try:
        plan = build_plan(lock, cache, fetch, verify_all=verify_all)
        expected = expected_files(plan, root, interpreter)
        manifest = manifest_text(key, lock, interpreter, platform)
        scripts = plan.infos[0].console_scripts
        name = entry or (scripts[0][0] if scripts else "")
        _require(
            bool(name) and any(script[0] == name for script in scripts),
            f"{lock.package} has no console script {entry!r}",
        )
        with _key_lock(cache, key):
            return _settle(root, key, name, expected, manifest, reinstall)
    except OSError as exc:
        raise BootstrapError(f"cannot install into {root}: {exc}") from exc


WINDOWS_LOCK_ATTEMPTS = 1200
WINDOWS_LOCK_INTERVAL = 0.5


def _windows() -> bool:
    return sys.platform == "win32"


def _lock_posix(handle: Any) -> Callable[[], None]:
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_EX)
    return lambda: fcntl.flock(handle, fcntl.LOCK_UN)


def _lock_windows(
    handle: Any, locking_api: Any = None, sleep: Callable[[float], None] = time.sleep
) -> Callable[[], None]:
    if locking_api is None:
        import msvcrt as locking_api

    handle.seek(0)
    for attempt in range(WINDOWS_LOCK_ATTEMPTS):
        try:
            locking_api.locking(handle.fileno(), locking_api.LK_NBLCK, 1)
            break
        except OSError:
            if attempt == WINDOWS_LOCK_ATTEMPTS - 1:
                raise
            sleep(WINDOWS_LOCK_INTERVAL)

    def release() -> None:
        handle.seek(0)
        locking_api.locking(handle.fileno(), locking_api.LK_UNLCK, 1)

    return release


@contextlib.contextmanager
def _file_lock(path: Path, lock: Callable[[Any], Callable[[], None]] | None = None) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        release = (lock or (_lock_windows if _windows() else _lock_posix))(handle)
        try:
            yield
        finally:
            release()


def _key_lock(cache: Path, key: str) -> contextlib.AbstractContextManager[None]:
    return _file_lock(cache / "installs" / f".lock-{key}")


def _entry_path(root: Path, name: str) -> Path:
    return root / "tree" / "bin" / (f"{name}.cmd" if _windows() else name)


def _publish(staging: Path, root: Path) -> None:
    os.replace(staging, root)


def _settle(
    root: Path, key: str, name: str, expected: Mapping[str, bytes], manifest: str, reinstall: bool
) -> Installed:
    if root.exists() and not reinstall:
        recorded = root / MANIFEST
        if recorded.is_file():
            _require(
                recorded.read_text(encoding="utf-8") == manifest,
                f"the warm install at {root} was recorded for a different artifact, dependency set, interpreter or "
                "platform than this lock requires; refusing to use it (pass --reinstall to rebuild it)",
            )
            if content_matches(root / "tree", expected):
                return Installed(_entry_path(root, name), root, key, False)
    reconstructed = root.exists()
    staging = root.parent / f".build-{key}-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        staging.mkdir(parents=True)
        (staging / "tree").mkdir()
        _write_tree(staging / "tree", expected)
        (staging / MANIFEST).write_text(manifest, encoding="utf-8")
        _require(content_matches(staging / "tree", expected), "the staged install does not match the verified bytes")
        if root.exists():
            shutil.rmtree(root)
        _publish(staging, root)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    _require(content_matches(root / "tree", expected), "the installed content does not match the verified bytes")
    return Installed(_entry_path(root, name), root, key, reconstructed)


def verify_installed(lock: Lock, cache: Path | None, fetch: Fetch | None = None) -> Installed:
    cache = (cache or _default_cache()).resolve()
    fetch = fetch or default_fetch()
    interpreter, platform = interpreter_identity(), platform_identity()
    key = install_key(lock, interpreter, platform)
    root = cache / "installs" / key
    try:
        _require((root / MANIFEST).is_file(), f"no install exists for {lock.package} {lock.version} at {root}")
        plan = build_plan(lock, cache, fetch)
        expected = expected_files(plan, root, interpreter)
        _require(
            (root / MANIFEST).read_text(encoding="utf-8") == manifest_text(key, lock, interpreter, platform),
            f"the install at {root} was recorded for a different lock",
        )
        _require(content_matches(root / "tree", expected), f"the install at {root} does not match the verified bytes")
        script = plan.infos[0].console_scripts[0][0]
        return Installed(_entry_path(root, script), root, key, False)
    except OSError as exc:
        raise BootstrapError(f"cannot verify the install at {root}: {exc}") from exc


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="Consumer git checkout holding the trusted commit.")
    parser.add_argument("--commit", required=True, help="Full 40-hex trusted consumer commit to read the lock from.")
    parser.add_argument("--lock-path", default=LOCK_PATH)
    parser.add_argument("--cache-dir", default=None, help=f"Install cache root (default: ${CACHE_ENV} or user cache).")
    parser.add_argument("--entry", default=None, help="Console script to resolve (default: the first one).")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="theseus bootstrap",
        description="Install theseus from a lock at a trusted consumer commit; every byte is hash-verified first.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install", help="Verify and install; print the executable path.")
    _common(install_parser)
    install_parser.add_argument("--verify-all-sources", action="store_true", help="Require every byte source to match.")
    install_parser.add_argument("--reinstall", action="store_true", help="Discard a mismatched warm install.")
    install_parser.add_argument(
        "--print-argv",
        action="store_true",
        help="Print the launch argv as a JSON list (interpreter, -IBS, script) instead of the executable path.",
    )
    verify_parser = commands.add_parser("verify", help="Verify an existing install without creating one.")
    _common(verify_parser)
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    cache = Path(args.cache_dir) if args.cache_dir else None
    try:
        lock = read_lock(Path(args.repo), args.commit, args.lock_path)
        if args.command == "verify":
            installed = verify_installed(lock, cache)
        else:
            installed = install(
                lock,
                cache,
                entry=args.entry,
                verify_all=getattr(args, "verify_all_sources", False),
                reinstall=getattr(args, "reinstall", False),
            )
    except BootstrapError as exc:
        print(f"theseus bootstrap: refused: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "print_argv", False):
        script = installed.executable.parent / installed.executable.name.removesuffix(".cmd")
        print(json.dumps([str(interpreter_identity()["executable"]), "-IBS", str(script)]))
    else:
        print(installed.executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
