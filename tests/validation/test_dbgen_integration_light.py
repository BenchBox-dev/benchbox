import tempfile
from pathlib import Path

import pytest

from benchbox.core.tpch.generator import TPCHDataGenerator

pytestmark = pytest.mark.slow



@pytest.mark.validation
def test_dbgen_generates_small_table():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)

        gen = TPCHDataGenerator(scale_factor=0.01, output_dir=out, verbose=False, uncompressed_output=True)

        try:
            if hasattr(gen, "_run_dbgen_native"):
                gen._run_dbgen_native(out)
            else:
                if hasattr(gen, "_run_file_based_dbgen"):
                    gen._run_file_based_dbgen(out)
                else:
                    if hasattr(gen, "generate"):
                        gen.generate()
        except Exception:
            pass

        candidates = [
            out / "region.tbl",
            out / "region.dat",
            out / "REGION.tbl",
            out / "REGION.dat",
        ]
        exists = [p for p in candidates if p.exists() and p.stat().st_size > 0]
        assert exists, "dbgen did not produce expected REGION output"


