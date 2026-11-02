from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tests.utilities.paths import REPO_ROOT

_HOLDER = (
    "import sys\n"
    "from pathlib import Path\n"
    "from benchbox.utils import host_lock\n"
    "host_lock.acquire(Path(sys.argv[1]) / host_lock.LOCK_NAME, phase='test', gate='held-by-sibling', wait_seconds=0)\n"
    "sys.stdout.write('held\\n')\n"
    "sys.stdout.flush()\n"
    "sys.stdin.read()\n"
)


@contextmanager
def held_lock(lock_dir: Path) -> Iterator[None]:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    for key in ("BENCHBOX_TEST_SESSION_OWNER", "BENCHBOX_SKIP_TEST_LOCK"):
        env.pop(key, None)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(lock_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        yield
    finally:
        if holder.stdin is not None:
            holder.stdin.close()
        holder.wait(timeout=30)
