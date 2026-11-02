# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
import sys
from collections.abc import Sequence
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Optional

import yaml


@lru_cache(maxsize=1)
def is_development_install() -> bool:
    import benchbox

    package_path = Path(benchbox.__file__).parent

    project_root = package_path.parent
    if (project_root / "pyproject.toml").exists():
        try:
            content = (project_root / "pyproject.toml").read_text(encoding="utf-8")
            if 'name = "benchbox"' in content:
                return True
        except Exception:
            pass

    return "site-packages" not in str(package_path)


@lru_cache(maxsize=1)
def is_uv_tool_environment() -> bool:
    normalized_executable = str(Path(sys.executable)).replace("\\", "/").lower()
    return "/uv/tools/" in normalized_executable


PLATFORM_TO_EXTRA: dict[str, str]


def get_install_command(extra: str) -> str:
    resolved_extra = PLATFORM_TO_EXTRA.get(extra.lower(), extra)

    if is_development_install():
        if is_uv_tool_environment():
            return f'uv pip install --python "{sys.executable}" "benchbox[{resolved_extra}]"'
        return f"uv sync --extra {resolved_extra}"
    else:
        return f'uv pip install "benchbox[{resolved_extra}]"'


def get_package_install_message(packages: str, description: str = "") -> str:
    prefix = f"{description} " if description else ""
    return (
        f"{prefix}Install with:\n  pip install {packages}  # standalone\n  uv add {packages}       # inside a project"
    )


def get_extra_install_message(extra_name: str, description: str = "") -> str:
    prefix = f"{description}\n" if description else ""
    return (
        f"{prefix}Install with:\n"
        f"  pip install 'benchbox[{extra_name}]'  # pip/venv\n"
        f"  uv add benchbox --extra {extra_name}  # uv project"
    )


class DependencyInfo:
    def __init__(
        self,
        name: str,
        description: str,
        packages: list[str],
        install_command: str,
        use_cases: list[str],
        platforms: list[str],
    ):
        self.name = name
        self.description = description
        self.packages = packages
        self.install_command = install_command
        self.use_cases = use_cases
        self.platforms = platforms

    @property
    def extra_name(self) -> str:
        marker = "--extra "
        index = self.install_command.find(marker)
        if index == -1:
            return self.name
        return self.install_command[index + len(marker) :].split()[0]

    def get_install_message(self) -> str:
        return get_extra_install_message(self.extra_name)


class InstallationScenario:
    def __init__(
        self,
        name: str,
        description: str,
        platforms: Sequence[str],
        dependency_groups: Sequence[str],
        notes: Optional[str] = None,
    ):
        self.name = name
        self.description = description
        self.platforms = list(platforms)
        self.dependency_groups = list(dependency_groups)
        self.notes = notes or ""

    def _extras_spec(self) -> str:
        if not self.dependency_groups:
            return ""
        return ",".join(self.dependency_groups)

    @property
    def extras_label(self) -> str:
        return self._extras_spec() or "core"

    @property
    def uv_command(self) -> str:
        extras = self._extras_spec()
        if extras:
            extra_flags = " ".join(f"--extra {e}" for e in extras.split(","))
            return f"uv add benchbox {extra_flags}"
        return "uv add benchbox"

    @property
    def uv_pip_command(self) -> str:
        extras = self._extras_spec()
        if extras:
            return f'uv pip install "benchbox[{extras}]"'
        return "uv pip install benchbox"

    @property
    def pip_command(self) -> str:
        extras = self._extras_spec()
        if extras:
            return f'python -m pip install "benchbox[{extras}]"'
        return "python -m pip install benchbox"

    @property
    def pipx_command(self) -> str:
        extras = self._extras_spec()
        if extras:
            return f'pipx install "benchbox[{extras}]"'
        return "pipx install benchbox"


def _load_dependency_payload() -> dict[str, Any]:
    with resources.files(__package__).joinpath("dependencies.yaml").open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("dependencies.yaml must contain a mapping")
    return payload


def _dependency_info_from_payload(entry: dict[str, Any]) -> DependencyInfo:
    return DependencyInfo(
        name=str(entry["name"]),
        description=str(entry["description"]),
        packages=list(entry["packages"]),
        install_command=str(entry["install_command"]),
        use_cases=list(entry["use_cases"]),
        platforms=list(entry["platforms"]),
    )


def _scenario_from_payload(entry: dict[str, Any]) -> InstallationScenario:
    return InstallationScenario(
        name=str(entry["name"]),
        description=str(entry["description"]),
        platforms=list(entry["platforms"]),
        dependency_groups=list(entry["dependency_groups"]),
        notes=str(entry.get("notes") or ""),
    )


