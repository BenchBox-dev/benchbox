import hashlib
import re
from pathlib import Path

import pytest

import benchbox.core.tpcds_obt as tpcds_obt_package
from benchbox.core.tpcds_obt.catalog.loader import PLACEHOLDER_PATTERN, OBTCatalogError, OBTQuery, load_catalog
from benchbox.utils.tpc_compilation import get_tpc_templates_dir

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

TEMPLATE_DIR = get_tpc_templates_dir("tpc-ds") / "query_templates"
DEFINE_PATTERN = re.compile(r"^define\s+(\w+)\s*=\s*(.*?);\s*$", re.IGNORECASE | re.DOTALL)
FACT_COLUMN_PATTERN = re.compile(r"^(ss|sr|ws|wr|cs|cr)_\w+$")
UNPORTED_DEFINES = {14: {"DAY"}}
EXCLUDED_QUERY_IDS = {21, 22, 37, 39, 46, 64, 68, 72, 82, 84}


def _template_text(query: OBTQuery) -> str:
    return (TEMPLATE_DIR / query.template).read_text(encoding="utf-8")


def _template_definitions(text: str) -> dict[str, str]:
    definitions: dict[str, str] = {}
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if buffer:
            buffer.append(stripped)
        elif stripped.lower().startswith("define"):
            buffer = [stripped]
        else:
            continue
        if stripped.endswith(";"):
            match = DEFINE_PATTERN.match(" ".join(buffer))
            if match:
                definitions[match.group(1)] = match.group(2)
            buffer = []
    return definitions


def _template_placeholders(text: str, names: set[str]) -> dict[str, tuple[int, ...]]:
    body = "\n".join(line for line in text.splitlines() if not line.strip().lower().startswith(("define", "--")))
    found: dict[str, set[int]] = {name: set() for name in names}
    for name, index in PLACEHOLDER_PATTERN.findall(body):
        if name in found:
            found[name].add(int(index or 1))
    return {name: tuple(sorted(indices)) for name, indices in found.items()}


def _choice_items(definition: str) -> set[str]:
    return {item.strip() for choice in re.findall(r'\{"([^"]+)"', definition) for item in choice.split(",")}


def _queries() -> list[OBTQuery]:
    return sorted(load_catalog().values(), key=lambda query: query.id)


def test_catalog_covers_the_published_query_ids() -> None:
    ids = {query.id for query in _queries()}
    assert len(ids) == 89
    assert not ids & EXCLUDED_QUERY_IDS


@pytest.mark.parametrize("query", _queries(), ids=lambda query: f"q{query.id}")
def test_template_sha256_matches_the_tpcds_source(query: OBTQuery) -> None:
    digest = hashlib.sha256((TEMPLATE_DIR / query.template).read_bytes()).hexdigest()
    assert digest == query.template_sha256, f"query{query.id}.tpl changed; re-derive the catalog entry"


@pytest.mark.parametrize("query", _queries(), ids=lambda query: f"q{query.id}")
def test_placeholders_match_the_dsqgen_defines(query: OBTQuery) -> None:
    text = _template_text(query)
    defined = {name for name in _template_definitions(text) if not name.startswith("_")}
    assert set(query.parameters) <= defined, f"query{query.id} declares parameters the template does not define"
    template_usage = _template_placeholders(text, set(query.parameters))
    for name, indices in query.parameters.items():
        assert set(indices) <= set(template_usage[name]) or not template_usage[name], (query.id, name)
    unused_defines = defined - set(query.parameters) - UNPORTED_DEFINES.get(query.id, set())
    for name in unused_defines:
        assert not _template_placeholders(text, {name})[name], (query.id, name)


@pytest.mark.parametrize("query", _queries(), ids=lambda query: f"q{query.id}")
def test_identifier_map_covers_the_dsqgen_choices(query: OBTQuery) -> None:
    definitions = _template_definitions(_template_text(query))
    choices: set[str] = set()
    for name in query.identifier_parameters:
        choices |= _choice_items(definitions[name])
    lowered = {choice.lower() for choice in choices}
    assert set(query.identifier_map) <= lowered, f"query{query.id} maps identifiers the template no longer draws"
    unmapped = {
        choice
        for choice in choices
        if FACT_COLUMN_PATTERN.match(choice.lower()) and choice.lower() not in query.identifier_map
    }
    assert not unmapped, f"query{query.id} draws source columns with no OBT mapping: {sorted(unmapped)}"


def test_catalog_rejects_undeclared_placeholders() -> None:
    from benchbox.core.tpcds_obt.catalog import loader

    with pytest.raises(OBTCatalogError):
        loader._parse_entry(
            0,
            {"id": 1, "template": "query1.tpl", "template_sha256": "x", "sql": "SELECT [YEAR]", "parameters": {}},
        )


def test_obt_package_does_not_import_sqlglot() -> None:
    package_dir = Path(tpcds_obt_package.__file__).parent
    offenders = [
        str(path.relative_to(package_dir))
        for path in package_dir.rglob("*.py")
        if "sqlglot" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders
