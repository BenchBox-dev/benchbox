#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import NamedTuple

Runner = Callable[[list[str]], tuple[int, str]]

RATE_LIMIT = "GitHub rate limit"
SERVER_ERROR = "GitHub server error"
NETWORK = "network error"
TRACKER_READ = "tracker read unparseable"

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (5.0, 15.0, 45.0)
SECONDARY_LIMIT_SECONDS = 60.0
RESET_MARGIN_SECONDS = 1.0
DEFAULT_BUDGET_SECONDS = 90.0
ATTEMPTS_EXHAUSTED = "did not clear after"
BEYOND_BUDGET = "cannot be waited out"
RATE_LIMIT_COMMAND = ["gh", "api", "rate_limit"]
TRACKER_SHOW_PREFIX = ("tractable", "batch", "show")
WRITE_FLAGS = frozenset({"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"})
READ_ONLY_GH = frozenset({("run", "download"), ("run", "list"), ("run", "view"), ("pr", "view"), ("pr", "list")})

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        RATE_LIMIT,
        re.compile(r"API rate limit|secondary rate limit|rate limit exceeded|Retry-After: ?\d|HTTP 429", re.IGNORECASE),
    ),
    (
        SERVER_ERROR,
        re.compile(
            r"HTTP 5\d\d\b|returned 5\d\d|\b50[0-4] (?:Bad Gateway|Service Unavailable|Gateway Time-?out)|ServerBusy|Egress is over the account limit|Bad Gateway|"
            r"Service Unavailable|Gateway Time-?out|Internal Server Error",
            re.IGNORECASE,
        ),
    ),
    (
        NETWORK,
        re.compile(
            r"i/o timeout|TLS handshake timeout|net/http: .*timeout|Client\.Timeout|context deadline exceeded|"
            r"timed out after|connection reset|connection refused|unexpected EOF|no such host|"
            r"temporary failure in name resolution|network is unreachable|dial tcp",
            re.IGNORECASE,
        ),
    ),
    (TRACKER_READ, re.compile(r"unparseable output", re.IGNORECASE)),
)
_LOOKUP_FAILURE = re.compile(r"could not be read|cannot read|could not be looked up|lookup", re.IGNORECASE)
_RETRY_AFTER = re.compile(r"retry-after\D{0,4}(\d+)", re.IGNORECASE)


class Transient(NamedTuple):
    kind: str
    retry_after: float | None = None
    line: str = ""


