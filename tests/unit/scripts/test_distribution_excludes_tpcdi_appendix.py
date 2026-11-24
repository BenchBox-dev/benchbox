from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from benchbox.utils.binary_manifest import DEFAULT_ROOT, DISTRIBUTION_EXCLUDED
from scripts.verify_distribution_binaries import verify_distribution_binaries

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
APPENDIX_SQL = (
    "tpcdi_audit.sql",
    "tpcdi_validation.sql",
    "tpcdi_visibility_1.sql",
    "tpcdi_visibility_2.sql",
)


def test_source_checkout_keeps_the_appendix_sql() -> None:
    for filename in APPENDIX_SQL:
        assert (DEFAULT_ROOT / "tpc-di" / filename).is_file()
        assert (ROOT / "_sources" / "tpc-di" / filename).is_file()
    assert {f"tpc-di/{filename}" for filename in APPENDIX_SQL} == DISTRIBUTION_EXCLUDED


def test_distribution_binary_verification_rejects_a_leaked_appendix_file(tmp_path: Path) -> None:
    wheel = tmp_path / "benchbox-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("benchbox/_binaries/tpc-di/tpcdi_audit.sql", b"leak")
    with pytest.raises(ValueError, match="tpc-di/tpcdi_audit.sql"):
        verify_distribution_binaries(wheel)
