import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).parents[3]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _declared_requirements(config: dict) -> list[str]:
    project = config["project"]
    requirements = list(project.get("dependencies", []))
    requirements.extend(
        requirement
        for group in project.get("optional-dependencies", {}).values()
        for requirement in group
        if isinstance(requirement, str)
    )
    requirements.extend(
        requirement
        for group in config.get("dependency-groups", {}).values()
        for requirement in group
        if isinstance(requirement, str)
    )
    return requirements


def _can_resolve_mcp_major(requirement: Requirement, major: int) -> bool:
    return Version(f"{major}.0.0") in requirement.specifier


@pytest.mark.parametrize(
    ("declaration", "resolvable_majors"),
    [
        ("mcp<2", {1}),
        ("mcp<=2", {1, 2}),
        ("mcp>=1,<3", {1, 2}),
        ("mcp>=2,<3", {2}),
    ],
)
def test_mcp_major_resolution_probe(declaration: str, resolvable_majors: set[int]) -> None:
    requirement = Requirement(declaration)
    assert {major for major in (1, 2, 3) if _can_resolve_mcp_major(requirement, major)} == resolvable_majors


def test_every_mcp_dependency_requires_major_version_2() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)

    mcp_requirements = [
        Requirement(value) for value in _declared_requirements(config) if Requirement(value).name == "mcp"
    ]

    assert mcp_requirements, "pyproject.toml must declare the MCP SDK"
    for requirement in mcp_requirements:
        assert not _can_resolve_mcp_major(requirement, 1), (
            f"MCP requirement {requirement!s} can resolve unsupported SDK 1 after the v2 migration"
        )
        assert _can_resolve_mcp_major(requirement, 2), f"MCP requirement {requirement!s} cannot resolve required SDK 2"
        assert not _can_resolve_mcp_major(requirement, 3), (
            f"MCP requirement {requirement!s} can resolve unreviewed SDK 3"
        )