class Budget:
    def __init__(
        self,
        seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.clock = clock
        self.sleep = sleep
        self.wall = wall
        self.deadline = clock() + seconds

    def remaining(self) -> float:
        return max(self.deadline - self.clock(), 0.0)

    def wait(self, seconds: float) -> bool:
        if seconds > self.remaining():
            return False
        self.sleep(max(seconds, 0.0))
        return True


def classify(text: object) -> Transient | None:
    message = str(text)
    for kind, pattern in _PATTERNS:
        found = pattern.search(message)
        if found:
            match = _RETRY_AFTER.search(message)
            line = next((item for item in message.splitlines() if found.group(0) in item), message)
            return Transient(kind, float(match.group(1)) if match else None, line)
    return None


def is_transient(text: object) -> bool:
    return classify(text) is not None


def rate_limit_reset(run: Runner, wall: Callable[[], float] = time.time) -> float | None:
    try:
        code, out = run(RATE_LIMIT_COMMAND)
    except Exception:
        return None
    if code != 0:
        return None
    try:
        resources = json.loads(out).get("resources", {})
        resets = [
            float(entry["reset"])
            for entry in resources.values()
            if isinstance(entry, dict) and entry.get("remaining") == 0 and "reset" in entry
        ]
    except (ValueError, AttributeError, TypeError, KeyError):
        return None
    return min(resets) - wall() if resets else None


def describe_reset(seconds: float, wall: Callable[[], float] = time.time) -> str:
    stamp = datetime.fromtimestamp(wall() + seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"resets at {stamp} (in {max(seconds, 0.0) / 60:.1f} min)"


def retryable_read(cmd: Sequence[str]) -> bool:
    if len(cmd) < 2:
        return False
    if cmd[0] == "gh" and cmd[1] == "api":
        if "graphql" in cmd:
            return not any("mutation" in part.lower() for part in cmd)
        return not WRITE_FLAGS.intersection(cmd)
    if cmd[0] == "gh" and len(cmd) > 2 and (cmd[1], cmd[2]) in READ_ONLY_GH:
        return True
    return is_tracker_show(cmd)


def is_tracker_show(cmd: Sequence[str]) -> bool:
    names = tuple(cmd)
    return any(names[i : i + 3] == TRACKER_SHOW_PREFIX for i in range(len(names)))


def _tracker_unreadable(cmd: Sequence[str], code: int, out: str) -> bool:
    if code == 0 or not is_tracker_show(cmd):
        return False
    try:
        return not isinstance(json.loads(out), dict)
    except ValueError:
        return True


class RetryingRunner:
    def __init__(self, run: Runner, budget: Budget, *, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._run = run
        self.budget = budget
        self.max_attempts = max_attempts

    def _attempt(self, cmd: list[str]) -> tuple[int, str, Transient | None]:
        try:
            code, out = self._run(cmd)
        except Exception as exc:
            transient = classify(exc)
            if transient is None:
                raise
            return 1, str(exc), transient
        if code == 0:
            return code, out, None
        if _tracker_unreadable(cmd, code, out):
            return code, out, Transient(TRACKER_READ)
        return code, out, classify(out)

    def _delay(self, transient: Transient, attempt: int) -> tuple[float, str]:
        if transient.kind != RATE_LIMIT:
            return BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)], ""
        if transient.retry_after is not None:
            return transient.retry_after + RESET_MARGIN_SECONDS, ""
        until_reset = rate_limit_reset(self._run, self.budget.wall)
        if until_reset is None:
            return SECONDARY_LIMIT_SECONDS, ""
        return until_reset + RESET_MARGIN_SECONDS, describe_reset(until_reset, self.budget.wall)

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        if not retryable_read(cmd):
            return self._run(cmd)
        started = self.budget.clock()
        attempt = 0
        while True:
            code, out, transient = self._attempt(cmd)
            if transient is None:
                return code, out
            attempt += 1
            delay, reset = self._delay(transient, attempt - 1)
            if attempt >= self.max_attempts:
                return self._give_up(code, out, transient, attempt, started, reset, True, "")
            if not self.budget.wait(delay):
                why = f"the next retry needs {delay:.0f}s but {self.budget.remaining():.0f}s of the wait remain"
                return self._give_up(code, out, transient, attempt, started, reset, False, why)

    def _give_up(
        self,
        code: int,
        out: str,
        transient: Transient,
        attempt: int,
        started: float,
        reset: str,
        exhausted: bool,
        why: str,
    ) -> tuple[int, str]:
        spent = self.budget.clock() - started
        detail = "; ".join(part for part in (reset, why) if part)
        suffix = f" ({detail})" if detail else ""
        outcome = (
            f"{ATTEMPTS_EXHAUSTED} {attempt} attempt(s) over {spent:.0f}s"
            if exhausted
            else f"{BEYOND_BUDGET} inside the wait budget after {attempt} attempt(s)"
        )
        return code or 1, f"transient {transient.kind} {outcome}{suffix}: {out.strip()}"


def waiting_on(text: object) -> bool:
    message = str(text)
    if BEYOND_BUDGET in message:
        return False
    return ATTEMPTS_EXHAUSTED in message or classify(message) is not None


def reason_is_transient(text: object) -> bool:
    message = str(text)
    return ATTEMPTS_EXHAUSTED in message and BEYOND_BUDGET not in message


def explain(cause: str, run: Runner | None = None, wall: Callable[[], float] = time.time) -> str | None:
    transient = classify(cause)
    if transient is None or not _LOOKUP_FAILURE.search(cause):
        return None
    guidance = "GitHub could not be read just now, so the review did not run and nothing was pushed."
    if transient.kind == RATE_LIMIT:
        until_reset = rate_limit_reset(run or _live_run, wall)
        if until_reset is not None:
            guidance += f" The limit {describe_reset(until_reset, wall)}; run make pr-open again after that."
        else:
            guidance += " The reset time is shown by gh api rate_limit; run make pr-open again after it."
    else:
        guidance += " This is a transient failure; run make pr-open again in a few minutes."
    return f"{transient.kind}: {' '.join(cause.split())[:300]}. {guidance}"


def _live_run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60, check=False)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify a GitHub read failure as transient and print retry guidance."
    )
    parser.add_argument("cause")
    parser.add_argument("--check", action="store_true", help="exit 0 when the text is a transient failure, else 1")
    args = parser.parse_args(argv)
    if args.check:
        return 0 if is_transient(args.cause) else 1
    message = explain(args.cause)
    if message is None:
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
