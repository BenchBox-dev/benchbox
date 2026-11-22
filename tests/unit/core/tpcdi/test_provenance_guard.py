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
TPC_DIRS = ("benchbox/_binaries/tpc-di/", "_sources/tpc-di/")
APPROVED_HASHES = REPO_ROOT / "quality" / "comment-policy-approved-external-hashes.json"
FORBIDDEN_NAMES = frozenset(
    {
        "pdgf.jar",
        "digen.jar",
        "digen.asp",
        "digen_build.zip",
        "tpc-di.jar",
        "tpc-di_src.jar",
        "commons-net-3.3.jar",
        "commons.lang3.time.jar",
        "javassist.jar",
        "log4j-1.2.15.jar",
        "xercesimpl.jar",
        "xml-apis.jar",
        "constants.properties",
        "pseudotextgenerator.properties",
        "log4j.xml",
        "pdgf.log",
    }
)
FORBIDDEN_NAME_PATTERNS = (re.compile(r"tpc-di-.*\.xml"), re.compile(r".*-audit\.xml"))
TOOLS_ZIP_MARKERS = ("tpc-di", "tpcdi", "digen", "pdgf")
COPYRIGHT_LINE = "Copyright (c) 2023-2026 Shannon Barrow and the databricks-tpc-di contributors"
ATTRIBUTION_SENTENCE = "BenchBox's TPC-DI ETL is based on Shannon Barrow's databricks-tpc-di (MIT)."
MIT_INSTRUCTION = (
    "You will consider the license to be MIT for the purposes of this effort and we will include the usual "
    "TPC caveats and limitations"
)
DECISIONS_SHA256 = "e1a09a4e668fe9c66461b270c021f9f19627308c42ffd811bc06545afcdf9de5"
APPENDIX_SHARED_COPY = {
    "AppendixA/tpcdi_audit.sql": "32eb0f55a08b8cf5a906d4aff563539d42c873c72d6dc662798218b2bc3107fc",
    "AppendixB/tpcdi_validation.sql": "9ee9ef8144c7f53bb6fb0d4ac489dda2379e7aa1b8e24d7a12df1539b7f86c02",
    "AppendixC/tpcdi_visibility_2.sql": "a22645d5c9d2095d8d77549db3e7ecbc8d5d5417e8f0973fbd92150347ea894a",
}
FORBIDDEN_GROUPS = ("generator_and_tools", "audit_snapshots", "digen_output")


def load_hash_list() -> dict:
    return json.loads(HASH_LIST.read_text(encoding="utf-8"))


def forbidden_hashes(hash_list: dict) -> tuple[set[str], int]:
    entries = [entry for group in FORBIDDEN_GROUPS for entry in hash_list["forbidden_by_content"][group]]
    return {entry["sha256"] for entry in entries}, max(entry["bytes"] for entry in entries)


def official_hashes() -> set[str]:
    return {entry["sha256"] for entry in json.loads(APPROVED_HASHES.read_text(encoding="utf-8"))["files"]}


def tree_files(root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    ).stdout
    return sorted(name for name in listed.decode("utf-8").split("\0") if name and (root / name).is_file())


def file_digests(root: Path, files: list[str], max_bytes: int) -> dict[str, str]:
    digests = {}
    for name in files:
        path = root / name
        if path.stat().st_size <= max_bytes:
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def recorded_hash_violations(digests: dict[str, str], forbidden: set[str], official: set[str]) -> list[str]:
    return [
        name
        for name, digest in digests.items()
        if digest in forbidden and not (name.startswith(TOOLS_DIR) and digest in official)
    ]


def official_misplaced_violations(digests: dict[str, str], official: set[str]) -> list[str]:
    return [
        name
        for name, digest in digests.items()
        if digest in official and not any(name.startswith(directory) for directory in TPC_DIRS)
    ]


def misplaced_tool_violations(files: list[str]) -> list[str]:
    found = []
    for name in files:
        if name.startswith(TOOLS_DIR):
            continue
        base = name.rsplit("/", 1)[-1].lower()
        marked = base.replace("_", "-")
        is_tools_zip = base.endswith(".zip") and any(marker in marked for marker in TOOLS_ZIP_MARKERS)
        is_named = base in FORBIDDEN_NAMES or any(pattern.fullmatch(base) for pattern in FORBIDDEN_NAME_PATTERNS)
        if is_named or base.endswith(".dict") or is_tools_zip:
            found.append(name)
    return found


