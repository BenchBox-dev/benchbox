from __future__ import annotations

import os
from pathlib import Path

OUTPUTS_ENV = "BENCHBOX_TPCDI_DIGEN_OUTPUTS"


def digen_outputs() -> dict[int, Path]:
    outputs = {}
    for item in filter(None, (part.strip() for part in os.environ.get(OUTPUTS_ENV, "").split(","))):
        scale, _, path = item.partition("=")
        outputs[int(scale)] = Path(path)
    return outputs
