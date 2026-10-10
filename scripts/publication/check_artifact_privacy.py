#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    from benchbox.validation.bundle import unanonymized_tuning_findings
except ImportError:

    def unanonymized_tuning_findings(payload):
        return ["tuning detector unavailable (benchbox.validation.bundle could not be imported)"]


SENSITIVE_PATTERNS = (
    (re.compile(r"ghp_[0-9a-zA-Z]{36}"), "GitHub Personal Access Token (ghp_)"),
    (re.compile(r"github_pat_[0-9a-zA-Z_]{82}"), "GitHub Fine-Grained PAT"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "Private Key PEM header"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
)

AWS_EXAMPLE_TOKENS = frozenset(
    {
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    }
)

_CONNECTION_RE = re.compile(r"(?:postgresql|postgres|mysql)://([^\s/:@]+):([^\s/@]+)@([^\s/@]+)")

PLACEHOLDER_PASSWORDS = frozenset(
    {
        "password",
        "pass",
        "your-password",
        "your_password",
        "changeme",
        "***",
        "<password>",
    }
)


def _connection_credential_leaks(content: str) -> int:
    leaks = 0
    for match in _CONNECTION_RE.finditer(content):
        if match.group(2).strip().lower() not in PLACEHOLDER_PASSWORDS:
            leaks += 1
    return leaks


SCANNABLE_EXTENSIONS = (
    ".html",
    ".htm",
    ".json",
    ".js",
    ".css",
    ".svg",
    ".txt",
    ".xml",
    ".csv",
    ".yaml",
    ".yml",
)


def scan_file_for_privacy(file_path: Path) -> list[str]:
    findings: list[str] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        findings.append(f"Could not read '{file_path}': {e}")
        return findings

    for pat, desc in SENSITIVE_PATTERNS:
        matches = [m for m in pat.findall(content) if m not in AWS_EXAMPLE_TOKENS]
        if matches:
            findings.append(f"Detected {desc} in '{file_path.name}' ({len(matches)} occurrence(s))")

    conn_leaks = _connection_credential_leaks(content)
    if conn_leaks:
        findings.append(
            f"Detected Database connection string with credentials in '{file_path.name}' ({conn_leaks} occurrence(s))"
        )

    if file_path.suffix.lower() == ".json":
        try:
            payload = json.loads(content)
        except ValueError:
            payload = None
        for finding in unanonymized_tuning_findings(payload):
            findings.append(f"Detected unanonymized tuning diagnostics in '{file_path.name}': {finding}")

    return findings


def scan_directory_for_privacy(target_dir: Path) -> list[str]:
    if not target_dir.exists():
        return [f"Target directory '{target_dir}' does not exist"]

    findings: list[str] = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in SCANNABLE_EXTENSIONS:
                file_findings = scan_file_for_privacy(p)
                for fnd in file_findings:
                    rel = p.relative_to(target_dir).as_posix()
                    findings.append(f"{rel}: {fnd}")

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan assembled artifacts for sensitive data")
    parser.add_argument("target_dir", type=Path, nargs="?", default=Path("publication/out/site"))
    args = parser.parse_args(argv)

    print(f"Scanning '{args.target_dir}' for sensitive data and privacy invariants...")
    findings = scan_directory_for_privacy(args.target_dir)

    if findings:
        print("❌ Privacy and sensitivity scan FAILED:")
        for fnd in findings:
            print(f"  - {fnd}")
        return 1

    print("✅ Privacy and sensitivity scan PASSED: zero sensitive leaks detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
