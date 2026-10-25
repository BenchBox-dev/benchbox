from __future__ import annotations

import contextlib
import importlib
import importlib.machinery
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

try:
    from importlib import metadata as importlib_metadata
except ImportError:
    import importlib_metadata

logger = logging.getLogger(__name__)


class DriverRuntimeStrategy(str, Enum):
    CURRENT_PROCESS = "current-process"
    ISOLATED_SITE_PACKAGES = "isolated-site-packages"
    EXTERNAL_RUNTIME = "external-runtime"


@dataclass(frozen=True)
class DriverRuntimeLocation:
    package: str
    version: str
    strategy: str
    runtime_path: str | None = None
    python_executable: str | None = None


@dataclass(frozen=True)
class DriverResolution:
    package: str
    requested: str | None
    resolved: str | None
    auto_install_used: bool
    actual: str | None = None
    runtime_strategy: str = DriverRuntimeStrategy.CURRENT_PROCESS.value
    runtime_path: str | None = None
    runtime_python_executable: str | None = None


def _normalize_package_name(package: str) -> str:
    return package.replace("_", "-")


def _normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _candidate_runtime_roots() -> list[Path]:
    configured = os.getenv("BENCHBOX_DRIVER_RUNTIME_ROOTS", "").strip()
    roots: list[Path] = []

    if configured:
        for entry in configured.split(os.pathsep):
            entry = entry.strip()
            if entry:
                roots.append(Path(entry))
    else:
        cwd = Path.cwd()
        roots.extend(
            [
                cwd / "benchmark_runs" / "driver_envs",
                cwd / "benchmark_runs" / "duckdb_version_envs",
            ]
        )

    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        deduped.append(root)
    return deduped


def _iter_site_packages(venv_root: Path) -> Iterable[Path]:
    yield from venv_root.glob("lib/python*/site-packages")
    windows_site_packages = venv_root / "Lib" / "site-packages"
    if windows_site_packages.exists():
        yield windows_site_packages


def _find_distribution_version(site_packages: Path, package_name: str) -> str | None:
    expected = _normalize_distribution_name(package_name)
    for dist in importlib_metadata.distributions(path=[str(site_packages)]):
        metadata_name = dist.metadata.get("Name") if dist.metadata else None
        if not metadata_name:
            continue
        if _normalize_distribution_name(metadata_name) == expected:
            return dist.version
    return None


_CPYTHON_ABI_RE = re.compile(r"\.cpython-(\d{2,3})")


def _validate_runtime_abi(site_packages: Path, package_name: str) -> bool:
    pkg_dir = site_packages / package_name.replace("-", "_")
    if not pkg_dir.is_dir():
        return True

    current_tag = f"{sys.version_info.major}{sys.version_info.minor}"
    has_tagged_extension = False

    for child in pkg_dir.iterdir():
        name = child.name
        if not child.is_file():
            continue
        if not (name.endswith(".so") or name.endswith(".pyd")):
            continue
        m = _CPYTHON_ABI_RE.search(name)
        if m is None:
            continue
        has_tagged_extension = True
        if m.group(1) == current_tag:
            return True

    return not has_tagged_extension


def discover_isolated_runtime(
    *,
    package_name: str,
    requested_version: str,
) -> DriverRuntimeLocation | None:
    requested = requested_version.strip()
    if not requested:
        return None

    normalized_package = _normalize_package_name(package_name)
    expected = _normalize_distribution_name(normalized_package)

    for root in _candidate_runtime_roots():
        if not root.exists():
            continue
        for candidate in root.iterdir():
            if not candidate.is_dir():
                continue
            python_executable = candidate / "bin" / "python"
            if not python_executable.exists():
                python_executable = candidate / "Scripts" / "python.exe"
            if not python_executable.exists():
                python_executable = None

            for site_packages in _iter_site_packages(candidate):
                if not site_packages.exists():
                    continue
                version = _find_distribution_version(site_packages, expected)
                if version == requested:
                    if not _validate_runtime_abi(site_packages, normalized_package):
                        logger.warning(
                            "Skipping isolated runtime at %s: ABI mismatch "
                            "(no extension files match current Python %d.%d). "
                            "Delete this runtime or rebuild for current Python.",
                            site_packages,
                            sys.version_info.major,
                            sys.version_info.minor,
                        )
                        continue
                    return DriverRuntimeLocation(
                        package=normalized_package,
                        version=version,
                        strategy=DriverRuntimeStrategy.ISOLATED_SITE_PACKAGES.value,
                        runtime_path=str(site_packages),
                        python_executable=str(python_executable) if python_executable else None,
                    )
    return None


def _purge_module_tree(module_name: str) -> None:
    prefix = f"{module_name}."
    for loaded in list(sys.modules.keys()):
        if loaded == module_name or loaded.startswith(prefix):
            sys.modules.pop(loaded, None)


def _module_origin(module: object) -> str | None:
    return str(getattr(module, "__file__", "")) or None


def _module_is_from_runtime(module: object, runtime_path: str) -> bool:
    origin = _module_origin(module)
    if not origin:
        return False
    try:
        return Path(origin).resolve().is_relative_to(Path(runtime_path).resolve())
    except Exception:
        return False


