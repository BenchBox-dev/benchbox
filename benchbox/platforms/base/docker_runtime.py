from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from benchbox.core.results.environment import ContainerEnvironment, PlatformRuntimeEnvironment


@dataclass(frozen=True)
class DockerRuntimeDiscoveryRequest:
    platform_name: str
    host: str | None = None
    port: int | str | None = None
    container_name: str | None = None
    service_names: tuple[str, ...] = ()
    image_names: tuple[str, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DockerCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


DockerCommandRunner = Callable[[Sequence[str]], DockerCommandResult]

_PLATFORM_HINTS: dict[str, dict[str, tuple[str, ...]]] = {
    "clickhouse": {"services": ("clickhouse", "clickhouse-server"), "images": ("clickhouse",)},
    "clickhouse-server": {"services": ("clickhouse", "clickhouse-server"), "images": ("clickhouse",)},
    "trino": {"services": ("trino",), "images": ("trino",)},
    "presto": {"services": ("presto",), "images": ("presto",)},
    "postgresql": {"services": ("postgres", "postgresql"), "images": ("postgres",)},
    "postgres": {"services": ("postgres", "postgresql"), "images": ("postgres",)},
    "starrocks": {"services": ("starrocks", "starrocks-fe"), "images": ("starrocks",)},
    "doris": {"services": ("doris", "doris-fe"), "images": ("doris",)},
    "singlestore": {"services": ("singlestore",), "images": ("singlestore", "memsql")},
    "databend": {"services": ("databend",), "images": ("databend",)},
}


def discover_docker_runtime(
    request: DockerRuntimeDiscoveryRequest,
    *,
    runner: DockerCommandRunner | None = None,
) -> dict[str, dict[str, Any]]:
    if request.host and not _is_localhost(request.host):
        return _unavailable_result("not_localhost", f"Host {request.host!r} is not local to the BenchBox client")

    runner = runner or _default_docker_runner
    version_result = _run_docker_command(runner, ("docker", "version", "--format", "{{json .}}"))
    if version_result["error"] is not None:
        return _unavailable_result(version_result["error"], version_result["message"])
    docker_engine_version = _parse_engine_version(version_result["stdout"])

    ps_result = _run_docker_command(runner, ("docker", "ps", "--format", "{{json .}}"))
    if ps_result["error"] is not None:
        return _unavailable_result(
            ps_result["error"], ps_result["message"], docker_engine_version=docker_engine_version
        )

    containers = _parse_ps_output(ps_result["stdout"])
    container_summary = _select_container(containers, request)
    if container_summary is None:
        return _unavailable_result(
            "container_not_found",
            "No running Docker container matched the platform discovery hints",
            docker_engine_version=docker_engine_version,
        )

    container_id = str(container_summary.get("ID") or container_summary.get("Id") or container_summary.get("Names"))
    inspect_result = _run_docker_command(runner, ("docker", "inspect", container_id))
    if inspect_result["error"] is not None:
        return _unavailable_result(
            inspect_result["error"],
            inspect_result["message"],
            docker_engine_version=docker_engine_version,
        )

    inspect_data = _parse_inspect_output(inspect_result["stdout"])
    if inspect_data is None:
        return _unavailable_result(
            "inspect_parse_error",
            "Docker inspect output was not a JSON object or singleton list",
            docker_engine_version=docker_engine_version,
        )

    return _available_result(inspect_data, docker_engine_version=docker_engine_version)


def docker_request_from_config(
    platform_name: str,
    config: Mapping[str, Any] | None,
) -> DockerRuntimeDiscoveryRequest:
    config = config or {}
    platform_key = platform_name.lower().replace(" ", "-")
    hints = _PLATFORM_HINTS.get(platform_key, {})
    service_names = _as_tuple(config.get("service_name")) + tuple(hints.get("services", ()))
    image_names = _as_tuple(config.get("image")) + tuple(hints.get("images", ()))
    labels = config.get("labels") if isinstance(config.get("labels"), Mapping) else {}
    return DockerRuntimeDiscoveryRequest(
        platform_name=platform_name,
        host=_first_value(config, ("host", "hostname", "server", "endpoint")),
        port=_first_value(config, ("port", "http_port", "host_port")),
        container_name=config.get("container_name"),
        service_names=service_names,
        image_names=image_names,
        labels=labels,
    )


def _default_docker_runner(args: Sequence[str]) -> DockerCommandResult:
    completed = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return DockerCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _run_docker_command(runner: DockerCommandRunner, args: Sequence[str]) -> dict[str, str | None]:
    try:
        result = runner(args)
    except FileNotFoundError:
        return {"stdout": "", "error": "docker_not_found", "message": "Docker CLI is not installed or not on PATH"}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "error": "docker_timeout", "message": "Docker CLI command timed out"}
    except PermissionError as exc:
        return {"stdout": "", "error": "permission_denied", "message": str(exc)}
    except Exception as exc:
        return {"stdout": "", "error": type(exc).__name__, "message": str(exc)}

    if result.returncode != 0:
        stderr = result.stderr.strip()
        error = "permission_denied" if "permission" in stderr.lower() else "docker_command_failed"
        return {"stdout": result.stdout, "error": error, "message": stderr or "Docker command failed"}
    return {"stdout": result.stdout, "error": None, "message": None}


