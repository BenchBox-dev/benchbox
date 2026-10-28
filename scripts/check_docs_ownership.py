from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OWNERS = {"site-owned", "core-only", "core-generated"}
LANDING_SOURCE = "docs/README.md"
TRANSITION_KEYS = {"readme_landing", "sql_contract", "publication_controls", "operations_inventory"}
GENERATOR_ROOTS = ("scripts", "_project")


def destination(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"invalid transition destination: {value!r}")
    normalized = value[:-1] if value.endswith("/") else value
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {".", ".."} for part in parts) or PurePosixPath(normalized).as_posix() != normalized:
        raise ValueError(f"unsafe transition destination: {value!r}")


def validate_transitions(root: Path, transitions: Any, files: dict[str, str], tracked: set[str]) -> None:
    if not isinstance(transitions, dict):
        raise ValueError("transitions must be an object keyed by transition name")
    missing = sorted(TRANSITION_KEYS - transitions.keys())
    unknown = sorted(transitions.keys() - TRANSITION_KEYS)
    if missing or unknown:
        raise ValueError(
            f"transitions must define exactly {sorted(TRANSITION_KEYS)!r}: missing={missing!r}, unknown={unknown!r}"
        )
    for name, record in transitions.items():
        if not isinstance(record, dict) or not {"sources", "core_destination", "site_destination", "action"} <= set(
            record
        ):
            raise ValueError(f"transition {name!r} must define sources, core_destination, site_destination and action")
        sources = record["sources"]
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"transition {name!r} must list at least one source")
        for source in sources:
            repo_path(source, ("docs",))
        destination(record["core_destination"])
        destination(record["site_destination"])
        if not isinstance(record["action"], str) or not record["action"]:
            raise ValueError(f"transition {name!r} must name its action")
        for source in sources:
            retained = files.get(source) == "core-only"
            if retained != (source in tracked):
                problem = "is not tracked" if retained else "is still tracked"
                raise ValueError(
                    f"transition {name!r} source {source!r} {problem} "
                    f"but ownership says it is {files.get(source, 'relocated')!r}"
                )
        core_destination = record["core_destination"]
        if core_destination is not None:
            if core_destination.endswith("/"):
                if not any(path.startswith(core_destination) for path in tracked):
                    raise ValueError(f"transition {name!r} destination {core_destination!r} holds no tracked files")
            else:
                require_source(root, core_destination, tracked)
    landing = transitions["readme_landing"]
    if landing["sources"] != [LANDING_SOURCE]:
        raise ValueError(f"readme_landing sources must be exactly {[LANDING_SOURCE]!r}")
    selector = landing.get("marker_selector")
    if not isinstance(selector, dict) or not {"source", "symbol", "version_group"} <= set(selector):
        raise ValueError("readme_landing marker_selector must define source, symbol and version_group")
    repo_path(selector["source"], ("scripts",))
    require_source(root, selector["source"], tracked)
    symbol = selector["symbol"]
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("readme_landing marker_selector must name a symbol")
    source_text = (root / selector["source"]).read_text(encoding="utf-8")
    if not re.search(rf"^{re.escape(symbol)}\b\s*=", source_text, flags=re.MULTILINE):
        raise ValueError(f"marker_selector symbol {symbol!r} is not defined in {selector['source']!r}")
    if landing.get("site_owner") not in OWNERS or not landing.get("freeze_comparison"):
        raise ValueError("readme_landing must define site_owner and freeze_comparison")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def repo_path(value: Any, roots: tuple[str, ...], directory: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid repository path: {value!r}")
    path = value[:-1] if directory and value.endswith("/") else value
    parts = PurePosixPath(path).parts
    if (
        not parts
        or parts[0] not in roots
        or any(part in {".", ".."} for part in parts)
        or PurePosixPath(path).as_posix() != path
        or len(parts) < 2
    ):
        raise ValueError(f"unsafe repository path: {value!r}")
    return value


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def require_source(root: Path, path: str, tracked: set[str]) -> None:
    source = root / path
    if path not in tracked or not source.is_file() or not source.resolve().is_relative_to(root):
        raise ValueError(f"missing, untracked or unsafe source: {path!r}")


def generated_outputs(generated: Any, tracked: set[str]) -> dict[str, str]:
    if not isinstance(generated, list):
        raise ValueError("generated must be an output list")
    outputs: dict[str, str] = {}
    for record in generated:
        if not isinstance(record, dict) or set(record) != {"path", "generator"}:
            raise ValueError(f"generated records must contain exactly path and generator: {record!r}")
        path = repo_path(record["path"], ("docs",), directory=True)
        generator = repo_path(record["generator"], GENERATOR_ROOTS)
        if path in outputs:
            raise ValueError(f"duplicate generated output: {path!r}")
        if generator in tracked:
            raise ValueError(f"generator {generator!r} for {path!r} must be absent from the core tree")
        outputs[path] = generator
    for output in outputs:
        leaked = sorted(
            path for path in tracked if path == output or (output.endswith("/") and path.startswith(output))
        )
        if leaked:
            raise ValueError(f"generated output {output!r} must be absent from the core tree: tracked {leaked[:3]!r}")
    for left in outputs:
        for right in outputs:
            if left != right and left.endswith("/") and right.startswith(left):
                raise ValueError(f"generated outputs overlap: {left!r} contains {right!r}")
    if not any(output.endswith("/") for output in outputs):
        raise ValueError("generated outputs must catalogue the relocated generated directory")
    return outputs


def validate(root: Path, manifest_path: Path) -> dict[str, int]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "files", "generated", "transitions"}:
        raise ValueError("manifest must contain exactly schema, files, generated and transitions")
    if type(manifest["schema"]) is not int or manifest["schema"] != 1:
        raise ValueError("unsupported ownership schema")
    files = manifest["files"]
    if not isinstance(files, dict):
        raise ValueError("files must be an ownership object")
    for path, owner in files.items():
        repo_path(path, ("docs",))
        if not isinstance(owner, str) or owner not in OWNERS:
            raise ValueError(f"unknown owner for {path!r}: {owner!r}")
    tracked = set(git(root, "ls-files", "-z").rstrip("\0").split("\0")) - {""}
    outputs = generated_outputs(manifest["generated"], tracked)
    validate_transitions(root, manifest["transitions"], files, tracked)
    for path, owner in files.items():
        if path in outputs and owner != "core-generated":
            raise ValueError(f"ownership mismatch for {path!r}: expected 'core-generated', got {owner!r}")
        if owner != "core-only" and path in tracked:
            raise ValueError(f"{owner} path {path!r} must be absent from the core tree")
        if owner == "core-only":
            require_source(root, path, tracked)
    tracked_docs = {path for path in tracked if path.startswith("docs/")}
    retained = {path for path, owner in files.items() if owner == "core-only"}
    if retained != tracked_docs:
        raise ValueError(
            f"tracked docs coverage mismatch: missing={sorted(tracked_docs - retained)!r}, "
            f"stale={sorted(retained - tracked_docs)!r}"
        )
    counts = Counter(files.values())
    return {owner: counts[owner] for owner in sorted(OWNERS)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check tracked documentation ownership and generated boundaries.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=Path("quality/docs-ownership.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    try:
        counts = validate(root, manifest_path)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({"counts": counts, "tracked": sum(counts.values())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
