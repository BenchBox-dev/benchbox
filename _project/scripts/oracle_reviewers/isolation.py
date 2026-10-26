from __future__ import annotations

import os
import sys
from pathlib import Path

SYS_PTRACE = 1 << 19


def require_isolation(proc: Path = Path("/proc")) -> None:
    if not sys.platform.startswith("linux"):
        raise ValueError("reviewer isolation requires Linux")
    if int((proc / "sys/kernel/yama/ptrace_scope").read_text(encoding="utf-8").strip()) not in (1, 2, 3):
        raise ValueError("Yama must restrict tracing of unrelated processes")
    status_text = (proc / "self/status").read_text(encoding="utf-8")
    status = dict(line.split(":", 1) for line in status_text.splitlines() if ":" in line)
    uids = [int(value) for value in status["Uid"].split()]
    if len(uids) != 4 or len(set(uids)) != 1 or uids[0] == 0 or uids[0] != os.getuid():
        raise ValueError("reviewer must run with one non-root UID")
    for name in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
        if int(status[name], 16) & SYS_PTRACE:
            raise ValueError("reviewer must not hold CAP_SYS_PTRACE")


def main() -> int:
    try:
        require_isolation()
    except (OSError, ValueError, KeyError) as exc:
        print(f"oracle-verdict: isolation prerequisite failed: {exc}", file=sys.stderr)
        return 1
    print("oracle-verdict: Linux non-root UID, ptrace capabilities and Yama prerequisite passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
