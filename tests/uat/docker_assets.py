from __future__ import annotations

import functools
import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

DOCKER_PLATFORM_SWITCH_MODES: tuple[str, ...] = ("off", "containers", "volumes", "images")
DOCKER_FIXED_CONTAINER_NAME_POLICIES: tuple[str, ...] = ("fail", "override", "allow")

_CONTAINER_NAME_MAX_LEN = 64
_REPLICA_SUFFIX_MAX_LEN = len("-99")
_PROJECT_NAME_MAX_LEN = 63

CONTAINER_CLI_ENV_VAR = "BENCHBOX_CONTAINER_CLI"


class DockerAssetError(ValueError):
    pass


CLICKHOUSE_MEMORY_LIMIT_ENV_VAR = "CLICKHOUSE_MEMORY_LIMIT"
_MEMORY_VALUE_RE = re.compile(r"^\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>[KMGTPE]?i?B?)\s*$", re.IGNORECASE)
_COMPOSE_MEMORY_UNITS = {
    "": 1,
    "b": 1,
    **dict.fromkeys(("k", "kb", "ki", "kib"), 1024),
    **dict.fromkeys(("m", "mb", "mi", "mib"), 1024**2),
    **dict.fromkeys(("g", "gb", "gi", "gib"), 1024**3),
    **dict.fromkeys(("t", "tb", "ti", "tib"), 1024**4),
}
_RUNTIME_MEMORY_UNITS = {
    **_COMPOSE_MEMORY_UNITS,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
}


def _parse_memory_bytes(value: str, units: dict[str, int]) -> int:
    if not isinstance(value, str):
        raise DockerAssetError(f"memory limit must be a string, got {type(value).__name__}")
    match = _MEMORY_VALUE_RE.fullmatch(value)
    if match is None:
        raise DockerAssetError(f"invalid memory limit {value!r}; use a positive value such as 4g or 4096MiB")
    try:
        amount = float(match.group("value"))
    except (OverflowError, ValueError) as exc:
        raise DockerAssetError(f"invalid memory limit {value!r}; use a positive value such as 4g or 4096MiB") from exc
    multiplier = units.get(match.group("unit").lower())
    if multiplier is None or amount <= 0:
        raise DockerAssetError(f"invalid memory limit {value!r}; use a positive value such as 4g or 4096MiB")
    try:
        size = int(amount * multiplier)
    except (OverflowError, ValueError) as exc:
        raise DockerAssetError(f"invalid memory limit {value!r}; use a positive value such as 4g or 4096MiB") from exc
    if size <= 0:
        raise DockerAssetError(f"invalid memory limit {value!r}; use a positive value such as 4g or 4096MiB")
    return size


def parse_memory_bytes(value: str) -> int:
    return _parse_memory_bytes(value, _COMPOSE_MEMORY_UNITS)


def parse_runtime_memory_bytes(value: str) -> int:
    return _parse_memory_bytes(value, _RUNTIME_MEMORY_UNITS)


def resolve_clickhouse_memory_limit(
    configured: str | None = None, *, env: dict[str, str] | None = None
) -> tuple[str, int]:
    environment = os.environ if env is None else env
    value = (
        environment[CLICKHOUSE_MEMORY_LIMIT_ENV_VAR] if CLICKHOUSE_MEMORY_LIMIT_ENV_VAR in environment else configured
    )
    if value is None or not str(value).strip():
        raise DockerAssetError(
            f"{CLICKHOUSE_MEMORY_LIMIT_ENV_VAR} is required for ClickHouse server UAT; "
            "set the measured calibration rung explicitly"
        )
    if not isinstance(value, str):
        raise DockerAssetError(f"memory limit must be a string, got {type(value).__name__}")
    normalized = value.strip()
    return normalized, parse_memory_bytes(normalized)


def compose_stats_command(spec: DockerPlatformSpec, project_name: str) -> list[str]:
    if not spec.services:
        raise DockerAssetError(f"Platform {spec.platform!r} has no compose services to inspect")
    return [
        resolve_container_cli(),
        "stats",
        "--no-stream",
        "--format",
        "{{json .}}",
        f"{project_name}-{spec.services[0]}-1",
    ]


