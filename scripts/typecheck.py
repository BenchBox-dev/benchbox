from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from scripts.sync_sqlglot_runtime import ROOT, RUNTIME, check_runtime


def main() -> int:
    runtime = check_runtime()
    with tempfile.TemporaryDirectory(prefix="benchbox-typecheck-") as temporary:
        with zipfile.ZipFile(RUNTIME / runtime["archive"]) as archive:
            for member in archive.namelist():
                if member.endswith((".py", ".pyi", "/py.typed")):
                    archive.extract(member, temporary)
        for relative in ("benchbox/__init__.py", "benchbox/_vendor/__init__.py"):
            shutil.copyfile(ROOT / relative, Path(temporary) / relative)
        return subprocess.run(
            ["ty", "check", "--extra-search-path", str(Path(temporary)), *sys.argv[1:]],
            cwd=ROOT,
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
