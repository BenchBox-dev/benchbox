# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_ABC_SIGNATURES: dict[str, list[str]] = {
    "create_schema": ["benchmark", "connection"],
    "load_data": ["benchmark", "connection", "data_dir"],
    "execute_query": ["connection", "query", "query_id"],
}

_SKIP_FILES = {
    "__init__.py",
    "_spark_helpers.py",
    "adapter_factory.py",
    "cloud_shared.py",
    "presto_trino_utils.py",
    "datafusion_write_transformer.py",
}

_PLATFORMS_DIR = Path(__file__).parents[3] / "benchbox" / "platforms"
_SKIP_DIRS = frozenset({"base", "credentials", "dataframe"})


def _positional_params(funcdef: ast.FunctionDef) -> list[str]:
    args = funcdef.args
    positional = [a.arg for a in args.args[1:]]
    return positional


def _collect_adapter_method_overrides() -> list[tuple[str, str, str, list[str]]]:
    overrides: list[tuple[str, str, str, list[str]]] = []

    for py_file in sorted(_PLATFORMS_DIR.rglob("*.py")):
        relative_path = py_file.relative_to(_PLATFORMS_DIR)
        if any(part in _SKIP_DIRS for part in relative_path.parts):
            continue
        if py_file.name in _SKIP_FILES:
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = set()
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.add(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.add(base.attr)

            is_abstract_base = node.name in {"PlatformAdapter", "BaseDdlOptimizer"}
            if is_abstract_base:
                continue

            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                if item.name not in _ABC_SIGNATURES:
                    continue
                params = _positional_params(item)
                overrides.append((relative_path.as_posix(), node.name, item.name, params))

    return overrides


_OVERRIDES = _collect_adapter_method_overrides()


@pytest.mark.parametrize("file_name,class_name,method_name,actual_params", _OVERRIDES)
def test_method_signature_matches_abc(
    file_name: str,
    class_name: str,
    method_name: str,
    actual_params: list[str],
) -> None:
    expected = _ABC_SIGNATURES[method_name]
    leading = actual_params[: len(expected)]

    assert leading == expected, (
        f"{file_name}::{class_name}.{method_name} positional params {actual_params!r} "
        f"do not match ABC contract {expected!r}.\n"
        f"The orchestrator calls this method with positional args in the ABC order; "
        f"mismatched names indicate parameter binding bugs (see C2/C3 audit findings)."
    )