def parse_runtime_memory_limit(text: str) -> int | None:
    usage: str | None = None
    limit: str | None = None
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if isinstance(payload, dict):
            usage = payload.get("MemUsage") or payload.get("MemUsageBytes") or payload.get("memory_usage")
            limit = payload.get("MemLimit") or payload.get("MemLimitBytes") or payload.get("memory_limit")
            if limit is None and isinstance(usage, str) and "/" in usage:
                _, limit = usage.split("/", 1)
            if limit is not None:
                try:
                    return parse_runtime_memory_bytes(str(limit).strip())
                except DockerAssetError:
                    return None
        if candidate.upper().startswith(("CONTAINER ID", "NAME")):
            continue
        table_match = re.search(r"(?P<usage>\S+\s*/\s*(?P<limit>\S+))", candidate)
        if table_match:
            try:
                return parse_runtime_memory_bytes(table_match.group("limit"))
            except DockerAssetError:
                return None
    return None


def _which_container_cli(cli: str) -> str | None:
    return shutil.which(cli)


def _current_platform() -> str:
    return sys.platform


@functools.lru_cache(maxsize=1)
def resolve_container_cli() -> str:
    override = os.environ.get(CONTAINER_CLI_ENV_VAR)
    if override:
        candidate, source = override, f"{CONTAINER_CLI_ENV_VAR} override"
    elif _current_platform() == "darwin" and _which_container_cli("mocker") is not None:
        candidate, source = "mocker", "darwin mocker-if-present default"
    else:
        candidate, source = "docker", "platform default"
    if _which_container_cli(candidate) is None:
        raise DockerAssetError(
            f"Resolved container CLI {candidate!r} ({source}) is not available on PATH; "
            f"install it or set {CONTAINER_CLI_ENV_VAR} to a Docker-CLI-compatible binary that is."
        )
    return candidate


def container_engine_identity() -> tuple[str, str]:
    binary = resolve_container_cli()
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        text = (completed.stdout or completed.stderr or "").strip()
        version = text.splitlines()[0] if text else f"{binary} --version produced no output"
    except (OSError, subprocess.SubprocessError) as exc:
        version = f"{binary} --version failed: {exc}"
    return binary, version


@dataclass(frozen=True)
class DockerPlatformSpec:
    platform: str
    compose_files: tuple[Path, ...]
    services: tuple[str, ...] = ()
    fixed_container_names: tuple[str, ...] = ()
    managed_start_allowed: bool = True
    tcp_probe_label: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class DockerCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    dry_run: bool = False
    error: str | None = None

    @property
    def command(self) -> str:
        return shlex.join(self.argv)

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.error is None


DockerRunner = Callable[..., DockerCommandResult]


def _repo_path(relative: str) -> Path:
    return REPO_ROOT / relative


