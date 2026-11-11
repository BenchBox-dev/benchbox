import logging

import pytest

from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_core_quiet_suppresses_logs_and_output(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    b = JoinOrderSyntheticBenchmark(scale_factor=0.01, output_dir="/tmp/joinorder_synthetic", verbose=2)

    b.quiet = True
    b.generate_data = list
    b.generate_data()

    assert not [rec for rec in caplog.records if rec.levelno in (logging.INFO, logging.DEBUG)]

    captured = capsys.readouterr()
    assert captured.out == ""


