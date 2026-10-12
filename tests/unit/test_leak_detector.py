from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities import leak_detector
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_legitimate_monkeypatch_and_chdir_pass(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BENCHBOX_LEAK_DETECTOR_PROBE", "1")
    import benchbox.utils.printing as printing

    monkeypatch.setattr(printing, "_QUIET", True)


def test_generator_fixture_restoring_state_passes(tmp_path) -> None:
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert os.getcwd() == str(tmp_path.resolve())
    finally:
        os.chdir(old)


def test_detect_and_restore_reports_and_restores(tmp_path) -> None:
    import benchbox.utils.config_interface as ci
    import benchbox.utils.printing as printing

    baseline = leak_detector.snapshot()
    try:
        os.chdir(tmp_path)
        os.environ["BENCHBOX_LEAK_DETECTOR_PROBE"] = "secret-value"
        printing._QUIET = not baseline["globals"]["benchbox.utils.printing._QUIET"]
        ci._config_provider = object()
        problems = leak_detector.detect_and_restore(baseline)
        assert problems == [
            "cwd",
            "env[BENCHBOX_LEAK_DETECTOR_PROBE]",
            "benchbox.utils.printing._QUIET",
            "benchbox.utils.config_interface._config_provider",
        ]
        assert "secret-value" not in str(problems)
        assert leak_detector.snapshot() == baseline
    finally:
        leak_detector.detect_and_restore(baseline)


@pytest.mark.parametrize(
    ("value", "module_loaded", "accepted"),
    [
        ("system", True, True),
        ("system", False, False),
        ("jemalloc", True, False),
    ],
)
def test_library_import_env_is_accepted_only_as_declared(monkeypatch, value, module_loaded, accepted) -> None:
    key, module = "BENCHBOX_LEAK_LIBRARY_PROBE", "benchbox_leak_probe_library"
    monkeypatch.setattr(leak_detector, "LIBRARY_IMPORT_ENV", ((key, "system", module),))
    monkeypatch.delenv(key, raising=False)
    if module_loaded:
        monkeypatch.setitem(sys.modules, module, object())
    else:
        monkeypatch.delitem(sys.modules, module, raising=False)

    baseline = leak_detector.snapshot()
    monkeypatch.setenv(key, value)
    problems = leak_detector.detect_and_restore(baseline)

    assert problems == ([] if accepted else [f"env[{key}]"])
    assert (os.environ.get(key) == value) is accepted


def test_snowflake_arrow_pool_is_the_declared_library_import_env() -> None:
    assert ("ARROW_DEFAULT_MEMORY_POOL", "system", "snowflake.connector.options") in leak_detector.LIBRARY_IMPORT_ENV


def test_provider_compared_by_identity_not_equality(monkeypatch) -> None:
    import benchbox.utils.config_interface as ci

    class EqualProvider:
        def __eq__(self, other):
            return True

    before = EqualProvider()
    monkeypatch.setattr(ci, "_config_provider", before)
    baseline = leak_detector.snapshot()
    ci._config_provider = EqualProvider()
    assert leak_detector.detect_and_restore(baseline) == ["benchbox.utils.config_interface._config_provider"]
    assert ci._config_provider is before


def test_snapshot_does_not_allocate_default_provider(monkeypatch) -> None:
    import benchbox.utils.config_interface as ci

    def forbidden():
        raise AssertionError("default provider must not be allocated")

    monkeypatch.setattr(ci, "get_default_config_provider", forbidden)
    assert (
        leak_detector.snapshot()["globals"]["benchbox.utils.config_interface._config_provider"] is ci._config_provider
    )


def test_clean_state_reports_nothing() -> None:
    assert leak_detector.detect_and_restore(leak_detector.snapshot()) == []


def test_unloaded_globals_use_raw_defaults_without_imports(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "benchbox.utils.printing", raising=False)
    monkeypatch.delitem(sys.modules, "benchbox.utils.config_interface", raising=False)
    monkeypatch.delitem(sys.modules, "rich.prompt", raising=False)
    monkeypatch.delitem(sys.modules, "benchbox.platforms.credentials.bigquery", raising=False)
    assert leak_detector.snapshot()["globals"] == {
        "benchbox.utils.printing._QUIET": False,
        "benchbox.utils.config_interface._config_provider": None,
        "rich.prompt.Confirm": None,
        "benchbox.platforms.credentials.bigquery.Confirm": None,
    }
    assert "benchbox.utils.printing" not in sys.modules
    assert "benchbox.utils.config_interface" not in sys.modules
    assert "rich.prompt" not in sys.modules
    assert "benchbox.platforms.credentials.bigquery" not in sys.modules


@pytest.mark.parametrize(
    ("body", "surface"),
    [
        ('os.environ["LEAKY_PROBE"] = "secret-value"', "env[LEAKY_PROBE]"),
        ('os.environ["EXISTING_PROBE"] = "changed-secret"', "env[EXISTING_PROBE]"),
        ('del os.environ["EXISTING_PROBE"]', "env[EXISTING_PROBE]"),
        ('os.chdir("changed")', "cwd"),
        ("printing._QUIET = True", "benchbox.utils.printing._QUIET"),
        ("ci._config_provider = object()", "benchbox.utils.config_interface._config_provider"),
        ("rich.prompt.Confirm = object()", "rich.prompt.Confirm"),
        ("bigquery.Confirm = object()", "benchbox.platforms.credentials.bigquery.Confirm"),
        (
            'replacement = types.ModuleType("replacement"); '
            'sys.modules["benchbox.platforms.credentials.bigquery"] = replacement; '
            "credentials.bigquery = replacement",
            "sys.modules[benchbox.platforms.credentials.bigquery]",
        ),
    ],
)
def test_leaking_test_fails_at_teardown_and_next_test_is_clean(tmp_path: Path, body: str, surface: str) -> None:
    result = _run_probe(tmp_path, body)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2 passed, 1 error" in result.stdout
    assert "test leaked process state past teardown (restored)" in result.stdout
    assert surface in result.stdout
    assert "secret-value" not in result.stdout
    assert "changed-secret" not in result.stdout


def _run_probe(
    tmp_path: Path, body: str, *, teardown: str = "", clean: str = "", mark: str = ""
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "changed").mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = unit\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom tests.utilities.leak_detector import restore_global\n"
        "@pytest.fixture(autouse=True)\ndef reset(request, _hermetic_state):\n"
        "    yield\n"
        '    restore_global(request.node, "benchbox.utils.printing", "_QUIET", False)\n'
        '    restore_global(request.node, "benchbox.utils.config_interface", "_config_provider", None)\n',
        encoding="utf-8",
    )
    (tmp_path / "test_probe.py").write_text(
        "import os, pytest, sys, types\n"
        "import rich.prompt\n"
        "import benchbox.platforms.credentials as credentials\n"
        "import benchbox.platforms.credentials.bigquery as bigquery\n"
        "import benchbox.utils.printing as printing\n"
        "import benchbox.utils.config_interface as ci\n"
        "pytestmark = pytest.mark.unit\n"
        "ORIGINAL_CWD = os.getcwd()\n"
        "ORIGINAL_RICH_CONFIRM = rich.prompt.Confirm\n"
        "ORIGINAL_BIGQUERY_CONFIRM = bigquery.Confirm\n"
        "ORIGINAL_BIGQUERY_MODULE = bigquery\n"
        "@pytest.fixture\ndef finalizer():\n    yield\n"
        f"    {teardown or 'pass'}\n"
        f"{mark}\n"
        f"def test_a(finalizer, monkeypatch):\n    {body}\n"
        "def test_b():\n"
        "    assert os.getcwd() == ORIGINAL_CWD\n"
        '    assert "LEAKY_PROBE" not in os.environ\n'
        '    assert os.environ["EXISTING_PROBE"] == "original"\n'
        "    assert printing._QUIET is False\n"
        "    assert ci._config_provider is None\n"
        "    assert rich.prompt.Confirm is ORIGINAL_RICH_CONFIRM\n"
        "    assert bigquery.Confirm is ORIGINAL_BIGQUERY_CONFIRM\n"
        '    assert sys.modules["benchbox.platforms.credentials.bigquery"] is ORIGINAL_BIGQUERY_MODULE\n'
        "    assert credentials.bigquery is ORIGINAL_BIGQUERY_MODULE\n"
        f"    {clean or 'pass'}\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), EXISTING_PROBE="original")
    env.pop("PYTEST_CURRENT_TEST", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--confcutdir",
            str(tmp_path),
            "-p",
            "tests.utilities.leak_detector",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
            "-q",
            "test_probe.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_finalizer_leak_is_detected_after_all_teardown(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, "pass", teardown='os.environ["LEAKY_PROBE"] = "secret-value"')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2 passed, 1 error" in result.stdout
    assert "env[LEAKY_PROBE]" in result.stdout


def test_leak_restored_when_another_finalizer_fails(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        "pass",
        teardown='os.environ["LEAKY_PROBE"] = "secret-value"; raise RuntimeError("cleanup-failed")',
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2 passed, 1 error" in result.stdout
    assert "env[LEAKY_PROBE]" in result.stdout
    assert "cleanup-failed" in result.stdout


def test_expected_failure_cannot_hide_a_leak(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        'os.environ["LEAKY_PROBE"] = "secret-value"',
        mark='@pytest.mark.xfail(reason="must not hide a state leak")',
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "env[LEAKY_PROBE]" in result.stdout
    assert "1 error" in result.stdout


def test_restored_monkeypatch_and_generator_teardown_pass(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        'monkeypatch.chdir("changed"); monkeypatch.setenv("LEAKY_PROBE", "temporary"); '
        'monkeypatch.setattr(printing, "_QUIET", True); monkeypatch.setattr(ci, "_config_provider", object())',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def _run_module_fixture_probe(tmp_path: Path, last_test_body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = unit\n", encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(
        "import os, pytest\npytestmark = pytest.mark.unit\n"
        '@pytest.fixture(scope="module")\ndef module_env():\n'
        '    previous = os.environ.get("MODULE_PROBE")\n'
        '    os.environ["MODULE_PROBE"] = "module-value"\n'
        "    yield\n"
        "    if previous is None:\n"
        '        os.environ.pop("MODULE_PROBE", None)\n'
        "    else:\n"
        '        os.environ["MODULE_PROBE"] = previous\n'
        "def test_first(module_env):\n    pass\n"
        f"def test_last(module_env):\n    {last_test_body}\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    env.pop("PYTEST_CURRENT_TEST", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--confcutdir",
            str(tmp_path),
            "-p",
            "tests.utilities.leak_detector",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
            "-q",
            "test_probe.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    "import_statement",
    ["import rich.prompt", "import benchbox.platforms.credentials.bigquery"],
)
def test_first_import_of_registered_module_is_not_reported_as_a_leak(tmp_path: Path, import_statement: str) -> None:
    result = _run_module_fixture_probe(tmp_path, import_statement)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_module_fixture_restoring_state_is_not_blamed_on_the_last_test(tmp_path: Path) -> None:
    result = _run_module_fixture_probe(tmp_path, "pass")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    assert "leaked" not in result.stdout


def test_a_leak_by_the_last_test_is_still_detected_next_to_a_module_fixture(tmp_path: Path) -> None:
    result = _run_module_fixture_probe(tmp_path, 'os.environ["LEAKY_PROBE"] = "secret-value"')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "env[LEAKY_PROBE]" in result.stdout
    assert "env[MODULE_PROBE]" not in result.stdout
    assert "secret-value" not in result.stdout