def load_driver_module(
    *,
    import_name: str,
    resolution: DriverResolution,
    strict_version_check: bool = True,
):
    if resolution.runtime_strategy != DriverRuntimeStrategy.ISOLATED_SITE_PACKAGES.value:
        if resolution.auto_install_used:
            existing = sys.modules.get(import_name)
            already_correct = (
                existing is not None
                and resolution.requested
                and getattr(existing, "__version__", None) == str(resolution.requested)
            )
            if not already_correct:
                _purge_module_tree(import_name)
                importlib.invalidate_caches()
        module = importlib.import_module(import_name)
    else:
        runtime_path = (resolution.runtime_path or "").strip()
        if not runtime_path:
            raise RuntimeError(
                f"Runtime strategy '{resolution.runtime_strategy}' requires runtime_path for module '{import_name}'."
            )
        runtime_dir = Path(runtime_path)
        if not runtime_dir.exists():
            raise RuntimeError(f"Runtime path does not exist for module '{import_name}': {runtime_path}")

        _purge_module_tree(import_name)
        sys.path.insert(0, runtime_path)
        try:
            module = importlib.import_module(import_name)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to import module '{import_name}' from isolated runtime '{runtime_path}'."
            ) from exc
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(runtime_path)

        if not _module_is_from_runtime(module, runtime_path):
            origin = _module_origin(module) or "unknown"
            raise RuntimeError(
                f"Module '{import_name}' was loaded from '{origin}', expected isolated runtime '{runtime_path}'."
            )

    if strict_version_check and resolution.requested:
        actual = getattr(module, "__version__", None)
        if actual and str(actual) != str(resolution.requested):
            raise RuntimeError(
                f"Module '{import_name}' loaded version {actual}, but requested version is {resolution.requested}."
            )

    return module


def _get_installed_version(package: str) -> str | None:
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return None


def _run_install_command(command: list[str]) -> None:
    logger.debug("Running install command: %s", " ".join(shlex.quote(part) for part in command))
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        output = result.stdout.strip()
        raise RuntimeError(
            f"Driver auto-install failed. Command: {' '.join(shlex.quote(part) for part in command)}\n{output}"
        )


def _should_auto_install(auto_install: bool) -> bool:
    if auto_install:
        return True
    env_flag = os.getenv("BENCHBOX_DRIVER_AUTO_INSTALL", "").strip().lower()
    return env_flag in {"1", "true", "yes", "on"}


def ensure_driver_version(
    *,
    package_name: str | None,
    requested_version: str | None,
    auto_install: bool = False,
    install_hint: str | None = None,
) -> DriverResolution:

    if not package_name:
        requested = (requested_version or "").strip() or None
        if requested is not None:
            raise RuntimeError(
                "driver_version was requested, but this platform does not declare a driver package "
                "for runtime isolation."
            )
        return DriverResolution(package="", requested=None, resolved=None, auto_install_used=False)

    normalized_package = _normalize_package_name(package_name)
    requested = (requested_version or "").strip() or None

    installed_version = _get_installed_version(normalized_package)
    logger.debug(
        "Detected driver package %s version %s (requested %s)",
        normalized_package,
        installed_version,
        requested,
    )

    if requested is None:
        if installed_version is None:
            hint = install_hint or f"uv add {normalized_package}"
            raise RuntimeError(
                f"Driver package '{normalized_package}' is not installed. "
                "Install it or specify a driver_version in the configuration." + f"\nSuggested command: {hint}"
            )
        return DriverResolution(
            package=normalized_package,
            requested=None,
            resolved=installed_version,
            auto_install_used=False,
            actual=installed_version,
            runtime_strategy=DriverRuntimeStrategy.CURRENT_PROCESS.value,
            runtime_path=None,
            runtime_python_executable=sys.executable,
        )

    if installed_version == requested:
        return DriverResolution(
            package=normalized_package,
            requested=requested,
            resolved=installed_version,
            auto_install_used=False,
            actual=installed_version,
            runtime_strategy=DriverRuntimeStrategy.CURRENT_PROCESS.value,
            runtime_path=None,
            runtime_python_executable=sys.executable,
        )

    isolated_runtime = discover_isolated_runtime(
        package_name=normalized_package,
        requested_version=requested,
    )
    if isolated_runtime is not None:
        return DriverResolution(
            package=normalized_package,
            requested=requested,
            resolved=isolated_runtime.version,
            actual=isolated_runtime.version,
            auto_install_used=False,
            runtime_strategy=isolated_runtime.strategy,
            runtime_path=isolated_runtime.runtime_path,
            runtime_python_executable=isolated_runtime.python_executable,
        )

    auto_install_enabled = _should_auto_install(auto_install)
    if not auto_install_enabled:
        hint = install_hint or f"uv add {normalized_package}=={requested}"
        raise RuntimeError(
            "Driver package '{package}' is at version {installed} but version {requested} was requested. "
            "Install the correct version or enable driver_auto_install.".format(
                package=normalized_package,
                installed=installed_version or "not installed",
                requested=requested,
            )
            + f"\nSuggested command: {hint}"
        )

    commands_to_try = [
        ["uv", "pip", "install", "--python", sys.executable, f"{normalized_package}=={requested}"],
        [sys.executable, "-m", "pip", "install", f"{normalized_package}=={requested}"],
    ]

    last_error: Exception | None = None
    for command in commands_to_try:
        try:
            _run_install_command(command)
            importlib.invalidate_caches()
            installed_version = _get_installed_version(normalized_package)
            if installed_version == requested:
                return DriverResolution(
                    package=normalized_package,
                    requested=requested,
                    resolved=installed_version,
                    auto_install_used=True,
                    actual=installed_version,
                    runtime_strategy=DriverRuntimeStrategy.CURRENT_PROCESS.value,
                    runtime_path=None,
                    runtime_python_executable=sys.executable,
                )
        except Exception as exc:
            last_error = exc
            logger.warning("Driver auto-install command failed: %s", exc)

    hint = install_hint or f"uv add {normalized_package}=={requested}"
    base_message = (
        f"Failed to install driver package '{normalized_package}' at version {requested}. "
        "Install the package manually and retry."
    )
    if last_error:
        base_message += f"\nReason: {last_error}"
    base_message += f"\nSuggested command: {hint}"
    raise RuntimeError(base_message)
