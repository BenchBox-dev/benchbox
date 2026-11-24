from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

DatagenBackend = Literal["legacy", "native"]

VALID_DATAGEN_BACKENDS: tuple[DatagenBackend, ...] = ("legacy", "native")


class DatagenBackendError(ValueError):
    pass


@dataclass(frozen=True)
class NativeBackendSpec:
    benchmark: str
    mechanism: Literal["tpcgen-cli"]
    implemented: bool = False
    required_extra: str | None = None
    required_executable: str | None = None
    required_package: str | None = None
    min_scale_factor: float | None = None
    require_integer_scale: bool = False
    unsupported_platforms: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class DatagenBackendSelection:
    benchmark: str
    backend: DatagenBackend
    source: Literal["default", "explicit"]
    mechanism: str | None = None
    backend_version: str | None = None


DEFAULT_DATAGEN_BACKENDS: Mapping[str, DatagenBackend] = MappingProxyType({})

_WINDOWS_ARM64_PLATFORMS: frozenset[tuple[str, str]] = frozenset(
    {
        ("windows", "arm64"),
        ("windows", "aarch64"),
    }
)

NATIVE_BACKEND_SPECS: Mapping[str, NativeBackendSpec] = MappingProxyType(
    {
        "tpch": NativeBackendSpec(
            benchmark="tpch",
            mechanism="tpcgen-cli",
            implemented=False,
            required_extra="native-datagen",
            required_executable="tpcgen-cli",
            required_package="tpcgen-cli",
            unsupported_platforms=_WINDOWS_ARM64_PLATFORMS,
        ),
        "tpcds": NativeBackendSpec(
            benchmark="tpcds",
            mechanism="tpcgen-cli",
            implemented=False,
            required_extra="native-datagen",
            required_executable="tpcgen-cli",
            required_package="tpcgen-cli",
            min_scale_factor=1.0,
            require_integer_scale=True,
            unsupported_platforms=_WINDOWS_ARM64_PLATFORMS,
        ),
    }
)


def normalize_datagen_backend(value: str) -> DatagenBackend:
    normalized = str(value).strip().lower()
    if normalized == "auto":
        raise DatagenBackendError(
            "Invalid --datagen-backend value 'auto': 'auto' mode is not supported. "
            "Choose 'legacy' or 'native', or omit the flag to use the benchmark default."
        )
    if normalized == "legacy":
        return "legacy"
    if normalized == "native":
        return "native"
    allowed = ", ".join(VALID_DATAGEN_BACKENDS)
    raise DatagenBackendError(f"Invalid --datagen-backend value {value!r}. Must be one of: {allowed}.")


def get_default_datagen_backend(benchmark: str | None) -> DatagenBackend:
    if not benchmark:
        return "legacy"
    return DEFAULT_DATAGEN_BACKENDS.get(str(benchmark).strip().lower(), "legacy")


def resolve_env_executable(
    executable: str,
    *,
    python_executable: str | Path | None = None,
) -> Path | None:
    py_path = Path(python_executable if python_executable is not None else sys.executable)
    bin_dir = py_path.parent
    candidates = [executable]
    if os.name == "nt" and not executable.lower().endswith(".exe"):
        candidates.insert(0, f"{executable}.exe")
    for name in candidates:
        candidate = bin_dir / name
        if not candidate.is_file():
            continue
        if os.name != "nt":
            if os.access(candidate, os.X_OK):
                return candidate
        elif candidate.suffix.lower() in {".exe", ".cmd", ".bat"}:
            return candidate
    return None


def resolve_installed_package_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def is_native_executable_available(
    executable: str,
    *,
    package_name: str | None = None,
    extra_checker: Callable[[str], bool] | None = None,
    python_executable: str | Path | None = None,
) -> bool:
    if extra_checker is not None:
        return bool(extra_checker(executable))
    if resolve_env_executable(executable, python_executable=python_executable) is None:
        return False
    if package_name is not None and resolve_installed_package_version(package_name) is None:
        return False
    return True


def _current_platform_key(
    platform_system: str | None,
    platform_machine: str | None,
) -> tuple[str, str]:
    system_name = (platform_system if platform_system is not None else platform.system()).strip().lower()
    machine_name = (platform_machine if platform_machine is not None else platform.machine()).strip().lower()
    return system_name, machine_name


