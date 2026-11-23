from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from benchbox.core.tpcdi import tools
from benchbox.utils.binary_manifest import MANIFEST_NAME, verify_binary_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parents[4]
BINARIES = REPO_ROOT / "benchbox" / "_binaries"
TOOLS_DIR = BINARIES / "tpc-di" / "tools"
ARCHIVE = TOOLS_DIR / "TPC-DI_Tools_v1.1.0.zip"
RECORDED_SHA256 = "d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b"
RECORDED_BYTES = 6_676_639
SHIPPED = ("EULA.txt", "PDGF-LICENSE.txt", "PDGF-THIRD-PARTY-LICENSE.txt", "SOURCING.md", "TPC-DI_Tools_v1.1.0.zip")

_spec = importlib.util.spec_from_file_location(
    "check_tpcdi_copy_guard", REPO_ROOT / "scripts" / "check_tpcdi_copy_guard.py"
)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache"


def test_shipped_archive_matches_the_recorded_hash_and_size():
    assert sha256(ARCHIVE) == RECORDED_SHA256
    assert ARCHIVE.stat().st_size == RECORDED_BYTES
    assert tools.TOOLS_ARCHIVE_SHA256 == RECORDED_SHA256


def test_every_shipped_file_is_registered_in_the_manifest():
    manifest = json.loads((BINARIES / MANIFEST_NAME).read_text(encoding="utf-8"))["files"]
    for name in SHIPPED:
        assert f"tpc-di/tools/{name}" in manifest
    assert manifest["tpc-di/tools/TPC-DI_Tools_v1.1.0.zip"] == RECORDED_SHA256


