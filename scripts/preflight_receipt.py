#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

SCHEMA = 1
RECEIPT_SCHEMA = 2
TAIL_LINES = 40
COMMAND_DISPLAY_LIMIT = 110
RECEIPT_DIR_ENV = "BENCHBOX_PREFLIGHT_RECEIPT_DIR"
ENTRY_KEYS = frozenset({"step", "test", "reason", "tracker"})
ACCEPTED_OUTCOMES = frozenset({"passed", "unverified", "known-failure"})
ALLOWLISTED_STEPS = frozenset({"linux-pytest"})
PYTEST_FAILED_EXIT = 1
SELECTION_KEYS = ("python_files", "test_files", "linux_files")
TREE_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SUMMARY_LINE = re.compile(r"^\W*(?P<body>.*?\d+ \w+.*?) in (?P<secs>[0-9.]+)s\b")
COUNT = re.compile(r"(\d+) (passed|failed|skipped|deselected|errors?|xfailed|xpassed|warnings?)")
FAILED_ID = re.compile(r"^(?:FAILED|ERROR) (?P<id>.+?)(?: - .*)?$")
UNVERIFIED = re.compile(r"^WARNING: (?P<why>Linux not verified.*)$")
BLOCK_BEGIN = "<!-- preflight-receipt: v1 begin tree={tree} -->"
BLOCK_END = "<!-- preflight-receipt: v1 end -->"
BLOCK_PATTERN = re.compile(
    r"<!-- preflight-receipt: v1 begin tree=[0-9a-f]+ -->.*?<!-- preflight-receipt: v1 end -->\n?", re.DOTALL
)


class ReceiptError(Exception):
    pass


