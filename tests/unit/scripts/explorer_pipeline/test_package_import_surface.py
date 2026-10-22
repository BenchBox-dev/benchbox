from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import _project.scripts.explorer_pipeline as package

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]

EXPECTED_EXPORTS = {
    "BuildStats": "pipeline",
    "BundleTransformer": "transformer",
    "DetailResult": "models",
    "DuckDBSnapshotBuilder": "duckdb_builder",
    "ExplorerPipeline": "pipeline",
    "ManifestEntry": "models",
    "QueryTiming": "models",
}


def test_all_lists_every_public_name() -> None:
    assert sorted(package.__all__) == sorted(EXPECTED_EXPORTS)


@pytest.mark.parametrize(("name", "module_name"), sorted(EXPECTED_EXPORTS.items()))
def test_public_name_resolves_to_submodule_object(name: str, module_name: str) -> None:
    submodule = importlib.import_module(f"_project.scripts.explorer_pipeline.{module_name}")
    assert getattr(package, name) is getattr(submodule, name)


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'missing_name'"):
        package.__getattr__("missing_name")


def test_dir_includes_lazy_names() -> None:
    assert set(EXPECTED_EXPORTS) <= set(dir(package))


def test_importing_contract_does_not_load_heavy_modules() -> None:
    script = (
        "import json, sys\n"
        "import _project.scripts.explorer_pipeline.contract\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m == 'benchbox' or m == 'pandas' or m.endswith('explorer_pipeline.models') "
        "or m.endswith('explorer_pipeline.duckdb_builder'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == []


def test_from_import_of_public_name_works_in_fresh_interpreter() -> None:
    script = (
        "import importlib, sys\n"
        "module = importlib.import_module('_project.scripts.explorer_pipeline')\n"
        "for name in sys.argv[1:]:\n"
        "    getattr(module, name)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, *sorted(EXPECTED_EXPORTS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
