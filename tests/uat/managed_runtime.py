from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchbox.utils.resource_limits import MemorySnapshot
from tests.uat import docker_assets, mocker_launch
from tests.uat.clickhouse_memory import runtime_limit_matches_rung
from tests.uat.config import UATConfig

STARROCKS_MEMORY_LIMIT_ENV_VAR = "STARROCKS_MEMORY_LIMIT"
STARROCKS_DEFAULT_MEMORY_LIMIT = "8g"
STARROCKS_READINESS = (
    "sh",
    "-c",
    'mysql -h127.0.0.1 -P9030 -uroot --skip-password -e "SHOW FRONTENDS\\G" | '
    'grep -q "Alive: true" && mysql -h127.0.0.1 -P9030 -uroot --skip-password '
    '-e "SHOW BACKENDS\\G" | grep -q "Alive: true"',
)

CEDARDB_READINESS = (
    "sh",
    "-c",
    'PGPASSWORD="$CEDAR_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$CEDAR_USER" -d "$CEDAR_DB" -tAc "SELECT 1"',
)
PG_MOONCAKE_READINESS = (
    "sh",
    "-c",
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1"',
)
_COORDINATOR_QUERY_SCRIPT = """\
command -v curl >/dev/null 2>&1 || exit 127
r=$(curl -fsS -X POST -H "$USER_HEADER: benchbox" --data "SELECT 1" http://localhost:8080/v1/statement) || exit 1
i=0
while [ "$i" -lt 60 ]; do
  case "$r" in *'"error"'*) echo "$r" >&2; exit 1;; esac
  case "$r" in *'"data":[[1]]'*) exit 0;; esac
  n=$(echo "$r" | grep -o '"nextUri":"[^"]*"' | cut -d'"' -f4)
  [ -n "$n" ] || { echo "$r" >&2; exit 1; }
  sleep 1
  r=$(curl -fsS "http://localhost:8080/${n#*://*/}") || exit 1
  i=$((i+1))
done
exit 1
"""
PRESTO_READINESS = ("sh", "-c", f"USER_HEADER=X-Presto-User\n{_COORDINATOR_QUERY_SCRIPT}")
TRINO_READINESS = ("sh", "-c", f"USER_HEADER=X-Trino-User\n{_COORDINATOR_QUERY_SCRIPT}")

_APPLICATION_READINESS: dict[str, tuple[tuple[str, ...], float]] = {
    "starrocks": (STARROCKS_READINESS, 0.0),
    "cedardb": (CEDARDB_READINESS, 15.0),
    "pg-mooncake": (PG_MOONCAKE_READINESS, 15.0),
    "presto": (PRESTO_READINESS, 60.0),
    "trino": (TRINO_READINESS, 60.0),
}

_PROBE_TOOL_OPTIONAL = frozenset({"cedardb", "pg-mooncake", "presto", "trino"})
_COMMAND_NOT_FOUND_EXIT_CODES = frozenset({126, 127})

DockerRunner = Callable[..., docker_assets.DockerCommandResult]
RecordEvent = Callable[..., None]


def resolve_starrocks_memory_limit(
    configured: str | None = STARROCKS_DEFAULT_MEMORY_LIMIT, *, env: dict[str, str] | None = None
) -> tuple[str, int]:
    environment = os.environ if env is None else env
    raw = environment.get(STARROCKS_MEMORY_LIMIT_ENV_VAR, configured)
    if raw is None:
        raise docker_assets.DockerAssetError("StarRocks memory request is required")
    if not isinstance(raw, str) or not raw.strip():
        raise docker_assets.DockerAssetError("StarRocks memory request must be a non-empty string")
    normalized = raw.strip()
    return normalized, docker_assets.parse_memory_bytes(normalized)


def compose_environment(config: UATConfig, spec: docker_assets.DockerPlatformSpec, runs_dir: Path) -> dict[str, str]:
    starrocks_limit = (
        resolve_starrocks_memory_limit(config.preflight.starrocks_memory_limit)[0]
        if spec.platform == "starrocks"
        else None
    )
    return docker_assets.compose_environment(
        spec,
        benchmark_runs_dir=runs_dir,
        memory_limit=config.preflight.clickhouse_memory_limit,
        starrocks_memory_limit=starrocks_limit,
    )


