from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "_sources/compilation/scripts/compile-all-platforms.sh"


def _selection_fragment() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index('CONTAINER_ENGINE="${BENCHBOX_CONTAINER_ENGINE:-}"')
    end = text.index("\n\n_host_arch() {", start)
    return text[start:end] + '\nprintf "%s\\n" "$CONTAINER_ENGINE"\n'


def _write_engine(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("container_status,expected", [(1, "docker"), (0, "container")])
def test_auto_selection_requires_healthy_apple_container(tmp_path: Path, container_status: int, expected: str) -> None:
    _write_engine(tmp_path / "container", f'[ "$1 $2" = "system status" ] && exit {container_status}\nexit 0')
    _write_engine(tmp_path / "docker", "exit 0")
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "BENCHBOX_CONTAINER_ENGINE": ""}

    result = subprocess.run(
        ["sh", "-c", _selection_fragment()],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.stdout.strip() == expected


