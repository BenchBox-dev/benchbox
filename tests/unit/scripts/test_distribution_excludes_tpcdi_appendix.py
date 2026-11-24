from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from benchbox.utils.binary_manifest import DEFAULT_ROOT
from scripts.verify_distribution_binaries import DISTRIBUTION_EXCLUDED, verify_distribution_binaries

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
APPENDIX_SQL = (
    "tpcdi_audit.sql",
    "tpcdi_validation.sql",
    "tpcdi_visibility_1.sql",
    "tpcdi_visibility_2.sql",
)
KEPT = (
    "EULA.txt",
    "NOTICE.txt",
    "tools/EULA.txt",
    "tools/PDGF-LICENSE.txt",
    "tools/PDGF-THIRD-PARTY-LICENSE.txt",
    "tools/SOURCING.md",
    "tools/TPC-DI_Tools_v1.1.0.zip",
)


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--wheel", "--sdist", "--out-dir", str(out), str(ROOT)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    return next(out.glob("*.whl")), next(out.glob("*.tar.gz"))


def _member_bytes(wheel: Path, sdist: Path) -> dict[str, dict[str, bytes]]:
    with zipfile.ZipFile(wheel) as archive:
        wheel_members = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_members = {
            member.name.split("/", 1)[1]: archive.extractfile(member).read() for member in archive if member.isfile()
        }
    return {"wheel": wheel_members, "sdist": sdist_members}


def test_source_checkout_keeps_the_appendix_sql() -> None:
    for filename in APPENDIX_SQL:
        assert (DEFAULT_ROOT / "tpc-di" / filename).is_file()
        assert (ROOT / "_sources" / "tpc-di" / filename).is_file()
    assert {f"tpc-di/{filename}" for filename in APPENDIX_SQL} == DISTRIBUTION_EXCLUDED


def test_built_distributions_exclude_the_appendix_sql(distributions: tuple[Path, Path]) -> None:
    for kind, members in _member_bytes(*distributions).items():
        leaked = sorted(name for name in members if name.rsplit("/", 1)[-1] in APPENDIX_SQL)
        assert not leaked, f"{kind} contains appendix SQL: {leaked}"


def test_built_distributions_keep_the_tools_archive_and_licence_files(distributions: tuple[Path, Path]) -> None:
    for kind, members in _member_bytes(*distributions).items():
        for name in KEPT:
            shipped = members[f"benchbox/_binaries/tpc-di/{name}"]
            assert shipped == (DEFAULT_ROOT / "tpc-di" / name).read_bytes(), f"{kind}: {name} differs"
    assert _member_bytes(*distributions)["sdist"]["_sources/tpc-di/EULA.txt"]
    assert _member_bytes(*distributions)["sdist"]["_sources/tpc-di/NOTICE.txt"]


def test_distribution_binary_verification_accepts_the_built_distributions(distributions: tuple[Path, Path]) -> None:
    for distribution in distributions:
        verify_distribution_binaries(distribution)


def test_distribution_binary_verification_rejects_a_leaked_appendix_file(tmp_path: Path) -> None:
    wheel = tmp_path / "benchbox-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("benchbox/_binaries/tpc-di/tpcdi_audit.sql", b"leak")
    with pytest.raises(ValueError, match="tpc-di/tpcdi_audit.sql"):
        verify_distribution_binaries(wheel)
