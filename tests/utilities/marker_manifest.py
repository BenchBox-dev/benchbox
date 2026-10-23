from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_deselected: list[pytest.Item] = []


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--marker-manifest",
        default=None,
        help="Write every collected and deselected test id with its marker names to this JSON file.",
    )


def pytest_deselected(items: list[pytest.Item]) -> None:
    _deselected.extend(items)


def _record(item: pytest.Item) -> dict[str, object]:
    return {
        "id": item.nodeid,
        "markers": sorted({mark.name for mark in item.iter_markers()}),
    }


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    target = session.config.getoption("--marker-manifest")
    if not target or session.testsfailed:
        return
    records = [_record(item) for item in session.items]
    records.extend(_record(item) for item in _deselected)
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + f".{os.getpid()}.tmp")
    partial.write_text(json.dumps({"version": 1, "items": records}, separators=(",", ":")), encoding="utf-8")
    partial.replace(path)
