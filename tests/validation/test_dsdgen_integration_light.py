import tempfile
from pathlib import Path

import pytest

from benchbox.core.tpcds.generator import TPCDSDataGenerator

pytestmark = pytest.mark.medium


@pytest.mark.validation
def test_dsdgen_generates_small_table_streaming():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)

        gen = TPCDSDataGenerator(scale_factor=1.0, output_dir=out, verbose=False)

        gen._generate_single_table_streaming(out, "call_center")

        compressed = out / gen.get_compressed_filename("call_center.dat")
        assert compressed.exists(), f"Expected file not found: {compressed}"
        assert compressed.stat().st_size > 0
