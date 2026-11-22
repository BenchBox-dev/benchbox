from __future__ import annotations

import os
import signal
import subprocess
import time

KILL_GRACE_SECONDS = 0.2


def kill_process_group(proc: subprocess.Popen) -> None:
    if hasattr(os, "killpg") and hasattr(signal, "SIGTERM") and hasattr(signal, "SIGKILL"):
        try:
            if not signal_process_group(proc.pid, signal.SIGTERM):
                return
            time.sleep(KILL_GRACE_SECONDS)
        finally:
            signal_process_group(proc.pid, signal.SIGKILL)
        return
    proc.kill()


def signal_process_group(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True
