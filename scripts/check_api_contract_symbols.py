#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SYMBOLS = REPO_ROOT / "_project" / "design" / "api-public-symbols.json"

PROBE = Path(__file__).resolve().with_name("api_contract_probe.py")
MAX_WORKERS = 8


class InterpreterNotFound(Exception):
    pass


def load_inventory(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_python(python: str) -> str:
    candidate = python if os.sep in python else (shutil.which(python) or python)
    absolute = os.path.abspath(os.path.expanduser(candidate))
    if not (os.path.isfile(absolute) and os.access(absolute, os.X_OK)):
        raise InterpreterNotFound(f"python interpreter not found or not executable: {python}")
    return absolute


def probe_one(python: str, workdir: str, entry: dict[str, Any]) -> dict[str, Any]:
    completed = subprocess.run(
        [python, str(PROBE), json.dumps(entry)],
        capture_output=True,
        text=True,
        cwd=workdir,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        if completed.returncode != 0 or not lines:
            raise ValueError(f"exit {completed.returncode}: {completed.stderr.strip()[-500:]}")
        return json.loads(lines[-1])
    except ValueError as exc:
        return {
            "python": None,
            "install": None,
            "result": {"import_error": f"probe crashed: {exc}", "alias_errors": [], "signature": None},
        }


def run_probe(python: str, symbols: list[dict[str, Any]], package: str = "benchbox") -> dict[str, Any]:
    interpreter = resolve_python(python)
    entries = [
        {
            "package": package,
            "symbol": s["symbol"],
            "module": s["module"],
            "name": s.get("name"),
            "aliases": s.get("aliases", []),
        }
        for s in symbols
    ]
    with tempfile.TemporaryDirectory(prefix="api-contract-") as workdir:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            reports = list(pool.map(lambda entry: probe_one(interpreter, workdir, entry), entries))
    healthy = [r for r in reports if r["install"] is not None]
    install = (
        healthy[0]["install"]
        if healthy
        else {"version": None, "file": None, "direct_url": False, "error": "no probe succeeded"}
    )
    return {
        "installed_version": install["version"],
        "python": healthy[0]["python"] if healthy else None,
        "install": install,
        "results": {entry["symbol"]: report["result"] for entry, report in zip(entries, reports, strict=True)},
    }


def install_failures(probe: dict[str, Any], repo_root: Path) -> list[str]:
    install = probe["install"]
    failures: list[str] = []
    if install.get("error"):
        failures.append(f"install: {install['error'].strip()}")
    if install.get("direct_url"):
        failures.append("install: distribution has direct_url.json (editable or direct install, not a wheel)")
    location = install.get("file")
    if location is None:
        failures.append("install: package has no __file__")
    elif Path(location).resolve().is_relative_to(repo_root.resolve()):
        failures.append(f"install: package imported from inside the repo: {location}")
    return failures


def evaluate(
    inventory: dict[str, Any],
    probe: dict[str, Any],
    *,
    strict: bool = False,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    expected_version = inventory["source"]["version"]
    failures: list[str] = install_failures(probe, repo_root)
    unrecorded: list[str] = []
    expected_failures: list[str] = []
    passed = 0
    if probe["installed_version"] != expected_version:
        failures.append(f"version: installed {probe['installed_version']} != expected {expected_version}")
    expected_python = inventory["source"].get("python")
    if expected_python and probe.get("python") != expected_python:
        failures.append(f"python: probe {probe.get('python')} != expected {expected_python}")
    for entry in inventory["symbols"]:
        symbol = entry["symbol"]
        result = probe["results"][symbol]
        problems: list[str] = []
        known = entry.get("known_import_failure")
        if known is not None:
            if not (isinstance(known, dict) and known.get("reason") and known.get("match")):
                problems.append("known_import_failure needs nonempty reason and match")
            elif not result["import_error"]:
                problems.append(f"known failure now passes: remove known_import_failure ({known['reason']})")
            elif known["match"] in result["import_error"]:
                expected_failures.append(symbol)
                continue
            else:
                problems.append(
                    f"known failure fails with a different error (expected {known['match']!r}): {result['import_error']}"
                )
            failures.extend(f"{symbol}: {problem}" for problem in problems)
            continue
        if result["import_error"]:
            problems.append(f"import failure: {result['import_error']}")
        problems.extend(f"alias mismatch: {error}" for error in result["alias_errors"])
        if not result["import_error"]:
            if "signature" not in entry:
                unrecorded.append(symbol)
            elif entry["signature"] != result["signature"]:
                problems.append(f"signature drift: recorded {entry['signature']!r}, found {result['signature']!r}")
        if problems:
            failures.extend(f"{symbol}: {problem}" for problem in problems)
        else:
            passed += 1
    if strict:
        failures.extend(f"{symbol}: unrecorded signature" for symbol in unrecorded)
    return {
        "version": probe["installed_version"],
        "total": len(inventory["symbols"]),
        "passed": passed,
        "unrecorded": unrecorded,
        "expected_failures": expected_failures,
        "failures": failures,
        "ok": not failures,
    }


def write_inventory(path: Path, inventory: dict[str, Any]) -> None:
    path.write_text(json.dumps(inventory, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_check(args: argparse.Namespace) -> int:
    inventory = load_inventory(args.symbols)
    probe = run_probe(args.python, inventory["symbols"], inventory["source"]["package"])
    report = evaluate(inventory, probe, strict=args.strict, repo_root=args.repo_root)
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


def cmd_record(args: argparse.Namespace) -> int:
    inventory = load_inventory(args.symbols)
    probe = run_probe(args.python, inventory["symbols"], inventory["source"]["package"])
    install = install_failures(probe, args.repo_root)
    if install:
        print("refusing to record; " + "; ".join(install), file=sys.stderr)
        return 1
    expected = inventory["source"]["version"]
    if probe["installed_version"] != expected:
        print(f"installed {probe['installed_version']} != expected {expected}", file=sys.stderr)
        return 1
    known = {e["symbol"]: e.get("known_import_failure") for e in inventory["symbols"]}
    broken = []
    for symbol, result in probe["results"].items():
        rule = known[symbol]
        if rule and result["import_error"] and rule.get("match") and rule["match"] in result["import_error"]:
            continue
        if result["import_error"] or result["alias_errors"] or rule:
            broken.append(symbol)
    if broken:
        print("refusing to record; broken symbols: " + ", ".join(broken), file=sys.stderr)
        return 1
    recorded = 0
    for entry in inventory["symbols"]:
        result = probe["results"][entry["symbol"]]
        if result["import_error"]:
            continue
        entry["signature"] = result["signature"]
        recorded += 1
    write_inventory(args.symbols, inventory)
    print(f"recorded {recorded} signatures")
    return 0


def cmd_venv(args: argparse.Namespace) -> int:
    source = load_inventory(args.symbols)["source"]
    venv_dir = Path(args.dir)
    subprocess.run(["uv", "venv", "--python", source["python"], str(venv_dir)], check=True)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_dir / "bin" / "python"),
            "--exclude-newer",
            source["exclude_newer"],
            f"benchbox[tpcdi]=={source['version']}",
        ],
        check=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the public API symbol list against an installed wheel.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--python", required=True)
    check.add_argument("--symbols", type=Path, default=DEFAULT_SYMBOLS)
    check.add_argument("--strict", action="store_true")
    check.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    check.set_defaults(func=cmd_check)

    record = sub.add_parser("record")
    record.add_argument("--python", required=True)
    record.add_argument("--symbols", type=Path, default=DEFAULT_SYMBOLS)
    record.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    record.set_defaults(func=cmd_record)

    venv = sub.add_parser("venv")
    venv.add_argument("--dir", required=True)
    venv.add_argument("--symbols", type=Path, default=DEFAULT_SYMBOLS)
    venv.set_defaults(func=cmd_venv)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InterpreterNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
