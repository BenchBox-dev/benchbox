import json
import os
import sys
from pathlib import Path

import pytest

scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)


@pytest.fixture(scope="session")
def inert_scan(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    from filelock import FileLock

    from _project.scripts import inert_paths

    root = Path(__file__).resolve().parents[3]
    if "PYTEST_XDIST_WORKER" not in os.environ:
        return inert_paths.scan_tree(root)
    shared = tmp_path_factory.getbasetemp().parent / "inert_scan.json"
    with FileLock(str(shared) + ".lock"):
        if shared.is_file():
            return json.loads(shared.read_text(encoding="utf-8"))
        refs = inert_paths.scan_tree(root)
        shared.write_text(json.dumps(refs), encoding="utf-8")
        return refs
