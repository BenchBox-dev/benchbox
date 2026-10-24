from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, TypedDict, cast

from _pytest.mark.expression import Expression


class FastLanePolicy(TypedDict, total=False):
    enabled: bool
    forbidden_marker_expressions: list[str]
    forbidden_path_substrings: list[str]


def _load_fast_lane_policy(path: Path) -> FastLanePolicy:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fast lane policy must be a JSON object")
    return cast(FastLanePolicy, data)


_MANIFEST_PLUGIN = "tests.utilities.marker_manifest"


def _collect_manifest(repo_root: Path, manifest: Path) -> None:
    env = dict(os.environ)
    env["BENCHBOX_SKIP_TEST_LOCK"] = "1"
    plugin_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(part for part in (plugin_root, env.get("PYTHONPATH", "")) if part)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-n",
        "0",
        "--collect-only",
        "-q",
        "-p",
        "no:warnings",
        "--assert=plain",
        "-p",
        _MANIFEST_PLUGIN,
        f"--marker-manifest={manifest}",
    ]
    try:
        result = subprocess.run(cmd, cwd=repo_root, env=env, capture_output=True, text=True, timeout=300, check=False)
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part.decode() if isinstance(part, bytes) else part for part in (exc.stdout, exc.stderr) if part
        )
        raise _collect_environment_error("timed out after 300 s", output) from exc
    if result.returncode not in (0, 5) or not manifest.is_file():
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise _collect_environment_error(f"exit {result.returncode}", output)


class FastLaneCollectError(RuntimeError):
    pass


def _collect_environment_error(reason: str, output: str) -> FastLaneCollectError:
    tail = "\n".join(line for line in output.strip().splitlines()[-5:])
    return FastLaneCollectError(
        f"could not run pytest --collect-only ({reason}) using interpreter {sys.executable}. "
        "This usually means pytest or benchbox is not importable in that "
        "environment - run the check from the project environment, e.g. "
        "`uv run -- python _project/scripts/fast_lane_ceiling_check.py --strict`. "
        f"Last output lines:\n{tail}"
    )


def _read_manifest(path: Path) -> list[tuple[str, frozenset[str]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["version"] != 1:
            raise ValueError(f"unsupported manifest version {data['version']!r}")
        items = [(str(item["id"]), frozenset(item["markers"])) for item in data["items"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FastLaneCollectError(f"marker manifest {path} is missing or unreadable: {exc}") from exc
    if not items:
        raise FastLaneCollectError(f"marker manifest {path} records no collected tests")
    return items


def _compile_marker_expression(expr: str) -> Expression:
    try:
        return Expression.compile(expr)
    except Exception as exc:
        raise FastLaneCollectError(f"invalid marker expression {expr!r}: {exc}") from exc


def _marker_matcher(markers: frozenset[str]) -> Callable[..., bool]:
    def _match(name: str, /, **kwargs: object) -> bool:
        if kwargs:
            raise FastLaneCollectError(f"marker expression arguments are not supported: {name}({kwargs})")
        return name in markers

    return _match


def _check_fast_lane_policy(repo_root: Path, policy: FastLanePolicy, manifest: Path | None = None) -> list[str]:
    if not policy or not policy.get("enabled", True):
        return []

    forbidden_marker_expressions = policy.get("forbidden_marker_expressions", [])
    forbidden_path_substrings = policy.get("forbidden_path_substrings", [])
    if not forbidden_marker_expressions and not forbidden_path_substrings:
        return []

    if manifest is not None:
        items = _read_manifest(manifest)
    else:
        with tempfile.TemporaryDirectory(prefix="benchbox-marker-manifest-") as tmp:
            target = Path(tmp) / "manifest.json"
            _collect_manifest(repo_root, target)
            items = _read_manifest(target)

    violations: list[str] = []
    fast_items = [(nodeid, markers) for nodeid, markers in items if _marker_matcher(markers)("fast")]
    print(f"Fast lane collected {len(fast_items)} test(s) from {len(items)} in the marker manifest")

    if forbidden_path_substrings:
        offending_ids = [
            nodeid
            for nodeid, _ in fast_items
            if "::" in nodeid and any(substring in nodeid for substring in forbidden_path_substrings)
        ]
        if offending_ids:
            violations.append("fast lane includes Java/Spark-adjacent test paths: " + ", ".join(offending_ids[:10]))

    for expr in forbidden_marker_expressions:
        compiled = _compile_marker_expression(f"fast and {expr}")
        count = sum(1 for _, markers in items if compiled.evaluate(_marker_matcher(markers)))
        print(f"Fast lane intersection '{expr}' count: {count}")
        if count != 0:
            violations.append(f"fast lane unexpectedly includes {count} test(s) matching 'fast and {expr}'")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the fast-lane forbidden-marker and forbidden-path guards.")
    parser.add_argument(
        "--fast-lane-policy",
        default="_project/config/fast_test_lane_policy.json",
        help="Fast-lane guardrail JSON file path",
    )
    manifest_source = parser.add_mutually_exclusive_group()
    manifest_source.add_argument("--manifest", help="Marker manifest written by `make lint-markers`")
    manifest_source.add_argument(
        "--manifest-from-env",
        action="store_true",
        help="Read the marker manifest path from $MARKER_MANIFEST; fails when it is unset",
    )
    parser.add_argument("--strict", action="store_true", help="Fail on any fast-lane policy violation")
    args = parser.parse_args()
    manifest_path = args.manifest
    if args.manifest_from_env:
        manifest_path = os.environ.get("MARKER_MANIFEST")
        if not manifest_path:
            parser.error("--manifest-from-env requires MARKER_MANIFEST to be set")

    repo_root = Path(__file__).resolve().parents[2]

    fast_lane_policy = _load_fast_lane_policy(repo_root / args.fast_lane_policy)
    fast_lane_violations: list[str]
    fast_lane_environment_error = False
    try:
        fast_lane_violations = _check_fast_lane_policy(
            repo_root, fast_lane_policy, Path(manifest_path) if manifest_path else None
        )
    except FastLaneCollectError as exc:
        fast_lane_violations = []
        print(f"Fast lane policy violations: {len(fast_lane_violations)}")
        for violation in fast_lane_violations:
            print(f"FAST_LANE_VIOLATION: {violation}")
        print(f"FAST_LANE_ENVIRONMENT_ERROR: {exc}", file=sys.stderr)
        fast_lane_environment_error = True
    else:
        print(f"Fast lane policy violations: {len(fast_lane_violations)}")
        for violation in fast_lane_violations:
            print(f"FAST_LANE_VIOLATION: {violation}")

    if args.strict and (fast_lane_violations or fast_lane_environment_error):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