_DOCKER_PLATFORM_SPECS: dict[str, DockerPlatformSpec] = {
    "clickhouse-server": DockerPlatformSpec(
        platform="clickhouse-server",
        compose_files=(_repo_path("docker/clickhouse/docker-compose.yml"),),
        services=("clickhouse",),
    ),
    "cedardb": DockerPlatformSpec(
        platform="cedardb",
        compose_files=(_repo_path("docker/cedardb/docker-compose.yml"),),
        services=("cedardb",),
    ),
    "starrocks": DockerPlatformSpec(
        platform="starrocks",
        compose_files=(_repo_path("docker/starrocks/docker-compose.yml"),),
        services=("starrocks",),
    ),
    "postgresql": DockerPlatformSpec(
        platform="postgresql",
        compose_files=(_repo_path("docker/postgresql/docker-compose.yml"),),
        services=("postgresql",),
    ),
    "presto": DockerPlatformSpec(
        platform="presto",
        compose_files=(_repo_path("docker/presto/docker-compose.yml"),),
        services=("presto",),
    ),
    "trino": DockerPlatformSpec(
        platform="trino",
        compose_files=(_repo_path("docker/trino/docker-compose.yml"),),
        services=("trino",),
    ),
    "databend": DockerPlatformSpec(
        platform="databend",
        compose_files=(_repo_path("docker/databend/docker-compose.yml"),),
        services=("minio", "minio-setup", "databend"),
    ),
    "doris": DockerPlatformSpec(
        platform="doris",
        compose_files=(_repo_path("docker/doris/docker-compose.yml"),),
        services=("doris",),
    ),
    "influxdb": DockerPlatformSpec(
        platform="influxdb",
        compose_files=(_repo_path("docker/influxdb/docker-compose.yml"),),
        services=("influxdb",),
    ),
    "lakesail": DockerPlatformSpec(
        platform="lakesail",
        compose_files=(_repo_path("docker/lakesail/docker-compose.yml"),),
        services=("lakesail-connect",),
        notes="Start only lakesail-connect for host-run UAT; BENCHBOX_DATA_DIR is set to the sweep output root.",
    ),
    "pg-duckdb": DockerPlatformSpec(
        platform="pg-duckdb",
        compose_files=(_repo_path("docker/postgres-extensions/docker-compose.pg-duckdb.yaml"),),
        services=("pg-duckdb",),
        notes="PostgreSQL-family stack on localhost:5432; run sequentially with other PG-family platforms.",
    ),
    "pg-mooncake": DockerPlatformSpec(
        platform="pg-mooncake",
        compose_files=(_repo_path("docker/postgres-extensions/docker-compose.pg-mooncake.yaml"),),
        services=("pg-mooncake",),
        notes="PostgreSQL-family stack on localhost:5432; run sequentially with other PG-family platforms.",
    ),
    "timescaledb": DockerPlatformSpec(
        platform="timescaledb",
        compose_files=(_repo_path("docker/postgres-extensions/docker-compose.timescaledb.yaml"),),
        services=("timescaledb",),
        notes="PostgreSQL-family stack on localhost:5432; run sequentially with other PG-family platforms.",
    ),
    "questdb": DockerPlatformSpec(
        platform="questdb",
        compose_files=(_repo_path("docker/questdb/docker-compose.yml"),),
        services=("questdb",),
    ),
    "singlestore": DockerPlatformSpec(
        platform="singlestore",
        compose_files=(_repo_path("docker/singlestore/docker-compose.yml"),),
        services=("singlestore",),
    ),
    "velox": DockerPlatformSpec(
        platform="velox",
        compose_files=(_repo_path("docker/velox/docker-compose.yml"),),
        services=("velox-connect",),
        notes="Start only velox-connect for host-run UAT; BENCHBOX_DATA_DIR is set to the sweep output root.",
    ),
}


def _adapter_service_ports() -> dict[str, int]:
    from benchbox.core.platform_manifest import get_adapter_imports

    coords = {key.lower(): (module, class_name) for key, module, class_name in get_adapter_imports()}
    ports: dict[str, int] = {}
    for platform in _DOCKER_PLATFORM_SPECS:
        module_path, class_name = coords[platform.lower()]
        if (port := getattr(importlib.import_module(module_path), class_name).default_service_port) is None:
            raise RuntimeError(f"{platform} adapter declares no default_service_port")
        ports[platform] = port
    return ports


PLATFORM_SERVICE_PORT: dict[str, int] = _adapter_service_ports()

_PLATFORM_STATIC_OPTS: dict[str, list[str]] = {
    "velox": ["--platform-option", "deployment=remote"],
}

_PLATFORM_SECONDARY_PORTS: dict[str, dict[str, int]] = {
    "questdb": {"http_port": 9000},
    "starrocks": {"http_port": 8040},
    "doris": {"http_port": 8030, "be_http_port": 8040},
}

_PLATFORM_PASSWORD_ENV_KEYS: dict[str, str] = {
    "clickhouse-server": "CLICKHOUSE_PASSWORD",
    "cedardb": "CEDAR_PASSWORD",
    "pg-duckdb": "POSTGRES_PASSWORD",
    "pg-mooncake": "POSTGRES_PASSWORD",
    "timescaledb": "POSTGRES_PASSWORD",
    "postgresql": "POSTGRES_PASSWORD",
    "singlestore": "ROOT_PASSWORD",
}

