from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from benchbox.utils.printing import emit

_PYPROJECT_PATH = Path("pyproject.toml")
_UV_LOCK_PATH = Path("uv.lock")


class DependencyValidationError(RuntimeError):
    pass


def _load_toml(path: Path) -> Mapping[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _collect_locked_versions(lock_data: Mapping[str, object]) -> dict[str, set[Version]]:

    packages: dict[str, set[Version]] = {}
    package_list: list[object] = list(lock_data.get("package", []))
    for pkg in package_list:
        if not isinstance(pkg, Mapping):
            continue
        name_obj = pkg.get("name")
        version_obj = pkg.get("version")
        if not isinstance(name_obj, str) or not isinstance(version_obj, str):
            continue
        canonical = canonicalize_name(name_obj)
        packages.setdefault(canonical, set()).add(Version(version_obj))
    return packages


def _requirements_from_strings(values: Iterable[str]) -> list[Requirement]:
    requirements: list[Requirement] = []
    for raw in values:
        requirement = Requirement(raw)
        requirements.append(requirement)
    return requirements


def _validate_requirement(requirement: Requirement, versions: set[Version]) -> bool:
    if not versions:
        return False
    if requirement.specifier:
        return any(requirement.specifier.contains(version, prereleases=True) for version in versions)
    return True


def _format_requirement(requirement: Requirement) -> str:
    if requirement.specifier:
        return f"{requirement.name}{requirement.specifier}"
    return requirement.name


def validate_dependency_versions(
    pyproject_data: Mapping[str, object],
    lock_data: Mapping[str, object],
) -> list[str]:

    project_section = pyproject_data.get("project")
    if not isinstance(project_section, Mapping):
        raise DependencyValidationError("pyproject.toml missing [project] section")

    packages = _collect_locked_versions(lock_data)
    problems: list[str] = []

    declared_dependencies = project_section.get("dependencies", [])
    requirements = _requirements_from_strings(declared_dependencies)

    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        versions = packages.get(name, set())
        if not _validate_requirement(requirement, versions):
            problems.append(f"Missing satisfying lock entry for {_format_requirement(requirement)}")

    optional_deps = project_section.get("optional-dependencies", {})
    if isinstance(optional_deps, Mapping):
        for extra, entries in optional_deps.items():
            if not isinstance(entries, Sequence):
                continue
            for requirement in _requirements_from_strings(entries):
                name = canonicalize_name(requirement.name)
                versions = packages.get(name, set())
                if not _validate_requirement(requirement, versions):
                    problems.append(
                        f"Missing satisfying lock entry for {_format_requirement(requirement)} (extra: {extra})"
                    )

    return problems


def build_matrix_summary(
    pyproject_data: Mapping[str, object],
    lock_data: Mapping[str, object],
) -> dict[str, object]:

    project_section = pyproject_data.get("project")
    optional_deps = {}
    if isinstance(project_section, Mapping):
        raw_extras = project_section.get("optional-dependencies", {})
        if isinstance(raw_extras, Mapping):
            optional_deps = {
                extra: list(entries) for extra, entries in sorted(raw_extras.items()) if isinstance(entries, Sequence)
            }

    matrix = {
        "python_requires": lock_data.get("requires-python", "unspecified"),
        "resolution_markers": list(lock_data.get("resolution-markers", [])),
        "optional_dependencies": optional_deps,
    }
    return matrix


def _print_matrix(summary: Mapping[str, object]) -> None:
    python_range = summary.get("python_requires", "unspecified")
    markers: list[str] = list(summary.get("resolution_markers", []))
    optional = summary.get("optional_dependencies", {})

    emit("Python compatibility")
    emit(f"  Supported range: {python_range}")
    if markers:
        emit("  Solver markers:")
        for marker in markers:
            emit(f"    - {marker}")
    emit()

    if optional:
        emit("Optional dependency groups")
        for extra, deps in optional.items():
            printable = ", ".join(deps)
            emit(f"  * {extra}: {printable}")
    else:
        emit("No optional dependencies declared.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate BenchBox dependency declarations.")
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Display the compatibility matrix summary in addition to running validation.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=_PYPROJECT_PATH,
        help="Path to pyproject.toml",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=_UV_LOCK_PATH,
        help="Path to uv.lock",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    pyproject_data = _load_toml(args.pyproject)
    lock_data = _load_toml(args.lock)

    problems = validate_dependency_versions(pyproject_data, lock_data)
    if problems:
        for problem in problems:
            sys.stderr.write(f"{problem}\n")
        return 1

    if args.matrix:
        summary = build_matrix_summary(pyproject_data, lock_data)
        _print_matrix(summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
