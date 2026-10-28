from __future__ import annotations

import os
from pathlib import Path

from . import commands
from .candidate_stub import LOOPBACK

EGRESS_BLOCK = f"http://{LOOPBACK}:9"
PROXY_NAMES = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")


def quiet_env(home: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in commands.OMP_BASE_ENV if name in os.environ}
    env.update(HOME=str(home), TMPDIR=str(home / "tmp"))
    for name in PROXY_NAMES:
        env[name] = EGRESS_BLOCK
    env["NO_PROXY"] = env["no_proxy"] = commands.GATEWAY_NO_PROXY
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    return env
