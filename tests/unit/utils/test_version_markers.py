import importlib

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_validate_version_consistency_succeeds():
    version_module = importlib.import_module("benchbox.utils.version")

    version_module.validate_version_consistency()