_DEPENDENCY_PAYLOAD = _load_dependency_payload()
PLATFORM_TO_EXTRA = dict(_DEPENDENCY_PAYLOAD["platform_to_extra"])
DATAFRAME_DEPENDENCY_GROUPS: dict[str, DependencyInfo] = {
    name: _dependency_info_from_payload(entry)
    for name, entry in _DEPENDENCY_PAYLOAD["dataframe_dependency_groups"].items()
}
DEPENDENCY_GROUPS: dict[str, DependencyInfo] = {
    name: _dependency_info_from_payload(entry) for name, entry in _DEPENDENCY_PAYLOAD["dependency_groups"].items()
}
INSTALLATION_SCENARIOS: tuple[InstallationScenario, ...] = tuple(
    _scenario_from_payload(entry) for entry in _DEPENDENCY_PAYLOAD["installation_scenarios"]
)
PACKAGE_IMPORT_NAMES: dict[str, str] = dict(_DEPENDENCY_PAYLOAD["package_import_names"])


def check_platform_dependencies(platform: str, packages: Optional[Sequence[str]] = None) -> tuple[bool, list[str]]:
    if packages is None:
        dep_info = DEPENDENCY_GROUPS.get(platform.lower())
        platforms_packages: Sequence[str] = dep_info.packages if dep_info else ()
    else:
        platforms_packages = packages

    missing: list[str] = []
    for package in platforms_packages:
        original_cwd = os.getcwd()
        try:
            import_name = PACKAGE_IMPORT_NAMES.get(package, package.replace("-", "_"))
            __import__(import_name)
        except ImportError:
            missing.append(package)
        finally:
            os.chdir(original_cwd)

    return len(missing) == 0, missing


def get_dependency_error_message(platform: str, missing_packages: list[str]) -> str:
    platform_lower = platform.lower()
    dep_info = DEPENDENCY_GROUPS.get(platform_lower)

    if not dep_info:
        packages_str = ", ".join(missing_packages)
        return (
            f"Missing required dependencies for {platform}: {packages_str}\n"
            f"Install with: uv pip install {' '.join(missing_packages)}"
        )

    message_parts = [
        f"Missing dependencies for {platform} platform:",
        f"  Extra: benchbox[{dep_info.name}]",
        f"  Required packages: {', '.join(missing_packages)}",
        "",
        dep_info.get_install_message(),
        "",
        "This extra provides:",
        f"  • {dep_info.description}",
    ]

    if dep_info.use_cases:
        message_parts.extend(
            [
                f"  • Use cases: {', '.join(dep_info.use_cases)}",
            ]
        )

    if dep_info.platforms:
        message_parts.extend(
            [
                f"  • Supports: {', '.join(dep_info.platforms)}",
            ]
        )

    message_parts.extend(
        [
            "",
            "Bundle installations (recommended):",
            "  • For all cloud platforms: uv add benchbox --extra cloud",
            "  • For everything: uv add benchbox --extra all",
            "",
            "Alternative (pip-compatible):",
            '  • For all cloud platforms: uv pip install "benchbox[cloud]"',
            '  • For everything: uv pip install "benchbox[all]"',
            "",
            f"Need more guidance? Run: benchbox check-deps --platform {platform_lower}",
        ]
    )

    return "\n".join(message_parts)


def get_installation_recommendations(use_case: Optional[str] = None) -> list[str]:
    recommendations = []

    if use_case:
        use_case_lower = use_case.lower()
        if "cloud" in use_case_lower or "multi" in use_case_lower:
            recommendations.append("uv add benchbox --extra cloud  # All major cloud platforms")
        elif "databricks" in use_case_lower or "delta" in use_case_lower:
            recommendations.append("uv add benchbox --extra databricks  # Databricks + Unity Catalog")
        elif "bigquery" in use_case_lower or "google" in use_case_lower:
            recommendations.append("uv add benchbox --extra bigquery  # Google BigQuery + Cloud Storage")
        elif "redshift" in use_case_lower or "aws" in use_case_lower:
            recommendations.append("uv add benchbox --extra redshift  # Amazon Redshift + S3")
        elif "snowflake" in use_case_lower:
            recommendations.append("uv add benchbox --extra snowflake  # Snowflake cloud DW")
        elif "clickhouse" in use_case_lower:
            recommendations.append("uv add benchbox --extra clickhouse  # ClickHouse analytics")
        elif "presto" in use_case_lower:
            recommendations.append("uv add benchbox --extra presto  # PrestoDB distributed SQL")
        elif "trino" in use_case_lower:
            recommendations.append("uv add benchbox --extra trino  # Trino/Starburst distributed SQL")

    if not recommendations:
        recommendations.extend(
            [
                "uv add benchbox --extra cloud          # Major cloud platforms (recommended)",
                "uv add benchbox --extra all            # All platforms + features",
                "uv add benchbox --extra cloudstorage   # Cloud storage helpers only",
                "uv add benchbox --extra databricks     # Databricks only",
                "uv add benchbox --extra bigquery       # BigQuery only",
                "uv add benchbox --extra redshift       # Redshift only",
                "uv add benchbox --extra snowflake      # Snowflake only",
                "uv add benchbox --extra clickhouse     # ClickHouse only",
            ]
        )

    return recommendations


