from __future__ import annotations

import datetime as dt
import json
import os
import platform
import shlex
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from benchbox.mcp.errors import scrub_secret_material
from benchbox.utils.clock import elapsed_seconds, mono_time
from tests.uat import docker_assets

_FILE_BYTES = 16 * 1024 * 1024
_COMMAND_SECONDS = 10
_CAPTURE_SECONDS = 60
_LOG_ROOT = "/data/deploy/starrocks"
_CEDARDB_LOG_LINES = 50000
_CEDARDB_EVENT_WINDOW_S = 6 * 3600
_CGROUP_MEMORY = (
    "status=0; for f in /sys/fs/cgroup/memory.events "
    "/sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.max; "
    'do echo "$f"; cat "$f" || status=1; done; exit "$status"'
)
_CONTAINER_STATE = "{{json .State}}\n{{json .RestartCount}}\n{{json .HostConfig.Memory}}\n{{json .Image}}"
Collect = Callable[[str, list[str]], str]


def _host_metadata() -> dict[str, object]:
    metadata: dict[str, object] = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "kernel": platform.uname()._asdict(),
        "cpu_count": os.cpu_count(),
        "runner": {key: os.environ[key] for key in ("ImageOS", "ImageVersion", "RUNNER_ARCH") if key in os.environ},
    }
    for name, path in {
        "cpuinfo": "/proc/cpuinfo",
        "system_vendor": "/sys/class/dmi/id/sys_vendor",
        "product_name": "/sys/class/dmi/id/product_name",
    }.items():
        try:
            with Path(path).open(encoding="utf-8", errors="replace") as stream:
                metadata[name] = stream.read(65536)
        except OSError as exc:
            metadata[name] = {"unavailable": type(exc).__name__}
    vendor = str(metadata.get("system_vendor", ""))
    metadata["provider"] = "AWS" if "Amazon" in vendor else "Azure" if "Microsoft" in vendor else "unknown"
    return metadata


def _log_script(pattern: str, *, limit: int = _FILE_BYTES, count: int = 1) -> str:
    return (
        f"files=$(ls -1dt -- {_LOG_ROOT}/{pattern} 2>/dev/null) || exit 1; "
        'file_count=$(printf "%s\\n" "$files" | wc -l); '
        f'[ "$file_count" -le {count} ] || printf "TRUNCATED=true\\n"; '
        "IFS='\n'; found=0; for f in $files; do "
        '[ -f "$f" ] || continue; '
        'printf "SOURCE %s BYTES " "$f"; size=$(wc -c < "$f"); echo "$size"; '
        f'[ "$size" -le {limit} ] || printf "TRUNCATED=true\\n"; '
        f'tail -c {limit} "$f" || exit $?; '
        f'found=$((found+1)); [ "$found" -lt {count} ] || break; '
        'done; [ "$found" -gt 0 ]'
    )