_PLATFORM_INJECT_HOST_PORT_OPT: frozenset[str] = frozenset({"clickhouse-server", "singlestore", "starrocks", "doris"})

_PLATFORM_INJECT_SPARK_CONNECT_ENDPOINT_OPT: frozenset[str] = frozenset({"lakesail", "velox"})

_PLATFORM_LOCAL_MANAGED_STATIC_OPTS: dict[str, list[str]] = {
    "cedardb": ["--platform-option", "username=benchbox", "--platform-option", "database=benchbox_test"],
    "postgresql": ["--platform-option", "username=benchbox"],
}

_COMPOSE_PORT_MAPPING_RE = re.compile(r'^\s*-\s*"?(?P<mapping>[^"\s]+)"?\s*$')
_ENV_DEFAULT_RE = re.compile(r"^\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*):-(?P<default>\d+)\}$")
_ENV_BARE_RE = re.compile(r"^\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}$")
_HOST_CONTAINER_RE = re.compile(r"^(?P<host>.+):(?P<container>\d+)$")


def _resolve_host_token(token: str, env: dict[str, str]) -> int | None:
    token = token.strip().strip('"')
    m = _ENV_DEFAULT_RE.match(token)
    if m:
        raw = env.get(m.group("var"), m.group("default"))
        return int(raw) if str(raw).isdigit() else int(m.group("default"))
    m = _ENV_BARE_RE.match(token)
    if m:
        raw = env.get(m.group("var"))
        return int(raw) if raw and raw.isdigit() else None
    if token.isdigit():
        return int(token)
    if ":" in token and token.rsplit(":", 1)[-1].isdigit():
        return int(token.rsplit(":", 1)[-1])
    return None


def _iter_compose_port_mappings(compose_file: Path) -> Iterable[tuple[str, int]]:
    try:
        text = compose_file.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        entry = _COMPOSE_PORT_MAPPING_RE.match(line)
        if not entry:
            continue
        hc = _HOST_CONTAINER_RE.match(entry.group("mapping"))
        if not hc:
            continue
        yield hc.group("host"), int(hc.group("container"))


def resolve_published_host_port(platform: str, *, env: dict[str, str] | None = None) -> int | None:
    service_port = PLATFORM_SERVICE_PORT.get(platform)
    return resolve_published_host_port_for_container(platform, service_port, env=env)


def resolve_published_host_port_for_container(
    platform: str,
    container_port: int | None,
    *,
    env: dict[str, str] | None = None,
) -> int | None:
    env = os.environ if env is None else env
    spec = _DOCKER_PLATFORM_SPECS.get(platform)
    if container_port is None or spec is None:
        return None
    for compose_file in spec.compose_files:
        for host_token, mapped_container_port in _iter_compose_port_mappings(compose_file):
            if mapped_container_port == container_port:
                resolved = _resolve_host_token(host_token, env)
                if resolved is not None:
                    return resolved
    return None


@functools.cache
def _compose_document(compose_file: Path) -> dict:
    payload = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _compose_service_environment_value(
    platform: str,
    key: str,
    *,
    env: dict[str, str] | None = None,
) -> str | None:
    process_env = os.environ if env is None else env
    spec = _DOCKER_PLATFORM_SPECS.get(platform)
    if spec is None:
        return None
    for compose_file in spec.compose_files:
        services = _compose_document(compose_file).get("services", {})
        if not isinstance(services, dict):
            continue
        for service_name in spec.services:
            service = services.get(service_name, {})
            if not isinstance(service, dict):
                continue
            raw_environment = service.get("environment", {})
            if isinstance(raw_environment, list):
                entries = dict(
                    entry.split("=", 1) for entry in raw_environment if isinstance(entry, str) and "=" in entry
                )
            elif isinstance(raw_environment, dict):
                entries = raw_environment
            else:
                continue
            raw = entries.get(key)
            if raw is None:
                continue
            text = str(raw)
            default_match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}", text)
            if default_match:
                return process_env.get(default_match.group(1), default_match.group(2))
            bare_match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text)
            if bare_match:
                return process_env.get(bare_match.group(1))
            return text
    return None