def list_available_dependency_groups() -> dict[str, DependencyInfo]:
    return DEPENDENCY_GROUPS.copy()


def get_dependency_group_packages(platform: str) -> list[str]:

    dep_info = DEPENDENCY_GROUPS.get(platform.lower())
    return list(dep_info.packages) if dep_info else []


def get_installation_scenarios() -> tuple[InstallationScenario, ...]:
    return INSTALLATION_SCENARIOS


def get_installation_matrix_rows() -> list[tuple[str, str, str, str, str, str]]:

    rows: list[tuple[str, str, str, str, str, str]] = []
    for scenario in INSTALLATION_SCENARIOS:
        platforms = ", ".join(scenario.platforms)
        rows.append(
            (
                scenario.name,
                platforms,
                scenario.extras_label,
                scenario.uv_command,
                scenario.pip_command,
                scenario.pipx_command,
            )
        )
    return rows


def validate_dependency_group(group_name: str) -> bool:
    return group_name.lower() in DEPENDENCY_GROUPS


def get_dependency_decision_tree() -> str:
    return """
BenchBox Dependency Installation Guide
=====================================

Choose your installation based on your needs:

Quick Start (Recommended)
   └── uv add benchbox --extra cloud
       • Includes: Databricks, BigQuery, Redshift, Snowflake
       • Best for: Most users, cloud platform comparison
       • Excludes: ClickHouse (add [all] if needed)
       Alternative: uv pip install "benchbox[cloud]"

Cloud Storage Paths
   └── uv add benchbox --extra cloudstorage
       • Enables: AWS S3, Google Cloud Storage, Azure Data Lake paths
       • Best for: Remote output directories or data staging without new adapters
       Alternative: uv pip install "benchbox[cloudstorage]"

Cloud Platform Specific
   ├── Databricks/Spark  → uv add benchbox --extra databricks
   ├── Google BigQuery   → uv add benchbox --extra bigquery
   ├── Amazon Redshift   → uv add benchbox --extra redshift
   └── Snowflake        → uv add benchbox --extra snowflake

Analytics Database
   └── ClickHouse       → uv add benchbox --extra clickhouse

Development/Testing
   ├── Everything       → uv add benchbox --extra all
   └── Core only        → uv add benchbox

Scenarios:
   • Multi-cloud comparison     → [cloud]
   • Single platform focus     → [platform-name]
   • Local development         → [all]
   • Minimal footprint        → benchbox (core only)
   • Maximum compatibility     → [all]

ℹ️  Core installation (just 'benchbox') includes DuckDB and works for:
   • Local benchmarking and testing
   • Data generation and query development
   • SQLite-based workflows

💡 Note: Add 'Alternative: uv pip install "benchbox[...]"' for pip-compatible syntax
"""


INSTALLATION_GUIDE_URL = "https://benchbox.dev/docs/usage/installation.html"


def find_missing_module(error: BaseException | None) -> str | None:
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ModuleNotFoundError) and current.name:
            if current.name != "benchbox" and not current.name.startswith("benchbox."):
                return current.name
        pending.append(getattr(current, "original_error", None))
        pending.append(current.__cause__)
    return None


class BenchmarkDependencyError(ImportError):
    def __init__(self, benchmark: str, module: str, extras: Sequence[str] = ()):
        self.benchmark = benchmark
        self.module = module
        self.extras = tuple(extras)
        self.summary = f"Benchmark '{benchmark}' requires the Python package '{module}', which is not installed."
        if self.extras:
            self.remedy = f"Install the '{','.join(self.extras)}' extra: {get_install_command(','.join(self.extras))}"
        else:
            self.remedy = (
                f"Install '{module}' in this environment. See the installation guide: {INSTALLATION_GUIDE_URL}"
            )
        super().__init__(f"{self.summary} {self.remedy}", name=module)


class BenchmarkLoadError(RuntimeError):
    def __init__(self, benchmark: str, reason: str):
        self.benchmark = benchmark
        self.reason = reason
        super().__init__(f"Benchmark '{benchmark}' could not be loaded: {reason}")


BENCHMARK_LOAD_ERRORS = (BenchmarkDependencyError, BenchmarkLoadError)
