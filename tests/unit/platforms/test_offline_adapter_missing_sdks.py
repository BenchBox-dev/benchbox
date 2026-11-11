from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

INVENTORY = Path(__file__).with_name("test_offline_adapter_inventory.py")
SUCCESS_MESSAGE = "49 adapters, 94 real SQL assertions, 2 positive alternatives, no vendor SDK imports"


CHILD_SCRIPT = r"""
if not __debug__:
    raise SystemExit("assertions are disabled, so the inventory would pass without checking anything")
import importlib.abc
import runpy
import sys
from benchbox.core.platform_manifest import get_adapter_imports, get_platform_metadata

metadata = get_platform_metadata()
keys = {key for key, _, _ in get_adapter_imports()}
roots = {
    library.get("import_name", library["name"]).split(".")[0]
    for key in keys
    for library in metadata[key].get("libraries", [])
} - {"sqlite3"}
assert {"duckdb", "polars", "snowflake", "pyspark", "google", "boto3"} <= roots

class NoVendorSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in roots:
            raise ModuleNotFoundError("SDK deliberately absent: " + fullname, name=fullname)

for name in list(sys.modules):
    if name.split(".")[0] in roots:
        del sys.modules[name]
sys.meta_path.insert(0, NoVendorSDK())
suite = runpy.run_path(sys.argv[1])
suite["test_inventory_conserves_all_concrete_adapters"]()
suite["test_every_dialect_is_covered_by_translation_or_exemption"]()
for key, dialect in suite["ADAPTER_DIALECTS"].items():
    suite["test_adapter_class_loads_and_reports_expected_dialect"](key)
    if dialect in suite["EXPECTED_SQL"]:
        for statement in ("ddl", "query"):
            suite["test_real_translation_of_ddl_and_query"](key, statement)
    else:
        suite["test_non_sql_platforms_reject_strict_sql_translation"](key)
suite["test_polars_structured_schema_without_sql_or_sdk"]()
suite["test_influx_measurement_schema_and_typed_line_protocol"]()
assert not any(name.split(".")[0] in roots for name in sys.modules)
print("49 adapters, 94 real SQL assertions, 2 positive alternatives, no vendor SDK imports")
"""


def _run_child(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", CHILD_SCRIPT, str(INVENTORY)],
        cwd=INVENTORY.parents[3],
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def test_all_adapter_semantics_with_vendor_sdk_imports_blocked(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    env = {key: value for key, value in os.environ.items() if key != "PYTHONOPTIMIZE"}
    result = _run_child(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert SUCCESS_MESSAGE in result.stdout


def test_child_refuses_to_run_with_assertions_disabled() -> None:
    result = _run_child({**os.environ, "PYTHONOPTIMIZE": "1"})
    assert result.returncode != 0
    assert "assertions are disabled" in result.stderr
    assert SUCCESS_MESSAGE not in result.stdout