def host_reachability_endpoint(platform: str, *, env: dict[str, str] | None = None) -> str | None:
    host_port = resolve_published_host_port(platform, env=env)
    return None if host_port is None else f"localhost:{host_port}"


def platform_extra_opts(platform: str, *, env: dict[str, str] | None = None) -> list[str]:
    opts: list[str] = []
    if platform in _PLATFORM_INJECT_HOST_PORT_OPT:
        host_port = resolve_published_host_port(platform, env=env)
        if host_port is not None:
            opts += ["--platform-option", f"port={host_port}"]
    for option, container_port in _PLATFORM_SECONDARY_PORTS.get(platform, {}).items():
        host_port = resolve_published_host_port_for_container(platform, container_port, env=env)
        if host_port is not None:
            opts += ["--platform-option", f"{option}={host_port}"]
    if platform == "singlestore":
        password = _compose_service_environment_value(platform, _PLATFORM_PASSWORD_ENV_KEYS[platform], env=env)
        if password is not None:
            opts += ["--platform-option", f"password={password}"]
    opts += list(_PLATFORM_STATIC_OPTS.get(platform, []))
    if platform in _PLATFORM_INJECT_SPARK_CONNECT_ENDPOINT_OPT:
        host_port = resolve_published_host_port(platform, env=env)
        if host_port is not None:
            opts += ["--platform-option", f"endpoint=sc://localhost:{host_port}"]
    return opts


def local_managed_platform_extra_opts(platform: str, *, env: dict[str, str] | None = None) -> list[str]:
    opts = list(_PLATFORM_LOCAL_MANAGED_STATIC_OPTS.get(platform, []))
    password_key = _PLATFORM_PASSWORD_ENV_KEYS.get(platform)
    if password_key is not None:
        password = _compose_service_environment_value(platform, password_key, env=env)
        if password is not None:
            opts += ["--platform-option", f"password={password}"]
    return opts


_DOCKER_PLATFORM_SPECS = {
    platform: replace(spec, tcp_probe_label=host_reachability_endpoint(platform))
    for platform, spec in _DOCKER_PLATFORM_SPECS.items()
}


def docker_platform_specs() -> dict[str, DockerPlatformSpec]:
    return dict(_DOCKER_PLATFORM_SPECS)


def is_docker_platform(platform: str) -> bool:
    return platform in _DOCKER_PLATFORM_SPECS


def docker_platform_spec(platform: str) -> DockerPlatformSpec:
    try:
        return _DOCKER_PLATFORM_SPECS[platform]
    except KeyError as exc:
        raise DockerAssetError(f"No UAT Docker compose spec is registered for platform {platform!r}") from exc


def _max_declared_service_len(platform: str) -> int:
    spec = _DOCKER_PLATFORM_SPECS.get(platform)
    if spec is None or not spec.services:
        return 0
    return max(len(service) for service in spec.services)


def _project_name_budget(platform: str, max_service_len: int | None = None) -> int:
    service_len = _max_declared_service_len(platform) if max_service_len is None else max_service_len
    if service_len <= 0:
        return _PROJECT_NAME_MAX_LEN
    derived = _CONTAINER_NAME_MAX_LEN - service_len - _REPLICA_SUFFIX_MAX_LEN - 1
    return min(_PROJECT_NAME_MAX_LEN, derived)


def compose_project_name(
    config_name: str,
    platform: str,
    prefix: str = "benchbox-uat",
    *,
    max_service_len: int | None = None,
) -> str:
    raw = f"{prefix}-{config_name}-{platform}".lower()
    name = re.sub(r"[^a-z0-9_-]+", "-", raw)
    name = re.sub(r"[-_]{2,}", "-", name).strip("-_")
    if not name or not name[0].isalnum():
        name = f"uat-{name}".strip("-_")
    budget = _project_name_budget(platform, max_service_len)
    if len(name) > budget:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        keep = max(budget - len(digest) - 1, 1)
        name = f"{name[:keep].rstrip('-_')}-{digest}"
    return name


def validate_project_name_budget(spec: DockerPlatformSpec, project_name: str) -> None:
    budget = _project_name_budget(spec.platform)
    if len(project_name) > budget:
        raise DockerAssetError(
            f"Docker compose project name {project_name!r} is {len(project_name)} chars, "
            f"which exceeds the {budget}-char budget for platform {spec.platform!r} "
            "(derived from the 64-char container-id limit minus the longest started "
            "service name and replica-suffix headroom; see compose_project_name())."
        )


def _compose_base_command(spec: DockerPlatformSpec, project_name: str) -> list[str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name):
        raise DockerAssetError(f"Unsafe Docker compose project name {project_name!r}")
    validate_project_name_budget(spec, project_name)
    argv = [resolve_container_cli(), "compose", "-p", project_name]
    for compose_file in spec.compose_files:
        argv.extend(["-f", str(compose_file)])
    return argv


def compose_up_command(
    spec: DockerPlatformSpec,
    project_name: str,
    *,
    start_timeout_s: int = 300,
) -> list[str]:
    argv = _compose_base_command(spec, project_name)
    argv.extend(["up", "-d", "--wait", "--wait-timeout", str(start_timeout_s)])
    argv.extend(spec.services)
    return argv


def compose_ps_command(spec: DockerPlatformSpec, project_name: str) -> list[str]:
    argv = _compose_base_command(spec, project_name)
    argv.extend(["ps", "-a"])
    return argv


_UNHEALTHY_PS_STATE_RE = re.compile(r"\b(?:Exited|Restarting|Created|Dead|Paused)\b|\bExit\s+\d+|\(unhealthy\)")

_PS_HEADER_RE = re.compile(r"^\s*(?:NAME|Name)\b")
_PS_SEPARATOR_RE = re.compile(r"^[\s\-+]+$")


def compose_ps_service_rows(ps_stdout: str) -> tuple[str, ...]:
    rows: list[str] = []
    for line in ps_stdout.splitlines():
        if not line.strip():
            continue
        if _PS_HEADER_RE.match(line) or _PS_SEPARATOR_RE.match(line):
            continue
        rows.append(line)
    return tuple(rows)


def compose_ps_unhealthy_services(ps_stdout: str) -> tuple[str, ...]:
    unhealthy: list[str] = []
    for line in compose_ps_service_rows(ps_stdout):
        columns = line.split(None, 1)
        remainder = columns[1] if len(columns) > 1 else ""
        if _UNHEALTHY_PS_STATE_RE.search(remainder):
            unhealthy.append(columns[0])
    return tuple(unhealthy)


def _resolve_compose_memory_value(value: object, *, env: dict[str, str] | None) -> str:
    raw = str(value).strip()
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw)
    if match is None:
        return raw
    environment = os.environ if env is None else env
    resolved = environment.get(match.group(1))
    if not resolved:
        raise DockerAssetError(f"compose memory variable {match.group(1)!r} is not set")
    return resolved.strip()


def compose_declared_memory_limits(spec: DockerPlatformSpec, *, env: dict[str, str] | None = None) -> dict[str, str]:
    limits: dict[str, str] = {}
    for compose_file in spec.compose_files:
        try:
            data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(services, dict):
            continue
        for name, service in services.items():
            if not isinstance(service, dict):
                continue
            mem_limit = service.get("mem_limit")
            if mem_limit:
                limits[str(name)] = _resolve_compose_memory_value(mem_limit, env=env)
                continue
            deploy = service.get("deploy")
            resources = deploy.get("resources") if isinstance(deploy, dict) else None
            limits_block = resources.get("limits") if isinstance(resources, dict) else None
            declared = limits_block.get("memory") if isinstance(limits_block, dict) else None
            if declared:
                limits[str(name)] = _resolve_compose_memory_value(declared, env=env)
    return limits


def compose_pull_command(spec: DockerPlatformSpec, project_name: str) -> list[str]:
    argv = _compose_base_command(spec, project_name)
    argv.extend(["pull", "--ignore-buildable"])
    argv.extend(spec.services)
    return argv


