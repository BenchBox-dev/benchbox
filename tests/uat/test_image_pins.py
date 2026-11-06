from __future__ import annotations

from pathlib import Path

import pytest

from tests.uat import image_pins

pytestmark = pytest.mark.medium

REPO_ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "ab" * 32


def _compose(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _violations(root: Path) -> list[str]:
    return [f"{v.path.name}:{v.line}:{v.reference}" for v in image_pins.find_floating_images(root)]


def test_floating_latest_tag_is_rejected_with_its_line(tmp_path):
    _compose(
        tmp_path,
        "docker/singlestore/docker-compose.yml",
        "services:\n  s:\n    image: ghcr.io/singlestore-labs/singlestoredb-dev:latest\n",
    )

    assert _violations(tmp_path) == ["docker-compose.yml:3:ghcr.io/singlestore-labs/singlestoredb-dev:latest"]


def test_digest_pinned_images_pass_with_or_without_a_tag(tmp_path):
    _compose(
        tmp_path,
        "docker/a/docker-compose.yml",
        f"services:\n  a:\n    image: repo/a:1.2@{DIGEST}\n  b:\n    image: repo/b@{DIGEST}\n",
    )

    assert image_pins.find_floating_images(tmp_path) == []


def test_both_yml_and_yaml_compose_variants_are_checked(tmp_path):
    _compose(tmp_path, "docker/x/docker-compose.pg.yaml", "services:\n  x:\n    image: repo/x:1\n")
    _compose(tmp_path, "docker/x/docker-compose.pg.yml", "services:\n  y:\n    image: repo/y:1\n")

    assert sorted(_violations(tmp_path)) == [
        "docker-compose.pg.yaml:3:repo/x:1",
        "docker-compose.pg.yml:3:repo/y:1",
    ]


def test_environment_default_is_the_reference_that_must_be_pinned(tmp_path):
    _compose(
        tmp_path,
        "docker/a/docker-compose.yml",
        f"services:\n  a:\n    image: ${{A_IMAGE:-repo/a:1@{DIGEST}}}\n  b:\n    image: ${{B_IMAGE:-repo/b:1}}\n",
    )

    assert _violations(tmp_path) == ["docker-compose.yml:5:repo/b:1"]


def test_environment_reference_without_default_is_rejected(tmp_path):
    _compose(tmp_path, "docker/a/docker-compose.yml", "services:\n  a:\n    image: ${A_IMAGE}\n")

    assert len(image_pins.find_floating_images(tmp_path)) == 1


def test_truncated_digest_is_not_a_pin(tmp_path):
    _compose(tmp_path, "docker/a/docker-compose.yml", "services:\n  a:\n    image: repo/a:1@sha256:abc123\n")

    assert len(image_pins.find_floating_images(tmp_path)) == 1


def test_locally_built_service_requires_pinned_base_images(tmp_path):
    _compose(
        tmp_path,
        "docker/local/docker-compose.yml",
        "services:\n  l:\n    image: benchbox-local:dev\n    build:\n      context: ../..\n"
        "      dockerfile: docker/local/Dockerfile\n",
    )
    dockerfile = _compose(
        tmp_path,
        "docker/local/Dockerfile",
        f"FROM repo/base:1@{DIGEST} AS build\nFROM build AS final\nFROM scratch\nFROM python:3.14-slim\n",
    )

    violations = image_pins.find_floating_images(tmp_path)

    assert [(v.path, v.line, v.reference) for v in violations] == [(dockerfile.resolve(), 4, "python:3.14-slim")]


def test_missing_dockerfile_is_reported(tmp_path):
    _compose(
        tmp_path,
        "docker/local/docker-compose.yml",
        "services:\n  l:\n    build:\n      context: .\n      dockerfile: Dockerfile\n",
    )

    assert len(image_pins.find_floating_images(tmp_path)) == 1


def test_every_uat_compose_image_in_the_repository_is_pinned():
    assert image_pins.compose_files(REPO_ROOT)
    assert image_pins.find_floating_images(REPO_ROOT) == []


def test_cli_exit_codes_follow_the_check(tmp_path, capsys):
    _compose(tmp_path, "docker/a/docker-compose.yml", "services:\n  a:\n    image: repo/a:latest\n")

    assert image_pins.main(["--root", str(tmp_path)]) == 1
    assert "repo/a:latest" in capsys.readouterr().err

    _compose(tmp_path, "docker/a/docker-compose.yml", f"services:\n  a:\n    image: repo/a:latest@{DIGEST}\n")
    assert image_pins.main(["--root", str(tmp_path)]) == 0
