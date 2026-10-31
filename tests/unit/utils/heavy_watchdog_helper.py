from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def spawn(role: str, *args: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", role, *args])


def touch_memory(mib: int) -> bytearray:
    size = mib * 2**20
    buffer = bytearray(size)
    for offset in range(0, size, 4096):
        buffer[offset] = 1
    return buffer


def alloc(mib: str, pid_file: str) -> None:
    held = touch_memory(int(mib))
    Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(60)
    del held


def nested_grandchild(directory: str, mib: str) -> None:
    root = Path(directory)
    (root / "gc.pid").write_text(str(os.getpid()), encoding="utf-8")
    while not (root / "go").exists():
        time.sleep(0.05)
    held = touch_memory(int(mib))
    time.sleep(60)
    del held


def nested_child(directory: str, mib: str) -> None:
    spawn("nested_grandchild", directory, mib).wait()


def nested_holder(directory: str, mib: str) -> None:
    spawn("nested_child", directory, mib)
    (Path(directory) / "holder.ready").write_text("x", encoding="utf-8")
    time.sleep(60)


def orphan_holder(directory: str) -> None:
    root = Path(directory)
    first = os.fork()
    if first == 0:
        os.setsid()
        if os.fork() == 0:
            alloc("400", str(root / "daemon.pid"))
        os._exit(0)
    os.waitpid(first, 0)
    spawn("alloc", "400", str(root / "victim.pid"))
    time.sleep(60)


def guarded_holder(directory: str) -> None:
    from benchbox.utils import host_lock

    root = Path(directory)
    host_lock.acquire(root / "test.lock", phase="t", guard=True)
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (root / "kid.pid").write_text(str(kid.pid), encoding="utf-8")
    while not (root / "go").exists():
        time.sleep(0.05)
    held = touch_memory(300)
    time.sleep(60)
    del held


def gil_holder(directory: str) -> None:
    root = Path(directory)
    spawn("alloc", "400", str(root / "victim.pid"))
    while not (root / "victim.pid").exists():
        time.sleep(0.05)
    (root / "spinning").write_text("x", encoding="utf-8")
    end = time.time() + 20
    while time.time() < end:
        sum(range(30_000_000))


def driver(config_json: str) -> None:
    from benchbox.utils import heavy_watchdog as hw

    config = json.loads(config_json)
    policy = hw.WatchPolicy(
        holder_pid=config["pid"],
        holder_created=config["created"],
        budget_bytes=config["budget"],
        mode=config["mode"],
        kill_free_disk_gib=0.0,
        interval_seconds=0.2,
        grace_seconds=2.0,
    )
    hw.Watchdog(policy, pressure_reader=lambda: 1, disk_reader=lambda roots: []).run()


ROLES = {
    "alloc": alloc,
    "nested_grandchild": nested_grandchild,
    "nested_child": nested_child,
    "nested_holder": nested_holder,
    "orphan_holder": orphan_holder,
    "guarded_holder": guarded_holder,
    "gil_holder": gil_holder,
    "driver": driver,
}


if __name__ == "__main__":
    ROLES[sys.argv[1]](*sys.argv[2:])
