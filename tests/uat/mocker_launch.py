from __future__ import annotations

import os
import re
import shlex
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tests.uat import docker_assets

MOCKER_CLI = "mocker"
MOCKER_LAUNCH_PLATFORMS = frozenset({"doris", "singlestore"})
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

_VAR_RE = re.compile(r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?-)(?P<default>[^}]*))?\}")
_ESCAPED_DOLLAR = "\x00"
_HEALTH_POLL_INTERVAL_S = 5.0


@dataclass(frozen=True)
class MockerService:
    service: str
    container_name: str
    image: str
    platform: str | None = None
    memory: str | None = None
    cpus: str | None = None
    entrypoint: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    volumes: tuple[str, ...] = ()
    named_volumes: tuple[str, ...] = ()
    privileged: bool = False
    restart: str | None = None
    healthcheck: tuple[str, ...] = ()
    health_interval_s: float = _HEALTH_POLL_INTERVAL_S
    unsupported: tuple[str, ...] = field(default_factory=tuple)


def applies(spec: docker_assets.DockerPlatformSpec) -> bool:
    return spec.platform in MOCKER_LAUNCH_PLATFORMS and docker_assets.resolve_container_cli() == MOCKER_CLI


def container_name(project_name: str, service: str) -> str:
    return f"{project_name}-{service}-1"


def _expand(raw: object, env: Mapping[str, str]) -> str:
    text = str(raw).replace("$$", _ESCAPED_DOLLAR)

    def replace(match: re.Match[str]) -> str:
        var = match.group("var")
        value = env.get(var)
        op = match.group("op")
        if op == ":-":
            return value if value else match.group("default")
        if op == "-":
            return value if value is not None else match.group("default")
        if value is None:
            raise docker_assets.DockerAssetError(f"compose variable {var!r} is not set")
        return value

    return _VAR_RE.sub(replace, text).replace(_ESCAPED_DOLLAR, "$")


def _words(raw: object, env: Mapping[str, str]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, list):
        return tuple(_expand(part, env) for part in raw)
    return tuple(shlex.split(_expand(raw, env)))


