from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLI_DESCRIPTION = "Collect and deterministically partition release-canary pytest node IDs."

MARKER_EXPRESSION = "(slow or resource_heavy) and not (stress or live_integration)"
MEDIUM_MARKER_EXPRESSION = "medium and not (slow or stress or resource_heavy or live_integration)"
DEFAULT_SHARD_COUNT = 6
MEDIUM_SHARD_COUNT = 4


def _canonical_node_ids(node_ids: Iterable[str]) -> list[str]:
    values = list(node_ids)
    if not values:
        raise ValueError("node-id input is empty")
    if any(not isinstance(node_id, str) or not node_id.strip() for node_id in values):
        raise ValueError("node-id input contains an empty or non-string value")
    if len(set(values)) != len(values):
        raise ValueError("node-id input contains duplicates")
    return sorted(values)


def parse_collection_output(output: str) -> list[str]:
    node_ids = []
    for line in output.splitlines():
        candidate = line.strip()
        if candidate.startswith("tests/") and "::" in candidate:
            node_ids.append(candidate)
    return _canonical_node_ids(node_ids)


def read_node_ids(path: Path) -> list[str]:
    return _canonical_node_ids(path.read_text(encoding="utf-8").splitlines())


@dataclass(frozen=True)
class Durations:
    tests: Mapping[str, float]
    default_seconds: float

    def weight(self, node_id: str) -> float:
        return self.tests.get(node_id, self.default_seconds)


