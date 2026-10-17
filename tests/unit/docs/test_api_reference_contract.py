from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).parents[3]
API_DOCS = ROOT / "docs/reference/python-api"
SYMBOLS = ROOT / "_project/design/api-public-symbols.json"
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
ANCHOR = re.compile(r"""(?:\(([A-Za-z0-9_.-]+)\)=|\bid=["']([^"']+)["']|<a\s+name=["']([^"']+)["'])""")


def _authored_names() -> set[str]:
    names: set[str] = set()
    for page in API_DOCS.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        for heading in HEADING.findall(text):
            names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", heading))
        for groups in ANCHOR.findall(text):
            names.update(group for group in groups if group)
    return names


def _documented(symbol: dict[str, str], names: set[str]) -> bool:
    candidates = {symbol["symbol"], *filter(None, [symbol.get("name")]), *symbol.get("aliases", [])}
    candidates |= {c.rsplit(".", 1)[-1] for c in candidates}
    return bool(candidates & names)


@pytest.mark.skipif(not SYMBOLS.exists(), reason="_project site inventory is not in this checkout (release tree)")
def test_every_public_symbol_has_an_authored_heading_or_anchor() -> None:
    names = _authored_names()
    inventory = json.loads(SYMBOLS.read_text(encoding="utf-8"))
    missing = sorted(s["symbol"] for s in inventory["symbols"] if not _documented(s, names))
    assert not missing, f"public symbols without an authored heading or anchor under {API_DOCS}: {missing}"