def _available_result(
    inspect_data: Mapping[str, Any], *, docker_engine_version: str | None
) -> dict[str, dict[str, Any]]:
    container = _container_from_inspect(inspect_data, docker_engine_version=docker_engine_version)
    runtime = PlatformRuntimeEnvironment(
        runtime_type="docker_container",
        collection_status="available",
        source="observed",
        os=container.get("os"),
        arch=container.get("arch"),
        engine_host=container.get("container_name"),
    ).to_dict()
    return {"platform_runtime": runtime, "container": container}


def _unavailable_result(
    error_class: str,
    message: str,
    *,
    docker_engine_version: str | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        "platform_runtime": PlatformRuntimeEnvironment(
            runtime_type="unknown",
            collection_status="unavailable",
            source="unavailable",
            collection_error_class=error_class,
            collection_error_message=message,
        ).to_dict(),
        "container": ContainerEnvironment(
            collection_status="unavailable",
            source="unavailable",
            docker_engine_version=docker_engine_version,
            collection_errors=[{"type": error_class, "message": message}],
        ).to_dict(),
    }


def _container_from_inspect(
    inspect_data: Mapping[str, Any],
    *,
    docker_engine_version: str | None,
) -> dict[str, Any]:
    config = inspect_data.get("Config") if isinstance(inspect_data.get("Config"), Mapping) else {}
    host_config = inspect_data.get("HostConfig") if isinstance(inspect_data.get("HostConfig"), Mapping) else {}
    state = inspect_data.get("State") if isinstance(inspect_data.get("State"), Mapping) else {}
    return ContainerEnvironment(
        collection_status="available",
        source="observed",
        docker_engine_version=docker_engine_version,
        image=config.get("Image") or inspect_data.get("ImageName"),
        image_digest=_first_repo_digest(inspect_data),
        container_id=str(inspect_data.get("Id")) if inspect_data.get("Id") else None,
        container_name=str(inspect_data.get("Name", "")).lstrip("/") or None,
        os=inspect_data.get("Os") or inspect_data.get("OS"),
        arch=inspect_data.get("Architecture") or inspect_data.get("Arch"),
        requested_platform=inspect_data.get("Platform") or host_config.get("Platform"),
        cpu_limit=_cpu_limit(host_config),
        memory_limit=host_config.get("Memory") or None,
        port_mappings=_port_mappings(inspect_data),
        bind_mounts=_mounts(inspect_data, mount_type="bind"),
        volumes=_mounts(inspect_data, mount_type="volume"),
        collection_errors=[],
    ).to_dict() | ({"state": state.get("Status")} if state.get("Status") else {})


