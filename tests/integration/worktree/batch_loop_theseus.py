from __future__ import annotations

THESEUS_HOME_ENV = "FAKE_THESEUS_HOME"

THESEUS_PROGRAM = """\
#!{python}
import json
import sys

if len(sys.argv) > 1 and sys.argv[1] == "preflight" and "--json" in sys.argv:
    print(json.dumps({{"reasons": [], "advisories": []}}))
    sys.exit(0)
print("unsupported fake theseus command: " + " ".join(sys.argv[1:]), file=sys.stderr)
sys.exit(2)
"""

THESEUS_LOCAL = """\
import hashlib
import subprocess


class Unavailable(Exception):
    pass


def _git(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Unavailable(f"git {args[0]} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def member_binding(root, base, head):
    return {
        "head_tree": _git(root, "rev-parse", f"{head}^{{tree}}").strip(),
        "patch_digest": hashlib.sha256(_git(root, "diff", base, head).encode()).hexdigest(),
    }
"""

BOOTSTRAP_TEMPLATE = """\
import argparse
import os
import sys
import tempfile
from pathlib import Path

PROGRAM = {program!r}
LOCAL = {local!r}
HOME_ENV = {home_env!r}

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["install"])
parser.add_argument("--repo", required=True)
parser.add_argument("--commit", required=True)
args = parser.parse_args()

base = Path(os.environ.get(HOME_ENV) or tempfile.gettempdir()) / "fake-theseus"
install = base / "install"
(install / "bin").mkdir(parents=True, exist_ok=True)
package = install / "site" / "theseus_reviewer"
package.mkdir(parents=True, exist_ok=True)
(package / "__init__.py").write_text("")
(package / "local.py").write_text(LOCAL)
program = install / "bin" / "theseus"
program.write_text(PROGRAM.format(python=sys.executable))
program.chmod(0o755)
print(program)
"""

BOOTSTRAP = BOOTSTRAP_TEMPLATE.format(program=THESEUS_PROGRAM, local=THESEUS_LOCAL, home_env=THESEUS_HOME_ENV)
