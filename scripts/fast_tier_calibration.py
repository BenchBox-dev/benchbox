from __future__ import annotations

import argparse
import json
import os
import platform
from collections.abc import Iterator
from pathlib import Path

import pytest

from benchbox.utils.clock import elapsed_seconds, mono_time

SCHEMA = 1
CALIBRATION_ITERATIONS = 6_000_000
CALIBRATION_REPEATS = 3
_MASK = (1 << 64) - 1


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--collection-timing", type=Path, default=None)


def _is_xdist_controller(config: pytest.Config) -> bool:
    return bool(config.getoption("numprocesses", default=0)) and not hasattr(config, "workerinput")


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session: pytest.Session) -> Iterator[None]:
    start = mono_time()
    yield
    output = session.config.getoption("collection_timing")
    if output is None or _is_xdist_controller(session.config):
        return
    worker = getattr(session.config, "workerinput", {}).get("workerid", "main")
    line = json.dumps({"process": worker, "seconds": elapsed_seconds(start)}) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(line)


def run_workload(iterations: int) -> int:
    state = 0x9E3779B97F4A7C15
    for _ in range(iterations):
        state = (state * 6364136223846793005 + 1442695040888963407) & _MASK
        state ^= state >> 29
    return state


def measure_workload(iterations: int | None = None, repeats: int = CALIBRATION_REPEATS) -> dict[str, object]:
    iterations = CALIBRATION_ITERATIONS if iterations is None else iterations
    runs: list[float] = []
    checksum = 0
    for _ in range(repeats):
        start = mono_time()
        checksum = run_workload(iterations)
        runs.append(elapsed_seconds(start))
    return {"iterations": iterations, "checksum": checksum, "run_seconds": runs, "best_seconds": min(runs)}


def read_collection_seconds(path: Path) -> list[float]:
    if not path.is_file():
        return []
    return [json.loads(line)["seconds"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_record(collection_file: Path) -> dict[str, object]:
    collection = read_collection_seconds(collection_file)
    return {
        "schema": SCHEMA,
        "workload": measure_workload(),
        "collection": {
            "processes": len(collection),
            "max_seconds": max(collection) if collection else None,
            "seconds": collection,
        },
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record runner speed calibration and pytest collection time.")
    parser.add_argument("--collection-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    record = build_record(args.collection_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    workload = record["workload"]
    collection = record["collection"]
    print(
        f"Runner calibration best {workload['best_seconds']:.3f}s over {workload['iterations']} iterations; "
        f"collection max {collection['max_seconds']} s across {collection['processes']} process(es)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
