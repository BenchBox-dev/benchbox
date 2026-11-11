# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from pathlib import Path

import pytest

import benchbox

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_TPCDS_GENERATOR_DIR = Path(benchbox.__file__).parent / "core" / "tpcds" / "generator"

_TERMINATE_PAIR = re.compile(r'tpcds_option\("terminate"\)\s*,\s*"([^"]+)"')
_TERMINATE_TOKEN = re.compile(r'tpcds_option\("terminate"\)')
_RAW_OPTION = re.compile(r'"[-/][A-Za-z_]+"')


def _generator_sources() -> list[Path]:
    return sorted(_TPCDS_GENERATOR_DIR.glob("*.py"))


def test_generator_sources_present() -> None:
    sources = _generator_sources()
    assert sources, f"no TPC-DS generator sources under {_TPCDS_GENERATOR_DIR}"


@pytest.mark.parametrize("source", _generator_sources(), ids=lambda p: p.name)
def test_every_terminate_flag_is_n(source: Path) -> None:
    text = source.read_text(encoding="utf-8")

    token_count = len(_TERMINATE_TOKEN.findall(text))
    if token_count == 0:
        pytest.skip(f"{source.name} issues no dsdgen -terminate flag")

    values = _TERMINATE_PAIR.findall(text)
    assert len(values) == token_count, (
        f"{source.name}: {token_count} '-terminate' token(s) but "
        f"{len(values)} were followed by a string value -- a call site may pass "
        "the flag without an argument."
    )
    for value in values:
        assert value == "n", (
            f"{source.name}: dsdgen invoked with '-terminate {value}'; must be "
            "'n' to disable trailing field separators (BenchBox framing "
            "convention)."
        )


@pytest.mark.parametrize("source", _generator_sources(), ids=lambda p: p.name)
def test_dsdgen_options_use_the_platform_prefix(source: Path) -> None:
    raw = _RAW_OPTION.findall(source.read_text(encoding="utf-8"))
    assert not raw, f"{source.name}: pass dsdgen options through tpcds_option(), not raw literals {raw}"


