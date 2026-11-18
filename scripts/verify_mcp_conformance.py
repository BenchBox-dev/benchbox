#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from benchbox.mcp.readiness import (
    AUTOMATED_GATES,
    CONFORMANCE_REVISION,
    CURRENT_PROTOCOL_VERSION,
    EXTERNAL_GATES,
    INSPECTOR_INTEGRITY,
    INSPECTOR_REVISION,
    INSPECTOR_VERSION,
)

CLI_DESCRIPTION = "Run the revision-pinned MCP conformance and Inspector acceptance gate."

CONFORMANCE_REPOSITORY = "https://github.com/modelcontextprotocol/conformance.git"
SCENARIOS = (
    "caching",
    "dns-rebinding-protection",
    "http-header-validation",
    "tools-list",
    "resources-list",
    "prompts-list",
    "server-sse-multiple-streams",
    "server-stateless",
)
EXPECTED_FAILURE_IDS = (
    "server-stateless:sep-2575-server-rejects-undeclared-capability",
    "server-stateless:sep-2575-missing-capability-http-400",
)
EXPECTED_WARNING_IDS: tuple[str, ...] = ()


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 600) -> None:
    subprocess.run(command, cwd=cwd, check=True, timeout=timeout)


def _parse_result_ids(output: str, status: str) -> list[str]:
    result_ids: list[str] = []
    for line in output.splitlines():
        if status not in line:
            continue
        match = re.search(r"\[(sep-[^\]]+)\]", line)
        result_ids.append(match.group(1).strip() if match else line.strip())
    return result_ids


def _qualify_result_ids(result_ids: list[str], scenario: str) -> list[str]:
    return [result_id if ":" in result_id else f"{scenario}:{result_id}" for result_id in result_ids]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[bytes], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"BenchBox MCP exited before readiness (code {process.returncode})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("BenchBox MCP did not open its loopback port")


def _prepare_conformance(checkout: Path) -> Path:
    _run(["git", "init", "--quiet", str(checkout)])
    _run(["git", "remote", "add", "origin", CONFORMANCE_REPOSITORY], cwd=checkout)
    _run(["git", "fetch", "--quiet", "--depth=1", "origin", CONFORMANCE_REVISION], cwd=checkout)
    _run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=checkout)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if revision != CONFORMANCE_REVISION:
        raise RuntimeError("Conformance checkout revision mismatch")
    _run(["npm", "ci", "--ignore-scripts"], cwd=checkout)
    _run(["npm", "run", "build"], cwd=checkout)
    return checkout / "dist" / "index.js"


def _run_protocol_gate(url: str, protocol_version: str, workspace: Path) -> None:
    conformance = _prepare_conformance(workspace / "conformance")
    for scenario in SCENARIOS:
        command = [
            "node",
            str(conformance),
            "server",
            "--url",
            url,
            "--scenario",
            scenario,
            "--spec-version",
            protocol_version,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        output = (result.stdout or "") + (result.stderr or "")
        qualified_failed = _qualify_result_ids(_parse_result_ids(output, "FAILURE"), scenario)
        qualified_warnings = _qualify_result_ids(_parse_result_ids(output, "WARNING"), scenario)

        expected_failures = [fid for fid in EXPECTED_FAILURE_IDS if fid.startswith(f"{scenario}:")]
        expected_warnings = [wid for wid in EXPECTED_WARNING_IDS if wid.startswith(f"{scenario}:")]
        if result.returncode != 0 and not qualified_failed:
            sys.stdout.write(output)
            sys.stderr.write(result.stderr or "")
            raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
        unexpected_failures = [fid for fid in qualified_failed if fid not in expected_failures]
        missing_failures = [fid for fid in expected_failures if fid not in qualified_failed]
        unexpected_warnings = [wid for wid in qualified_warnings if wid not in expected_warnings]
        missing_warnings = [wid for wid in expected_warnings if wid not in qualified_warnings]
        if unexpected_failures or missing_failures or unexpected_warnings or missing_warnings:
            sys.stdout.write(output)
            sys.stderr.write(result.stderr or "")
            raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
        if expected_failures and result.returncode == 0:
            sys.stdout.write(output)
            sys.stderr.write(result.stderr or "")
            raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
        if result.returncode != 0:
            sys.stdout.write(output)
        else:
            if output.strip():
                sys.stdout.write(output)


def _inspector_pin_matches(npm_view_output: str) -> bool:
    metadata = json.loads(npm_view_output)
    if isinstance(metadata, list):
        if len(metadata) != 1:
            return False
        metadata = metadata[0]
    return metadata == {"dist.integrity": INSPECTOR_INTEGRITY, "gitHead": INSPECTOR_REVISION}


def _run_inspector(url: str) -> None:
    npm_view_output = subprocess.check_output(
        [
            "npm",
            "view",
            f"@modelcontextprotocol/inspector@{INSPECTOR_VERSION}",
            "dist.integrity",
            "gitHead",
            "--json",
        ],
        text=True,
    )
    if not _inspector_pin_matches(npm_view_output):
        raise RuntimeError("Inspector package metadata does not match the pinned revision and integrity")
    _run(
        [
            "npx",
            "--yes",
            f"@modelcontextprotocol/inspector@{INSPECTOR_VERSION}",
            "--cli",
            url,
            "--transport",
            "http",
            "--method",
            "tools/list",
            "--format",
            "json",
        ]
    )


def _run_automated_acceptance() -> None:
    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/mcp/test_protocol_compatibility.py",
            "tests/integration/mcp/test_multiworker_acceptance.py",
            "tests/integration/mcp/test_observability_and_cache.py",
            "-q",
        ]
    )


def _write_evidence(path: Path, protocol_version: str) -> None:
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("Refusing to write production evidence from a dirty worktree")
    source_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    automated = dict.fromkeys(sorted(AUTOMATED_GATES), True)
    automated["multiworker"] = False
    payload = {
        "source_revision": source_revision,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "protocol_version": protocol_version,
        "conformance_revision": CONFORMANCE_REVISION,
        "inspector_version": INSPECTOR_VERSION,
        "inspector_revision": INSPECTOR_REVISION,
        "inspector_integrity": INSPECTOR_INTEGRITY,
        "automated": automated,
        "external": dict.fromkeys(sorted(EXTERNAL_GATES), False),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--protocol-version", required=True)
    parser.add_argument("--evidence-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.protocol_version != CURRENT_PROTOCOL_VERSION:
        raise SystemExit(f"unsupported production protocol version: {args.protocol_version}")
    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    with tempfile.TemporaryDirectory(prefix="benchbox-mcp-conformance-") as temp:
        workspace = Path(temp)
        with (workspace / "server.log").open("wb") as server_log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "benchbox.mcp",
                    "--transport",
                    "streamable-http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--results-dir",
                    str(workspace / "results"),
                    "--charts-dir",
                    str(workspace / "charts"),
                ],
                stdout=subprocess.DEVNULL,
                stderr=server_log,
                env=dict(os.environ),
            )
            try:
                _wait_for_port(port, process)
                _run_protocol_gate(url, args.protocol_version, workspace)
                _run_inspector(url)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    if args.evidence_output is not None:
        _run_automated_acceptance()
        _write_evidence(args.evidence_output, args.protocol_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
