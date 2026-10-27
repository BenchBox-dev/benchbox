from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile

from scripts.sync_sqlglot_runtime import ROOT, RUNTIME, check_runtime

TYPING_ROOT = ROOT / "typings" / "sqlglot-runtime"


def extract_typing_data() -> None:
    runtime = check_runtime()
    for entry in TYPING_ROOT.iterdir():
        if entry.name != ".gitkeep":
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    with zipfile.ZipFile(RUNTIME / runtime["archive"]) as archive:
        for member in archive.namelist():
            if member.endswith((".py", ".pyi", "/py.typed")):
                archive.extract(member, TYPING_ROOT)
    for relative in ("benchbox/__init__.py", "benchbox/_vendor/__init__.py"):
        shutil.copyfile(ROOT / relative, TYPING_ROOT / relative)


def main() -> int:
    extract_typing_data()
    return subprocess.run(["ty", "check", *sys.argv[1:]], cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
