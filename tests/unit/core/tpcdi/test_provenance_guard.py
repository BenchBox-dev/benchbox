from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parents[4]
SCRIPT = REPO_ROOT / "scripts" / "check_tpcdi_copy_guard.py"

_spec = importlib.util.spec_from_file_location("check_tpcdi_copy_guard", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

TOOLS_DIR = guard.TOOLS_DIR
ETL_DIR = guard.ETL_DIR
ETL_PROVENANCE = guard.ETL_PROVENANCE


def write(root: Path, name: str, content: bytes) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return name


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def make_tree(root: Path, hashes: list[dict], official: list[str]) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    write(
        root,
        guard.HASH_LIST,
        json.dumps(
            {"forbidden_by_content": {"generator_and_tools": hashes, "audit_snapshots": [], "digen_output": []}}
        ).encode(),
    )
    write(
        root,
        guard.APPROVED_HASHES,
        json.dumps({"files": [{"sha256": value, "label": "x"} for value in official]}).encode(),
    )


def run_script(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT), "--root", str(root)], capture_output=True, text=True, cwd=root
    )


def test_real_tree_passes():
    assert guard.check(REPO_ROOT) == []


def test_script_reports_ok_on_the_real_tree():
    result = run_script(REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "copy guard: OK"


def test_hash_list_is_not_empty():
    forbidden, max_bytes = guard.load_forbidden(REPO_ROOT)
    assert len(forbidden) > 300
    assert max_bytes > 0


def test_empty_hash_list_is_refused(tmp_path):
    make_tree(tmp_path, [], [])
    assert run_script(tmp_path).returncode == 2


def test_script_fails_on_a_recorded_hash(tmp_path):
    content = b"generator text taken from the project\n"
    make_tree(tmp_path, [{"sha256": digest(content), "bytes": len(content)}], [])
    write(tmp_path, "benchbox/core/tpcdi/copied.txt", content)
    result = run_script(tmp_path)
    assert result.returncode == 1
    assert "benchbox/core/tpcdi/copied.txt" in result.stderr


def test_guard_allows_a_recorded_hash_in_the_tools_directory_only_when_official(tmp_path):
    content = b"official tool bytes\n"
    name = write(tmp_path, TOOLS_DIR + "DIGen.jar", content)
    digests = guard.file_digests(tmp_path, [name], 1000)
    forbidden = {digest(content)}
    assert guard.recorded_hash_violations(digests, forbidden, set(forbidden)) == []
    assert guard.recorded_hash_violations(digests, forbidden, set()) == [name]


def test_script_fails_on_a_recorded_hash_in_the_tools_directory_without_an_official_match(tmp_path):
    content = b"generator code copied into the tools directory\n"
    make_tree(tmp_path, [{"sha256": digest(content), "bytes": len(content)}], ["0" * 64])
    write(tmp_path, TOOLS_DIR + "tpcdi_gen/audit.py", content)
    result = run_script(tmp_path)
    assert result.returncode == 1
    assert TOOLS_DIR + "tpcdi_gen/audit.py" in result.stderr


def test_script_passes_a_recorded_official_hash_in_the_tools_directory(tmp_path):
    content = b"official tool bytes\n"
    make_tree(tmp_path, [{"sha256": digest(content), "bytes": len(content)}], [digest(content)])
    write(tmp_path, TOOLS_DIR + "DIGen.jar", content)
    assert run_script(tmp_path).returncode == 0


def test_script_fails_on_an_official_archive_file_outside_the_tpc_directories(tmp_path):
    content = b"bytes of a file of the official archive\n"
    make_tree(tmp_path, [{"sha256": "1" * 64, "bytes": 1000}], [digest(content)])
    write(tmp_path, "benchbox/core/tpcdi/renamed.bin", content)
    write(tmp_path, TOOLS_DIR + "renamed.bin", content)
    write(tmp_path, "_sources/tpc-di/renamed.bin", content)
    result = run_script(tmp_path)
    assert result.returncode == 1
    assert "benchbox/core/tpcdi/renamed.bin" in result.stderr
    assert "_sources/tpc-di/renamed.bin" not in result.stderr
    assert TOOLS_DIR + "renamed.bin" not in result.stderr


def test_script_fails_on_a_planted_tool_file(tmp_path):
    make_tree(tmp_path, [{"sha256": "1" * 64, "bytes": 1}], [])
    write(tmp_path, "vendor/pdgf.jar", b"x")
    result = run_script(tmp_path)
    assert result.returncode == 1
    assert "vendor/pdgf.jar" in result.stderr


def test_script_fails_on_a_gitlink(tmp_path):
    make_tree(tmp_path, [{"sha256": "1" * 64, "bytes": 1}], [])
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/tools"],
        check=True,
    )
    result = run_script(tmp_path)
    assert result.returncode == 1
    assert "vendor/tools" in result.stderr


def test_gitlink_entries_lists_only_mode_160000(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/tools"],
        check=True,
    )
    write(tmp_path, "plain.txt", b"x\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "plain.txt"], check=True)
    assert guard.gitlink_entries(tmp_path) == ["vendor/tools"]


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
    assert guard.misplaced_tool_violations([name]) == [name]


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
    assert guard.misplaced_tool_violations([name]) == []


def test_script_fails_on_an_unlisted_sql_file(tmp_path):
    make_tree(tmp_path, [{"sha256": "1" * 64, "bytes": 1}], [])
    write(tmp_path, ETL_DIR + "dimensions/dim_customer.sql", b"SELECT 1\n")
    result = run_script(tmp_path)
    assert result.returncode == 1
    assert "dim_customer.sql" in result.stderr


def test_script_accepts_a_listed_sql_file(tmp_path):
    make_tree(tmp_path, [{"sha256": "1" * 64, "bytes": 1}], [])
    listed = write(tmp_path, ETL_DIR + "dimensions/dim_customer.sql", b"SELECT 1\n")
    other = write(tmp_path, ETL_DIR + "dimensions/dim_account.sql", b"SELECT 2\n")
    write(tmp_path, ETL_PROVENANCE, json.dumps({"files": [{"path": listed}]}).encode())
    result = run_script(tmp_path)
    assert result.returncode == 1
    assert other in result.stderr
    assert listed not in result.stderr


def test_script_ignores_sql_outside_the_etl_directory(tmp_path):
    make_tree(tmp_path, [{"sha256": "1" * 64, "bytes": 1}], [])
    write(tmp_path, "benchbox/core/tpch/queries/q1.sql", b"SELECT 1\n")
    assert run_script(tmp_path).returncode == 0


def test_script_ignores_planted_python_modules_and_conftests(tmp_path):
    make_tree(tmp_path, [{"sha256": "1" * 64, "bytes": 1}], [])
    write(tmp_path, "json.py", b"raise SystemExit(0)\n")
    write(tmp_path, "pytest.py", b"raise SystemExit(0)\n")
    write(tmp_path, "conftest.py", b"raise SystemExit(0)\n")
    write(tmp_path, "vendor/pdgf.jar", b"x")
    assert run_script(tmp_path).returncode == 1
