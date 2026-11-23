from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from benchbox.core.query_catalog import get_sql_render, list_query_ids, query_source_path
from benchbox.utils.tpc_compilation import get_tpc_templates_dir

ROOT = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.unit, pytest.mark.fast]

CANONICAL_TPCH_EULA = ROOT / "_sources" / "tpc-h" / "EULA.txt"
CANONICAL_TPCDS_EULA = ROOT / "_sources" / "tpc-ds" / "EULA.txt"
SOURCE_TPCDS_EULA = ROOT / "_sources" / "tpc-ds" / "query_templates" / "EULA.txt"


def _runtime_tpch_eula() -> Path:
    return get_tpc_templates_dir("tpc-h") / "queries" / "EULA.txt"


def _runtime_tpcds_eula() -> Path:
    return get_tpc_templates_dir("tpc-ds") / "query_templates" / "EULA.txt"


@pytest.mark.parametrize(
    ("canonical", "adjacent"),
    [
        pytest.param(CANONICAL_TPCH_EULA, _runtime_tpch_eula, id="tpch-runtime"),
        pytest.param(CANONICAL_TPCDS_EULA, _runtime_tpcds_eula, id="tpcds-runtime"),
        pytest.param(CANONICAL_TPCDS_EULA, lambda: SOURCE_TPCDS_EULA, id="tpcds-source-tree"),
    ],
)
def test_adjacent_tpc_eulas_match_canonical_notices(canonical: Path, adjacent) -> None:
    adjacent_path = adjacent()
    assert canonical.is_file(), f"canonical EULA missing: {canonical}"
    assert adjacent_path.is_file(), f"adjacent EULA missing: {adjacent_path}"
    assert adjacent_path.read_bytes() == canonical.read_bytes(), (
        f"adjacent EULA drifted from canonical: {adjacent_path}"
    )


def test_runtime_tpcds_templates_dir_is_the_shipped_bundle() -> None:
    assert get_tpc_templates_dir("tpc-ds") == ROOT / "benchbox" / "_binaries" / "tpc-ds" / "templates"


def test_query_catalog_resolves_templates_beside_notices() -> None:
    assert len(list_query_ids("tpch")) == 22
    assert query_source_path("tpch", "1") == "benchbox/_binaries/tpc-h/templates/queries/1.sql"
    assert "select" in get_sql_render("tpch", "1").sql.lower()

    assert len(list_query_ids("tpcds")) == 99
    assert query_source_path("tpcds", "1") == "_sources/tpc-ds/query_templates/query1.tpl"
    assert "select" in get_sql_render("tpcds", "1").sql.lower()


TPC_LEGEND = "THE TPC SOFTWARE IS AVAILABLE WITHOUT CHARGE FROM TPC."
TPCDI_SOURCE_DIR = ROOT / "_sources" / "tpc-di"
TPCDI_RUNTIME_DIR = ROOT / "benchbox" / "_binaries" / "tpc-di"
TPCDI_EULA_SHA256 = "017bd9dbc4c332ac433fc0cc018f3691b330f8aca82a05a20274512de423576e"
TPCDI_SHIPPED_FILES = (
    "EULA.txt",
    "NOTICE.txt",
    "tpcdi_audit.sql",
    "tpcdi_validation.sql",
    "tpcdi_visibility_1.sql",
    "tpcdi_visibility_2.sql",
)
NOTICE_DIRECTORIES = {
    "tpch-runtime": ROOT / "benchbox" / "_binaries" / "tpc-h",
    "tpcds-runtime": ROOT / "benchbox" / "_binaries" / "tpc-ds",
    "tpcdi-runtime": TPCDI_RUNTIME_DIR,
    "tpch-source-tree": ROOT / "_sources" / "tpc-h",
    "tpcds-source-tree": ROOT / "_sources" / "tpc-ds",
    "tpcdi-source-tree": TPCDI_SOURCE_DIR,
}


def _legend_is_line_one(notice: Path) -> bool:
    lines = notice.read_text(encoding="utf-8").splitlines()
    return bool(lines) and lines[0] == TPC_LEGEND


@pytest.mark.parametrize("directory", NOTICE_DIRECTORIES.values(), ids=NOTICE_DIRECTORIES.keys())
def test_tpc_notice_has_the_legend_on_line_one(directory: Path) -> None:
    assert _legend_is_line_one(directory / "NOTICE.txt")


@pytest.mark.parametrize("directory", NOTICE_DIRECTORIES.values(), ids=NOTICE_DIRECTORIES.keys())
def test_tpc_notice_check_fails_when_the_legend_is_removed(directory: Path, tmp_path: Path) -> None:
    original = (directory / "NOTICE.txt").read_text(encoding="utf-8")
    stripped = tmp_path / "NOTICE.txt"
    stripped.write_text(original.removeprefix(TPC_LEGEND + "\n"), encoding="utf-8")
    assert not _legend_is_line_one(stripped)
    moved = tmp_path / "MOVED.txt"
    moved.write_text("TPC notice\n" + original, encoding="utf-8")
    assert not _legend_is_line_one(moved)


@pytest.mark.parametrize("suite", ["tpc-h", "tpc-ds"])
def test_runtime_notice_sits_beside_the_shipped_binaries(suite: str) -> None:
    runtime = ROOT / "benchbox" / "_binaries" / suite
    assert (runtime / "NOTICE.txt").is_file()
    assert any(path.is_file() for path in runtime.glob("*/*gen*"))


@pytest.mark.parametrize("suite", ["tpc-h", "tpc-ds"])
def test_source_notice_is_listed_for_the_source_distribution(suite: str) -> None:
    assert f"include _sources/{suite}/NOTICE.txt" in (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("filename", TPCDI_SHIPPED_FILES)
def test_tpcdi_source_and_runtime_files_are_byte_identical(filename: str) -> None:
    source = TPCDI_SOURCE_DIR / filename
    runtime = TPCDI_RUNTIME_DIR / filename
    assert source.is_file(), f"missing: {source}"
    assert runtime.is_file(), f"missing: {runtime}"
    assert runtime.read_bytes() == source.read_bytes()


def test_tpcdi_eula_is_the_published_text() -> None:
    assert hashlib.sha256((TPCDI_RUNTIME_DIR / "EULA.txt").read_bytes()).hexdigest() == TPCDI_EULA_SHA256


def test_tpcdi_eula_wording_equals_the_tpch_eula() -> None:
    def words(path: Path) -> list[str]:
        text = path.read_bytes().decode("cp1252", errors="replace")
        text = re.sub(r"[\u2018\u2019\u201c\u201d\ufffd\"']", "", text)
        text = re.sub(r"(?m)^\s*(\d+|[a-z])[.\t ]", "", text)
        return re.sub(r"\W+", " ", text).lower().split()

    assert words(TPCDI_RUNTIME_DIR / "EULA.txt") == words(CANONICAL_TPCH_EULA)


def test_tpcdi_notice_names_the_unofficial_appendix_source() -> None:
    text = (TPCDI_RUNTIME_DIR / "NOTICE.txt").read_text(encoding="utf-8")
    assert "unofficial third-party copy" in text
    assert "18eca39b128f5fcc86cbc9c1e53e30ed0e685fa7" in text
    assert TPCDI_EULA_SHA256 in text
    for filename in TPCDI_SHIPPED_FILES[2:]:
        digest = hashlib.sha256((TPCDI_RUNTIME_DIR / filename).read_bytes()).hexdigest()
        assert f"{digest}  {filename}" in text


def test_readme_licence_section_carries_the_legend() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert TPC_LEGEND in text.replace("\n", " ")
