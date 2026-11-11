from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

import benchbox
import benchbox.utils.tpc_compilation as tpc_mod

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(benchbox.__file__).resolve().parent.parent


def _parse_like_fixed(raw: str) -> tuple[str, str] | None:
    s = raw.strip()
    if not s:
        return None
    if s.startswith("MD5") and "(" in s and ")" in s and "=" in s:
        try:
            lpar = s.index("(")
            rpar = s.index(")", lpar)
            eq = s.index("=", rpar)
            fn = s[lpar + 1 : rpar].strip().lstrip("./")
            h = s[eq + 1 :].strip().split()[0]
            if fn and h:
                return h, fn
        except ValueError:
            pass
    parts = s.split()
    if len(parts) >= 2:
        return parts[0], parts[-1].lstrip("*./")
    return None


class TestVerifyChecksumParsesRealManifests:
    @pytest.mark.parametrize(
        "raw,expected_hash,expected_name",
        [
            ("60fd091f0b5dfdfdafcd352a73c81eb8 dbgen", "60fd091f0b5dfdfdafcd352a73c81eb8", "dbgen"),
            ("43f6d3a7d5e87111abd6446f42ac5d63  dbgen", "43f6d3a7d5e87111abd6446f42ac5d63", "dbgen"),
            ("a050c4143571d41db7d2f1903f6b50ed  ./dsqgen", "a050c4143571d41db7d2f1903f6b50ed", "dsqgen"),
            ("MD5 (dbgen) = 88c14d33eadf178b009086b63764ae80", "88c14d33eadf178b009086b63764ae80", "dbgen"),
            ("MD5 (./dsqgen) = 1618a480bc85b6860be74aae803e9199", "1618a480bc85b6860be74aae803e9199", "dsqgen"),
            (
                "MD5 (checksums.md5) = d41d8cd98f00b204e9800998ecf8427e",
                "d41d8cd98f00b204e9800998ecf8427e",
                "checksums.md5",
            ),
        ],
    )
    def test_parse_both_formats(self, raw, expected_hash, expected_name):
        parsed = _parse_like_fixed(raw)
        assert parsed is not None
        h, name = parsed
        assert h == expected_hash
        assert name == expected_name


class TestVerifyChecksumBSDLabeledBinary:
    def test_bsd_entry_verifies_when_bytes_match(self, tmp_path: Path):
        binary = tmp_path / "dbgen"
        binary.write_bytes(b"fake-dbgen-content")
        digest = hashlib.md5(binary.read_bytes()).hexdigest()
        (tmp_path / "checksums.md5").write_text(f"MD5 (dbgen) = {digest}\n", encoding="utf-8")
        tpc_mod._checksum_cache.pop(binary, None)
        compiler = tpc_mod.TPCCompiler.__new__(tpc_mod.TPCCompiler)
        assert compiler._verify_checksum(binary) is True

    def test_bsd_entry_fails_when_bytes_mismatch(self, tmp_path: Path):
        binary = tmp_path / "dbgen"
        binary.write_bytes(b"real-content")
        fake_hash = "0" * 32
        assert fake_hash != hashlib.md5(binary.read_bytes()).hexdigest()
        (tmp_path / "checksums.md5").write_text(f"MD5 (dbgen) = {fake_hash}\n", encoding="utf-8")
        tpc_mod._checksum_cache.pop(binary, None)
        compiler = tpc_mod.TPCCompiler.__new__(tpc_mod.TPCCompiler)
        assert compiler._verify_checksum(binary) is False

    def test_gnu_entry_still_verifies(self, tmp_path: Path):
        binary = tmp_path / "dbgen"
        binary.write_bytes(b"gnu-content")
        digest = hashlib.md5(binary.read_bytes()).hexdigest()
        (tmp_path / "checksums.md5").write_text(f"{digest}  dbgen\n", encoding="utf-8")
        tpc_mod._checksum_cache.pop(binary, None)
        compiler = tpc_mod.TPCCompiler.__new__(tpc_mod.TPCCompiler)
        assert compiler._verify_checksum(binary) is True

    def test_gnu_with_dot_slash_prefix_verifies(self, tmp_path: Path):
        binary = tmp_path / "dsdgen"
        binary.write_bytes(b"gnu-slash-content")
        digest = hashlib.md5(binary.read_bytes()).hexdigest()
        (tmp_path / "checksums.md5").write_text(f"{digest}  ./dsdgen\n", encoding="utf-8")
        tpc_mod._checksum_cache.pop(binary, None)
        compiler = tpc_mod.TPCCompiler.__new__(tpc_mod.TPCCompiler)
        assert compiler._verify_checksum(binary) is True

    def test_empty_bsd_digest_is_skipped_before_valid_entry(self, tmp_path: Path):
        binary = tmp_path / "dbgen"
        binary.write_bytes(b"bsd-content")
        (tmp_path / "checksums.md5").write_text(
            "MD5 (checksums.md5) =\nMD5 (dbgen) = " + "0" * 32 + "\n",
            encoding="utf-8",
        )
        tpc_mod._checksum_cache.pop(binary, None)
        compiler = tpc_mod.TPCCompiler.__new__(tpc_mod.TPCCompiler)

        assert compiler._verify_checksum(binary) is False


class TestBundledChecksumsMatchBinaries:
    def test_bundled_checksums(self):
        binaries_root = REPO_ROOT / "_binaries"
        assert binaries_root.is_dir(), f"missing _binaries at {binaries_root}"
        manifests = sorted(binaries_root.rglob("checksums.md5"))
        assert manifests, "no checksums.md5 found under _binaries"
        mismatches: list[str] = []
        for cs in manifests:
            parent = cs.parent
            for raw in cs.read_text(encoding="utf-8").splitlines():
                parsed = _parse_like_fixed(raw)
                if parsed is None:
                    continue
                expected, filename = parsed
                if filename == "checksums.md5":
                    continue
                binary = parent / filename
                if not binary.exists():
                    mismatches.append(f"{cs}::{filename} missing binary {binary}")
                    continue
                actual = hashlib.md5(binary.read_bytes()).hexdigest()
                if actual != expected:
                    mismatches.append(f"{cs}::{filename} mismatch actual={actual} expected={expected}")
        assert not mismatches, "\n".join(mismatches)