def compose_build_command(spec: DockerPlatformSpec, project_name: str) -> list[str]:
    argv = _compose_base_command(spec, project_name)
    argv.append("build")
    argv.extend(spec.services)
    return argv


def compose_down_command(spec: DockerPlatformSpec, project_name: str, cleanup_mode: str) -> list[str]:
    if cleanup_mode == "off":
        raise DockerAssetError("cleanup_mode='off' does not have a Docker teardown command")
    if cleanup_mode not in {"containers", "volumes", "images"}:
        raise DockerAssetError(f"Unknown Docker cleanup mode {cleanup_mode!r}; valid: containers, volumes, images")

    argv = _compose_base_command(spec, project_name)
    argv.append("down")
    if cleanup_mode in {"volumes", "images"}:
        argv.append("-v")
    argv.append("--remove-orphans")
    if cleanup_mode == "images":
        argv.extend(["--rmi", "local"])
    return argv


def validate_managed_start_allowed(spec: DockerPlatformSpec, fixed_container_name_policy: str = "fail") -> None:
    if fixed_container_name_policy not in DOCKER_FIXED_CONTAINER_NAME_POLICIES:
        raise DockerAssetError(
            f"Unknown fixed-container-name policy {fixed_container_name_policy!r}; "
            f"valid: {', '.join(DOCKER_FIXED_CONTAINER_NAME_POLICIES)}"
        )
    if not spec.managed_start_allowed:
        reason = spec.notes or "the compose file is not eligible for UAT-managed startup"
        raise DockerAssetError(f"Platform {spec.platform!r} cannot be UAT-managed: {reason}")
    if spec.fixed_container_names:
        names = ", ".join(spec.fixed_container_names)
        if fixed_container_name_policy == "fail":
            raise DockerAssetError(
                f"Platform {spec.platform!r} compose file declares fixed container_name value(s): {names}; "
                "remove/override them before enabling UAT-managed startup"
            )
        if fixed_container_name_policy == "override":
            raise DockerAssetError(
                f"Platform {spec.platform!r} requested fixed-container-name override, but no override is registered"
            )


def compose_environment(
    spec: DockerPlatformSpec,
    *,
    benchmark_runs_dir: Path | str | None = None,
    memory_limit: str | None = None,
    starrocks_memory_limit: str | None = "4g",
) -> dict[str, str]:
    environment: dict[str, str] = {}
    if spec.platform == "clickhouse-server":
        resolved, _ = resolve_clickhouse_memory_limit(memory_limit)
        environment[CLICKHOUSE_MEMORY_LIMIT_ENV_VAR] = resolved
    if spec.platform == "starrocks":
        environment["STARROCKS_MEMORY_LIMIT"] = os.environ.get("STARROCKS_MEMORY_LIMIT", starrocks_memory_limit or "4g")
    if spec.platform not in {"lakesail", "velox"}:
        return environment
    if benchmark_runs_dir is None:
        raise DockerAssetError(
            f"benchmark_runs_dir is required for platform {spec.platform!r} -- its compose "
            "file mounts BENCHBOX_DATA_DIR at the SAME absolute path inside the container as "
            "on the host, so omitting it would let the child process silently inherit "
            "whatever ambient BENCHBOX_DATA_DIR (if any) is set in the caller's environment "
            "instead of the resolved run directory. Pass an absolute benchmark_runs_dir / "
            "--benchmark-runs-dir."
        )
    data_dir = Path(benchmark_runs_dir).expanduser()
    if not data_dir.is_absolute():
        raise DockerAssetError(
            f"BENCHBOX_DATA_DIR must be an absolute path for platform {spec.platform!r} "
            f"(got {str(benchmark_runs_dir)!r}) -- its compose file mounts BENCHBOX_DATA_DIR "
            "at the SAME absolute path inside the container as on the host, so a relative "
            "value can never match. Pass an absolute benchmark_runs_dir / --benchmark-runs-dir."
        )
    environment["BENCHBOX_DATA_DIR"] = str(data_dir)
    return environment


