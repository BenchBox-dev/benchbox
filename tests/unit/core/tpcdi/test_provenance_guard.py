from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parents[4]
SOURCES = REPO_ROOT / "_sources" / "tpc-di"
DATABRICKS = SOURCES / "databricks-tpc-di"
HASH_LIST = DATABRICKS / "FORBIDDEN_HASHES.json"
DECISION_RECORD = REPO_ROOT / "_project" / "decisions" / "tpcdi-databricks-base-provenance-2026-11-21.md"
TOOLS_DIR = "benchbox/_binaries/tpc-di/tools/"
ETL_DIR = "benchbox/core/tpcdi/etl/"
ETL_PROVENANCE = "benchbox/core/tpcdi/etl/PROVENANCE.json"
FORBIDDEN_NAMES = frozenset({"pdgf.jar", "digen.jar"})
TOOLS_ZIP_MARKERS = ("tpc-di", "tpcdi", "digen", "pdgf")
COPYRIGHT_LINE = "Copyright (c) 2023-2026 Shannon Barrow and the databricks-tpc-di contributors"
ATTRIBUTION_SENTENCE = "BenchBox's TPC-DI ETL is based on Shannon Barrow's databricks-tpc-di (MIT)."
MIT_INSTRUCTION = (
    "You will consider the license to be MIT for the purposes of this effort and we will include the usual "
    "TPC caveats and limitations"
)
DECISIONS_SHA256 = "cd5b92e5df4fc32ab4c0c30f151318e99f49b351f1d68afb96fb9f0b21a862ea"
APPENDIX_SHARED_COPY = {
    "AppendixA/tpcdi_audit.sql": "32eb0f55a08b8cf5a906d4aff563539d42c873c72d6dc662798218b2bc3107fc",
    "AppendixB/tpcdi_validation.sql": "9ee9ef8144c7f53bb6fb0d4ac489dda2379e7aa1b8e24d7a12df1539b7f86c02",
    "AppendixC/tpcdi_visibility_2.sql": "a22645d5c9d2095d8d77549db3e7ecbc8d5d5417e8f0973fbd92150347ea894a",
}
FORBIDDEN_GROUPS = ("generator_and_tools", "audit_snapshots", "digen_output")


def load_hash_list() -> dict:
    return json.loads(HASH_LIST.read_text(encoding="utf-8"))


def forbidden_index(hash_list: dict) -> dict[str, set]:
    entries = [entry for group in FORBIDDEN_GROUPS for entry in hash_list["forbidden_by_content"][group]]
    return {"sha256": {entry["sha256"] for entry in entries}, "bytes": {entry["bytes"] for entry in entries}}


def tree_files(root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    ).stdout
    return sorted(name for name in listed.decode("utf-8").split("\0") if name and (root / name).is_file())


def recorded_hash_violations(root: Path, files: list[str], index: dict) -> list[str]:
    found = []
    for name in files:
        if name.startswith(TOOLS_DIR):
            continue
        path = root / name
        if path.stat().st_size not in index["bytes"]:
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() in index["sha256"]:
            found.append(name)
    return found


def misplaced_tool_violations(files: list[str]) -> list[str]:
    found = []
    for name in files:
        if name.startswith(TOOLS_DIR):
            continue
        base = name.rsplit("/", 1)[-1].lower()
        is_tools_zip = base.endswith(".zip") and any(marker in base for marker in TOOLS_ZIP_MARKERS)
        if base in FORBIDDEN_NAMES or base.endswith(".dict") or is_tools_zip:
            found.append(name)
    return found


def listed_etl_sql(root: Path) -> set[str]:
    path = root / ETL_PROVENANCE
    if not path.is_file():
        return set()
    return {entry["path"] for entry in json.loads(path.read_text(encoding="utf-8"))["files"]}


def unlisted_sql_violations(root: Path, files: list[str]) -> list[str]:
    listed = listed_etl_sql(root)
    return [name for name in files if name.startswith(ETL_DIR) and name.endswith(".sql") and name not in listed]


def write(root: Path, name: str, content: bytes) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return name


def index_of(content: bytes) -> dict:
    return {"sha256": {hashlib.sha256(content).hexdigest()}, "bytes": {len(content)}}


def test_hash_list_covers_the_projects_files():
    hash_list = load_hash_list()
    assert hash_list["commit"].startswith("8e0f944")
    for group in FORBIDDEN_GROUPS:
        assert hash_list["forbidden_by_content"][group]
    for entry in (e for group in FORBIDDEN_GROUPS for e in hash_list["forbidden_by_content"][group]):
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        assert entry["bytes"] > 0
        assert entry["path"].startswith("src/tools/")
    appendix = {
        entry["path"].split("Appendix_v1.1.0/", 1)[1]: entry["sha256"] for entry in hash_list["appendix_v1_1_0"]
    }
    assert set(appendix) == {
        "AppendixA/tpcdi_audit.sql",
        "AppendixB/tpcdi_validation.sql",
        "AppendixC/tpcdi_visibility_1.sql",
        "AppendixC/tpcdi_visibility_2.sql",
    }
    for name, digest in APPENDIX_SHARED_COPY.items():
        assert appendix[name] == digest