def _non_negative(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return float(value)


def load_durations(path: Path) -> Durations:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tests = payload.get("tests") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(tests, dict):
        raise ValueError("duration file must be a schema 1 object with a tests map")
    default_seconds = _non_negative(payload.get("default_seconds"), "default_seconds")
    return Durations(
        {
            node_id: _non_negative(
                record.get("p95_seconds") if isinstance(record, dict) else None, f"duration for {node_id!r}"
            )
            for node_id, record in tests.items()
        },
        default_seconds,
    )


def shard_group(node_id: str) -> str:
    return node_id.split("::", 1)[0]


def _balanced_assignments(ordered: list[str], shard_count: int, durations: Durations) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for node_id in ordered:
        groups.setdefault(shard_group(node_id), []).append(node_id)
    weight = {group: sum(durations.weight(node_id) for node_id in members) for group, members in groups.items()}
    loads = [0.0] * shard_count
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for group in sorted(groups, key=lambda candidate: (-weight[candidate], candidate)):
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        shards[target].extend(groups[group])
        loads[target] += weight[group]
    return [sorted(shard) for shard in shards]


def partition_node_ids(
    node_ids: Sequence[str], shard_index: int, shard_count: int, durations: Durations | None = None
) -> list[str]:
    if isinstance(shard_count, bool) or not isinstance(shard_count, int) or shard_count < 1:
        raise ValueError("shard_count must be a positive integer")
    if isinstance(shard_index, bool) or not isinstance(shard_index, int) or not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must be an integer in [0, shard_count)")
    ordered = _canonical_node_ids(node_ids)
    if durations is None:
        return ordered[shard_index::shard_count]
    return _balanced_assignments(ordered, shard_count, durations)[shard_index]


def _node_ids_sha256(node_ids: Sequence[str]) -> str:
    payload = "\n".join(node_ids) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_binding(durations_path: Path | None) -> tuple[Durations | None, dict[str, str | None]]:
    if durations_path is None:
        return None, {"durations_sha256": None}
    digest = hashlib.sha256(durations_path.read_bytes()).hexdigest()
    return load_durations(durations_path), {"durations_sha256": digest}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_node_ids(path: Path, node_ids: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(node_ids) + "\n" if node_ids else ""
    path.write_text(payload, encoding="utf-8")


def collect_node_ids(
    input_path: Path,
    nodeids_output: Path,
    summary_output: Path,
    *,
    expected_count: int,
    shard_count: int,
    checked_ref: str = "",
    checked_sha: str = "",
    github_output: Path | None = None,
    workflow: str = "release-canary.yml",
    job: str = "collect-credential-free-non-fast",
    marker_expression: str = MARKER_EXPRESSION,
    durations_path: Path | None = None,
) -> int:
    if expected_count < 1:
        raise ValueError("expected_count must be a positive integer")
    if isinstance(shard_count, bool) or not isinstance(shard_count, int) or shard_count < 1:
        raise ValueError("shard_count must be a positive integer")

    node_ids = parse_collection_output(input_path.read_text(encoding="utf-8"))
    if len(node_ids) != expected_count:
        raise ValueError(f"collected node-id count {len(node_ids)} does not match expected count {expected_count}")

    durations, binding = _load_binding(durations_path)
    shard_counts = [len(partition_node_ids(node_ids, index, shard_count, durations)) for index in range(shard_count)]
    if sum(shard_counts) != len(node_ids):
        raise ValueError("shard count conservation check failed")

    _write_node_ids(nodeids_output, node_ids)
    _write_json(
        summary_output,
        {
            "workflow": workflow,
            "job": job,
            "checked_ref": checked_ref,
            "commit_sha": checked_sha,
            "marker_expression": marker_expression,
            "total_count": len(node_ids),
            "shard_count": shard_count,
            "shard_counts": shard_counts,
            "node_ids_sha256": _node_ids_sha256(node_ids),
            **{key: value for key, value in binding.items() if value is not None},
        },
    )
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"collected_count={len(node_ids)}\n")
    return len(node_ids)


def write_shard(
    input_path: Path,
    nodeids_output: Path,
    summary_output: Path,
    *,
    shard_index: int,
    shard_count: int,
    workflow: str = "release-canary.yml",
    job: str = "credential-free-non-fast",
    marker_expression: str = MARKER_EXPRESSION,
    collection_summary: Path | None = None,
    checked_sha: str | None = None,
    durations_path: Path | None = None,
) -> int:
    node_ids = read_node_ids(input_path)
    durations, binding = _load_binding(durations_path)
    if collection_summary is not None or checked_sha is not None:
        if collection_summary is None or not checked_sha:
            raise ValueError("collection summary and checked SHA must be supplied together")
        collection = json.loads(collection_summary.read_text(encoding="utf-8"))
        expected = {
            "workflow": workflow,
            "commit_sha": checked_sha,
            "marker_expression": marker_expression,
            "total_count": len(node_ids),
            "shard_count": shard_count,
            "node_ids_sha256": _node_ids_sha256(node_ids),
            "shard_counts": [
                len(partition_node_ids(node_ids, index, shard_count, durations)) for index in range(shard_count)
            ],
            **binding,
        }
        if not isinstance(collection, dict) or any(collection.get(key) != value for key, value in expected.items()):
            raise ValueError("collection evidence does not match the checkout, selector or node IDs")
    shard_node_ids = partition_node_ids(node_ids, shard_index, shard_count, durations)
    _write_node_ids(nodeids_output, shard_node_ids)
    _write_json(
        summary_output,
        {
            "workflow": workflow,
            "job": job,
            "marker_expression": marker_expression,
            **({"commit_sha": checked_sha} if checked_sha else {}),
            "shard_index": shard_index,
            "shard_count": shard_count,
            "assigned_count": len(shard_node_ids),
            "total_count": len(node_ids),
            "source_node_ids_sha256": _node_ids_sha256(node_ids),
            "node_ids_sha256": _node_ids_sha256(shard_node_ids),
            **{key: value for key, value in binding.items() if value is not None},
        },
    )
    return len(shard_node_ids)


def _validate_node_outcomes(outcomes: object, assigned: list[str]) -> None:
    if not isinstance(outcomes, list) or any(not isinstance(item, dict) for item in outcomes):
        raise ValueError("medium outcome evidence is missing or malformed")
    if [item.get("node_id") for item in outcomes] != assigned:
        raise ValueError("medium outcome node IDs do not match the exact assignment")
    for item in outcomes:
        reports = item.get("reports")
        if not isinstance(reports, list) or not reports or any(not isinstance(report, dict) for report in reports):
            raise ValueError("medium outcome reports are missing or malformed")
        expected_phases = (
            ["setup", "call", "teardown"] if reports[0].get("outcome") == "passed" else ["setup", "teardown"]
        )
        if [report.get("phase") for report in reports] != expected_phases:
            raise ValueError("medium outcome phases are missing, duplicated or inconsistent")
        for report in reports:
            outcome = report.get("outcome")
            if outcome not in ("passed", "skipped"):
                raise ValueError("medium outcome contradicts successful pytest exit status")
            reason = report.get("skip_reason")
            xfail = report.get("xfail_reason")
            if "skip_reason" not in report or "xfail_reason" not in report:
                raise ValueError("medium outcome reason fields are missing")
            if outcome == "skipped":
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("medium skipped outcome has no observed reason")
            elif reason is not None:
                raise ValueError("medium passed outcome cannot have a skip reason")
            if xfail is not None and not isinstance(xfail, str):
                raise ValueError("medium outcome xfail reason is malformed")


def _validate_required_outcomes(outcomes: object, required: list[str]) -> None:
    by_node = {item["node_id"]: item["reports"] for item in outcomes if isinstance(item, dict)}
    for node_id in required:
        reports = by_node.get(node_id)
        if not reports:
            raise ValueError(f"required case produced no report: {node_id}")
        if [report.get("phase") for report in reports] != ["setup", "call", "teardown"]:
            raise ValueError(f"required case did not run setup, call and teardown: {node_id}")
        if any(report.get("outcome") != "passed" for report in reports):
            raise ValueError(f"required case did not pass: {node_id}")
        if any(report.get("skip_reason") is not None or report.get("xfail_reason") is not None for report in reports):
            raise ValueError(f"required case was skipped or expected to fail: {node_id}")


def verify_required_cases(evidence_path: Path, checked_sha: str, nodeids_path: Path) -> None:
    required = read_node_ids(nodeids_path)
    if not required:
        raise ValueError("required case list is empty")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict) or (
        evidence.get("commit_sha") != checked_sha
        or evidence.get("complete") is not True
        or evidence.get("pytest_exit_status") != 0
        or evidence.get("assigned_node_ids") != required
        or evidence.get("executed_node_ids") != required
        or not evidence.get("collected_node_ids")
        or any(ids != required for ids in evidence["collected_node_ids"])
    ):
        raise ValueError("required cases were not all collected and executed successfully for the checked SHA")
    _validate_node_outcomes(evidence.get("node_outcomes"), required)
    _validate_required_outcomes(evidence["node_outcomes"], required)


