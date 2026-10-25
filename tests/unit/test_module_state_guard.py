from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.utilities import module_state_guard
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DOCS_EXPORT_LEAK = """
import logging

import pytest

from benchbox.utils.printing import set_quiet


@pytest.fixture(scope="module")
def facts():
    logging.getLogger("benchbox").setLevel(logging.ERROR)
    logging.getLogger("benchbox").addHandler(logging.StreamHandler())
    set_quiet(True)
    yield {}


def test_uses_facts(facts):
    assert facts == {}
"""


def _run_synthetic_module(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_synthetic_leak.py").write_text(textwrap.dedent(source))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.utilities.module_state_guard",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            "-c",
            "/dev/null",
            str(tmp_path / "test_synthetic_leak.py"),
        ],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_module_fixture_that_changes_logging_and_quiet_state_fails_its_module(tmp_path: Path) -> None:
    result = _run_synthetic_module(tmp_path, DOCS_EXPORT_LEAK)

    assert result.returncode != 0, result.stdout
    assert "1 passed" in result.stdout
    assert "1 error" in result.stdout
    assert "logger 'benchbox' level changed from NOTSET to ERROR" in result.stdout
    assert "logger 'benchbox' handlers changed from [] to [StreamHandler]" in result.stdout
    assert "quiet flag changed from False to True" in result.stdout


def test_module_that_restores_what_it_changes_passes(tmp_path: Path) -> None:
    clean = DOCS_EXPORT_LEAK.replace(
        "    yield {}\n",
        "    yield {}\n    logging.getLogger('benchbox').setLevel(logging.NOTSET)\n"
        "    logging.getLogger('benchbox').handlers[:] = []\n    set_quiet(False)\n",
    )

    result = _run_synthetic_module(tmp_path, clean)

    assert result.returncode == 0, result.stdout


def test_diff_names_an_installed_tracer_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry import trace

    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    before = module_state_guard.snapshot()
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", object())

    assert module_state_guard.diff(before, module_state_guard.snapshot()) == [
        "tracer provider changed from NoneType to object"
    ]


def test_diff_names_a_logger_created_with_a_changed_level() -> None:
    before = module_state_guard.snapshot()
    name = "benchbox.module_state_guard_probe"
    logger = logging.getLogger(name)
    logger.setLevel(logging.CRITICAL)
    try:
        problems = module_state_guard.diff(before, module_state_guard.snapshot())
    finally:
        logger.setLevel(logging.NOTSET)

    assert problems == [f"logger {name!r} level changed from NOTSET to CRITICAL"]


def test_restore_returns_logging_and_quiet_state_to_the_snapshot() -> None:
    from benchbox.utils.printing import is_quiet, set_quiet

    before = module_state_guard.snapshot()
    logger = logging.getLogger("benchbox")
    handler = logging.StreamHandler()
    original_level = logger.level
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)
    set_quiet(not is_quiet())
    try:
        module_state_guard.restore(before)
        assert module_state_guard.diff(before, module_state_guard.snapshot()) == []
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        set_quiet(before["quiet"])