def _validate_native_request(
    benchmark_key: str,
    *,
    scale_factor: float | None,
    platform_system: str | None,
    platform_machine: str | None,
    extra_checker: Callable[[str], bool] | None,
    version_resolver: Callable[[str], str | None] | None,
) -> tuple[NativeBackendSpec, str | None]:
    spec = NATIVE_BACKEND_SPECS.get(benchmark_key)
    if spec is None:
        raise DatagenBackendError(
            f"Benchmark '{benchmark_key}' does not support --datagen-backend native. "
            "Omit --datagen-backend or pass --datagen-backend legacy."
        )

    if scale_factor is not None:
        sf = float(scale_factor)
        if spec.min_scale_factor is not None and sf < spec.min_scale_factor:
            raise DatagenBackendError(
                f"Benchmark '{benchmark_key}' with --datagen-backend native requires "
                f"scale_factor >= {spec.min_scale_factor:g} (got {sf:g}). "
                "Use --datagen-backend legacy for sub-scale generation."
            )
        if spec.require_integer_scale and not sf.is_integer():
            raise DatagenBackendError(
                f"Benchmark '{benchmark_key}' with --datagen-backend native requires an integer scale factor "
                f"(got {sf:g}). Use --datagen-backend legacy for non-integer scales."
            )

    platform_key = _current_platform_key(platform_system, platform_machine)
    if platform_key in spec.unsupported_platforms:
        raise DatagenBackendError(
            f"Benchmark '{benchmark_key}' with --datagen-backend native is not supported on "
            f"{platform_key[0]} {platform_key[1]} (no published native wheel). "
            "Use --datagen-backend legacy on this platform."
        )

    if not spec.implemented:
        raise DatagenBackendError(
            f"Native data generation adapter for '{benchmark_key}' is not enabled in this build yet. "
            "Use --datagen-backend legacy."
        )

    if spec.required_executable and not is_native_executable_available(
        spec.required_executable,
        package_name=spec.required_package,
        extra_checker=extra_checker,
    ):
        extra_name = spec.required_extra or "native-datagen"
        raise DatagenBackendError(
            f"Benchmark '{benchmark_key}' with --datagen-backend native requires optional extra "
            f"'{extra_name}' ('{spec.required_executable}' was not found in this Python environment). "
            f"Install it with 'uv sync --extra {extra_name}' or 'pip install \"benchbox[{extra_name}]\"', "
            "or pass --datagen-backend legacy."
        )

    resolved_version: str | None = None
    if spec.required_package:
        resolver = version_resolver if version_resolver is not None else resolve_installed_package_version
        resolved_version = resolver(spec.required_package)

    return spec, resolved_version


def resolve_datagen_backend(
    benchmark: str | None,
    requested: str | None = None,
    *,
    scale_factor: float | None = None,
    platform_system: str | None = None,
    platform_machine: str | None = None,
    extra_checker: Callable[[str], bool] | None = None,
    version_resolver: Callable[[str], str | None] | None = None,
) -> DatagenBackendSelection:
    benchmark_key = str(benchmark).strip().lower() if benchmark else ""

    if requested is None:
        default_backend = get_default_datagen_backend(benchmark_key)
        if default_backend == "native":
            spec, version = _validate_native_request(
                benchmark_key,
                scale_factor=scale_factor,
                platform_system=platform_system,
                platform_machine=platform_machine,
                extra_checker=extra_checker,
                version_resolver=version_resolver,
            )
            return DatagenBackendSelection(
                benchmark=benchmark_key,
                backend="native",
                source="default",
                mechanism=spec.mechanism,
                backend_version=version,
            )
        return DatagenBackendSelection(
            benchmark=benchmark_key,
            backend="legacy",
            source="default",
            mechanism="legacy",
        )

    backend = normalize_datagen_backend(requested)
    if backend == "legacy":
        return DatagenBackendSelection(
            benchmark=benchmark_key,
            backend="legacy",
            source="explicit",
            mechanism="legacy",
        )

    if not benchmark_key:
        raise DatagenBackendError(
            "--datagen-backend native requires a benchmark name. Specify --benchmark or use --datagen-backend legacy."
        )

    spec, version = _validate_native_request(
        benchmark_key,
        scale_factor=scale_factor,
        platform_system=platform_system,
        platform_machine=platform_machine,
        extra_checker=extra_checker,
        version_resolver=version_resolver,
    )
    return DatagenBackendSelection(
        benchmark=benchmark_key,
        backend="native",
        source="explicit",
        mechanism=spec.mechanism,
        backend_version=version,
    )


def resolve_backend_output_dir(
    base_dir: Path,
    benchmark: str | None,
    *,
    backend: str | None,
    scale_factor: float | None = None,
    backend_version: str | None = None,
    output_format: str | None = None,
) -> Path:
    if backend is None or str(backend).strip().lower() == "legacy":
        return base_dir
    from benchbox.utils.datagen_version import is_byte_identical_backend_output

    if is_byte_identical_backend_output(
        benchmark,
        backend_version=backend_version,
        output_format=output_format,
        scale_factor=scale_factor,
    ):
        return base_dir
    backend_slug = str(backend).strip().lower()
    return base_dir.parent / f"{base_dir.name}__backend_{backend_slug}"


__all__ = [
    "DEFAULT_DATAGEN_BACKENDS",
    "NATIVE_BACKEND_SPECS",
    "VALID_DATAGEN_BACKENDS",
    "DatagenBackend",
    "DatagenBackendError",
    "DatagenBackendSelection",
    "NativeBackendSpec",
    "get_default_datagen_backend",
    "is_native_executable_available",
    "normalize_datagen_backend",
    "resolve_backend_output_dir",
    "resolve_datagen_backend",
    "resolve_env_executable",
    "resolve_installed_package_version",
]
