from __future__ import annotations

import logging
import os
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


def _run_synthetic_modules(tmp_path: Path, sources: dict[str, str]) -> subprocess.CompletedProcess[str]:
    paths = []
    for name, source in sources.items():
        path = tmp_path / name
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        paths.append(str(path))
    config = tmp_path / "empty.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    environment = {"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    for inherited in ("SYSTEMROOT", "SystemRoot", "WINDIR", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        if inherited in os.environ:
            environment[inherited] = os.environ[inherited]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.utilities.module_state_guard",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "--rootdir",
            str(tmp_path),
            "-c",
            str(config),
            *paths,
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_synthetic_module(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    return _run_synthetic_modules(tmp_path, {"test_synthetic_leak.py": source})


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


def _leaking_fixture_module(leak: str, restore: str) -> str:
    return (
        "import logging\n\nimport pytest\n\n\n"
        '@pytest.fixture(scope="module")\ndef state():\n'
        + textwrap.indent(textwrap.dedent(leak), "    ")
        + "    yield\n"
        + textwrap.indent(textwrap.dedent(restore), "    ")
        + "\n\ndef test_uses_state(state):\n    assert True\n"
    )


TRANSPORT_FILTER_LEAK = """
from benchbox.mcp.security import configure_transport_security_logging

configure_transport_security_logging()
"""
TRANSPORT_FILTER_RESTORE = """
logging.getLogger("mcp.server.transport_security").filters[:] = []
"""
BENCHBOX_FILTER_LEAK = """
class RedactingFilter(logging.Filter):
    def filter(self, record):
        return True


logging.getLogger("benchbox.synthetic").addFilter(RedactingFilter())
"""
BENCHBOX_FILTER_RESTORE = """
logging.getLogger("benchbox.synthetic").filters[:] = []
"""
TRACER_LEAK = """
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

trace.set_tracer_provider(TracerProvider())
"""
TRACER_RESTORE = """
from opentelemetry import trace

trace._TRACER_PROVIDER = None
"""


@pytest.mark.parametrize(
    ("leak", "restore", "named"),
    [
        (
            TRANSPORT_FILTER_LEAK,
            TRANSPORT_FILTER_RESTORE,
            "logger 'mcp.server.transport_security' filters changed from [] to [_TransportSecurityLogFilter]",
        ),
        (
            BENCHBOX_FILTER_LEAK,
            BENCHBOX_FILTER_RESTORE,
            "logger 'benchbox.synthetic' filters changed from [] to [RedactingFilter]",
        ),
        (TRACER_LEAK, TRACER_RESTORE, "tracer provider changed from NoneType to TracerProvider"),
    ],
    ids=["transport-security-filter", "benchbox-logger-filter", "tracer-provider"],
)
def test_module_fixture_leaking_state_fails_its_module_and_its_restored_twin_passes(
    tmp_path: Path, leak: str, restore: str, named: str
) -> None:
    (tmp_path / "leaking").mkdir()
    (tmp_path / "restored").mkdir()
    leaking = _run_synthetic_module(tmp_path / "leaking", _leaking_fixture_module(leak, restore="pass\n"))
    restored = _run_synthetic_module(tmp_path / "restored", _leaking_fixture_module(leak, restore))

    assert leaking.returncode != 0, leaking.stdout
    assert "1 passed" in leaking.stdout
    assert "1 error" in leaking.stdout
    assert named in leaking.stdout
    assert restored.returncode == 0, restored.stdout


def test_the_module_after_a_leaking_module_sees_the_original_logging_and_quiet_state(tmp_path: Path) -> None:
    observer = """
        import logging

        from benchbox.utils.printing import is_quiet


        def test_state_is_original():
            logger = logging.getLogger("benchbox")
            assert logger.level == logging.NOTSET
            assert logger.handlers == []
            assert is_quiet() is False
    """
    result = _run_synthetic_modules(tmp_path, {"test_a_leak.py": DOCS_EXPORT_LEAK, "test_b_observer.py": observer})

    assert result.returncode != 0, result.stdout
    assert "2 passed" in result.stdout
    assert "1 error" in result.stdout
    assert "logger 'benchbox' level changed from NOTSET to ERROR" in result.stdout