def _select_container(
    containers: list[Mapping[str, Any]],
    request: DockerRuntimeDiscoveryRequest,
) -> Mapping[str, Any] | None:
    hints = _request_hints(request)
    scored: list[tuple[int, Mapping[str, Any]]] = []
    for container in containers:
        score = _score_container(container, request, hints)
        if score > 0:
            scored.append((score, container))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _score_container(
    container: Mapping[str, Any],
    request: DockerRuntimeDiscoveryRequest,
    hints: dict[str, set[str]],
) -> int:
    score = 0
    names = str(container.get("Names") or container.get("Name") or "").lower()
    image = str(container.get("Image") or "").lower()
    labels = _parse_label_string(str(container.get("Labels") or ""))
    ports = str(container.get("Ports") or "")

    if request.container_name and request.container_name.lower() in names:
        score += 100
    if any(service and service in names for service in hints["services"]):
        score += 50
    compose_service = labels.get("com.docker.compose.service", "").lower()
    if compose_service and compose_service in hints["services"]:
        score += 50
    if any(image_hint and image_hint in image for image_hint in hints["images"]):
        score += 25
    if request.port and _ports_include_host_port(ports, str(request.port)):
        score += 25
    for key, value in request.labels.items():
        if labels.get(key) == value:
            score += 10
    return score


def _request_hints(request: DockerRuntimeDiscoveryRequest) -> dict[str, set[str]]:
    platform_key = request.platform_name.lower().replace(" ", "-")
    defaults = _PLATFORM_HINTS.get(platform_key, {})
    return {
        "services": {item.lower() for item in (*request.service_names, *defaults.get("services", ()))},
        "images": {item.lower() for item in (*request.image_names, *defaults.get("images", ()))},
    }


def _parse_engine_version(stdout: str) -> str | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    server = data.get("Server") if isinstance(data, Mapping) else None
    if isinstance(server, Mapping):
        return server.get("Version") or server.get("APIVersion")
    return None


def _parse_ps_output(stdout: str) -> list[Mapping[str, Any]]:
    containers = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            containers.append(parsed)
    return containers


def _parse_inspect_output(stdout: str) -> Mapping[str, Any] | None:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], Mapping):
        return parsed[0]
    if isinstance(parsed, Mapping):
        return parsed
    return None


def _is_localhost(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "::1" or normalized.startswith("[::1]"):
        return True
    parsed = urlparse(normalized if "://" in normalized else f"//{normalized}")
    host_name = parsed.hostname or normalized.split(":", 1)[0]
    return host_name in {"localhost", "127.0.0.1", "::1"}


def _ports_include_host_port(ports: str, host_port: str) -> bool:
    return f":{host_port}->" in ports or ports.startswith(f"{host_port}->")


def _parse_label_string(labels: str) -> dict[str, str]:
    parsed = {}
    for item in labels.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _port_mappings(inspect_data: Mapping[str, Any]) -> dict[str, Any]:
    network = inspect_data.get("NetworkSettings") if isinstance(inspect_data.get("NetworkSettings"), Mapping) else {}
    ports = network.get("Ports") if isinstance(network.get("Ports"), Mapping) else {}
    return {str(key): value for key, value in ports.items() if value}


def _mounts(inspect_data: Mapping[str, Any], *, mount_type: str) -> list[dict[str, Any]]:
    mounts = inspect_data.get("Mounts")
    if not isinstance(mounts, list):
        return []
    selected = []
    for mount in mounts:
        if not isinstance(mount, Mapping) or mount.get("Type") != mount_type:
            continue
        selected.append(
            {
                "source": mount.get("Source"),
                "destination": mount.get("Destination"),
                "mode": mount.get("Mode"),
                "rw": mount.get("RW"),
            }
        )
    return selected


def _cpu_limit(host_config: Mapping[str, Any]) -> int | float | str | None:
    nano_cpus = host_config.get("NanoCpus")
    if nano_cpus:
        return float(nano_cpus) / 1_000_000_000
    cpu_quota = host_config.get("CpuQuota")
    cpu_period = host_config.get("CpuPeriod")
    if cpu_quota and cpu_period:
        return float(cpu_quota) / float(cpu_period)
    return host_config.get("CpusetCpus") or None


def _first_repo_digest(inspect_data: Mapping[str, Any]) -> str | None:
    repo_digests = inspect_data.get("RepoDigests")
    if isinstance(repo_digests, list) and repo_digests:
        return str(repo_digests[0])
    return None


def _first_value(config: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = config.get(key)
        if value:
            return value
    return None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item)
    return (str(value),)


__all__ = [
    "DockerCommandResult",
    "DockerRuntimeDiscoveryRequest",
    "discover_docker_runtime",
    "docker_request_from_config",
]
