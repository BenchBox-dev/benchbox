from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_CORE = Path(__file__).resolve().parents[3] / "benchbox" / "core"
_BARE_FLOAT = re.compile(r"\bFLOAT\b")
_SNOWFLAKE_VECTOR_ELEMENT = re.compile(r"VECTOR\(FLOAT,\s*(?:\{dim\}|\d+)\)")
_PROSE_KEYS = {"description", "comment", "notes"}


def _spec_files() -> list[Path]:
    paths = {*_CORE.rglob("*schema*.yaml"), *_CORE.rglob("table_specs.yaml")}
    return sorted(paths)


def _strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(node, str):
        return [(path, node)]
    if isinstance(node, dict):
        return [
            pair
            for key, value in node.items()
            if key not in _PROSE_KEYS
            for pair in _strings(value, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(node, list):
        return [pair for index, value in enumerate(node) for pair in _strings(value, f"{path}[{index}]")]
    return []


def _bare_float_values(spec: Any) -> list[tuple[str, str]]:
    return [
        (path, text) for path, text in _strings(spec) if _BARE_FLOAT.search(_SNOWFLAKE_VECTOR_ELEMENT.sub("", text))
    ]


def test_schema_specs_are_discovered():
    names = {path.parent.name for path in _spec_files()}
    assert {"vector_search", "tpch", "flightdata"} <= names


@pytest.mark.parametrize("spec_file", _spec_files(), ids=lambda path: str(path.relative_to(_CORE)))
def test_schema_spec_declares_float_width(spec_file: Path):
    spec = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
    assert _bare_float_values(spec) == []


@pytest.mark.parametrize(
    "text",
    ["FLOAT", "FLOAT[128]", "ARRAY<FLOAT>", "FLOAT[{dim}]", "ARRAY(FLOAT)"],
)
def test_guard_rejects_bare_float(text: str):
    assert _bare_float_values({"type": text}) == [("type", text)]


@pytest.mark.parametrize(
    "text",
    [
        "REAL",
        "REAL[128]",
        "ARRAY<REAL>",
        "DOUBLE",
        "Array(Float32)",
        "ARRAY<FLOAT64>",
        "VECTOR(FLOAT, {dim})",
        "VECTOR({dim}, F32)",
    ],
)
def test_guard_accepts_explicit_width(text: str):
    assert _bare_float_values({"type": text}) == []
