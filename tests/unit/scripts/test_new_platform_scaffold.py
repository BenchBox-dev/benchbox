from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from benchbox.core.execution_engine import (
    DEFAULT_EXECUTION_ENGINE,
    EXECUTION_ENGINE_CLASSES,
    RESERVED_OPTION_KEYS,
)
from scripts.new_platform_scaffold import adapter_stub, manifest_snippet, render, validate_snippet

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "new_platform_scaffold.py"


def _load_stub(tmp_path: Path, *, key: str = "examplengine", mode: str = "sql"):
    out = tmp_path / key
    out.mkdir()
    (out / "manifest.yaml").write_text(
        yaml.safe_dump(manifest_snippet(key=key, display_name="Examplengine", mode=mode), sort_keys=False)
    )
    stub_path = out / f"{key}_platform.py"
    stub_path.write_text(adapter_stub(key=key, display_name="Examplengine", mode=mode))
    spec = importlib.util.spec_from_file_location(f"{key}_platform", stub_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaffold_manifest_declares_no_reserved_canonical_keys(tmp_path: Path) -> None:
    snippet = manifest_snippet(key="examplengine", display_name="Examplengine", mode="sql")
    assert validate_snippet(snippet) == []
    assert snippet["capabilities"]["execution_engines"] == {}
    assert snippet["support_status"] == "experimental"


def test_scaffold_stub_resolves_default_engine_receipt(tmp_path: Path) -> None:
    module = _load_stub(tmp_path)

    class ConcreteAdapter(module.ExamplengineAdapter):
        @classmethod
        def add_cli_arguments(cls, parser):
            return parser

        @classmethod
        def from_config(cls, **config):
            return cls(**config)

        def apply_constraint_configuration(self, *args, **kwargs):
            return None

        def apply_platform_optimizations(self, *args, **kwargs):
            return None

        def configure_for_benchmark(self, *args, **kwargs):
            return None

        def create_connection(self, *args, **kwargs):
            raise NotImplementedError

        def create_schema(self, *args, **kwargs):
            raise NotImplementedError

        def execute_query(self, *args, **kwargs):
            raise NotImplementedError

        def load_data(self, *args, **kwargs):
            raise NotImplementedError

        def get_target_dialect(self):
            return "generic"

    adapter = ConcreteAdapter()
    assert adapter.platform_name == "examplengine"
    receipt = adapter.resolve_execution_engine(DEFAULT_EXECUTION_ENGINE)
    assert receipt.resolution == "platform_default"
    assert receipt.requested == DEFAULT_EXECUTION_ENGINE


def test_scaffold_stub_registers_no_option_specs(tmp_path: Path) -> None:
    module = _load_stub(tmp_path, key="cleanengine")
    source = (tmp_path / "cleanengine" / "cleanengine_platform.py").read_text()
    assert "PlatformOptionSpec" not in source
    assert module.CleanengineAdapter.platform_name == "cleanengine"
    for reserved in RESERVED_OPTION_KEYS:
        assert f'"{reserved}"' not in source


def test_scaffold_engine_classes_are_core_classes() -> None:
    assert set(EXECUTION_ENGINE_CLASSES) >= {"adaptive", "in-memory", "streaming"}
    assert DEFAULT_EXECUTION_ENGINE == "default"


def test_scaffold_cli_renders_plan_and_stubs(tmp_path: Path) -> None:
    rendered = render(key="demoengine", display_name="Demoengine", mode="dataframe")
    assert "execution_engines: {}" in rendered
    assert "class DemoengineAdapter(PlatformAdapter):" in rendered
    assert "benchbox/platforms/dataframe/demoengine_df.py" in rendered
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--name", "demoengine", "--kind", "dataframe", "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert (tmp_path / "manifest.yaml").exists()
    assert (tmp_path / "demoengine_platform.py").exists()
