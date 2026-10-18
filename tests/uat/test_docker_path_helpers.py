from __future__ import annotations

from pathlib import Path

import pytest

from tests.uat.docker_path_helpers import (
    compose_path_ends_with,
    find_env_files_with_non_absolute_data_dir,
    find_nested_variable_defaults,
    find_non_flat_benchbox_data_dir_mounts,
)

pytestmark = pytest.mark.medium

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "compose_path",
    (
        "/workspace/BenchBox/docker/postgresql/docker-compose.yml",
        r"D:\a\BenchBox\BenchBox\docker\postgresql\docker-compose.yml",
    ),
)
def test_compose_path_ends_with_accepts_posix_and_windows_separators(compose_path: str) -> None:
    assert compose_path_ends_with(compose_path, "docker", "postgresql", "docker-compose.yml")


def test_compose_path_ends_with_compares_complete_components() -> None:
    compose_path = "/workspace/BenchBox/docker/not-postgresql/docker-compose.yml"

    assert not compose_path_ends_with(compose_path, "docker", "postgresql", "docker-compose.yml")


def test_find_nested_variable_defaults_flags_the_braced_nested_pattern(tmp_path: Path) -> None:
    nested = tmp_path / "docker-compose.yml"
    nested.write_text('    - "${BENCHBOX_DATA_DIR:-${PWD}/benchmark_runs}:/data:ro"\n', encoding="utf-8")
    flat = tmp_path / "sibling" / "docker-compose.yml"
    flat.parent.mkdir()
    flat.write_text('    - "${BENCHBOX_DATA_DIR:-./benchmark_runs}:/data:ro"\n', encoding="utf-8")

    assert find_nested_variable_defaults(tmp_path) == [nested]


def test_find_nested_variable_defaults_flags_the_brace_less_nested_pattern(tmp_path: Path) -> None:
    nested = tmp_path / "docker-compose.yml"
    nested.write_text('    - "${BENCHBOX_DATA_DIR:-$PWD/benchmark_runs}:/data:ro"\n', encoding="utf-8")

    assert find_nested_variable_defaults(tmp_path) == [nested]


def test_find_nested_variable_defaults_does_not_flag_the_required_variable_form(tmp_path: Path) -> None:
    required = tmp_path / "docker-compose.yml"
    required.write_text('    - "${BENCHBOX_DATA_DIR:?required}:/data:ro"\n', encoding="utf-8")

    assert find_nested_variable_defaults(tmp_path) == []


def test_find_nested_variable_defaults_covers_yaml_extension(tmp_path: Path) -> None:
    nested = tmp_path / "docker-compose.pg-duckdb.yaml"
    nested.write_text('    - "${BENCHBOX_DATA_DIR:-${PWD}/x}:/data:ro"\n', encoding="utf-8")

    assert find_nested_variable_defaults(tmp_path) == [nested]


def test_no_repo_compose_file_has_a_nested_variable_default() -> None:
    bad = find_nested_variable_defaults(REPO_ROOT / "docker")

    assert not bad, [str(p) for p in bad]


def test_find_non_flat_benchbox_data_dir_mounts_flags_a_default(tmp_path: Path) -> None:
    lakesail_dir = tmp_path / "lakesail"
    lakesail_dir.mkdir()
    (lakesail_dir / "docker-compose.yml").write_text(
        '    - "${BENCHBOX_DATA_DIR:-./benchmark_runs}:${BENCHBOX_DATA_DIR:-./benchmark_runs}:ro"\n',
        encoding="utf-8",
    )

    assert find_non_flat_benchbox_data_dir_mounts(tmp_path) == [lakesail_dir / "docker-compose.yml"]


def test_find_non_flat_benchbox_data_dir_mounts_flags_the_required_variable_form(tmp_path: Path) -> None:
    velox_dir = tmp_path / "velox"
    velox_dir.mkdir()
    (velox_dir / "docker-compose.yml").write_text(
        '    - "${BENCHBOX_DATA_DIR:?required}:${BENCHBOX_DATA_DIR:?required}:ro"\n',
        encoding="utf-8",
    )

    assert find_non_flat_benchbox_data_dir_mounts(tmp_path) == [velox_dir / "docker-compose.yml"]


@pytest.mark.parametrize(
    "reference",
    (
        "${BENCHBOX_DATA_DIR-./x}",
        "${BENCHBOX_DATA_DIR?msg}",
        "${BENCHBOX_DATA_DIR:+/alt}",
        "${BENCHBOX_DATA_DIR+/alt}",
        "${BENCHBOX_DATA_DIR:=./x}",
    ),
)
def test_find_non_flat_benchbox_data_dir_mounts_flags_colon_less_posix_forms(tmp_path: Path, reference: str) -> None:
    compose_dir = tmp_path / "lakesail"
    compose_dir.mkdir()
    (compose_dir / "docker-compose.yml").write_text(f'    - "{reference}:{reference}:ro"\n', encoding="utf-8")

    assert find_non_flat_benchbox_data_dir_mounts(tmp_path) == [compose_dir / "docker-compose.yml"]


