from __future__ import annotations

import pytest

from benchbox.core.errors import ScaleFactorOutOfRangeError
from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize("scale_factor", [0.001, 0.009, 0.000001])
def test_scale_below_the_floor_is_refused(tmp_path, scale_factor):
    with pytest.raises(ScaleFactorOutOfRangeError, match="accepts a finite scale_factor from 0.01"):
        JoinOrderSyntheticBenchmark(scale_factor=scale_factor, output_dir=tmp_path)


def test_scale_at_the_floor_is_accepted(tmp_path):
    assert JoinOrderSyntheticBenchmark(scale_factor=0.01, output_dir=tmp_path).scale_factor == 0.01
