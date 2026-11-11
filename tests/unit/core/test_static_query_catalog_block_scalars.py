from __future__ import annotations

import re
from importlib import resources

import pytest
import yaml

from benchbox.core.static_query_catalog import load_static_query_catalog

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CATALOG_PACKAGES = [
    "benchbox.core.flightdata",
    "benchbox.core.nyctaxi",
    "benchbox.core.tsbs_devops",
]

_BLOCK_SQL = re.compile(r"(?m)^\s*sql: \|")


def _iter_sql(obj: object):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "sql" and isinstance(value, str):
                yield value
            else:
                yield from _iter_sql(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_sql(item)


@pytest.mark.parametrize("package", CATALOG_PACKAGES)
def test_all_sql_fields_are_block_scalars(package: str) -> None:
    raw = resources.files(package).joinpath("query_catalog.yaml").read_text(encoding="utf-8")
    n_fields = sum(1 for _ in _iter_sql(load_static_query_catalog(package)))
    assert n_fields, f"{package}: no sql fields found"

    assert 'sql: "' not in raw, f"{package}: escaped double-quoted sql scalar present"

    assert len(_BLOCK_SQL.findall(raw)) == n_fields, f"{package}: not every sql field is a block scalar"


@pytest.mark.parametrize("package", CATALOG_PACKAGES)
def test_sql_survives_yaml_round_trip(package: str) -> None:
    for sql in _iter_sql(load_static_query_catalog(package)):
        assert sql.strip(), f"{package}: empty sql"

        reloaded = yaml.safe_load(yaml.dump(sql, default_style="|", allow_unicode=True, width=10**9))
        assert reloaded == sql, f"{package}: sql does not survive a block-scalar round-trip"

        assert sql.count("{") == sql.count("}"), f"{package}: unbalanced placeholder braces in sql"


