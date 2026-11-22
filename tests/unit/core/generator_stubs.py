from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SPEC_ENV = "BENCHBOX_TEST_STUB_SPEC"

_STUB_SOURCE = """#!{python}
import json
import os
import sys
import time

spec = json.loads(os.environ[{spec_env!r}])
with open(spec["calls"], "a", encoding="utf-8") as handle:
    handle.write(str(os.getpid()) + " " + " ".join(sys.argv[1:]) + "\\n")
for name, rows in spec.get("files", {{}}).items():
    with open(name, "w", encoding="utf-8") as handle:
        handle.write("1|row|\\n" * rows)
if spec.get("stdout"):
    sys.stdout.write(spec["stdout"])
    sys.stdout.flush()
if spec.get("hang"):
    time.sleep(spec["hang"])
"""


class StubTool:
    def __init__(self, path: Path, calls: Path) -> None:
        self.path = path
        self.calls = calls

    def invocations(self) -> list[str]:
        if not self.calls.exists():
            return []
        return self.calls.read_text(encoding="utf-8").splitlines()

    def pids(self) -> list[int]:
        return [int(line.split()[0]) for line in self.invocations()]


def make_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, **spec: object) -> StubTool:
    directory = tmp_path / f"stub-{name}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(_STUB_SOURCE.format(python=sys.executable, spec_env=SPEC_ENV), encoding="utf-8")
    path.chmod(0o755)
    calls = directory / "calls.log"
    monkeypatch.setenv(SPEC_ENV, json.dumps({"calls": str(calls), **spec}))
    return StubTool(path, calls)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