def _collect_starrocks(collect: Collect, cli: str, container: str) -> None:
    execute = [cli, "exec", container]
    for name, pattern in (
        ("fe.log", "fe/log/fe.log"),
        ("fe.warn.log", "fe/log/fe.warn.log"),
        ("be.INFO", "be/log/be.INFO"),
        ("be.WARNING", "be/log/be.WARNING"),
        ("be.out", "be/log/be.out"),
    ):
        collect(name, [*execute, "sh", "-c", _log_script(pattern)])
    collect(
        "fe-gc.log",
        [*execute, "sh", "-c", _log_script("fe/log/fe.gc.log.*", limit=(_FILE_BYTES - 8192) // 8, count=8)],
    )
    collect(
        "backends.txt",
        [
            *execute,
            "mysql",
            "-h127.0.0.1",
            "-P9030",
            "-uroot",
            "--skip-password",
            "-e",
            "SHOW BACKENDS\\G SHOW BACKEND BLACKLIST\\G",
        ],
    )
    collect("memory.txt", [*execute, "sh", "-c", _CGROUP_MEMORY])
    collect(
        "fe-heap.txt",
        [
            *execute,
            "sh",
            "-c",
            'pid=$(pgrep -o -f "com.starrocks.StarRocksFE") || exit $?; '
            'jcmd "$pid" VM.flags && jcmd "$pid" GC.heap_info',
        ],
    )
    collect(
        "be-mem-limit.txt",
        [
            *execute,
            "sh",
            "-c",
            "set -e; value=$(curl -fsS --max-time 5 http://127.0.0.1:8040/varz); "
            'printf "%s\\n" "$value" | grep -E ' + shlex.quote(r"^mem_limit="),
        ],
    )
    collect(
        "fe-blacklist-config.txt",
        [
            *execute,
            "mysql",
            "-h127.0.0.1",
            "-P9030",
            "-uroot",
            "--skip-password",
            "-e",
            "ADMIN SHOW FRONTEND CONFIG LIKE 'black_host%';",
        ],
    )


def _collect_container_log_events_memory(collect: Collect, cli: str, container: str) -> None:
    collect("container.log", [cli, "logs", "--timestamps", "--tail", str(_CEDARDB_LOG_LINES), container])
    until = int(time.time())
    collect(
        "events.txt",
        [
            cli,
            "events",
            "--since",
            str(until - _CEDARDB_EVENT_WINDOW_S),
            "--until",
            str(until),
            "--filter",
            f"container={container}",
        ],
    )
    collect("memory.txt", [cli, "exec", container, "sh", "-c", _CGROUP_MEMORY])


_COLLECTORS: dict[str, Callable[[Collect, str, str], None]] = {
    "starrocks": _collect_starrocks,
    "cedardb": _collect_container_log_events_memory,
    "pg-mooncake": _collect_container_log_events_memory,
}
EVIDENCE_PLATFORMS: Sequence[str] = tuple(_COLLECTORS)


def capture_engine_evidence(
    platform: str,
    spec: docker_assets.DockerPlatformSpec,
    project_name: str,
    *,
    log_dir: Path,
    runner: Callable[..., docker_assets.DockerCommandResult],
    env: dict[str, str],
) -> tuple[str, ...]:
    destination = log_dir / f"{platform}-evidence"
    destination.mkdir(parents=True, exist_ok=True)
    started = mono_time()
    records: list[dict[str, object]] = []
    failures: list[str] = []

    def save(name: str, text: str) -> bool:
        encoded = scrub_secret_material(text).encode("utf-8")
        truncated = len(encoded) > _FILE_BYTES
        if truncated:
            marker = b"\n[bounded output omitted]\n"
            prefix = encoded[:512]
            encoded = prefix + marker + encoded[-(_FILE_BYTES - len(prefix) - len(marker)) :]
        (destination / name).write_bytes(encoded)
        return truncated

    save("host.json", json.dumps(_host_metadata(), indent=2))

    def collect(name: str, argv: list[str]) -> str:
        remaining = _CAPTURE_SECONDS - elapsed_seconds(started)
        if remaining <= 0:
            failures.append(f"{name}: capture deadline exhausted")
            records.append({"file": name, "status": "deadline-exhausted"})
            return ""
        try:
            result = runner(
                argv,
                dry_run=False,
                timeout_s=min(_COMMAND_SECONDS, remaining),
                cwd=docker_assets.REPO_ROOT,
                env=env,
            )
            truncated = save(name, result.stdout + "\nSTDERR\n" + result.stderr) or "TRUNCATED=true\n" in result.stdout
            records.append(
                {
                    "file": name,
                    "status": "ok" if result.succeeded else "failed",
                    "exit_code": result.returncode,
                    "timed_out": result.timed_out,
                    "truncated": truncated,
                }
            )
            if not result.succeeded:
                failures.append(f"{name}: exit={result.returncode}, timed_out={result.timed_out}")
            return result.stdout if result.succeeded else ""
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            records.append({"file": name, "status": "failed", "error_type": type(exc).__name__})
            return ""

    cli = docker_assets.resolve_container_cli()
    ids = collect("containers.txt", docker_assets.compose_container_ids_command(spec, project_name))
    container_ids = ids.split()
    if len(container_ids) != 1 or not all(char.isalnum() or char in "-_" for char in container_ids[0]):
        failures.append(f"expected exactly one {platform} container")
    else:
        container = container_ids[0]
        collect(
            "container-state.json",
            [cli, "inspect", "--format", _CONTAINER_STATE, container],
        )
        _COLLECTORS[platform](collect, cli, container)
    save(
        "manifest.json",
        json.dumps(
            {"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "commands": records, "failures": failures},
            indent=2,
        ),
    )
    return tuple(failures)