def _environment(raw: object, env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    if isinstance(raw, dict):
        return tuple((str(key), _expand(value, env)) for key, value in raw.items())
    if isinstance(raw, list):
        pairs: list[tuple[str, str]] = []
        for item in raw:
            key, _, value = _expand(item, env).partition("=")
            pairs.append((key, value))
        return tuple(pairs)
    return ()


def _truthy(raw: object, env: Mapping[str, str]) -> bool:
    return _expand(raw, env).strip().lower() in {"1", "true", "yes", "on"}


def _healthcheck(raw: object, env: Mapping[str, str]) -> tuple[tuple[str, ...], float]:
    if not isinstance(raw, dict) or "test" not in raw:
        return (), _HEALTH_POLL_INTERVAL_S
    test = raw["test"]
    if isinstance(test, list):
        parts = [_expand(part, env) for part in test]
        if parts and parts[0] == "CMD-SHELL":
            argv = ("sh", "-c", " ".join(parts[1:]))
        elif parts and parts[0] == "CMD":
            argv = tuple(parts[1:])
        else:
            argv = ()
    else:
        argv = ("sh", "-c", _expand(test, env))
    interval = _HEALTH_POLL_INTERVAL_S
    raw_interval = str(raw.get("interval", "")).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", raw_interval)
    if match:
        interval = min(float(match.group(1)), 15.0)
    return argv, interval


def _memory(service: dict, env: Mapping[str, str]) -> str | None:
    raw = service.get("mem_limit")
    if not raw:
        deploy = service.get("deploy")
        resources = deploy.get("resources") if isinstance(deploy, dict) else None
        limits = resources.get("limits") if isinstance(resources, dict) else None
        raw = limits.get("memory") if isinstance(limits, dict) else None
    return _expand(raw, env).strip() if raw else None


def _cpus(service: dict, env: Mapping[str, str]) -> str | None:
    raw = service.get("cpus")
    if not raw:
        deploy = service.get("deploy")
        resources = deploy.get("resources") if isinstance(deploy, dict) else None
        limits = resources.get("limits") if isinstance(resources, dict) else None
        raw = limits.get("cpus") if isinstance(limits, dict) else None
    return _expand(raw, env).strip() if raw else None


_SUPPORTED_KEYS = frozenset(
    {
        "image",
        "platform",
        "mem_limit",
        "cpus",
        "deploy",
        "entrypoint",
        "command",
        "ports",
        "environment",
        "volumes",
        "privileged",
        "restart",
        "healthcheck",
    }
)


def _volume_arg(
    project_name: str, raw: object, declared_volumes: frozenset[str], env: Mapping[str, str]
) -> tuple[str, str | None]:
    text = _expand(raw, env)
    source, _, rest = text.partition(":")
    if source in declared_volumes:
        name = f"{project_name}-{source}"
        return f"{name}:{rest}", name
    return text, None


def build_services(
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[MockerService, ...]:
    environment = {**os.environ, **(env or {})}
    services: list[MockerService] = []
    for compose_file in spec.compose_files:
        try:
            document = yaml.safe_load(Path(compose_file).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise docker_assets.DockerAssetError(f"cannot read compose file {compose_file}: {exc}") from exc
        raw_services = document.get("services") if isinstance(document, dict) else None
        if not isinstance(raw_services, dict):
            continue
        declared_volumes = frozenset(
            str(key) for key in (document.get("volumes") or {}) if isinstance(document.get("volumes"), dict)
        )
        for name, service in raw_services.items():
            if not isinstance(service, dict) or (spec.services and name not in spec.services):
                continue
            image = service.get("image")
            if not image:
                raise docker_assets.DockerAssetError(f"service {name!r} in {compose_file} has no image to run")
            volumes: list[str] = []
            named: list[str] = []
            for raw_volume in service.get("volumes") or ():
                arg, volume_name = _volume_arg(project_name, raw_volume, declared_volumes, environment)
                volumes.append(arg)
                if volume_name:
                    named.append(volume_name)
            healthcheck, interval = _healthcheck(service.get("healthcheck"), environment)
            platform = service.get("platform")
            restart = service.get("restart")
            services.append(
                MockerService(
                    service=str(name),
                    container_name=container_name(project_name, str(name)),
                    image=_expand(image, environment),
                    platform=_expand(platform, environment) if platform else None,
                    memory=_memory(service, environment),
                    cpus=_cpus(service, environment),
                    entrypoint=_words(service.get("entrypoint"), environment),
                    command=_words(service.get("command"), environment),
                    ports=tuple(_expand(port, environment) for port in service.get("ports") or ()),
                    environment=_environment(service.get("environment"), environment),
                    volumes=tuple(volumes),
                    named_volumes=tuple(named),
                    privileged=_truthy(service.get("privileged"), environment) if "privileged" in service else False,
                    restart=_expand(restart, environment) if restart else None,
                    healthcheck=healthcheck,
                    health_interval_s=interval,
                    unsupported=tuple(sorted(set(service) - _SUPPORTED_KEYS)),
                )
            )
    if not services:
        raise docker_assets.DockerAssetError(f"no compose service found for platform {spec.platform!r}")
    unsupported = [f"{svc.service}: {', '.join(svc.unsupported)}" for svc in services if svc.unsupported]
    if unsupported:
        raise docker_assets.DockerAssetError(
            "mocker launch cannot translate compose setting(s) that mocker run has no flag for: "
            + "; ".join(unsupported)
        )
    return tuple(services)


def run_command(service: MockerService, project_name: str) -> list[str]:
    argv = [
        MOCKER_CLI,
        "run",
        "-d",
        "--name",
        service.container_name,
        "--label",
        f"{COMPOSE_PROJECT_LABEL}={project_name}",
        "--label",
        f"{COMPOSE_SERVICE_LABEL}={service.service}",
    ]
    if service.platform:
        argv.extend(["--platform", service.platform])
    if service.memory:
        argv.extend(["-m", service.memory])
    if service.cpus:
        argv.extend(["--cpus", service.cpus])
    if service.privileged:
        argv.append("--privileged")
    if service.restart:
        argv.extend(["--restart", service.restart])
    for port in service.ports:
        argv.extend(["-p", port])
    for key, value in service.environment:
        argv.extend(["-e", f"{key}={value}"])
    for volume in service.volumes:
        argv.extend(["-v", volume])
    if service.entrypoint:
        argv.extend(["--entrypoint", service.entrypoint[0]])
    argv.append(service.image)
    argv.extend(service.entrypoint[1:])
    argv.extend(service.command)
    return argv


def exec_command(project_name: str, service: str, inner: Sequence[str]) -> list[str]:
    return [MOCKER_CLI, "exec", container_name(project_name, service), *inner]


def container_ids_command(project_name: str) -> list[str]:
    return [MOCKER_CLI, "ps", "-a", "-q", "--filter", f"label={COMPOSE_PROJECT_LABEL}={project_name}"]


def logs_command(spec: docker_assets.DockerPlatformSpec, project_name: str, *, tail: int = 500) -> list[str]:
    return [MOCKER_CLI, "logs", "--tail", str(tail), container_name(project_name, spec.services[0])]


def _summary(argv: Sequence[str], limit: int = 160) -> str:
    text = shlex.join(argv)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _failure(result: docker_assets.DockerCommandResult, label: str) -> docker_assets.DockerCommandResult:
    detail = result.error or result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    return docker_assets.DockerCommandResult(
        argv=result.argv,
        returncode=result.returncode or 1,
        stdout=result.stdout,
        stderr=f"{label}: {detail}",
        timed_out=result.timed_out,
        error=result.error,
    )


def launch_stack(
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    *,
    env: Mapping[str, str] | None = None,
    runner: docker_assets.DockerRunner = docker_assets.run_docker_command,
    timeout_s: int = 300,
    cwd: Path | str | None = None,
    dry_run: bool = False,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> docker_assets.DockerCommandResult:
    services = build_services(spec, project_name, env=env)
    run_env = dict(env) if env else None
    work_dir = cwd or docker_assets.REPO_ROOT
    commands: list[list[str]] = []

    def call(argv: list[str], *, step_timeout: int | float) -> docker_assets.DockerCommandResult:
        commands.append(argv)
        return runner(argv, dry_run=dry_run, timeout_s=int(step_timeout), cwd=work_dir, env=run_env)

    for service in services:
        call([MOCKER_CLI, "rm", "-f", service.container_name], step_timeout=60)
        for volume in service.named_volumes:
            created = call([MOCKER_CLI, "volume", "create", volume], step_timeout=60)
            if not created.succeeded and "already exists" not in f"{created.stderr} {created.stdout}".lower():
                return _failure(created, f"mocker volume create {volume} failed")
        started = call(run_command(service, project_name), step_timeout=timeout_s)
        if not started.succeeded:
            return _failure(started, f"mocker run for service {service.service} failed")

    deadline = monotonic() + timeout_s
    for service in services:
        if not service.healthcheck or dry_run:
            continue
        last = None
        while True:
            last = call(exec_command(project_name, service.service, service.healthcheck), step_timeout=60)
            if last.succeeded:
                break
            if monotonic() >= deadline:
                detail = last.error or last.stderr.strip() or last.stdout.strip() or f"exit {last.returncode}"
                return docker_assets.DockerCommandResult(
                    argv=tuple(commands[-1]),
                    returncode=1,
                    stdout="",
                    stderr=f"service {service.service} did not become healthy within {timeout_s}s: {detail}",
                )
            sleep(service.health_interval_s)

    return docker_assets.DockerCommandResult(
        argv=tuple(commands[-1]) if commands else (),
        returncode=0,
        stdout="\n".join(_summary(argv) for argv in commands),
        stderr="",
        dry_run=dry_run,
    )


def down_commands(spec: docker_assets.DockerPlatformSpec, project_name: str, cleanup_mode: str) -> list[list[str]]:
    if cleanup_mode == "off":
        raise docker_assets.DockerAssetError("cleanup_mode='off' does not have a Docker teardown command")
    if cleanup_mode not in {"containers", "volumes", "images"}:
        raise docker_assets.DockerAssetError(
            f"Unknown Docker cleanup mode {cleanup_mode!r}; valid: containers, volumes, images"
        )
    return [[MOCKER_CLI, "rm", "-f", container_name(project_name, service)] for service in spec.services]


def teardown_stack(
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    cleanup_mode: str,
    *,
    runner: docker_assets.DockerRunner = docker_assets.run_docker_command,
    timeout_s: int = 120,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> docker_assets.DockerCommandResult:
    commands = down_commands(spec, project_name, cleanup_mode)
    run_env = dict(env) if env else None
    work_dir = cwd or docker_assets.REPO_ROOT
    for argv in commands:
        result = runner(argv, dry_run=dry_run, timeout_s=timeout_s, cwd=work_dir, env=run_env)
        absent = "not found" in f"{result.stderr} {result.stdout}".lower() or "no such" in f"{result.stderr}".lower()
        if not result.succeeded and not absent:
            return result
    if cleanup_mode in {"volumes", "images"}:
        docker_assets.sweep_leaked_mocker_volumes(project_name, spec, runner=runner, dry_run=dry_run)
    return docker_assets.DockerCommandResult(
        argv=tuple(commands[-1]),
        returncode=0,
        stdout="\n".join(_summary(argv) for argv in commands),
        stderr="",
        dry_run=dry_run,
    )