def test_tree_has_no_recorded_file_outside_the_tools_directory():
    assert recorded_hash_violations(REPO_ROOT, tree_files(REPO_ROOT), forbidden_index(load_hash_list())) == []


def test_tree_has_no_tool_file_outside_the_tools_directory():
    assert misplaced_tool_violations(tree_files(REPO_ROOT)) == []


def test_tree_has_no_unlisted_etl_sql():
    assert unlisted_sql_violations(REPO_ROOT, tree_files(REPO_ROOT)) == []


def test_guard_fails_on_a_recorded_hash(tmp_path):
    content = b"generator text taken from the project\n"
    name = write(tmp_path, "benchbox/core/tpcdi/copied.txt", content)
    write(tmp_path, "benchbox/core/tpcdi/original.txt", b"different\n")
    assert recorded_hash_violations(tmp_path, [name, "benchbox/core/tpcdi/original.txt"], index_of(content)) == [name]


def test_guard_allows_a_recorded_hash_in_the_tools_directory(tmp_path):
    content = b"official tool bytes\n"
    name = write(tmp_path, TOOLS_DIR + "DIGen.jar", content)
    assert recorded_hash_violations(tmp_path, [name], index_of(content)) == []


@pytest.mark.parametrize(
    "name",
    [
        "benchbox/core/tpcdi/pdgf.jar",
        "vendor/DIGen.jar",
        "benchbox/core/tpcdi/generator/city.dict",
        "benchbox/_binaries/tpc-di/TPC-DI_Tools_v1.1.0.zip",
        "_sources/tpc-di/tpc-di-tool.zip",
    ],
)
def test_guard_fails_on_a_forbidden_file_name_outside_the_tools_directory(name):
    assert misplaced_tool_violations([name]) == [name]


@pytest.mark.parametrize(
    "name",
    [
        TOOLS_DIR + "pdgf.jar",
        TOOLS_DIR + "DIGen.jar",
        TOOLS_DIR + "city.dict",
        TOOLS_DIR + "TPC-DI_Tools_v1.1.0.zip",
        "benchbox/core/tpcdi/schema.py",
        "benchbox/_binaries/tpc-h/dists.dss",
    ],
)
def test_guard_allows_tool_files_in_the_tools_directory_and_other_files(name):
    assert misplaced_tool_violations([name]) == []


def test_guard_fails_on_an_unlisted_sql_file(tmp_path):
    unlisted = write(tmp_path, ETL_DIR + "dimensions/dim_customer.sql", b"SELECT 1\n")
    assert unlisted_sql_violations(tmp_path, [unlisted]) == [unlisted]


def test_guard_accepts_a_listed_sql_file(tmp_path):
    listed = write(tmp_path, ETL_DIR + "dimensions/dim_customer.sql", b"SELECT 1\n")
    other = write(tmp_path, ETL_DIR + "dimensions/dim_account.sql", b"SELECT 2\n")
    write(tmp_path, ETL_PROVENANCE, json.dumps({"files": [{"path": listed}]}).encode())
    assert unlisted_sql_violations(tmp_path, [listed, other]) == [other]


def test_guard_ignores_sql_outside_the_etl_directory(tmp_path):
    name = write(tmp_path, "benchbox/core/tpch/queries/q1.sql", b"SELECT 1\n")
    assert unlisted_sql_violations(tmp_path, [name]) == []


def test_license_copyright_line_is_the_owners_text():
    lines = (DATABRICKS / "LICENSE").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "MIT License"
    assert lines[2] == COPYRIGHT_LINE


def test_copyright_md_names_the_project_as_a_third_party_source():
    text = (REPO_ROOT / "COPYRIGHT.md").read_text(encoding="utf-8")
    assert COPYRIGHT_LINE in text
    assert ATTRIBUTION_SENTENCE in text
    assert "### Third-Party Sources" in text


def test_provenance_records_commit_and_the_owners_mit_instruction():
    text = (DATABRICKS / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "https://github.com/shannon-barrow/databricks-tpc-di" in text
    assert "8e0f944" in text
    assert "2026-11-21" in text
    assert f'> "{MIT_INSTRUCTION}"' in text
    assert ATTRIBUTION_SENTENCE in text


def test_decision_record_quotes_the_owner_decisions_unchanged():
    text = DECISION_RECORD.read_text(encoding="utf-8")
    section = re.search(r"^## Owner decisions, verbatim\n(.*?)(?=^## )", text, re.S | re.M)
    assert section is not None
    assert hashlib.sha256(section.group(1).strip().encode("utf-8")).hexdigest() == DECISIONS_SHA256
    assert f'"{MIT_INSTRUCTION}"' in section.group(1)
    assert "rely on the shared common versions for now and file a tracker to follow up on this later and settle it" in (
        section.group(1)
    )
    assert COPYRIGHT_LINE in section.group(1)
    assert "## Licence texts the decision to ship the tools rests on" in text