def _git(*args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    proc = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace", check=False, env=env)
    if proc.returncode != 0:
        raise ReceiptError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def receipt_dir(override: str = "") -> Path:
    if override:
        return Path(override)
    configured = os.environ.get(RECEIPT_DIR_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "benchbox" / "preflight"


def load_allowlist(path: Path | None) -> dict[tuple[str, str], dict[str, str]]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"known-failure allowlist {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or set(data) != {"schema", "entries"}:
        raise ReceiptError(f"known-failure allowlist {path} must be an object with schema {SCHEMA} and entries")
    entries = data["entries"]
    if not isinstance(entries, list):
        raise ReceiptError(f"known-failure allowlist {path}: entries must be a list")
    table: dict[tuple[str, str], dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise ReceiptError(f"known-failure allowlist {path}: every entry needs exactly {sorted(ENTRY_KEYS)}")
        if not all(isinstance(entry[key], str) and entry[key].strip() for key in ENTRY_KEYS):
            raise ReceiptError(f"known-failure allowlist {path}: entry fields must be non-empty strings")
        if entry["step"] not in ALLOWLISTED_STEPS:
            raise ReceiptError(
                f"known-failure allowlist {path}: step {entry['step']!r} cannot be allowlisted; "
                f"only {sorted(ALLOWLISTED_STEPS)} can"
            )
        key = (entry["step"], entry["test"])
        if key in table:
            raise ReceiptError(f"known-failure allowlist {path}: duplicate entry for {key[0]} {key[1]}")
        table[key] = {name: entry[name] for name in sorted(ENTRY_KEYS)}
    return table


def allowlist_digest(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_counts(lines: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in reversed(lines):
        match = SUMMARY_LINE.match(line.strip())
        if match is None:
            continue
        pairs = COUNT.findall(match.group("body"))
        if not pairs:
            continue
        for number, label in pairs:
            key = {"error": "errors", "warning": "warnings"}.get(label, label)
            counts[key] = counts.get(key, 0) + int(number)
        return counts
    return counts


def failed_ids(lines: Sequence[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        match = FAILED_ID.match(line.strip())
        if match is not None and match.group("id") not in found:
            found.append(match.group("id"))
    return found


def classify(
    name: str,
    exit_code: int,
    ok_codes: Sequence[int],
    lines: Sequence[str],
    counts: dict[str, int],
    allowlist: dict[tuple[str, str], dict[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    if exit_code in ok_codes:
        for line in lines:
            if UNVERIFIED.match(line.strip()):
                return "unverified", []
        return "passed", []
    ids = failed_ids(lines)
    if exit_code != PYTEST_FAILED_EXIT or not ids or counts.get("failed", 0) + counts.get("errors", 0) != len(ids):
        return "failed", []
    matched = [allowlist.get((name, test)) for test in ids]
    if any(entry is None for entry in matched):
        return "failed", []
    return "known-failure", [entry for entry in matched if entry is not None]


def run_step(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("preflight-receipt: step needs a command after --", file=sys.stderr)
        return 2
    ok_codes = [int(code) for code in args.ok_codes.split(",") if code]
    started = time.monotonic()
    tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
    summary: collections.deque[str] = collections.deque(maxlen=200)
    seen: list[str] = []
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace"
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        sys.stdout.write(raw)
        sys.stdout.flush()
        line = raw.rstrip("\n")
        tail.append(line)
        summary.append(line)
        if FAILED_ID.match(line.strip()) or UNVERIFIED.match(line.strip()):
            seen.append(line)
    code = proc.wait()
    lines = list(summary)
    counts = parse_counts(lines)
    outcome, known = classify(
        args.name, code, ok_codes, seen + lines, counts, load_allowlist(Path(args.allowlist)) if args.allowlist else {}
    )
    record = {
        "kind": "step",
        "name": args.name,
        "command": " ".join([os.path.basename(command[0]), *command[1:]]),
        "exit_code": code,
        "outcome": outcome,
        "counts": counts,
        "duration_s": round(time.monotonic() - started, 1),
        "output_tail": list(tail),
        "known_failures": known,
    }
    with open(args.stage, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    if outcome == "known-failure":
        print(
            f"preflight-receipt: {args.name} failed only on allowlisted environment-only failures; "
            "they are listed in the receipt and the PR body",
            file=sys.stderr,
        )
        return 0
    return code


def _tree_state() -> dict[str, object]:
    return {
        "tree": _git("rev-parse", "HEAD^{tree}"),
        "head": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=normal")),
        "tracked_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
    }


def run_begin(args: argparse.Namespace) -> int:
    try:
        state = _tree_state()
        base_sha = _git("rev-parse", "--verify", f"{args.base_ref}^{{commit}}")
        merge_base = _git("merge-base", base_sha, "HEAD")
    except ReceiptError as exc:
        state = {"error": str(exc)}
        base_sha = merge_base = ""
    record = {
        "kind": "begin",
        "state": state,
        "base_ref": args.base_ref,
        "base_sha": base_sha,
        "merge_base": merge_base,
        "started_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": sys.platform,
    }
    Path(args.stage).write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def run_note(args: argparse.Namespace) -> int:
    key, _, value = args.count.partition("=")
    if key not in SELECTION_KEYS or not value.isdigit():
        print(f"preflight-receipt: note must be one of {SELECTION_KEYS} set to a count", file=sys.stderr)
        return 2
    with open(args.stage, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "note", "key": key, "count": int(value)}, sort_keys=True) + "\n")
    return 0


def _read_stage(path: Path) -> tuple[dict[str, object], list[dict[str, object]], dict[str, int]]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    begins = [record for record in records if record.get("kind") == "begin"]
    if len(begins) != 1:
        raise ReceiptError("the preflight run did not record its starting tree")
    selection = dict.fromkeys(SELECTION_KEYS, 0)
    for record in records:
        if record.get("kind") == "note":
            selection[str(record["key"])] = int(record["count"])
    return begins[0], [record for record in records if record.get("kind") == "step"], selection


def overall(steps: Sequence[dict[str, object]]) -> str:
    return "passed" if steps and all(step.get("outcome") in ACCEPTED_OUTCOMES for step in steps) else "failed"


def write_receipt(args: argparse.Namespace) -> int:
    try:
        begin, steps, selection = _read_stage(Path(args.stage))
        start = begin["state"]
        assert isinstance(start, dict)
        if "error" in start:
            raise ReceiptError(f"no tree to bind the receipt to: {start['error']}")
        if start["dirty"]:
            raise ReceiptError("the working tree had uncommitted or untracked files when preflight started")
        end = _tree_state()
        if end["tree"] != start["tree"] or end["tracked_dirty"]:
            raise ReceiptError("the tree changed while preflight ran")
        allowlist = Path(args.allowlist) if args.allowlist else None
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "tree": start["tree"],
            "head": start["head"],
            "base_ref": begin["base_ref"],
            "base_sha": begin["base_sha"],
            "merge_base": begin["merge_base"],
            "selection": selection,
            "platform": begin["platform"],
            "started_at": begin["started_at"],
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "exit_status": args.exit_status,
            "status": overall(steps) if args.exit_status == 0 else "failed",
            "allowlist_sha256": allowlist_digest(allowlist),
            "steps": steps,
            "known_failures": [entry for step in steps for entry in step.get("known_failures", [])],
        }
        directory = receipt_dir(args.receipt_dir)
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
        target = directory / f"{start['tree']}.json"
        os.replace(tmp, target)
    except (ReceiptError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(
            f"preflight-receipt: receipt not written: {exc}; make pr-open will refuse until a clean run writes one",
            file=sys.stderr,
        )
        return 0
    print(f"preflight-receipt: {receipt['status']} receipt for tree {receipt['tree']} -> {target}", file=sys.stderr)
    return 0


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split()).replace("`", "'")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _step_result(step: dict[str, object]) -> str:
    counts = step.get("counts")
    shown = (
        ", ".join(f"{number} {label}" for label, number in sorted(counts.items())) if isinstance(counts, dict) else ""
    )
    outcome = str(step.get("outcome"))
    label = {"known-failure": "known environment-only failure", "unverified": "not verified"}.get(outcome, outcome)
    return f"{label}{f' ({shown})' if shown else ''}"


def render(receipt: dict[str, object]) -> str:
    steps = receipt["steps"]
    assert isinstance(steps, list)
    lines = [
        BLOCK_BEGIN.format(tree=receipt["tree"]),
        "### Preflight receipt",
        "",
        f"Tree `{receipt['tree']}`, base `{receipt['base_ref']}` at `{str(receipt['base_sha'])[:12]}`, "
        f"recorded {receipt['recorded_at']} on {receipt['platform']}. Author-produced evidence; CI is the proof.",
        "",
    ]
    for step in steps:
        lines.append(
            f"- `{step['name']}` exit {step['exit_code']}: {_step_result(step)}; "
            f"`{_clip(str(step['command']), COMMAND_DISPLAY_LIMIT)}`"
        )
    known = receipt.get("known_failures") or []
    if known:
        lines += ["", "Known environment-only failures (accepted from `.github/preflight-known-failures.json`):"]
        for entry in known:
            lines.append(
                f"- `{entry['step']}` `{entry['test']}`: {_clip(entry['reason'], 160)} (tracker `{entry['tracker']}`)"
            )
    unverified = [step for step in steps if step["outcome"] == "unverified"]
    for step in unverified:
        warning = next((line for line in step["output_tail"] if UNVERIFIED.match(line.strip())), "")
        lines += ["", f"`{step['name']}` was not verified: {_clip(warning.removeprefix('WARNING: '), 200)}"]
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def _expected_guards(receipt: dict[str, object]) -> list[str]:
    module_path = Path(__file__).resolve().parent.parent / "_project" / "scripts" / "preflight_targets.py"
    spec = importlib.util.spec_from_file_location("preflight_targets", module_path)
    if spec is None or spec.loader is None or not module_path.is_file():
        raise ReceiptError(f"cannot derive the required guard steps: {module_path} is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    merge_base, head = str(receipt.get("merge_base") or ""), str(receipt.get("head") or "")
    if not merge_base or not head:
        raise ReceiptError("the preflight receipt records no merge base or head; rerun `make pr-preflight`")
    paths = _git("diff", "--name-only", "--no-renames", merge_base, head).splitlines()
    return module.guards(paths)


def verify_receipt(directory: Path, tree: str, allowlist: dict[tuple[str, str], dict[str, str]]) -> dict[str, object]:
    if not TREE_PATTERN.fullmatch(tree):
        raise ReceiptError(f"{tree!r} is not a tree hash")
    path = directory / f"{tree}.json"
    if not path.is_file():
        raise ReceiptError(
            f"no preflight receipt for tree {tree}; run `make pr-preflight` on this exact committed tree "
            f"(looked in {directory}); a stale receipt from another tree does not count"
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"preflight receipt {path} is unreadable: {exc}") from exc
    if isinstance(receipt, dict) and receipt.get("schema") != RECEIPT_SCHEMA:
        raise ReceiptError(
            f"preflight receipt {path} predates guard-step recording (schema {receipt.get('schema')}, "
            f"need {RECEIPT_SCHEMA}); rerun `make pr-preflight`"
        )
    if not isinstance(receipt, dict) or receipt.get("tree") != tree:
        raise ReceiptError(f"preflight receipt {path} does not describe tree {tree}; rerun `make pr-preflight`")
    steps = receipt.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(step, dict) for step in steps):
        raise ReceiptError(f"preflight receipt {path} records no steps; rerun `make pr-preflight`")
    bad = [
        str(step.get("name"))
        for step in steps
        if step.get("outcome") not in ACCEPTED_OUTCOMES
        or (step.get("outcome") == "known-failure" and not _still_allowed(step, allowlist))
    ]
    if bad or receipt.get("exit_status") != 0 or receipt.get("status") != "passed":
        named = ", ".join(bad) if bad else "preflight exit status " + str(receipt.get("exit_status"))
        raise ReceiptError(
            f"the preflight receipt for tree {tree} shows a failed step ({named}); fix it and rerun `make pr-preflight`"
        )
    selection = receipt.get("selection")
    if not isinstance(selection, dict) or not all(isinstance(selection.get(key), int) for key in SELECTION_KEYS):
        raise ReceiptError(f"preflight receipt {path} records no step selection; rerun `make pr-preflight`")
    required: list[str] = []
    if selection["python_files"] > 0:
        required += ["ruff-check", "ruff-format"]
    if selection["test_files"] > 0:
        required.append("pytest")
    if selection["linux_files"] > 0:
        required.append("linux-pytest")
    required += _expected_guards(receipt)
    recorded = {step.get("name") for step in steps}
    missing = [name for name in required if name not in recorded]
    if missing:
        raise ReceiptError(
            f"the preflight receipt for tree {tree} lacks required step(s) {', '.join(missing)}; "
            "rerun `make pr-preflight`"
        )
    return receipt


def check_coverage(receipt: dict[str, object], base_ref: str, head: str = "HEAD") -> None:
    recorded = str(receipt.get("merge_base") or "")
    current = _git("merge-base", base_ref, head)
    covered = (
        bool(recorded)
        and subprocess.run(
            ["git", "merge-base", "--is-ancestor", recorded, current], capture_output=True, check=False
        ).returncode
        == 0
    )
    if not covered:
        raise ReceiptError(
            f"the preflight receipt was selected against merge base {recorded or 'unknown'}, which does not cover "
            f"the changes since {current} (the merge base with {base_ref}); rerun `make pr-preflight` without BASE "
            "or with a base that is an ancestor of the branch point"
        )


def _still_allowed(step: dict[str, object], allowlist: dict[tuple[str, str], dict[str, str]]) -> bool:
    known = step.get("known_failures")
    if not isinstance(known, list) or not known:
        return False
    return all(
        isinstance(entry, dict) and allowlist.get((str(entry.get("step")), str(entry.get("test")))) == entry
        for entry in known
    )


def run_verify(args: argparse.Namespace) -> int:
    try:
        tree = args.tree or _git("rev-parse", f"{args.head}^{{tree}}")
        receipt = verify_receipt(
            receipt_dir(args.receipt_dir), tree, load_allowlist(Path(args.allowlist) if args.allowlist else None)
        )
        if args.base_ref:
            check_coverage(receipt, args.base_ref, args.head)
        if args.summary_out:
            Path(args.summary_out).write_text(render(receipt), encoding="utf-8")
    except ReceiptError as exc:
        print(f"preflight-receipt: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record and verify the pr-preflight receipt for a tree.")
    sub = parser.add_subparsers(dest="action", required=True)
    begin = sub.add_parser("begin")
    begin.add_argument("--stage", required=True)
    begin.add_argument("--base-ref", required=True)
    step = sub.add_parser("step")
    step.add_argument("--stage", required=True)
    step.add_argument("--name", required=True)
    step.add_argument("--ok-codes", default="0")
    step.add_argument("--allowlist", default="")
    step.add_argument("command", nargs=argparse.REMAINDER)
    note = sub.add_parser("note")
    note.add_argument("--stage", required=True)
    note.add_argument("count")
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--stage", required=True)
    finalize.add_argument("--exit-status", type=int, required=True)
    finalize.add_argument("--allowlist", default="")
    finalize.add_argument("--receipt-dir", default="")
    verify = sub.add_parser("verify")
    verify.add_argument("--tree", default="")
    verify.add_argument("--head", default="HEAD")
    verify.add_argument("--allowlist", default="")
    verify.add_argument("--receipt-dir", default="")
    verify.add_argument("--summary-out", default="")
    verify.add_argument("--base-ref", default="")
    args = parser.parse_args(argv)
    if args.action == "begin":
        return run_begin(args)
    if args.action == "step":
        return run_step(args)
    if args.action == "note":
        return run_note(args)
    if args.action == "finalize":
        return write_receipt(args)
    return run_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