def test_find_non_flat_benchbox_data_dir_mounts_accepts_the_bare_form(tmp_path: Path) -> None:
    lakesail_dir = tmp_path / "lakesail"
    lakesail_dir.mkdir()
    (lakesail_dir / "docker-compose.yml").write_text(
        '    - "${BENCHBOX_DATA_DIR}:${BENCHBOX_DATA_DIR}:ro"\n', encoding="utf-8"
    )

    assert find_non_flat_benchbox_data_dir_mounts(tmp_path) == []


def test_find_non_flat_benchbox_data_dir_mounts_ignores_a_longer_variable_name(tmp_path: Path) -> None:
    other_dir = tmp_path / "lakesail"
    other_dir.mkdir()
    (other_dir / "docker-compose.yml").write_text('    - "${BENCHBOX_DATA_DIRECTORY}:/data:ro"\n', encoding="utf-8")

    assert find_non_flat_benchbox_data_dir_mounts(tmp_path) == []


def test_find_non_flat_benchbox_data_dir_mounts_covers_any_platform_not_just_lakesail_and_velox(
    tmp_path: Path,
) -> None:
    other_dir = tmp_path / "gluten"
    other_dir.mkdir()
    (other_dir / "docker-compose.yml").write_text(
        '    - "${BENCHBOX_DATA_DIR:-./benchmark_runs}:/data:ro"\n', encoding="utf-8"
    )

    assert find_non_flat_benchbox_data_dir_mounts(tmp_path) == [other_dir / "docker-compose.yml"]


def test_no_repo_lakesail_or_velox_compose_file_has_a_non_flat_benchbox_data_dir_mount() -> None:
    bad = find_non_flat_benchbox_data_dir_mounts(REPO_ROOT / "docker")

    assert not bad, [str(p) for p in bad]


def test_find_env_files_with_non_absolute_data_dir_flags_a_relative_value(tmp_path: Path) -> None:
    stack_dir = tmp_path / "lakesail"
    stack_dir.mkdir()
    (stack_dir / ".env").write_text("BENCHBOX_DATA_DIR=./benchmark_runs\n", encoding="utf-8")

    assert find_env_files_with_non_absolute_data_dir(tmp_path) == [stack_dir / ".env"]


def test_find_env_files_with_non_absolute_data_dir_flags_an_empty_value(tmp_path: Path) -> None:
    stack_dir = tmp_path / "velox"
    stack_dir.mkdir()
    (stack_dir / ".env").write_text("BENCHBOX_DATA_DIR=\n", encoding="utf-8")

    assert find_env_files_with_non_absolute_data_dir(tmp_path) == [stack_dir / ".env"]


def test_find_env_files_with_non_absolute_data_dir_accepts_an_absolute_value(tmp_path: Path) -> None:
    stack_dir = tmp_path / "lakesail"
    stack_dir.mkdir()
    (stack_dir / ".env").write_text("BENCHBOX_DATA_DIR=/mnt/benchbox-data\n", encoding="utf-8")

    assert find_env_files_with_non_absolute_data_dir(tmp_path) == []


def test_find_env_files_with_non_absolute_data_dir_ignores_unrelated_env_entries(tmp_path: Path) -> None:
    stack_dir = tmp_path / "velox"
    stack_dir.mkdir()
    (stack_dir / ".env").write_text("VELOX_IMAGE_TAG=dev\n", encoding="utf-8")

    assert find_env_files_with_non_absolute_data_dir(tmp_path) == []


@pytest.mark.parametrize("filename", (".env.local", ".env.lakesail", "compose.env"))
def test_find_env_files_with_non_absolute_data_dir_covers_non_dotenv_literal_filenames(
    tmp_path: Path, filename: str
) -> None:
    stack_dir = tmp_path / "lakesail"
    stack_dir.mkdir()
    (stack_dir / filename).write_text("BENCHBOX_DATA_DIR=./benchmark_runs\n", encoding="utf-8")

    assert find_env_files_with_non_absolute_data_dir(tmp_path) == [stack_dir / filename]


def test_no_repo_env_file_under_docker_sets_a_non_absolute_benchbox_data_dir() -> None:
    bad = find_env_files_with_non_absolute_data_dir(REPO_ROOT / "docker")

    assert not bad, [str(p) for p in bad]