def test_manifest_comparison_fails_when_one_byte_of_a_copy_changes():
    manifest = (BINARIES / MANIFEST_NAME).read_bytes()
    files = {
        path.relative_to(BINARIES).as_posix(): path.read_bytes()
        for path in BINARIES.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }
    verify_binary_payload(manifest, files)
    name = "tpc-di/tools/TPC-DI_Tools_v1.1.0.zip"
    changed = bytearray(files[name])
    changed[len(changed) // 2] ^= 1
    files[name] = bytes(changed)
    with pytest.raises(ValueError, match="hash differs"):
        verify_binary_payload(manifest, files)


@pytest.mark.parametrize(
    ("beside", "member"),
    [
        ("PDGF-LICENSE.txt", "Tools/PDGF/LICENSE.txt"),
        ("PDGF-THIRD-PARTY-LICENSE.txt", "Tools/PDGF/THIRD-PARTY-LICENSE.txt"),
    ],
)
def test_licence_texts_are_the_archive_members_unmodified(beside, member):
    with zipfile.ZipFile(ARCHIVE) as bundle:
        assert (TOOLS_DIR / beside).read_bytes() == bundle.read(member)


def test_tpc_eula_beside_the_tools_is_the_shipped_eula():
    assert (TOOLS_DIR / "EULA.txt").read_bytes() == (BINARIES / "tpc-di" / "EULA.txt").read_bytes()


def test_sourcing_file_records_the_archive_identity():
    text = (TOOLS_DIR / "SOURCING.md").read_text(encoding="utf-8")
    assert RECORDED_SHA256 in text
    assert "6,676,639" in text
    assert "internal, academic, and research purposes only" in text


def test_first_use_unpacks_into_the_cache_and_second_use_does_not(cache, monkeypatch):
    calls = []
    real_extract = tools._extract
    monkeypatch.setattr(
        tools, "_extract", lambda archive, destination: (calls.append(archive), real_extract(archive, destination))
    )

    first = tools.resolve_tools_dir()
    assert first.is_relative_to(cache)
    assert (first / "DIGen.jar").is_file()
    assert (first / "PDGF" / "pdgf.jar").is_file()
    assert len(calls) == 1

    second = tools.resolve_tools_dir()
    assert second == first
    assert len(calls) == 1


def test_unpack_never_touches_the_installed_package(cache):
    before = sorted(path.name for path in TOOLS_DIR.iterdir())
    tools.resolve_tools_dir()
    assert sorted(path.name for path in TOOLS_DIR.iterdir()) == before


def test_corrupted_archive_is_refused_and_nothing_is_unpacked(cache, tmp_path, monkeypatch):
    corrupted = tmp_path / "TPC-DI_Tools_v1.1.0.zip"
    data = bytearray(ARCHIVE.read_bytes())
    data[len(data) // 2] ^= 1
    corrupted.write_bytes(bytes(data))
    monkeypatch.setattr(tools, "TOOLS_ARCHIVE_PATH", corrupted)
    with pytest.raises(tools.ToolsArchiveError, match="corrupted"):
        tools.resolve_tools_dir()
    assert not (cache / "benchbox").exists()


def test_missing_archive_is_refused(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TOOLS_ARCHIVE_PATH", tmp_path / "absent.zip")
    with pytest.raises(tools.ToolsArchiveError, match="missing"):
        tools.resolve_tools_dir()


def test_incomplete_unpack_is_redone(cache):
    unpack_dir = cache / "benchbox" / tools.TOOLS_CACHE_NAME / tools.TOOLS_ARCHIVE_SHA256
    (unpack_dir / "Tools").mkdir(parents=True)
    (unpack_dir / "Tools" / "stale.txt").write_text("x", encoding="utf-8")
    resolved = tools.resolve_tools_dir()
    assert (resolved / "DIGen.jar").is_file()
    assert not (resolved / "stale.txt").exists()


def test_a_complete_unpack_published_by_another_process_is_kept(cache, monkeypatch):
    unpack_dir = cache / "benchbox" / tools.TOOLS_CACHE_NAME / tools.TOOLS_ARCHIVE_SHA256
    real_extract = tools._extract
    published = []

    def extract_then_lose_the_race(archive, destination):
        real_extract(archive, destination)
        real_extract(archive, unpack_dir)
        (unpack_dir / tools._COMPLETE_SENTINEL).write_text(tools.TOOLS_ARCHIVE_SHA256, encoding="utf-8")
        published.append((unpack_dir / "Tools" / "DIGen.jar").stat().st_ino)

    monkeypatch.setattr(tools, "_extract", extract_then_lose_the_race)
    resolved = tools.resolve_tools_dir()
    assert resolved == unpack_dir / "Tools"
    assert (resolved / "DIGen.jar").stat().st_ino == published[0]
    assert [path.name for path in unpack_dir.parent.iterdir()] == [tools.TOOLS_ARCHIVE_SHA256]


def test_an_extra_unpacked_file_is_removed_on_next_use(cache):
    resolved = tools.resolve_tools_dir()
    extra = resolved / "PDGF" / "planted.txt"
    extra.write_text("x", encoding="utf-8")
    assert tools.resolve_tools_dir() == resolved
    assert not extra.exists()


def test_an_edited_unpacked_file_is_restored_on_next_use(cache, monkeypatch):
    calls = []
    real_extract = tools._extract
    monkeypatch.setattr(
        tools, "_extract", lambda archive, destination: (calls.append(archive), real_extract(archive, destination))
    )
    resolved = tools.resolve_tools_dir()
    target = resolved / "PDGF" / "config" / "tpc-di-schema.xml"
    original = target.read_bytes()
    target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    assert tools.resolve_tools_dir() == resolved
    assert target.read_bytes() == original
    assert len(calls) == 2


def test_a_missing_unpacked_file_is_restored_on_next_use(cache):
    resolved = tools.resolve_tools_dir()
    (resolved / "README.txt").unlink()
    tools.resolve_tools_dir()
    assert (resolved / "README.txt").is_file()


def test_path_traversal_in_an_archive_is_refused(tmp_path):
    hostile = tmp_path / "hostile.zip"
    with zipfile.ZipFile(hostile, "w") as bundle:
        bundle.writestr("../escape.txt", "x")
    with pytest.raises(tools.ToolsArchiveError, match="path traversal"):
        tools._extract(hostile, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def git_tree(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in (guard.HASH_LIST, guard.APPROVED_HASHES):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / name, target)


def run_guard(root: Path) -> subprocess.CompletedProcess:
    script = REPO_ROOT / "scripts" / "check_tpcdi_copy_guard.py"
    return subprocess.run(
        [sys.executable, "-I", "-S", str(script), "--root", str(root)], capture_output=True, text=True, cwd=root
    )


def test_copy_guard_passes_with_the_archive_in_the_tools_directory(tmp_path):
    git_tree(tmp_path)
    target = tmp_path / guard.TOOLS_DIR / ARCHIVE.name
    target.parent.mkdir(parents=True)
    shutil.copy(ARCHIVE, target)
    result = run_guard(tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("where", ["vendor", "benchbox/_binaries/tpc-di", "_sources/tpc-di"])
def test_copy_guard_fails_on_a_copy_of_the_archive_elsewhere(tmp_path, where):
    git_tree(tmp_path)
    target = tmp_path / where / ARCHIVE.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ARCHIVE, target)
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert f"{where}/{ARCHIVE.name}" in result.stderr
