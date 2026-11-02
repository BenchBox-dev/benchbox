from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_PHASES: tuple[str, ...] = (
    "preflight",
    "execute",
    "validate",
    "package",
    "report",
)

VALID_TERMINAL_STATES: tuple[str, ...] = (
    "local-stage",
    "cloud-uploaded",
    "draft-pr",
    "merged-to-published-results",
)

VALID_DOCKER_PLATFORM_SWITCH_MODES: tuple[str, ...] = ("off", "containers", "volumes", "images")
VALID_DOCKER_FIXED_CONTAINER_NAME_POLICIES: tuple[str, ...] = ("fail", "override", "allow")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ExecuteConfig:
    per_cell_timeout_s: int = 600
    per_cell_memory_limit_gib: float = 0.0
    early_stop_after_s: int = 180
    early_stop_on_failure: bool = True
    phases_arg: str = "load,power"
    compression: str | None = None
    extra_args: tuple[str, ...] = ()
    skip_unreachable: bool = True
    parallel_platforms: bool = False
    liveness_probe_timeout_s: float = 2.0
    official: bool = False
    streams: int | None = None
    seed: int | None = None
    platform_chunking: bool = False


@dataclass(frozen=True)
class OutputConfig:
    benchmark_runs_dir_template: str = "~/Developer/benchmark_runs"
    logs_dir_template: str = "~/Developer/benchmark_runs/logs/uat_{date}_{time}"
    submissions_dir_template: str = "~/Developer/benchmark_runs/submissions/{name}"
    explicitly_set: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PreflightConfig:
    free_space_min_gib: float = 5.0
    free_space_path: str | None = None
    docker_required: bool = False
    noisy_neighbor_warn_load: float = 8.0
    local_platforms_check: bool = False
    free_memory_min_gib: float = 2.0
    clickhouse_memory_limit: str = "8g"
    starrocks_memory_limit: str = "8g"
    docker_memory_reserve_gib: float = 0.0


@dataclass(frozen=True)
class CleanupConfig:
    preserve_datagen: bool = True
    prune_databases: bool = True
    docker_manage_platforms: bool = False
    docker_platform_switch: str = "off"
    docker_project_prefix: str = "benchbox-uat"
    docker_start_timeout_s: int = 300
    docker_fixed_container_name_policy: str = "fail"
    docker_settle_s: int = 10


@dataclass(frozen=True)
class MatrixFilterConfig:
    groups: tuple[str, ...] | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    include_was_specified: bool = False

    @property
    def uses_implicit_group_default(self) -> bool:
        return self.groups is None and not self.include_was_specified and not self.include


@dataclass(frozen=True)
class ScalesConfig:
    rungs: tuple[float, ...] = (0.01,)
    override: float | None = None

    @property
    def requested_rungs(self) -> tuple[float, ...]:
        return (self.override,) if self.override is not None else self.rungs


@dataclass(frozen=True)
class ValidateConfig:
    validator_clean_rate_floor: float = 0.80


@dataclass(frozen=True)
class PackageConfig:
    submit_terminal_state: str | None = None
    service: str | None = None


@dataclass(frozen=True)
class ReportConfig:
    matrix_summary_tsv: str = "matrix_summary.tsv"
    cross_scale_coverage_min_pairs: int | None = None


@dataclass(frozen=True)
class CompatibilityConfig:
    release_gate_runtime_envelopes: bool = False


