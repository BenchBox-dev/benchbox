from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from tests.uat import docker_assets

ENTRYPOINT_MARKER_PATH = "/tmp/.benchbox-entrypoint-applied"

MEMORY_SHORTFALL_TOLERANCE = 0.85

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

    @property
    def declares_anything(self) -> bool:
        return bool(self.memory_bytes or self.ulimits or self.platform or self.entrypoint)


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
                memory_bytes=_declared_memory(service, environment),
                ulimits=_declared_ulimits(service.get("ulimits"), environment),
                platform=_interpolate(platform, environment) if platform else None,
                entrypoint=bool(entrypoint),
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
    if declared.platform:
        accepted = _ARCHITECTURES.get(declared.platform)
        arch = observed.get("arch", "")
        if accepted is not None and arch not in accepted:
            violations.append(f"platform: declared {declared.platform}, container reports machine {arch or 'unknown'}")
    if declared.entrypoint and observed.get("entrypoint_marker") != "present":
        violations.append(f"entrypoint: declared entrypoint did not run ({ENTRYPOINT_MARKER_PATH} is absent)")
    return violations


def probe_command(spec: docker_assets.DockerPlatformSpec, project_name: str, service: str) -> list[str]:
    argv = docker_assets.compose_ps_command(spec, project_name)
    argv[-2:] = ["exec", "-T", service, "sh", "-c", PROBE_SCRIPT]
    return argv


DockerRunner = Callable[..., docker_assets.DockerCommandResult]


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
    if settings.platform:
        names.append("platform")
    return ", ".join(names)
