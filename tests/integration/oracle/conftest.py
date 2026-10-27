from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from .harness import Harness

pytest.importorskip("fcntl")


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    if shutil.which("gh") is None or shutil.which("git") is None:
        pytest.fail("the oracle scenario harness needs the gh and git executables on PATH")
    built = Harness(tmp_path)
    try:
        yield built
        assert built.github.unhandled == [], built.github.unhandled
    finally:
        built.close()
