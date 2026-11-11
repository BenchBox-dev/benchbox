from __future__ import annotations

import subprocess
import sys

import pytest

from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_repo_root_points_at_checkout() -> None:
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert (REPO_ROOT / "tests" / "utilities" / "paths.py").is_file()


def test_paths_module_is_cwd_independent_and_import_free(tmp_path) -> None:
    script = (
        "import runpy, sys\n"
        f"ns = runpy.run_path({str(REPO_ROOT / 'tests' / 'utilities' / 'paths.py')!r})\n"
        "print(ns['REPO_ROOT'])\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in {'benchbox', 'tests'}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30
    )
    root_line, modules_line = result.stdout.splitlines()
    assert root_line == str(REPO_ROOT)
    assert modules_line == "[]"


def test_normal_paths_import_does_not_load_native_helpers(tmp_path) -> None:
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from tests.utilities.paths import REPO_ROOT\n"
        "assert 'tests.utilities.test_helpers' not in sys.modules\n"
        "assert 'duckdb' not in sys.modules\n"
        "assert not any(m.startswith('benchbox') for m in sys.modules)\n"
        "print(REPO_ROOT)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30
    )
    assert result.stdout.strip() == str(REPO_ROOT)


def test_lazy_public_helpers_preserve_package_api() -> None:
    import tests.utilities as utilities
    from tests.utilities import test_helpers

    for name in utilities.__all__:
        assert getattr(utilities, name) is getattr(test_helpers, name)
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = utilities.unknown_helper


