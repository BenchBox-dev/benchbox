from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parents[4]
DATABRICKS = REPO_ROOT / "_sources" / "tpc-di" / "databricks-tpc-di"
HASH_LIST = DATABRICKS / "FORBIDDEN_HASHES.json"
DECISION_RECORD = REPO_ROOT / "_project" / "decisions" / "tpcdi-databricks-base-provenance-2026-11-21.md"
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


def test_hash_list_covers_the_projects_files():
    hash_list = json.loads(HASH_LIST.read_text(encoding="utf-8"))
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
