from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from _project.scripts.oracle_reviewers import cli, local, omp_pin, runner
from _project.scripts.oracle_reviewers.policy import Reviewer

from .test_attempts import _v

POLICY_RELATIVE = ".github/oracle-reviewers.yml"


def main(log: Path, seconds: float) -> int:
    def review(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"start {time.time()}\n")
        time.sleep(seconds)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"end {time.time()}\n")
        return runner.ReviewOutcome(_v(), None, "")

    local.reachable = lambda url, proxy="": True
    local.keychain_key = lambda: ""
    local.pinned_omp = lambda text: (omp_pin.pin_from_text(text).version, Path(sys.executable))
    runner.review = review
    return cli.main(["local-review", "--policy", POLICY_RELATIVE, "--base-ref", "develop"])


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), float(sys.argv[2])))
