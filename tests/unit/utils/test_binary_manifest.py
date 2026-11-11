from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from benchbox.utils.binary_manifest import (
    DEFAULT_ROOT,
    MANIFEST_NAME,
    build_binary_manifest,
    main,
    read_binary_manifest,
    verify_binary_tree,
)
from scripts.verify_distribution_binaries import verify_distribution_binaries

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def binary_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "linux").mkdir(parents=True)
    (root / "linux" / "dbgen").write_bytes(b"generator bytes")
    (root / "dists.dss").write_bytes(b"distributions\n")
    (root / MANIFEST_NAME).write_bytes(build_binary_manifest(root))
    return root


def test_shipped_manifest_covers_the_real_package() -> None:
    verify_binary_tree(DEFAULT_ROOT)
    assert build_binary_manifest(DEFAULT_ROOT) == (DEFAULT_ROOT / MANIFEST_NAME).read_bytes()


def test_installed_verification_respects_quiet_output(binary_root: Path, monkeypatch, capsys) -> None:
    from benchbox.utils import printing

    monkeypatch.setattr(printing, "_QUIET", True)
    assert main(["--root", str(binary_root)]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("case", ["missing", "extra", "tampered", "missing-manifest", "linked"])
def test_tree_rejects_membership_and_byte_changes(
    binary_root: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    binary = binary_root / "linux" / "dbgen"
    if case == "missing":
        binary.unlink()
    elif case == "extra":
        (binary_root / "unexpected").write_bytes(b"extra")
    elif case == "tampered":
        binary.write_bytes(b"generator bytex")
    elif case == "missing-manifest":
        (binary_root / MANIFEST_NAME).unlink()
    else:
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == binary or original(path))
    with pytest.raises(ValueError):
        verify_binary_tree(binary_root)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": True, "files": {"dbgen": "a" * 64}},
        {"schema": 2, "files": {"dbgen": "a" * 64}},
        {"schema": 1, "files": {}},
        {"schema": 1, "files": {"../dbgen": "a" * 64}},
        {"schema": 1, "files": {"/dbgen": "a" * 64}},
        {"schema": 1, "files": {"./dbgen": "a" * 64}},
        {"schema": 1, "files": {"linux//dbgen": "a" * 64}},
        {"schema": 1, "files": {"C:/dbgen": "a" * 64}},
        {"schema": 1, "files": {"linux\\dbgen": "a" * 64}},
        {"schema": 1, "files": {MANIFEST_NAME: "a" * 64}},
        {"schema": 1, "files": {"dbgen": "invalid"}},
    ],
)
def test_manifest_rejects_invalid_schema_paths_and_hashes(payload: dict) -> None:
    with pytest.raises(ValueError):
        read_binary_manifest(json.dumps(payload).encode())


def test_manifest_rejects_duplicate_json_keys() -> None:
    payload = '{"schema":1,"files":{"dbgen":"' + "a" * 64 + '","dbgen":"' + "b" * 64 + '"}}'
    with pytest.raises(ValueError, match="duplicate"):
        read_binary_manifest(payload.encode())


def _write_distribution(
    destination: Path,
    files: dict[str, bytes],
    link: bool = False,
    duplicate: bool = False,
    extra_name: str | None = None,
    extra_type: int = stat.S_IFREG,
    extra_payload: bytes = b"evil",
    binary_mode: int = 0o644,
) -> None:
    if destination.suffix == ".whl":
        with zipfile.ZipFile(destination, "w") as archive:
            for name, payload in files.items():
                entry = zipfile.ZipInfo("benchbox/_binaries/" + name)
                entry.create_system = 3
                file_type = stat.S_IFLNK if link and name == "linux/dbgen" else stat.S_IFREG
                mode = binary_mode if name == "linux/dbgen" else 0o644
                entry.external_attr = (file_type | mode) << 16
                archive.writestr(entry, payload)
            if duplicate:
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr("benchbox/_binaries/linux/dbgen", files["linux/dbgen"])
            if extra_name is not None:
                entry = zipfile.ZipInfo(extra_name)
                entry.create_system = 3
                entry.external_attr = extra_type << 16
                archive.writestr(entry, extra_payload)
    else:
        with tarfile.open(destination, "w:gz") as archive:
            prefix = destination.name.removesuffix(".tar.gz") + "/benchbox/_binaries/"
            for name, payload in files.items():
                entry = tarfile.TarInfo(prefix + name)
                entry.size = len(payload)
                entry.mode = binary_mode if name == "linux/dbgen" else 0o644
                if link and name == "linux/dbgen":
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = "/outside"
                archive.addfile(entry, io.BytesIO(payload))
            if duplicate:
                entry = tarfile.TarInfo(prefix + "linux/dbgen")
                entry.size = len(files["linux/dbgen"])
                archive.addfile(entry, io.BytesIO(files["linux/dbgen"]))
            if extra_name is not None:
                entry = tarfile.TarInfo(extra_name)
                entry.size = len(extra_payload)
                if extra_type == stat.S_IFIFO:
                    entry.type = tarfile.FIFOTYPE
                archive.addfile(entry, io.BytesIO(extra_payload))