def _result_message(result: docker_assets.DockerCommandResult) -> str:
    if result.dry_run:
        return "dry-run: command recorded but not executed"
    if result.succeeded:
        return "command completed successfully"
    return result.error or result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"


def _readiness_command(spec: docker_assets.DockerPlatformSpec, project_name: str) -> list[str] | None:
    entry = _APPLICATION_READINESS.get(spec.platform)
    if entry is None:
        return None
    argv = docker_assets.compose_ps_command(spec, project_name)
    argv[-2:] = ["exec", "-T", spec.services[0], *entry[0]]
    return argv


def _readiness_timeout_s(config: UATConfig, spec: docker_assets.DockerPlatformSpec) -> float:
    floor = _APPLICATION_READINESS[spec.platform][1]
    probe = config.execute.liveness_probe_timeout_s or 2.0
    return min(config.cleanup.docker_start_timeout_s, max(probe, floor))


def check_application_readiness(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    docker_runner: DockerRunner,
    docker_events: list[Any],
    record_event: RecordEvent,
    log_dir: Path | None,
    benchmark_runs_dir: Path,
    action: str = "application-readiness",
    retry_window_s: float = 0.0,
    retry_interval_s: float = 0.0,
    sleep_fn: Callable[[float], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    command = _readiness_command(spec, project_name)
    if command is None:
        return None
    interval = max(retry_interval_s, 1.0)
    attempts = max(1, int(retry_window_s / interval) + 1)
    base_timeout = _readiness_timeout_s(config, spec)
    deadline = clock() + max(retry_window_s, base_timeout)
    result = None
    for attempt in range(attempts):
        result = docker_runner(
            command,
            dry_run=config.dry_run,
            timeout_s=max(1.0, min(base_timeout, deadline - clock())),
            cwd=docker_assets.REPO_ROOT,
            env=compose_environment(config, spec, benchmark_runs_dir),
        )
        record_event(
            docker_events,
            log_dir=log_dir,
            platform=spec.platform,
            action=action,
            status="ok" if result.succeeded else "failed",
            project_name=project_name,
            message=f"attempt={attempt + 1}/{attempts} {_result_message(result)}",
            result=result,
        )
        if result.succeeded:
            return None
        if spec.platform in _PROBE_TOOL_OPTIONAL and result.returncode in _COMMAND_NOT_FOUND_EXIT_CODES:
            record_event(
                docker_events,
                log_dir=log_dir,
                platform=spec.platform,
                action=action,
                status="skipped",
                project_name=project_name,
                message=(
                    f"WARNING: query readiness probe tooling is missing in the {spec.platform} container "
                    f"(exit {result.returncode}); falling back to healthcheck and TCP readiness only"
                ),
            )
            return None
        if attempt + 1 < attempts and clock() < deadline:
            (sleep_fn or time.sleep)(interval)
        elif clock() >= deadline:
            break
    assert result is not None
    return f"UAT-managed Docker application readiness check failed for {spec.platform} project {project_name}: {_result_message(result)}"


def check_declared_settings(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    docker_runner: DockerRunner,
    docker_events: list[Any],
    record_event: RecordEvent,
    log_dir: Path | None,
    benchmark_runs_dir: Path,
) -> str | None:
    if config.dry_run:
        return None
    reason = verify_declared_settings(
        spec,
        project_name,
        env=compose_environment(config, spec, benchmark_runs_dir),
        runner=docker_runner,
        timeout_s=int(min(config.cleanup.docker_start_timeout_s, 60)),
    )
    record_event(
        docker_events,
        log_dir=log_dir,
        platform=spec.platform,
        action="declared-settings",
        status="ok" if reason is None else "failed",
        project_name=project_name,
        message="declared compose settings verified" if reason is None else reason,
    )
    if reason is None:
        return None
    return f"UAT-managed Docker declared-settings check failed for {spec.platform} project {project_name}: {reason}"


def reconcile_starrocks_resources(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    docker_runner: DockerRunner,
    docker_events: list[Any],
    record_event: RecordEvent,
    log_dir: Path | None,
) -> str | None:
    if config.dry_run or spec.platform != "starrocks" or docker_assets.resolve_container_cli() != "mocker":
        return None
    selected, selected_bytes = resolve_starrocks_memory_limit(config.preflight.starrocks_memory_limit)
    stats_result = docker_runner(
        docker_assets.compose_stats_command(spec, project_name),
        dry_run=False,
        timeout_s=min(config.cleanup.docker_start_timeout_s, 30),
        cwd=docker_assets.REPO_ROOT,
        env=compose_environment(config, spec, Path("/tmp")),
    )
    runtime_limit = docker_assets.parse_runtime_memory_limit(stats_result.stdout)
    if (
        stats_result.succeeded
        and runtime_limit is not None
        and runtime_limit_matches_rung(runtime_limit, selected_bytes / (1024**3), requested_bytes=selected_bytes)
    ):
        record_event(
            docker_events,
            log_dir=log_dir,
            platform=spec.platform,
            action="resource-reconcile",
            status="ok",
            project_name=project_name,
            message=f"requested={selected} runtime_limit_bytes={runtime_limit} already matches",
            result=stats_result,
        )
        return None
    result = docker_runner(
        [
            docker_assets.resolve_container_cli(),
            "update",
            "--memory",
            selected,
            f"{project_name}-{spec.services[0]}-1",
        ],
        dry_run=False,
        timeout_s=min(config.cleanup.docker_start_timeout_s, 30),
        cwd=docker_assets.REPO_ROOT,
        env=compose_environment(config, spec, Path("/tmp")),
    )
    record_event(
        docker_events,
        log_dir=log_dir,
        platform=spec.platform,
        action="resource-reconcile",
        status="ok" if result.succeeded else "failed",
        project_name=project_name,
        message=_result_message(result),
        result=result,
    )
    if result.succeeded:
        return None
    return f"UAT-managed StarRocks resource reconciliation failed for {project_name}: {_result_message(result)}"


def check_memory_admission(
    config: UATConfig,
    *,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    platform: str,
    docker_runner: DockerRunner,
    docker_events: list[Any],
    record_event: RecordEvent,
    log_dir: Path | None,
    memory_reader: Callable[[], MemorySnapshot],
) -> str | None:
    if platform not in {"clickhouse-server", "starrocks"}:
        return None
    if platform == "starrocks":
        selected_value, selected_bytes = resolve_starrocks_memory_limit(config.preflight.starrocks_memory_limit)
        reserve = 0.0
    else:
        selected_value, selected_bytes = docker_assets.resolve_clickhouse_memory_limit(
            config.preflight.clickhouse_memory_limit
        )
        reserve = config.preflight.docker_memory_reserve_gib
    result = docker_runner(
        docker_assets.compose_stats_command(spec, project_name),
        dry_run=False,
        timeout_s=config.cleanup.docker_start_timeout_s,
        cwd=docker_assets.REPO_ROOT,
        env=compose_environment(config, spec, Path("/tmp")),
    )
    runtime_limit = docker_assets.parse_runtime_memory_limit(result.stdout)
    record_event(
        docker_events,
        log_dir=log_dir,
        platform=platform,
        action="memory-admission",
        status="ok" if result.succeeded and runtime_limit is not None else "failed",
        project_name=project_name,
        message=(f"requested={selected_value} requested_bytes={selected_bytes} runtime_limit_bytes={runtime_limit}"),
        result=result,
    )
    if not result.succeeded:
        return f"{platform} runtime memory admission failed for {project_name}: stats could not be read: {_result_message(result)}"
    if runtime_limit is None:
        return f"{platform} runtime memory admission failed for {project_name}: stats did not report a memory limit"
    if not runtime_limit_matches_rung(runtime_limit, selected_bytes / (1024**3), requested_bytes=selected_bytes):
        return f"{platform} runtime memory admission failed for {project_name}: requested {selected_value} ({selected_bytes} bytes), runtime reported {runtime_limit} bytes"
    if platform != "clickhouse-server":
        return None
    snapshot = memory_reader()
    required = selected_bytes / (1024**3) + reserve
    if snapshot.free_gib is None:
        return f"ClickHouse runtime memory admission failed for {project_name}: host available memory could not be measured"
    if snapshot.free_gib < required:
        return f"ClickHouse runtime memory admission failed for {project_name}: {snapshot.free_gib:.2f} GiB available < {required:.2f} GiB required"
    return None


ENTRYPOINT_MARKER_PATH = "/tmp/.benchbox-entrypoint-applied"

MEMORY_SHORTFALL_TOLERANCE = 0.85
MEMORY_VERIFIED_BY_RUNTIME_STATS = frozenset({"clickhouse-server", "starrocks"})

_UNLIMITED = -1
_CGROUP_UNLIMITED_FLOOR = 1 << 60

_ULIMIT_PROBES: dict[str, tuple[str, int]] = {
    "memlock": ("l", 1024),
    "nofile": ("n", 1),
    "nproc": ("u", 1),
}

_ARCHITECTURES: dict[str, frozenset[str]] = {
    "linux/amd64": frozenset({"x86_64", "amd64"}),
    "linux/x86_64": frozenset({"x86_64", "amd64"}),
    "linux/arm64": frozenset({"aarch64", "arm64"}),
    "linux/arm64/v8": frozenset({"aarch64", "arm64"}),
    "linux/aarch64": frozenset({"aarch64", "arm64"}),
}

_INTERPOLATION_RE = re.compile(r"^\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?::?-(?P<default>.*))?\}$")

PROBE_SCRIPT = f"""\
cg=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null)
echo "cgroup_memory=${{cg:-unknown}}"
while read -r key value _; do
  if [ "$key" = "MemTotal:" ]; then echo "mem_total_kb=$value"; fi
done </proc/meminfo
echo "ulimit_l_soft=$(ulimit -S -l 2>/dev/null)"
echo "ulimit_l_hard=$(ulimit -H -l 2>/dev/null)"
echo "ulimit_n_soft=$(ulimit -S -n 2>/dev/null)"
echo "ulimit_n_hard=$(ulimit -H -n 2>/dev/null)"
echo "ulimit_u_soft=$(ulimit -S -u 2>/dev/null)"
echo "ulimit_u_hard=$(ulimit -H -u 2>/dev/null)"
echo "arch=$(uname -m)"
echo "cpus=$(nproc 2>/dev/null)"
if [ -e {ENTRYPOINT_MARKER_PATH} ]; then echo "entrypoint_marker=present"; else echo "entrypoint_marker=absent"; fi
"""


@dataclass(frozen=True)
class DeclaredUlimit:
    name: str
    soft: int
    hard: int


@dataclass(frozen=True)
class DeclaredServiceSettings:
    service: str
    memory_bytes: int | None = None
    ulimits: tuple[DeclaredUlimit, ...] = ()
    platform: str | None = None
    entrypoint: bool = False
    cpus: float | None = None

    @property
    def declares_anything(self) -> bool:
        return bool(self.memory_bytes or self.ulimits or self.platform or self.entrypoint or self.cpus)


def _interpolate(raw: object, env: Mapping[str, str]) -> str:
    text = str(raw).strip()
    match = _INTERPOLATION_RE.fullmatch(text)
    if match is None:
        return text
    var = match.group("var")
    resolved = env.get(var)
    if resolved:
        return resolved.strip()
    default = match.group("default")
    if default is not None:
        return default.strip()
    raise docker_assets.DockerAssetError(f"compose variable {var!r} is not set")


def _ulimit_value(raw: object, env: Mapping[str, str]) -> int:
    value = int(_interpolate(raw, env))
    return _UNLIMITED if value < 0 else value


def _declared_ulimits(block: object, env: Mapping[str, str]) -> tuple[DeclaredUlimit, ...]:
    if not isinstance(block, dict):
        return ()
    declared: list[DeclaredUlimit] = []
    for name, value in block.items():
        if name not in _ULIMIT_PROBES:
            raise docker_assets.DockerAssetError(
                f"compose ulimit {name!r} is declared but declared-settings verification supports only "
                f"{', '.join(sorted(_ULIMIT_PROBES))}"
            )
        if isinstance(value, dict):
            soft = _ulimit_value(value.get("soft", value.get("hard")), env)
            hard = _ulimit_value(value.get("hard", value.get("soft")), env)
        else:
            soft = hard = _ulimit_value(value, env)
        declared.append(DeclaredUlimit(str(name), soft, hard))
    return tuple(declared)


def _declared_memory(service: dict, env: Mapping[str, str]) -> int | None:
    raw = service.get("mem_limit")
    if not raw:
        deploy = service.get("deploy")
        resources = deploy.get("resources") if isinstance(deploy, dict) else None
        limits = resources.get("limits") if isinstance(resources, dict) else None
        raw = limits.get("memory") if isinstance(limits, dict) else None
    if not raw:
        return None
    return docker_assets.parse_memory_bytes(_interpolate(raw, env))


def _declared_cpus(service: dict, env: Mapping[str, str]) -> float | None:
    raw = service.get("cpus")
    if not raw:
        deploy = service.get("deploy")
        resources = deploy.get("resources") if isinstance(deploy, dict) else None
        limits = resources.get("limits") if isinstance(resources, dict) else None
        raw = limits.get("cpus") if isinstance(limits, dict) else None
    if not raw:
        return None
    return float(_interpolate(raw, env))


def _entrypoint_text(raw: object) -> str:
    if isinstance(raw, list):
        return "\n".join(str(part) for part in raw)
    return str(raw)


def compose_declared_settings(
    spec: docker_assets.DockerPlatformSpec, *, env: Mapping[str, str] | None = None
) -> dict[str, DeclaredServiceSettings]:
    environment = {**os.environ, **(env or {})}
    declared: dict[str, DeclaredServiceSettings] = {}
    for compose_file in spec.compose_files:
        try:
            document = yaml.safe_load(Path(compose_file).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        services = document.get("services") if isinstance(document, dict) else None
        if not isinstance(services, dict):
            continue
        for name, service in services.items():
            if not isinstance(service, dict) or (spec.services and name not in spec.services):
                continue
            entrypoint = service.get("entrypoint")
            if entrypoint and ENTRYPOINT_MARKER_PATH not in _entrypoint_text(entrypoint):
                raise docker_assets.DockerAssetError(
                    f"service {name!r} in {compose_file} declares an entrypoint that does not create "
                    f"{ENTRYPOINT_MARKER_PATH}, so a dropped entrypoint cannot be detected"
                )
            platform = service.get("platform")
            settings = DeclaredServiceSettings(
                service=str(name),
                memory_bytes=None
                if spec.platform in MEMORY_VERIFIED_BY_RUNTIME_STATS
                else _declared_memory(service, environment),
                ulimits=_declared_ulimits(service.get("ulimits"), environment),
                platform=_interpolate(platform, environment) if platform else None,
                entrypoint=bool(entrypoint),
                cpus=_declared_cpus(service, environment),
            )
            if settings.declares_anything:
                declared[str(name)] = settings
    return declared


def parse_probe_output(text: str) -> dict[str, str]:
    observed: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            observed[key.strip()] = value.strip()
    return observed


def _effective_memory_bytes(observed: Mapping[str, str]) -> int | None:
    candidates: list[int] = []
    cgroup = observed.get("cgroup_memory", "")
    if cgroup.isdigit() and int(cgroup) < _CGROUP_UNLIMITED_FLOOR:
        candidates.append(int(cgroup))
    total_kb = observed.get("mem_total_kb", "")
    if total_kb.isdigit():
        candidates.append(int(total_kb) * 1024)
    return min(candidates) if candidates else None


def _format_gib(value: int) -> str:
    return f"{value / 1024**3:.2f} GiB"


def _observed_limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    if raw == "unlimited":
        return _UNLIMITED
    return int(raw) if raw.isdigit() else None


def _display_limit(value: int) -> str:
    return "unlimited" if value == _UNLIMITED else str(value)


def _ulimit_violation(declared: DeclaredUlimit, observed: Mapping[str, str]) -> str | None:
    flag, unit = _ULIMIT_PROBES[declared.name]
    soft_raw = observed.get(f"ulimit_{flag}_soft")
    hard_raw = observed.get(f"ulimit_{flag}_hard")
    soft = _observed_limit(soft_raw)
    hard = _observed_limit(hard_raw)
    if soft is None or hard is None:
        return f"ulimit {declared.name}: the container did not report its limit (soft={soft_raw!r} hard={hard_raw!r})"
    for label, want, got in (("soft", declared.soft, soft), ("hard", declared.hard, hard)):
        if want == _UNLIMITED:
            short = got != _UNLIMITED
        else:
            needed = -(-want // unit)
            short = got != _UNLIMITED and got < needed
            want = needed
        if short:
            return (
                f"ulimit {declared.name} {label}: declared {_display_limit(want)}, "
                f"container has {_display_limit(got)} (units of the shell's `ulimit -{flag}`)"
            )
    return None


def declared_settings_violations(declared: DeclaredServiceSettings, observed: Mapping[str, str]) -> list[str]:
    violations: list[str] = []
    if declared.memory_bytes:
        effective = _effective_memory_bytes(observed)
        if effective is None:
            violations.append("memory: the container did not report a memory limit")
        elif effective < declared.memory_bytes * MEMORY_SHORTFALL_TOLERANCE:
            violations.append(
                f"memory: declared {_format_gib(declared.memory_bytes)}, container sees {_format_gib(effective)}"
            )
    for ulimit in declared.ulimits:
        violation = _ulimit_violation(ulimit, observed)
        if violation:
            violations.append(violation)
    if declared.cpus:
        raw_cpus = observed.get("cpus", "")
        if not raw_cpus.isdigit():
            violations.append("cpus: the container did not report its CPU count")
        elif int(raw_cpus) < declared.cpus:
            violations.append(f"cpus: declared {declared.cpus:g}, container sees {raw_cpus}")
    if declared.platform:
        accepted = _ARCHITECTURES.get(declared.platform)
        arch = observed.get("arch", "")
        if accepted is not None and arch not in accepted:
            violations.append(f"platform: declared {declared.platform}, container reports machine {arch or 'unknown'}")
    if declared.entrypoint and observed.get("entrypoint_marker") != "present":
        violations.append(f"entrypoint: declared entrypoint did not run ({ENTRYPOINT_MARKER_PATH} is absent)")
    return violations


def probe_command(spec: docker_assets.DockerPlatformSpec, project_name: str, service: str) -> list[str]:
    if mocker_launch.applies(spec):
        return mocker_launch.exec_command(project_name, service, ["sh", "-c", PROBE_SCRIPT])
    argv = docker_assets.compose_ps_command(spec, project_name)
    argv[-2:] = ["exec", "-T", service, "sh", "-c", PROBE_SCRIPT]
    return argv


def verify_declared_settings(
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    *,
    env: Mapping[str, str] | None = None,
    runner: DockerRunner = docker_assets.run_docker_command,
    timeout_s: int = 60,
    cwd: Path | str | None = None,
) -> str | None:
    try:
        declared = compose_declared_settings(spec, env=env)
    except docker_assets.DockerAssetError as exc:
        return f"declared-settings check for {spec.platform} could not read the compose declarations: {exc}"
    cli = docker_assets.resolve_container_cli()
    problems: list[str] = []
    for service, settings in declared.items():
        result = runner(
            probe_command(spec, project_name, service),
            dry_run=False,
            timeout_s=timeout_s,
            cwd=cwd or docker_assets.REPO_ROOT,
            env=dict(env) if env else None,
        )
        if not result.succeeded:
            detail = result.error or result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            problems.append(
                f"service {service}: could not read the container's effective settings to verify "
                f"{_declared_names(settings)}: {detail}"
            )
            continue
        violations = declared_settings_violations(settings, parse_probe_output(result.stdout))
        if violations:
            problems.append(f"service {service}: " + "; ".join(violations))
    if not problems:
        return None
    return (
        f"container engine {cli} did not apply the compose settings declared for {spec.platform} "
        f"project {project_name}: " + " | ".join(problems)
    )


def _declared_names(settings: DeclaredServiceSettings) -> str:
    names: list[str] = []
    if settings.entrypoint:
        names.append("entrypoint")
    if settings.memory_bytes:
        names.append("memory")
    if settings.ulimits:
        names.extend(f"ulimit {limit.name}" for limit in settings.ulimits)
    if settings.cpus:
        names.append("cpus")
    if settings.platform:
        names.append("platform")
    return ", ".join(names)
