from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

PUBLIC_DISTRIBUTION = "sqlglot"
CLI_DESCRIPTION = "Install a BenchBox wheel beside public sqlglot and verify the bundled SQLGlot runtime."

CHECK = """
import sys
import zipfile

import sqlglot

import benchbox._vendor as vendor
from benchbox._vendor import sqlglot as bundled
from benchbox._vendor.sqlglot import exp, parse_one
from benchbox._vendor.sqlglot.dialects import duckdb as bundled_duckdb

entry = next(entry for entry in vendor.__path__ if entry.endswith(".zip/benchbox/_vendor"))
assert zipfile.is_zipfile(entry[: -len("/benchbox/_vendor")]), entry
assert ".zip" in bundled.__file__, bundled.__file__
assert ".zip" in bundled_duckdb.__file__, bundled_duckdb.__file__
assert isinstance(parse_one("SELECT 1 AS a"), exp.Select)
translated = bundled.transpile("SELECT CAST(a AS TEXT) FROM t", read="postgres", write="duckdb")
assert translated and "SELECT" in translated[0], translated
assert bundled is not sqlglot
assert bundled.exp is not sqlglot.exp
assert ".zip" not in sqlglot.__file__, sqlglot.__file__
assert sqlglot.__file__ != bundled.__file__
assert sys.modules["sqlglot"] is sqlglot
assert sys.modules["benchbox._vendor.sqlglot"] is bundled
print("bundled:", bundled.__file__)
print("public:", sqlglot.__file__)
"""

AFTER_UNINSTALL = """
import importlib.util

import sqlglot

assert importlib.util.find_spec("benchbox") is None
assert ".zip" not in sqlglot.__file__, sqlglot.__file__
assert sqlglot.transpile("SELECT 1", write="duckdb") == ["SELECT 1"]
print("public sqlglot intact after uninstall")
"""


def _run(*command: str | Path, cwd: Path) -> None:
    print("+", " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def smoke(wheel: Path) -> None:
    wheel = wheel.resolve()
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        environment = root / "venv"
        _run("uv", "venv", environment, cwd=root)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        _run("uv", "pip", "install", "--python", python, PUBLIC_DISTRIBUTION, wheel, cwd=root)
        _run(python, "-c", CHECK, cwd=root)
        _run("uv", "pip", "uninstall", "--python", python, "benchbox", cwd=root)
        _run(python, "-c", AFTER_UNINSTALL, cwd=root)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(CLI_DESCRIPTION, file=sys.stderr)
        return 2
    try:
        smoke(Path(argv[1]))
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"SQLGlot runtime install smoke: {error}", file=sys.stderr)
        return 1
    print(f"SQLGlot runtime install smoke passed: {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