@pytest.mark.parametrize("extension", ["whl", "tar.gz"])
@pytest.mark.parametrize("case", ["complete", "missing", "extra", "tampered", "forged-manifest", "link", "duplicate"])
def test_distribution_uses_trusted_source_manifest(
    binary_root: Path, tmp_path: Path, extension: str, case: str
) -> None:
    files = {
        path.relative_to(binary_root).as_posix(): path.read_bytes() for path in binary_root.rglob("*") if path.is_file()
    }
    if case == "missing":
        del files["linux/dbgen"]
    elif case == "extra":
        files["unexpected"] = b"extra"
    elif case in {"tampered", "forged-manifest"}:
        files["linux/dbgen"] = b"generator bytex"
        if case == "forged-manifest":
            manifest = json.loads(files[MANIFEST_NAME])
            manifest["files"]["linux/dbgen"] = hashlib.sha256(files["linux/dbgen"]).hexdigest()
            files[MANIFEST_NAME] = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    distribution = tmp_path / ("benchbox-1.2.3." + extension)
    _write_distribution(distribution, files, link=case == "link", duplicate=case == "duplicate")
    if case == "complete":
        verify_distribution_binaries(distribution, binary_root)
    else:
        with pytest.raises(ValueError):
            verify_distribution_binaries(distribution, binary_root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable permissions require a POSIX source filesystem")
@pytest.mark.parametrize("extension", ["whl", "tar.gz"])
@pytest.mark.parametrize("mode", [0o755, 0o644, 0o654])
def test_distribution_preserves_generator_execute_permissions(
    binary_root: Path, tmp_path: Path, extension: str, mode: int
) -> None:
    binary = binary_root / "linux" / "dbgen"
    binary.chmod(0o755)
    files = {
        path.relative_to(binary_root).as_posix(): path.read_bytes() for path in binary_root.rglob("*") if path.is_file()
    }
    distribution = tmp_path / ("benchbox-1.2.3." + extension)
    _write_distribution(distribution, files, binary_mode=mode)
    if mode == 0o755:
        verify_distribution_binaries(distribution, binary_root)
    else:
        with pytest.raises(ValueError, match="execute permissions"):
            verify_distribution_binaries(distribution, binary_root)


@pytest.mark.parametrize("extension", ["whl", "tar.gz"])
@pytest.mark.parametrize(
    "case",
    ["dot", "slash", "backslash", "parent", "absolute", "case", "trailing-dot", "trailing-slash", "relocation", "fifo"],
)
def test_archive_rejects_alias_overwrites_and_nonregular_members(
    binary_root: Path, tmp_path: Path, extension: str, case: str
) -> None:
    files = {
        path.relative_to(binary_root).as_posix(): path.read_bytes() for path in binary_root.rglob("*") if path.is_file()
    }
    distribution = tmp_path / ("benchbox-1.2.3." + extension)
    prefix = "" if extension == "whl" else "benchbox-1.2.3/"
    name = prefix + "benchbox/_binaries/linux/dbgen"
    extra_payload = b"evil"
    if case == "dot":
        name = "./" + name
    elif case == "slash":
        name = name.replace("benchbox/", "benchbox//")
    elif case == "backslash":
        name = name.replace("/", "\\")
    elif case == "parent":
        name = prefix + "benchbox/other/../_binaries/linux/dbgen"
    elif case == "absolute":
        name = "/" + name
    elif case == "case":
        name = name.replace("benchbox/_binaries", "BENCHBOX/_BINARIES")
    elif case == "trailing-dot":
        name += "."
    elif case == "trailing-slash":
        name += "/"
        extra_payload = files.pop("linux/dbgen")
    elif case == "relocation":
        name = prefix + "benchbox-1.2.3.data/purelib/benchbox/_binaries/linux/dbgen"
    else:
        files.pop("linux/dbgen")
    _write_distribution(
        distribution,
        files,
        extra_name=name,
        extra_type=stat.S_IFIFO if case == "fifo" else stat.S_IFREG,
        extra_payload=extra_payload,
    )
    with pytest.raises(ValueError):
        verify_distribution_binaries(distribution, binary_root)


@pytest.mark.parametrize("name,file_type", [("benchbox/", stat.S_IFREG), ("benchbox", stat.S_IFDIR)])
def test_wheel_rejects_contradictory_directory_types(
    binary_root: Path, tmp_path: Path, name: str, file_type: int
) -> None:
    files = {
        path.relative_to(binary_root).as_posix(): path.read_bytes() for path in binary_root.rglob("*") if path.is_file()
    }
    distribution = tmp_path / "benchbox-1.2.3.whl"
    _write_distribution(distribution, files, extra_name=name, extra_type=file_type)
    with pytest.raises(ValueError):
        verify_distribution_binaries(distribution, binary_root)


