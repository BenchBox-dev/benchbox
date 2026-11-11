from __future__ import annotations

import pytest

from benchbox.core.tuning.interface import TuningType
from benchbox.platforms.base.tuning import TuningHooksMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _HostWithCanonicalType(TuningHooksMixin):
    platform_name = "DuckDB (Display)"
    canonical_platform_type = "duckdb"


class _HostWithoutCanonicalType(TuningHooksMixin):
    platform_name = "duckdb"


class TestSupportsTuningTypeDisplayNamePath:
    def test_uses_canonical_platform_type_when_available(self):

        host = _HostWithCanonicalType()
        assert host.supports_tuning_type(TuningType.PARTITIONING) is True

    def test_falls_back_to_platform_name_when_canonical_type_missing(self):
        host = _HostWithoutCanonicalType()
        assert host.supports_tuning_type(TuningType.PARTITIONING) is True

    def test_named_tuning_type_override_takes_precedence(self):
        class _NamedHost(TuningHooksMixin):
            platform_name = "irrelevant display name"
            _supported_tuning_type_names = ("PARTITIONING",)

        host = _NamedHost()
        assert host.supports_tuning_type(TuningType.PARTITIONING) is True
        assert host.supports_tuning_type(TuningType.CLUSTERING) is False


