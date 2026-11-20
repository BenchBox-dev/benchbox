from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities import network_guard

pytestmark = [pytest.mark.unit, pytest.mark.medium]

PUBLIC_IP = "203.0.113.7"


def test_swallowed_violation_still_fails_the_test(tmp_path):
    inner = tmp_path / "test_inner.py"
    inner.write_text(
        "import socket\n"
        "import pytest\n"
        "pytestmark = pytest.mark.unit\n"
        "def test_swallows():\n"
        "    try:\n"
        f"        socket.create_connection(('{PUBLIC_IP}', 80), timeout=1)\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "BENCHBOX_SKIP_TEST_LOCK": "1", "PYTHONPATH": str(repo)}
    env.pop(network_guard.ENV_DIR, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(inner),
            "-c",
            str(repo / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            "-p",
            "tests.utilities.network_guard_plugin",
            "-p",
            "no:cacheprovider",
            "-n0",
            "-m",
            "unit",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode != 0, result.stdout
    assert "BenchBox network guard" in result.stdout


def _run_inner_pytest(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    inner = tmp_path / "test_inner.py"
    inner.write_text(body, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "BENCHBOX_SKIP_TEST_LOCK": "1", "PYTHONPATH": str(repo)}
    env.pop(network_guard.ENV_DIR, None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(inner),
            "-c",
            str(repo / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            "-p",
            "tests.utilities.network_guard_plugin",
            "-p",
            "no:cacheprovider",
            "-n0",
            "-m",
            "unit",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=tmp_path,
    )


def test_allow_network_without_a_reason_fails_collection(tmp_path):
    result = _run_inner_pytest(
        tmp_path,
        "import pytest\n@pytest.mark.unit\n@pytest.mark.allow_network\ndef test_x():\n    pass\n",
    )
    assert result.returncode != 0
    assert "allow_network requires a non-empty reason" in result.stdout + result.stderr


def test_allow_network_with_a_reason_collects(tmp_path):
    result = _run_inner_pytest(
        tmp_path,
        "import pytest\n@pytest.mark.unit\n@pytest.mark.allow_network(reason='subject is the call')\n"
        "def test_x():\n    pass\n",
    )
    assert result.returncode == 0, result.stdout


def test_expect_network_block_fails_when_nothing_was_blocked(tmp_path):
    result = _run_inner_pytest(
        tmp_path,
        "import pytest\npytestmark = pytest.mark.unit\n"
        "def test_x(expect_network_block):\n    with expect_network_block('203.0.113.7'):\n        pass\n",
    )
    assert result.returncode != 0
    assert "blocked nothing" in result.stdout


def test_expect_network_block_does_not_excuse_other_targets(tmp_path):
    result = _run_inner_pytest(
        tmp_path,
        "import socket\nimport pytest\npytestmark = pytest.mark.unit\n"
        "def test_x(expect_network_block):\n"
        "    with expect_network_block('203.0.113.7'):\n"
        "        try:\n            socket.create_connection(('203.0.113.7', 80), timeout=1)\n"
        "        except Exception:\n            pass\n"
        "    try:\n        socket.create_connection(('198.51.100.9', 80), timeout=1)\n"
        "    except Exception:\n        pass\n",
    )
    assert result.returncode != 0
    assert "198.51.100.9" in result.stdout