def verify_medium_shards(
    artifact_root: Path,
    checked_sha: str,
    workflow: str = "ci.yml",
    artifact_prefix: str = "t2-medium",
    durations_path: Path | None = None,
) -> None:
    durations, binding = _load_binding(durations_path)
    collection_root = artifact_root / f"{artifact_prefix}-nodeids-{checked_sha}"
    node_ids = read_node_ids(collection_root / "medium-nodeids.txt")
    collection = json.loads((collection_root / "medium-collection.json").read_text(encoding="utf-8"))
    expected = {
        "workflow": workflow,
        "job": "medium-collect",
        "commit_sha": checked_sha,
        "marker_expression": MEDIUM_MARKER_EXPRESSION,
        "shard_count": MEDIUM_SHARD_COUNT,
        "total_count": len(node_ids),
        "node_ids_sha256": _node_ids_sha256(node_ids),
        "shard_counts": [
            len(partition_node_ids(node_ids, index, MEDIUM_SHARD_COUNT, durations))
            for index in range(MEDIUM_SHARD_COUNT)
        ],
        **binding,
    }
    if not isinstance(collection, dict) or any(collection.get(key) != value for key, value in expected.items()):
        raise ValueError("medium collection does not match the checked SHA or selected node IDs")
    executed = []
    for index in range(MEDIUM_SHARD_COUNT):
        assigned = partition_node_ids(node_ids, index, MEDIUM_SHARD_COUNT, durations)
        if not assigned:
            raise ValueError("medium shard assignment is empty")
        shard_root = artifact_root / f"{artifact_prefix}-shard-{index}-{checked_sha}"
        evidence = json.loads((shard_root / f"shard-{index}-execution.json").read_text(encoding="utf-8"))
        if not isinstance(evidence, dict) or (
            evidence.get("commit_sha") != checked_sha
            or evidence.get("complete") is not True
            or evidence.get("pytest_exit_status") != 0
            or evidence.get("assigned_node_ids") != assigned
            or evidence.get("executed_node_ids") != assigned
            or not evidence.get("collected_node_ids")
            or any(ids != assigned for ids in evidence["collected_node_ids"])
        ):
            raise ValueError(f"medium shard {index} did not execute its exact assignment successfully")
        _validate_node_outcomes(evidence.get("node_outcomes"), assigned)
        executed.extend(evidence["executed_node_ids"])
    if sorted(executed) != node_ids:
        raise ValueError("medium shard execution does not conserve the collected node IDs")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="create the canonical node-id artifact")
    collect_parser.add_argument("--input", type=Path, required=True)
    collect_parser.add_argument("--nodeids-output", type=Path, required=True)
    collect_parser.add_argument("--summary-output", type=Path, required=True)
    collect_parser.add_argument("--expected-count", type=int, required=True)
    collect_parser.add_argument("--shard-count", type=int, default=DEFAULT_SHARD_COUNT)
    collect_parser.add_argument("--checked-ref", default="")
    collect_parser.add_argument("--checked-sha", default="")
    collect_parser.add_argument("--github-output", type=Path)
    collect_parser.add_argument("--workflow", default="release-canary.yml")
    collect_parser.add_argument("--job", default="collect-credential-free-non-fast")
    collect_parser.add_argument("--marker-expression", default=MARKER_EXPRESSION)
    collect_parser.add_argument("--durations", type=Path)

    shard_parser = subparsers.add_parser("shard", help="write one deterministic node-id shard")
    shard_parser.add_argument("--input", type=Path, required=True)
    shard_parser.add_argument("--nodeids-output", type=Path, required=True)
    shard_parser.add_argument("--summary-output", type=Path, required=True)
    shard_parser.add_argument("--shard-index", type=int, required=True)
    shard_parser.add_argument("--shard-count", type=int, required=True)
    shard_parser.add_argument("--workflow", default="release-canary.yml")
    shard_parser.add_argument("--job", default="credential-free-non-fast")
    shard_parser.add_argument("--marker-expression", default=MARKER_EXPRESSION)
    shard_parser.add_argument("--collection-summary", type=Path)
    shard_parser.add_argument("--checked-sha")
    shard_parser.add_argument("--durations", type=Path)
    verify_parser = subparsers.add_parser("verify-medium", help="verify exact medium shard coverage")
    verify_parser.add_argument("--artifacts", type=Path, required=True)
    verify_parser.add_argument("--checked-sha", required=True)
    verify_parser.add_argument("--workflow", default="ci.yml")
    verify_parser.add_argument("--artifact-prefix", default="t2-medium")
    verify_parser.add_argument("--durations", type=Path)
    required_parser = subparsers.add_parser(
        "verify-required", help="verify a run executed exactly the required cases and passed all of them"
    )
    required_parser.add_argument("--evidence", type=Path, required=True)
    required_parser.add_argument("--nodeids", type=Path, required=True)
    required_parser.add_argument("--checked-sha", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "collect":
            collect_node_ids(
                args.input,
                args.nodeids_output,
                args.summary_output,
                expected_count=args.expected_count,
                shard_count=args.shard_count,
                checked_ref=args.checked_ref,
                checked_sha=args.checked_sha,
                github_output=args.github_output,
                workflow=args.workflow,
                job=args.job,
                marker_expression=args.marker_expression,
                durations_path=args.durations,
            )
        elif args.command == "shard":
            write_shard(
                args.input,
                args.nodeids_output,
                args.summary_output,
                shard_index=args.shard_index,
                shard_count=args.shard_count,
                workflow=args.workflow,
                job=args.job,
                marker_expression=args.marker_expression,
                collection_summary=args.collection_summary,
                checked_sha=args.checked_sha,
                durations_path=args.durations,
            )
        elif args.command == "verify-required":
            verify_required_cases(args.evidence, args.checked_sha, args.nodeids)
        else:
            verify_medium_shards(args.artifacts, args.checked_sha, args.workflow, args.artifact_prefix, args.durations)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release-canary sharding error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