@dataclass(frozen=True)
class UATConfig:
    name: str
    description: str = ""
    phases: tuple[str, ...] = ("preflight", "execute", "report")
    dry_run: bool = False
    platforms: MatrixFilterConfig = field(default_factory=MatrixFilterConfig)
    benchmarks: MatrixFilterConfig = field(default_factory=MatrixFilterConfig)
    scales: ScalesConfig = field(default_factory=ScalesConfig)
    execute: ExecuteConfig = field(default_factory=ExecuteConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    preflight: PreflightConfig = field(default_factory=PreflightConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    validate: ValidateConfig = field(default_factory=ValidateConfig)
    package: PackageConfig = field(default_factory=PackageConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    compatibility: CompatibilityConfig = field(default_factory=CompatibilityConfig)

    @property
    def disk_gate_enabled(self) -> bool:
        return self.preflight.free_space_min_gib > 0

    @property
    def memory_gate_enabled(self) -> bool:
        return self.preflight.free_memory_min_gib > 0


DISK_GATE_DISABLED_WARNING_PREFIX = "[disk-gate] DISABLED by config"
MEMORY_GATE_DISABLED_WARNING_PREFIX = "[memory-gate] DISABLED by config"


def disk_gate_disabled_warning(config: UATConfig) -> str | None:
    if config.disk_gate_enabled:
        return None
    return (
        f"{DISK_GATE_DISABLED_WARNING_PREFIX}: preflight.free_space_min_gib=0 -- "
        "the free-space floor and per-cell disk watch will NOT run for this sweep"
    )


def memory_gate_disabled_warning(config: UATConfig) -> str | None:
    if config.memory_gate_enabled:
        return None
    return (
        f"{MEMORY_GATE_DISABLED_WARNING_PREFIX}: preflight.free_memory_min_gib=0 -- "
        "the free-memory headroom gate will NOT run before starting Docker-managed platforms"
    )


ROOT_FIELDS = frozenset(
    {
        "name",
        "description",
        "phases",
        "dry_run",
        "platforms",
        "benchmarks",
        "scales",
        "execute",
        "output",
        "preflight",
        "cleanup",
        "validate",
        "package",
        "report",
        "compatibility",
    }
)


def _reject_unknown_fields(payload: dict[str, Any], allowed: frozenset[str], section: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"Unknown field(s) in `{section}`: {', '.join(unknown)}")


def _validate_phases(phases: list[str]) -> tuple[str, ...]:
    if not phases:
        raise ConfigError("`phases:` must be a non-empty list of strings")
    out: list[str] = []
    for entry in phases:
        if not isinstance(entry, str):
            raise ConfigError(f"`phases[]` entries must be strings, got {type(entry).__name__}")
        if entry not in VALID_PHASES:
            raise ConfigError(f"Unknown phase {entry!r}; valid: {sorted(VALID_PHASES)}")
        out.append(entry)
    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in out:
        if entry in seen:
            duplicates.append(entry)
        else:
            seen.add(entry)
    if duplicates:
        raise ConfigError(f"`phases:` contains duplicate entries: {sorted(set(duplicates))}")
    canonical_index = {phase: idx for idx, phase in enumerate(VALID_PHASES)}
    indices = [canonical_index[phase] for phase in out]
    if indices != sorted(indices):
        raise ConfigError(
            f"`phases:` entries must follow canonical order {list(VALID_PHASES)}; got {out} "
            "-- the orchestrator walks `phases:` literally, so an out-of-order list "
            "(e.g. `report` before `execute`) silently produces an empty report"
        )
    return tuple(out)


def _require_positive_int(payload: dict[str, Any], key: str, *, default: int, section: str) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ConfigError(f"`{section}.{key}` must be an int, got bool")
    if isinstance(value, int):
        coerced = value
    elif isinstance(value, str) and value.lstrip("-").isdigit():
        coerced = int(value)
    else:
        raise ConfigError(f"`{section}.{key}` must be an int, got {type(value).__name__}={value!r}")
    if coerced <= 0:
        raise ConfigError(f"`{section}.{key}` must be > 0")
    return coerced


def _require_nonnegative_float(payload: dict[str, Any], key: str, *, default: float, section: str) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ConfigError(f"`{section}.{key}` must be a number, got bool")
    try:
        coerced = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"`{section}.{key}` must be a number, got {type(value).__name__}={value!r}") from exc
    if not math.isfinite(coerced) or coerced < 0:
        raise ConfigError(f"`{section}.{key}` must be a finite number >= 0")
    return coerced


def _require_bool(payload: dict[str, Any], key: str, *, default: bool, section: str) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"`{section}.{key}` must be a bool, got {type(value).__name__}={value!r}")
    return value


def _require_nonempty_string(payload: dict[str, Any], key: str, *, default: str, section: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"`{section}.{key}` must be a non-empty string")
    return value.strip()


def _as_string_tuple(value: Any, *, section: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(entry, str) for entry in value):
        return tuple(value)
    raise ConfigError(f"`{section}.{key}` must be a string or list of strings")


def _validate_matrix_filter(payload: dict[str, Any] | None, *, section: str) -> MatrixFilterConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError(f"`{section}:` must be a mapping")
    _reject_unknown_fields(payload, frozenset({"groups", "include", "exclude"}), section)
    groups = None if "groups" not in payload else _as_string_tuple(payload.get("groups"), section=section, key="groups")
    return MatrixFilterConfig(
        groups=groups,
        include=_as_string_tuple(payload.get("include"), section=section, key="include"),
        exclude=_as_string_tuple(payload.get("exclude"), section=section, key="exclude"),
        include_was_specified="include" in payload,
    )


def _validate_scales(payload: dict[str, Any] | None) -> ScalesConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`scales:` must be a mapping")
    _reject_unknown_fields(payload, frozenset({"rungs", "override"}), "scales")
    override = payload.get("override")
    if isinstance(override, bool):
        raise ConfigError("`scales.override` must be a number, got bool")
    try:
        override_value = float(override) if override is not None else None
    except (TypeError, ValueError) as exc:
        raise ConfigError("`scales.override` must be a number") from exc

    rungs_raw = payload.get("rungs", [override_value] if override_value is not None else [0.01])
    if isinstance(rungs_raw, (str, bytes)) or not isinstance(rungs_raw, (list, tuple)):
        raise ConfigError("`scales.rungs` must be a list of numbers")
    if any(isinstance(value, bool) for value in rungs_raw):
        raise ConfigError("`scales.rungs` entries must be numbers, got bool")
    try:
        rungs = tuple(float(value) for value in rungs_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError("`scales.rungs` must be a list of numbers") from exc
    if not rungs:
        raise ConfigError("`scales.rungs` must be non-empty")
    if "rungs" in payload and override_value is not None:
        raise ConfigError(
            "`scales.rungs` and `scales.override` are mutually exclusive -- "
            "set only one (see _project/specs/uat-framework.md Section 3)"
        )
    return ScalesConfig(rungs=rungs, override=override_value)


def _validate_execute(payload: dict[str, Any]) -> ExecuteConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`execute:` must be a mapping")
    _reject_unknown_fields(
        payload,
        frozenset(
            {
                "per_cell_timeout_s",
                "per_cell_memory_limit_gib",
                "early_stop_after_s",
                "early_stop_on_failure",
                "phases_arg",
                "compression",
                "extra_args",
                "skip_unreachable",
                "parallel_platforms",
                "liveness_probe_timeout_s",
                "official",
                "streams",
                "seed",
                "platform_chunking",
            }
        ),
        "execute",
    )
    parallel_platforms = _require_bool(payload, "parallel_platforms", default=False, section="execute")
    if parallel_platforms:
        raise ConfigError(
            "`execute.parallel_platforms: true` is forbidden — UAT W3 line 222: "
            "concurrent platform runs contaminate timings"
        )
    timeout = _require_positive_int(payload, "per_cell_timeout_s", default=600, section="execute")
    early_after = _require_positive_int(payload, "early_stop_after_s", default=180, section="execute")
    extra_args_raw = payload.get("extra_args", ())
    if extra_args_raw is None:
        extra_args = ()
    elif isinstance(extra_args_raw, (list, tuple)) and all(isinstance(entry, str) for entry in extra_args_raw):
        extra_args = tuple(extra_args_raw)
    else:
        raise ConfigError("`execute.extra_args` must be a list of strings")
    phases_arg_raw = payload.get("phases_arg", "load,power")
    if not isinstance(phases_arg_raw, str):
        raise ConfigError(
            f"`execute.phases_arg` must be a string, got {type(phases_arg_raw).__name__}={phases_arg_raw!r}"
        )
    phases_arg = phases_arg_raw
    official = _require_bool(payload, "official", default=False, section="execute")
    streams = _optional_positive_int(payload, "streams", section="execute")
    seed = _optional_int(payload, "seed", section="execute")
    if streams is not None and not official:
        raise ConfigError("`execute.streams` requires `execute.official: true` — see tests.uat.throughput")
    wants_throughput = "throughput" in {phase.strip().lower() for phase in phases_arg.split(",")}
    if official and wants_throughput and streams is None:
        raise ConfigError(
            "`execute.streams` is required when `execute.official: true` and "
            "`execute.phases_arg` includes `throughput` — `run-official` itself "
            "rejects a throughput phase without --streams"
        )
    return ExecuteConfig(
        per_cell_timeout_s=timeout,
        per_cell_memory_limit_gib=_require_nonnegative_float(
            payload, "per_cell_memory_limit_gib", default=0.0, section="execute"
        ),
        early_stop_after_s=early_after,
        early_stop_on_failure=_require_bool(payload, "early_stop_on_failure", default=True, section="execute"),
        phases_arg=phases_arg,
        compression=payload.get("compression"),
        extra_args=extra_args,
        skip_unreachable=_require_bool(payload, "skip_unreachable", default=True, section="execute"),
        parallel_platforms=parallel_platforms,
        liveness_probe_timeout_s=_require_nonnegative_float(
            payload,
            "liveness_probe_timeout_s",
            default=2.0,
            section="execute",
        ),
        official=official,
        streams=streams,
        seed=seed,
        platform_chunking=_require_bool(payload, "platform_chunking", default=False, section="execute"),
    )


def _optional_positive_int(payload: dict[str, Any], key: str, *, section: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if isinstance(value, bool):
        raise ConfigError(f"`{section}.{key}` must be an int, got bool")
    if isinstance(value, int):
        coerced = value
    elif isinstance(value, str) and value.lstrip("-").isdigit():
        coerced = int(value)
    else:
        raise ConfigError(f"`{section}.{key}` must be an int, got {type(value).__name__}={value!r}")
    if coerced <= 0:
        raise ConfigError(f"`{section}.{key}` must be > 0")
    return coerced


def _optional_int(payload: dict[str, Any], key: str, *, section: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if isinstance(value, bool):
        raise ConfigError(f"`{section}.{key}` must be an int, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    raise ConfigError(f"`{section}.{key}` must be an int, got {type(value).__name__}={value!r}")


def _validate_output(payload: dict[str, Any]) -> OutputConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`output:` must be a mapping")
    template_keys = ("benchmark_runs_dir_template", "logs_dir_template", "submissions_dir_template")
    _reject_unknown_fields(payload, frozenset(template_keys), "output")
    for key in template_keys:
        if key in payload and not isinstance(payload[key], str):
            raise ConfigError(f"`output.{key}` must be a string, got {type(payload[key]).__name__}={payload[key]!r}")
    explicitly_set = frozenset(key for key in template_keys if key in payload)
    return OutputConfig(
        benchmark_runs_dir_template=payload.get(
            "benchmark_runs_dir_template",
            "~/Developer/benchmark_runs",
        ),
        logs_dir_template=payload.get(
            "logs_dir_template",
            "~/Developer/benchmark_runs/logs/uat_{date}_{time}",
        ),
        submissions_dir_template=payload.get(
            "submissions_dir_template",
            "~/Developer/benchmark_runs/submissions/{name}",
        ),
        explicitly_set=explicitly_set,
    )


def _validate_preflight(payload: dict[str, Any] | None) -> PreflightConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`preflight:` must be a mapping")
    _reject_unknown_fields(
        payload,
        frozenset(
            {
                "free_space_min_gib",
                "free_space_path",
                "docker_required",
                "noisy_neighbor_warn_load",
                "local_platforms_check",
                "free_memory_min_gib",
                "clickhouse_memory_limit",
                "starrocks_memory_limit",
                "docker_memory_reserve_gib",
            }
        ),
        "preflight",
    )
    free_space_path = payload.get("free_space_path")
    if free_space_path is not None and not isinstance(free_space_path, str):
        raise ConfigError("`preflight.free_space_path` must be a string")
    return PreflightConfig(
        free_space_min_gib=_require_nonnegative_float(
            payload,
            "free_space_min_gib",
            default=5.0,
            section="preflight",
        ),
        free_space_path=free_space_path,
        docker_required=_require_bool(payload, "docker_required", default=False, section="preflight"),
        noisy_neighbor_warn_load=_require_nonnegative_float(
            payload,
            "noisy_neighbor_warn_load",
            default=8.0,
            section="preflight",
        ),
        local_platforms_check=_require_bool(payload, "local_platforms_check", default=False, section="preflight"),
        free_memory_min_gib=_require_nonnegative_float(
            payload,
            "free_memory_min_gib",
            default=2.0,
            section="preflight",
        ),
        clickhouse_memory_limit=_require_nonempty_string(
            payload,
            "clickhouse_memory_limit",
            default="8g",
            section="preflight",
        ),
        starrocks_memory_limit=_require_nonempty_string(
            payload,
            "starrocks_memory_limit",
            default="8g",
            section="preflight",
        ),
        docker_memory_reserve_gib=_require_nonnegative_float(
            payload,
            "docker_memory_reserve_gib",
            default=0.0,
            section="preflight",
        ),
    )


def _validate_cleanup(payload: dict[str, Any] | None) -> CleanupConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`cleanup:` must be a mapping")
    _reject_unknown_fields(
        payload,
        frozenset(
            {
                "preserve_datagen",
                "prune_databases",
                "docker_manage_platforms",
                "docker_platform_switch",
                "docker_project_prefix",
                "docker_start_timeout_s",
                "docker_fixed_container_name_policy",
                "docker_settle_s",
            }
        ),
        "cleanup",
    )

    preserve_datagen = _require_bool(payload, "preserve_datagen", default=True, section="cleanup")
    if not preserve_datagen:
        raise ConfigError("`cleanup.preserve_datagen: false` is not supported by UAT automation")
    docker_manage_platforms = _require_bool(payload, "docker_manage_platforms", default=False, section="cleanup")
    docker_platform_switch = str(payload.get("docker_platform_switch", "off"))
    if docker_platform_switch not in VALID_DOCKER_PLATFORM_SWITCH_MODES:
        raise ConfigError(
            f"Unknown `cleanup.docker_platform_switch` {docker_platform_switch!r}; "
            f"valid: {sorted(VALID_DOCKER_PLATFORM_SWITCH_MODES)}"
        )
    if not docker_manage_platforms and docker_platform_switch != "off":
        raise ConfigError(
            "`cleanup.docker_platform_switch` must be 'off' when "
            "`cleanup.docker_manage_platforms` is false; otherwise cleanup is a no-op or unsafe"
        )

    docker_project_prefix = str(payload.get("docker_project_prefix", "benchbox-uat")).strip()
    if not docker_project_prefix:
        raise ConfigError("`cleanup.docker_project_prefix` must be a non-empty string")
    docker_fixed_container_name_policy = str(payload.get("docker_fixed_container_name_policy", "fail"))
    if docker_fixed_container_name_policy not in VALID_DOCKER_FIXED_CONTAINER_NAME_POLICIES:
        raise ConfigError(
            "Unknown `cleanup.docker_fixed_container_name_policy` "
            f"{docker_fixed_container_name_policy!r}; valid: {sorted(VALID_DOCKER_FIXED_CONTAINER_NAME_POLICIES)}"
        )

    return CleanupConfig(
        preserve_datagen=preserve_datagen,
        prune_databases=_require_bool(payload, "prune_databases", default=True, section="cleanup"),
        docker_manage_platforms=docker_manage_platforms,
        docker_platform_switch=docker_platform_switch,
        docker_project_prefix=docker_project_prefix,
        docker_start_timeout_s=_require_positive_int(
            payload,
            "docker_start_timeout_s",
            default=300,
            section="cleanup",
        ),
        docker_fixed_container_name_policy=docker_fixed_container_name_policy,
        docker_settle_s=_require_positive_int(
            payload,
            "docker_settle_s",
            default=10,
            section="cleanup",
        ),
    )


def _validate_validate(payload: dict[str, Any] | None) -> ValidateConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`validate:` must be a mapping")
    _reject_unknown_fields(payload, frozenset({"validator_clean_rate_floor"}), "validate")
    floor = _require_nonnegative_float(
        payload,
        "validator_clean_rate_floor",
        default=0.80,
        section="validate",
    )
    if floor > 1.0:
        raise ConfigError(f"`validate.validator_clean_rate_floor` must be in [0.0, 1.0], got {floor}")
    return ValidateConfig(validator_clean_rate_floor=floor)


def _validate_package(payload: dict[str, Any] | None) -> PackageConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`package:` must be a mapping")
    _reject_unknown_fields(payload, frozenset({"submit_terminal_state", "service"}), "package")
    state = payload.get("submit_terminal_state")
    if state is not None and not isinstance(state, str):
        raise ConfigError("`package.submit_terminal_state` must be a string")
    service = payload.get("service")
    if service is not None and not isinstance(service, str):
        raise ConfigError("`package.service` must be a string")
    return PackageConfig(submit_terminal_state=state, service=service)


def _validate_report(payload: dict[str, Any] | None) -> ReportConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`report:` must be a mapping")
    _reject_unknown_fields(payload, frozenset({"matrix_summary_tsv", "cross_scale_coverage_min_pairs"}), "report")
    matrix_summary_tsv = payload.get("matrix_summary_tsv", "matrix_summary.tsv")
    if not isinstance(matrix_summary_tsv, str):
        raise ConfigError("`report.matrix_summary_tsv` must be a string")
    floor = payload.get("cross_scale_coverage_min_pairs")
    if floor is not None:
        if isinstance(floor, bool) or not isinstance(floor, int):
            raise ConfigError("`report.cross_scale_coverage_min_pairs` must be an int or null")
        if floor < 0:
            raise ConfigError("`report.cross_scale_coverage_min_pairs` must be >= 0")
    return ReportConfig(matrix_summary_tsv=matrix_summary_tsv, cross_scale_coverage_min_pairs=floor)


def _validate_compatibility(payload: dict[str, Any] | None) -> CompatibilityConfig:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError("`compatibility:` must be a mapping")
    _reject_unknown_fields(payload, frozenset({"release_gate_runtime_envelopes"}), "compatibility")
    return CompatibilityConfig(
        release_gate_runtime_envelopes=_require_bool(
            payload,
            "release_gate_runtime_envelopes",
            default=False,
            section="compatibility",
        )
    )


def load_config(path: str | Path) -> UATConfig:
    import yaml

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not isinstance(payload, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(payload).__name__}")
    return validate_config(payload)


def validate_config(payload: dict[str, Any]) -> UATConfig:
    _reject_unknown_fields(payload, ROOT_FIELDS, "root")
    name = payload.get("name")
    if not name or not isinstance(name, str):
        raise ConfigError("`name:` is required and must be a non-empty string")
    phases = _validate_phases(payload.get("phases") or ["preflight", "execute", "report"])
    platforms = _validate_matrix_filter(payload.get("platforms"), section="platforms")
    benchmarks = _validate_matrix_filter(payload.get("benchmarks"), section="benchmarks")
    scales = _validate_scales(payload.get("scales"))
    execute = _validate_execute(payload.get("execute") or {})
    if execute.official:
        from tests.uat.throughput import TPC_ALLOWED_SCALE_FACTORS

        non_compliant = sorted(rung for rung in scales.rungs if rung not in TPC_ALLOWED_SCALE_FACTORS)
        if non_compliant:
            raise ConfigError(
                f"`scales.rungs` {non_compliant} not TPC-compliant for `execute.official: true` "
                f"(run-official requires one of {sorted(TPC_ALLOWED_SCALE_FACTORS)})"
            )
    output = _validate_output(payload.get("output") or {})
    preflight = _validate_preflight(payload.get("preflight"))
    cleanup = _validate_cleanup(payload.get("cleanup"))
    validate = _validate_validate(payload.get("validate"))
    package = _validate_package(payload.get("package"))
    if "package" in phases:
        if package.submit_terminal_state is None:
            raise ConfigError("`package.submit_terminal_state` is required when the `package` phase is enabled")
        if package.submit_terminal_state not in VALID_TERMINAL_STATES:
            raise ConfigError(
                f"`package.submit_terminal_state` {package.submit_terminal_state!r} not in "
                f"{sorted(VALID_TERMINAL_STATES)}"
            )
        if package.submit_terminal_state == "cloud-uploaded" and not package.service:
            raise ConfigError("`package.submit_terminal_state: cloud-uploaded` requires `package.service`")
    report = _validate_report(payload.get("report"))
    compatibility = _validate_compatibility(payload.get("compatibility"))
    return UATConfig(
        name=name,
        description=str(payload.get("description", "")),
        phases=phases,
        dry_run=_require_bool(payload, "dry_run", default=False, section="root"),
        platforms=platforms,
        benchmarks=benchmarks,
        scales=scales,
        execute=execute,
        output=output,
        preflight=preflight,
        cleanup=cleanup,
        validate=validate,
        package=package,
        report=report,
        compatibility=compatibility,
    )