_FORBIDDEN_PRUNE_VERBS: frozenset[tuple[str, str]] = frozenset(
    {
        ("system", "prune"),
        ("volume", "prune"),
        ("image", "prune"),
        ("builder", "prune"),
    }
)

_APPLE_CONTAINER_CLI = "container"


def command_has_forbidden_prune(argv: Iterable[str]) -> bool:
    tokens = list(argv)
    if len(tokens) < 3:
        return False
    if Path(tokens[0]).name == _APPLE_CONTAINER_CLI:
        return False
    rest = tokens[1:]
    return any(pair in _FORBIDDEN_PRUNE_VERBS for pair in zip(rest, rest[1:]))


def list_mocker_volumes_matching(project_prefix: str, *, runner: DockerRunner | None = None) -> tuple[str, ...]:
    if resolve_container_cli() != "mocker":
        return ()
    run = runner or run_docker_command
    list_result = run(["mocker", "volume", "ls"])
    if not list_result.succeeded:
        return ()
    pattern = re.compile(rf"{re.escape(project_prefix)}[-_][A-Za-z0-9._-]+")
    found: list[str] = []
    for line in list_result.stdout.splitlines():
        for match in pattern.finditer(line):
            volume_name = match.group(0)
            if volume_name not in found:
                found.append(volume_name)
    return tuple(found)


def compose_declared_volume_names(spec: DockerPlatformSpec) -> tuple[str, ...]:
    names: list[str] = []
    for compose_file in spec.compose_files:
        try:
            data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        volumes = data.get("volumes") if isinstance(data, dict) else None
        if isinstance(volumes, dict):
            names.extend(str(key) for key in volumes)
    return tuple(dict.fromkeys(names))


def expected_mocker_volume_names(project_name: str, spec: DockerPlatformSpec) -> tuple[str, ...]:
    names: list[str] = []
    for key in compose_declared_volume_names(spec):
        names.append(f"{project_name}-{key}")
        names.append(f"{project_name}_{key}")
    return tuple(names)


def sweep_leaked_mocker_volumes(
    project_name: str,
    spec: DockerPlatformSpec,
    *,
    runner: DockerRunner | None = None,
    dry_run: bool = False,
) -> tuple[str, ...]:
    if dry_run or resolve_container_cli() != "mocker":
        return ()
    run = runner or run_docker_command
    list_result = run(["mocker", "volume", "ls"])
    if not list_result.succeeded:
        return ()
    existing = {token for line in list_result.stdout.splitlines() for token in line.split()}
    removed: list[str] = []
    for volume_name in expected_mocker_volume_names(project_name, spec):
        if volume_name not in existing:
            continue
        rm_result = run(["mocker", "volume", "rm", volume_name], dry_run=dry_run)
        if rm_result.succeeded:
            removed.append(volume_name)
    return tuple(removed)


def run_docker_command(
    argv: list[str],
    *,
    dry_run: bool = False,
    timeout_s: int = 300,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> DockerCommandResult:
    argv_tuple = tuple(argv)
    if command_has_forbidden_prune(argv_tuple):
        return DockerCommandResult(
            argv=argv_tuple,
            returncode=2,
            stdout="",
            stderr="",
            error="refusing to run forbidden Docker prune command",
        )
    if dry_run:
        return DockerCommandResult(argv=argv_tuple, returncode=0, stdout="", stderr="", dry_run=True)

    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd or REPO_ROOT),
            env=command_env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        return DockerCommandResult(
            argv=argv_tuple,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except FileNotFoundError as exc:
        return DockerCommandResult(
            argv=argv_tuple,
            returncode=127,
            stdout="",
            stderr=str(exc),
            error="docker command not found",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return DockerCommandResult(
            argv=argv_tuple,
            returncode=124,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            error=f"docker command timed out after {timeout_s}s",
        )
    except subprocess.SubprocessError as exc:
        return DockerCommandResult(
            argv=argv_tuple,
            returncode=1,
            stdout="",
            stderr=str(exc),
            error="docker command failed before completion",
        )
