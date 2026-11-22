from __future__ import annotations

import pytest

from benchbox.core.exceptions import BenchBoxError, RunTimeoutError
from benchbox.core.ssb.generator import SSBDataGenerator
from benchbox.ssb import SSB
from benchbox.utils import run_deadline
from benchbox.utils.run_deadline import RunDeadline

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_a_timed_out_generate_data_raises_the_error_importable_from_the_public_module(tmp_path, monkeypatch):
    def stopped(self):
        raise RunTimeoutError(60, "generate", detail="stub stopped")

    monkeypatch.setattr(SSBDataGenerator, "_generate_part_data", stopped)

    with pytest.raises(RunTimeoutError) as excinfo:
        SSB(scale_factor=0.01, output_dir=tmp_path / "data", compress_data=False).generate_data(timeout_seconds=60)

    assert excinfo.value.timeout_seconds == 60
    assert excinfo.value.phase == "generate"
    assert excinfo.value.detail == "stub stopped"


def test_the_deadline_records_the_same_class_the_public_module_exports():
    deadline = RunDeadline(30)

    assert type(deadline.record_timeout()) is RunTimeoutError
    assert run_deadline.RunTimeoutError is RunTimeoutError


def test_the_error_derives_from_exception_and_not_from_benchbox_error():
    assert RunTimeoutError.__bases__ == (Exception,)
    assert not issubclass(RunTimeoutError, BenchBoxError)