def gitlink_entries(root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "-z"], check=True, capture_output=True
    ).stdout.decode("utf-8")
    return sorted(
        entry.split("\t", 1)[1] for entry in listed.split("\0") if entry.startswith("160000 ") and "\t" in entry
    )


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


@pytest.fixture(scope="module")
def tree_digests() -> dict[str, str]:
    _, max_bytes = forbidden_hashes(load_hash_list())
    return file_digests(REPO_ROOT, tree_files(REPO_ROOT), max_bytes)


def test_tree_has_no_recorded_file_that_is_not_an_official_tool_file(tree_digests):
    forbidden, _ = forbidden_hashes(load_hash_list())
    assert recorded_hash_violations(tree_digests, forbidden, official_hashes()) == []


def test_tree_has_no_official_archive_file_outside_the_tpc_directories(tree_digests):
    assert official_misplaced_violations(tree_digests, official_hashes()) == []


def test_tree_has_no_tool_file_outside_the_tools_directory():
    assert misplaced_tool_violations(tree_files(REPO_ROOT)) == []


def test_tree_has_no_gitlink():
    assert gitlink_entries(REPO_ROOT) == []


def test_tree_has_no_unlisted_etl_sql():
    assert unlisted_sql_violations(REPO_ROOT, tree_files(REPO_ROOT)) == []


def test_guard_fails_on_a_recorded_hash(tmp_path):
    content = b"generator text taken from the project\n"
    name = write(tmp_path, "benchbox/core/tpcdi/copied.txt", content)
    other = write(tmp_path, "benchbox/core/tpcdi/original.txt", b"different\n")
    digests = file_digests(tmp_path, [name, other], 1000)
    forbidden = {hashlib.sha256(content).hexdigest()}
    assert recorded_hash_violations(digests, forbidden, set()) == [name]


def test_guard_allows_a_recorded_hash_in_the_tools_directory_only_when_official(tmp_path):
    content = b"official tool bytes\n"
    name = write(tmp_path, TOOLS_DIR + "DIGen.jar", content)
    digests = file_digests(tmp_path, [name], 1000)
    forbidden = {hashlib.sha256(content).hexdigest()}
    assert recorded_hash_violations(digests, forbidden, set(forbidden)) == []
    assert recorded_hash_violations(digests, forbidden, set()) == [name]


def test_guard_fails_on_a_recorded_hash_in_the_tools_directory_without_an_official_match(tmp_path):
    content = b"generator code copied into the tools directory\n"
    name = write(tmp_path, TOOLS_DIR + "tpcdi_gen/audit.py", content)
    digests = file_digests(tmp_path, [name], 1000)
    assert recorded_hash_violations(digests, {hashlib.sha256(content).hexdigest()}, {"0" * 64}) == [name]


def test_guard_fails_on_an_official_archive_file_outside_the_tpc_directories(tmp_path):
    content = b"bytes of a file of the official archive\n"
    outside = write(tmp_path, "benchbox/core/tpcdi/renamed.bin", content)
    in_tools = write(tmp_path, TOOLS_DIR + "renamed.bin", content)
    in_sources = write(tmp_path, "_sources/tpc-di/renamed.bin", content)
    digests = file_digests(tmp_path, [outside, in_tools, in_sources], 1000)
    assert official_misplaced_violations(digests, {hashlib.sha256(content).hexdigest()}) == [outside]


def test_guard_fails_on_a_gitlink(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    commit = "1" * 40
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor/tools"],
        check=True,
    )
    write(tmp_path, "plain.txt", b"x\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "plain.txt"], check=True)
    assert gitlink_entries(tmp_path) == ["vendor/tools"]


@pytest.mark.parametrize(
    "name",
    [
        "benchbox/core/tpcdi/pdgf.jar",
        "vendor/DIGen.jar",
        "benchbox/core/tpcdi/generator/city.dict",
        "benchbox/_binaries/tpc-di/TPC-DI_Tools_v1.1.0.zip",
        "_sources/tpc-di/tpc-di-tool.zip",
        "vendor/tpc_di_tools.zip",
        "vendor/tpc-di.jar",
        "vendor/tpc-di_src.jar",
        "vendor/extlib/xercesImpl.jar",
        "vendor/extlib/commons.lang3.time.jar",
        "benchbox/core/tpcdi/config/tpc-di-generation.xml",
        "benchbox/core/tpcdi/config/Account-Audit.xml",
        "vendor/Constants.properties",
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
        TOOLS_DIR + "tpc-di.jar",
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
