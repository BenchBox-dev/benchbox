# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.validation.bundle import KNOWN_PLATFORMS, ValidationResult, _validate_platform_section

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _validate_platform_name(name: str) -> ValidationResult:
    vr = ValidationResult(path="<test>")
    _validate_platform_section({"name": name}, vr)
    return vr


class TestBundlePlatformAllowlist:
    def test_ducklake_is_known(self):
        assert "ducklake" in KNOWN_PLATFORMS

    def test_ducklake_bundle_section_validates_clean(self):
        vr = _validate_platform_name("DuckLake")
        assert vr.errors == []
        assert vr.warnings == []

    def test_cedardb_bundle_section_validates_clean(self):
        vr = _validate_platform_name("CedarDB")
        assert vr.errors == []
        assert vr.warnings == []

    def test_genuinely_unknown_platform_still_warns(self):
        vr = _validate_platform_name("Not A Real Platform")
        assert vr.errors == []
        assert any("Unknown platform name" in warning for warning in vr.warnings)

    @pytest.mark.parametrize("name", ["Apache Doris", "SingleStore", "Velox", "Databend"])
    def test_release_platform_display_names_validate_clean(self, name):
        vr = _validate_platform_name(name)
        assert vr.warnings == []

    def test_every_manifest_platform_key_is_known(self):
        from benchbox.core.platform_manifest import PLATFORM_MANIFEST

        missing = sorted(
            entry.key
            for entry in PLATFORM_MANIFEST
            if not {entry.key, entry.key.replace("-", "_"), entry.key.replace("_", "-")} & KNOWN_PLATFORMS
        )
        assert missing == []
