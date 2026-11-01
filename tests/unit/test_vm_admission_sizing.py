from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(os.name == "nt", reason="exercises shell stubs"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "scripts" / "linux_preflight_pytest.sh"
GIB = 1024**3


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _container(tmp_path: Path, machines: list[dict[str, object]]) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    payload = tmp_path / "machines.json"
    payload.write_text(json.dumps(machines), encoding="utf-8")
    names = "\n".join(str(item["id"]) for item in machines)
    _stub(
        bin_dir,
        "container",
        f'if [ "$3" = "--format" ]; then cat "{payload}"; else echo NAME; cat <<EOF\n{names}\nEOF\nfi',
    )
    return bin_dir


def _machine(name: str, status: str, gib: int) -> dict[str, object]:
    return {"id": name, "status": status, "memory": gib * GIB, "cpus": 4}


def _need(path: str, machine: str = "benchbox-agent", fallback: str = "2") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "benchbox.utils.host_lock",
            "machine-need",
            "--machine",
            machine,
            "--fallback-gib",
            fallback,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path, "PYTHONPATH": str(REPO_ROOT)},
        cwd=REPO_ROOT,
        check=False,
    )


def test_a_stopped_machine_needs_half_its_configured_memory(tmp_path: Path) -> None:
    bin_dir = _container(tmp_path, [_machine("benchbox-agent", "stopped", 8)])

    result = _need(f"{bin_dir}:/usr/bin:/bin")

    assert result.returncode == 0
    assert result.stdout.strip() == "4"


def test_a_running_machine_needs_only_the_fallback(tmp_path: Path) -> None:
    bin_dir = _container(tmp_path, [_machine("benchbox-agent", "running", 8)])

    assert _need(f"{bin_dir}:/usr/bin:/bin").stdout.strip() == "2"


def test_an_unlisted_machine_needs_the_fallback_and_other_machines_are_ignored(tmp_path: Path) -> None:
    bin_dir = _container(tmp_path, [_machine("ss-dev", "stopped", 16)])

    assert _need(f"{bin_dir}:/usr/bin:/bin").stdout.strip() == "2"


def test_a_prefix_similar_machine_is_not_matched(tmp_path: Path) -> None:
    bin_dir = _container(
        tmp_path, [_machine("benchbox-agent-2", "stopped", 16), _machine("benchbox-agent", "stopped", 6)]
    )

    assert _need(f"{bin_dir}:/usr/bin:/bin").stdout.strip() == "3"


def test_an_unreadable_listing_needs_the_fallback(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "container", "echo not-json")

    assert _need(f"{bin_dir}:/usr/bin:/bin", fallback="3").stdout.strip() == "3"


def test_a_failing_listing_needs_the_fallback(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "container", "exit 1")

    assert _need(f"{bin_dir}:/usr/bin:/bin").stdout.strip() == "2"


def test_a_missing_container_cli_needs_the_fallback() -> None:
    assert _need("/usr/bin:/bin").stdout.strip() == "2"


def test_the_makefile_recipe_passes_the_helper_output_as_the_need() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "machine-need --machine $(CI_LINUX_MACHINE) --fallback-gib $(CI_LINUX_NEED_GIB)" in text
    assert '--need-gib "$$NEED_GIB"' in text
    assert "--need-gib $(CI_LINUX_NEED_GIB)" not in text


def test_the_setup_texts_name_the_machine_size_the_sizing_assumes() -> None:
    for relative in ("Makefile", "scripts/linux_preflight_pytest.sh"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "--memory 6G" in text
        assert "--memory 8G" not in text


darwin_arm64 = pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine() != "arm64", reason="Apple silicon only"
)


def _preflight(tmp_path: Path, run_status: int) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = _container(tmp_path, [_machine("benchbox-agent", "stopped", 8)])
    record = tmp_path / "uv-calls.txt"
    _stub(
        bin_dir,
        "uv",
        f'echo "$@" >> "{record}"\ncase "$*" in *machine-need*) echo 8;; *) exit {run_status};; esac',
    )
    test_list = tmp_path / "tests.txt"
    test_list.write_text("tests/unit/test_x.py\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        ["/bin/bash", str(PREFLIGHT), str(test_list)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        check=False,
    )
    return result, record


@darwin_arm64
def test_preflight_passes_the_machine_need_to_admission(tmp_path: Path) -> None:
    result, record = _preflight(tmp_path, 0)

    assert result.returncode == 0
    calls = record.read_text(encoding="utf-8")
    assert "machine-need --machine benchbox-agent --fallback-gib 2" in calls
    assert "--admit --need-gib 8 --guard" in calls


@darwin_arm64
def test_preflight_reports_a_refused_admission_as_unverified_not_a_failure(tmp_path: Path) -> None:
    result, _ = _preflight(tmp_path, 76)

    assert result.returncode == 0
    assert "Linux not verified -- admission refused" in result.stderr


@darwin_arm64
def test_preflight_keeps_other_failures_failing(tmp_path: Path) -> None:
    result, _ = _preflight(tmp_path, 1)

    assert result.returncode == 1
    assert "Linux not verified" not in result.stderr
