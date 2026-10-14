from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchbox.core.execution_engine import DEFAULT_EXECUTION_ENGINE, RESERVED_OPTION_KEYS
from benchbox.core.platform_manifest import PLATFORM_MANIFEST, _parse_manifest_entry, _validate_manifest_set
from scripts.new_platform_scaffold import (
    MANIFEST_FILE,
    adapter_stub,
    class_name,
    manifest_snippet,
    module_file,
    render,
    validate_snippet,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "new_platform_scaffold.py"


def _run_scaffold(tmp_path: Path, *, key: str, kind: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--name", key, "--kind", kind, "--output-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("kind", ["sql", "dataframe"])
def test_emitted_manifest_entry_passes_the_real_manifest_validator(tmp_path: Path, kind: str) -> None:
    completed = _run_scaffold(tmp_path, key="examplengine", kind=kind)
    assert completed.returncode == 0, completed.stderr

    written = json.loads((tmp_path / MANIFEST_FILE).read_text(encoding="utf-8"))
    entry = _parse_manifest_entry(written)
    _validate_manifest_set([*PLATFORM_MANIFEST, entry])

    assert entry.capabilities["default_mode"] == kind
    assert entry.capabilities["execution_engines"] == {}
    assert entry.support_status == "experimental"
    if kind == "sql":
        assert entry.adapter is not None
        assert entry.adapter.class_name == "ExamplengineAdapter"
        assert module_file(key="examplengine", mode=kind) == entry.adapter.module.replace(".", "/") + ".py"
    else:
        assert entry.adapter is None
        assert [alias.name for alias in entry.aliases] == ["examplengine-df"]


@pytest.mark.parametrize("kind", ["sql", "dataframe"])
def test_stub_file_matches_the_file_plan(tmp_path: Path, kind: str) -> None:
    completed = _run_scaffold(tmp_path, key="planengine", kind=kind)
    assert completed.returncode == 0, completed.stderr

    planned = module_file(key="planengine", mode=kind)
    assert f"- {planned}" in completed.stdout
    assert (tmp_path / Path(planned).name).exists()
    if kind == "dataframe":
        assert planned == "benchbox/platforms/dataframe/planengine_df.py"


def test_dataframe_stub_does_not_import_the_sql_adapter_base() -> None:
    imported = _imported_names(adapter_stub(key="framengine", display_name="Framengine", mode="dataframe"))
    assert "benchbox.platforms.base.PlatformAdapter" not in imported
    assert "benchbox.platforms.dataframe.expression_family.ExpressionFamilyAdapter" in imported


@pytest.mark.parametrize("kind", ["sql", "dataframe"])
def test_stub_resolves_the_default_engine_receipt(tmp_path: Path, kind: str) -> None:
    stub_path = tmp_path / f"receipt_{kind}.py"
    stub_path.write_text(adapter_stub(key="receiptengine", display_name="Receiptengine", mode=kind))
    spec = importlib.util.spec_from_file_location(f"receipt_{kind}", stub_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter_class = getattr(module, class_name(key="receiptengine", mode=kind))

    assert adapter_class.platform_name == "Receiptengine"
    receipt = adapter_class.resolve_execution_engine(None, DEFAULT_EXECUTION_ENGINE)
    assert receipt.resolution == "platform_default"
    assert receipt.requested == DEFAULT_EXECUTION_ENGINE


def test_stub_registers_no_option_specs_or_reserved_keys() -> None:
    source = adapter_stub(key="cleanengine", display_name="Cleanengine", mode="sql")
    assert "PlatformOptionSpec" not in source
    for reserved in RESERVED_OPTION_KEYS:
        assert f'"{reserved}"' not in source


def test_validate_snippet_reports_real_manifest_errors() -> None:
    assert validate_snippet(manifest_snippet(key="freshengine", display_name="Freshengine", mode="sql")) == []

    duplicate = manifest_snippet(key="duckdb", display_name="DuckDB", mode="sql")
    assert any("Duplicate canonical platform keys" in problem for problem in validate_snippet(duplicate))

    broken = manifest_snippet(key="brokenengine", display_name="Brokenengine", mode="sql")
    broken["capabilities"]["default_mode"] = None
    assert any("invalid default_mode" in problem for problem in validate_snippet(broken))

    unknown_class = manifest_snippet(key="classengine", display_name="Classengine", mode="sql")
    unknown_class["capabilities"]["execution_engines"] = {
        "turbo": {"class": "warp", "display_name": "Turbo", "description": "x", "dependencies": [], "selectable": True}
    }
    assert any("unknown class" in problem for problem in validate_snippet(unknown_class))


def test_cli_rejects_a_key_the_manifest_already_owns(tmp_path: Path) -> None:
    completed = _run_scaffold(tmp_path, key="duckdb", kind="sql")
    assert completed.returncode == 1
    assert "Duplicate canonical platform keys: duckdb" in completed.stderr
    assert not (tmp_path / MANIFEST_FILE).exists()


def test_render_prints_the_json_manifest_entry() -> None:
    rendered = render(key="demoengine", display_name="Demoengine", mode="dataframe")
    assert '"execution_engines": {}' in rendered
    assert "class DemoengineDataFrameAdapter(ExpressionFamilyAdapter):" in rendered
    assert "- benchbox/platforms/dataframe/demoengine_df.py" in rendered
