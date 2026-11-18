import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]

_PROBE_CODE = """
import sys

mode = sys.argv[2]
if mode == "detect":
    from benchbox.core.platform_registry import PlatformRegistry

    assert PlatformRegistry.is_platform_available("duckdb")
    assert "duckdb" in PlatformRegistry.get_available_platforms()
    assert "motherduck" in PlatformRegistry.get_available_platforms()
    import benchbox.platforms.duckdb

    assert "duckdb" not in sys.modules and "_duckdb" not in sys.modules
elif mode == "preload":
    import duckdb

from benchbox.platforms.duckdb import DuckDBAdapter

runtime_path = sys.argv[1]
try:
    adapter = DuckDBAdapter(
        database_path=":memory:",
        driver_package="duckdb",
        driver_version_requested="0.0.1",
        driver_version_resolved="0.0.1",
        driver_runtime_strategy="isolated-site-packages",
        driver_runtime_path=runtime_path,
    )
except ImportError as exc:
    print("ERROR::" + type(exc.__cause__).__name__ + "::" + str(exc))
else:
    print("VERSION::" + adapter.driver_version_actual)
"""


@pytest.fixture
def fake_runtime(tmp_path: Path) -> Path:
    site_packages = tmp_path / "site-packages"
    (site_packages / "duckdb").mkdir(parents=True)
    (site_packages / "_duckdb.py").write_text('__version__ = "0.0.1"\n')
    (site_packages / "duckdb" / "__init__.py").write_text(
        textwrap.dedent(
            """
            import _duckdb

            __version__ = _duckdb.__version__
            """
        )
    )
    return site_packages


def _run(mode: str, runtime_path: Path) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE_CODE, str(runtime_path), mode],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith(("VERSION::", "ERROR::"))]
    assert lines, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    return lines[-1]


def test_adapter_import_and_platform_detection_do_not_load_the_native_driver(fake_runtime: Path) -> None:
    assert _run("detect", fake_runtime) == "VERSION::0.0.1"


def test_requested_version_is_loaded_after_adapter_import_without_driver_import(fake_runtime: Path) -> None:
    assert _run("none", fake_runtime) == "VERSION::0.0.1"


def test_driver_loaded_before_request_fails_with_clear_error(fake_runtime: Path) -> None:
    result = _run("preload", fake_runtime)
    assert result.startswith("ERROR::RuntimeError::")
    assert "already loaded" in result
    assert "first one imported" in result
