from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

CLI_DESCRIPTION = (
    "Release gate: the bundled SQLGlot runtime must not carry a published security advisory.\n"
    "\n"
    "Package-level scanners such as pip-audit only see declared requirements, and SQLGlot is\n"
    "shipped inside benchbox/_vendor, so this check derives the upstream SQLGlot version from\n"
    "benchbox/_vendor/sqlglot.json and queries the OSV advisory database for that version.\n"
    "Any matching advisory, or an unreachable database, fails the gate.\n"
)

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
UPSTREAM_PACKAGE = "sqlglot"
PIN_PATH = Path("benchbox/_vendor/sqlglot.json")
REPO_ROOT = Path(__file__).resolve().parents[1]
_FORK_VERSION = re.compile(r"(\d+\.\d+\.\d+)\.\d+")

Querier = Callable[[str], list[Mapping[str, Any]]]


def upstream_version(pin: Mapping[str, Any]) -> str:
    version = pin.get("version")
    match = _FORK_VERSION.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise ValueError(f"bundled SQLGlot version {version!r} is not <upstream X.Y.Z>.<fork revision>")
    return match.group(1)


def query_osv(version: str) -> list[Mapping[str, Any]]:
    vulns: list[Mapping[str, Any]] = []
    page_token: str | None = None
    while True:
        body: dict[str, Any] = {"package": {"name": UPSTREAM_PACKAGE, "ecosystem": "PyPI"}, "version": version}
        if page_token:
            body["page_token"] = page_token
        request = urllib.request.Request(
            OSV_QUERY_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise RuntimeError(f"cannot query OSV advisories: {error}") from error
        vulns.extend(payload.get("vulns", []))
        page_token = payload.get("next_page_token")
        if not page_token:
            return vulns


def evaluate(pin: Mapping[str, Any], vulns: list[Mapping[str, Any]]) -> list[str]:
    version = upstream_version(pin)
    problems = []
    for vuln in vulns:
        aliases = ", ".join(vuln.get("aliases", []))
        summary = vuln.get("summary") or "no summary published"
        suffix = f" ({aliases})" if aliases else ""
        problems.append(
            f"bundled SQLGlot upstream {version} is affected by {vuln.get('id', 'unknown advisory')}{suffix}: "
            f"{summary}; refresh the fork onto a fixed upstream release and adopt the new wheel"
        )
    return problems


def check(root: Path, querier: Querier | None = None) -> list[str]:
    pin = json.loads((root / PIN_PATH).read_text(encoding="utf-8"))
    try:
        version = upstream_version(pin)
        vulns = (querier or query_osv)(version)
    except (OSError, RuntimeError, ValueError) as error:
        return [str(error)]
    return evaluate(pin, vulns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    arguments = parser.parse_args(argv)
    problems = check(arguments.root)
    if problems:
        print("sqlglot advisory check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("sqlglot advisory check OK: no published advisory matches the bundled upstream version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
