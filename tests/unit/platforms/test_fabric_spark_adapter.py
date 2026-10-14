from __future__ import annotations

import pytest

from benchbox.platforms.fabric_spark import FabricSparkAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_public_fabric_spark_import_is_available():

    assert FabricSparkAdapter is not None
