import os

import pytest

from benchbox.core.tpcds.c_tools import DSQGenBinary

pytestmark = pytest.mark.medium


@pytest.mark.validation
def test_dsqgen_binary_discovery_and_execution():
    dsq = DSQGenBinary()

    assert dsq.dsqgen_path is not None
    assert os.path.exists(dsq.dsqgen_path)
    assert os.access(dsq.dsqgen_path, os.X_OK)

    sql = dsq.generate(1, seed=12345, scale_factor=1.0, dialect="ansi")
    assert isinstance(sql, str)
    assert len(sql) > 50
    low = sql.lower()
    assert ("select" in low) or ("with" in low)


@pytest.mark.validation
def test_dsqgen_variations_and_validation():
    dsq = DSQGenBinary()
    vars_ = dsq.get_query_variations(14)
    assert isinstance(vars_, list) and vars_

    sql = dsq.generate(14, seed=42, scale_factor=1.0)
    assert isinstance(sql, str) and len(sql) > 50

    assert dsq.validate_query_id(14) is True
    assert dsq.validate_query_id("14a") is True
    assert dsq.validate_query_id("not") is False
