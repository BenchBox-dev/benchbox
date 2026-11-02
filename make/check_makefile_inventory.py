#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

MANIFEST_PATH = Path("make/inventory.json")
BASELINE_PATH = Path("make/monolith-baseline.json")
MIGRATION_PROOF_PATH = Path("make/migration-proof.json")
SCHEMA_VERSION = 2
MIGRATION_PROOF_SCHEMA_VERSION = 1
RESERVED_ROOT_VARIABLE = "BENCHBOX_MAKEFILE_ROOT"
DEVELOPMENT_TREE_TARGETS_VARIABLE = "DEVELOPMENT_TREE_ONLY_TARGETS"
EXPECTED_INCLUDE_ORDER = [
    "make/platform-tests.mk",
    "make/documentation.mk",
    "make/worktrees.mk",
    "make/worktree-maintenance.mk",
    "make/batch.mk",
    "make/help.mk",
]
HISTORICAL_INCLUDE_ORDER = [
    "make/platform-tests.mk",
    "make/documentation.mk",
    "make/worktrees.mk",
    "make/worktree-pool.mk",
    "make/worktree-maintenance.mk",
    "make/help.mk",
]
INVENTORY_TARGET = "makefile-inventory-check"
ADDED_RELEASE_CURATED_TESTS = [
    "tests/unit/scripts/test_check_complexity.py",
]
HELP_RECIPE_LINE = '\t@echo "  make makefile-inventory-check Verify public Make contract inventory and ordering"'
INCLUDE_RE = re.compile(r"^include\s+(.+?)\s*$")
VARIABLE_RE = re.compile(r"^(?:(override)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*([:+?]?=)(.*)$")
DEFINE_RE = re.compile(r"^define\s+([^\s]+)\s*$")
RULE_RE = re.compile(r"^([^#\t][^:=]*?):(.*)$")


@dataclass(frozen=True)
class SourceLine:
    text: str
    path: Path
    number: int


class InventoryError(RuntimeError):
    pass


def _digest(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_include(raw: str, root: Path, source: Path) -> Path:
    token = raw.strip()
    prefix = f"$({RESERVED_ROOT_VARIABLE})"
    if token.startswith(prefix):
        return root / token[len(prefix) :]
    if "$" in token or any(char.isspace() for char in token):
        raise InventoryError(f"{_relative(source, root)}: unsupported dynamic include {raw!r}")
    return (source.parent / token).resolve()


def expand_make_sources(root: Path) -> tuple[list[SourceLine], list[str]]:

    root = root.resolve()
    include_order: list[str] = []
    active: list[Path] = []

    def visit(path: Path) -> list[SourceLine]:
        path = path.resolve()
        if path in active:
            chain = " -> ".join(_relative(item, root) for item in [*active, path])
            raise InventoryError(f"Make include cycle: {chain}")
        if not path.is_file():
            raise InventoryError(f"required Make include is missing: {_relative(path, root)}")
        active.append(path)
        expanded: list[SourceLine] = []
        in_define = False
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            in_define = in_define or DEFINE_RE.match(text) is not None
            if not in_define and not text.startswith("\t"):
                match = INCLUDE_RE.match(text)
                if match:
                    included = _resolve_include(match.group(1), root, path)
                    include_order.append(_relative(included, root))
                    expanded.extend(visit(included))
                    continue
            expanded.append(SourceLine(text, path, number))
            if in_define and text == "endef":
                in_define = False
        active.pop()
        return expanded

    return visit(root / "Makefile"), include_order


def _statement(kind: str, body: str, **identity: Any) -> dict[str, Any]:
    return {"kind": kind, **identity, "body": body, "sha256": _digest(body.splitlines())}


def _semantic_digest(statements: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(statements, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _contract_digest(inventory: dict[str, Any]) -> str:
    material = {key: value for key, value in inventory.items() if key != "contract_sha256"}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_inventory(root: Path) -> dict[str, Any]:

    root = root.resolve()
    lines, include_order = expand_make_sources(root)
    rules: dict[str, list[dict[str, str]]] = {}
    variables: dict[str, list[dict[str, str]]] = {}
    macros: dict[str, list[str]] = {}
    semantic_statements: list[dict[str, Any]] = []
    phony: set[str] = set()
    default_goal: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        text = line.text
        if not text or text.lstrip().startswith("#"):
            index += 1
            continue
        define = DEFINE_RE.match(text)
        if define:
            block = [text]
            index += 1
            while index < len(lines) and lines[index].text != "endef":
                block.append(lines[index].text)
                index += 1
            if index >= len(lines):
                raise InventoryError(f"{_relative(line.path, root)}:{line.number}: unterminated define")
            block.append("endef")
            index += 1
            body = "\n".join(block)
            macros.setdefault(define.group(1), []).append(body)
            semantic_statements.append(_statement("define", body, name=define.group(1)))
            continue
        variable = VARIABLE_RE.match(text)
        if variable:
            block = [text]
            index += 1
            while block[-1].endswith("\\") and index < len(lines):
                block.append(lines[index].text)
                index += 1
            body = "\n".join(block)
            directive = variable.group(1) or ""
            name = variable.group(2)
            variables.setdefault(name, []).append({"directive": directive, "operator": variable.group(3), "body": body})
            semantic_statements.append(_statement("variable", body, name=name))
            continue
        rule = RULE_RE.match(text)
        if rule:
            targets = rule.group(1).strip().split()
            if targets == [f"$({DEVELOPMENT_TREE_TARGETS_VARIABLE})"]:
                records = variables.get(DEVELOPMENT_TREE_TARGETS_VARIABLE, [])
                if len(records) != 1:
                    raise InventoryError(
                        f"{_relative(line.path, root)}:{line.number}: development-tree target list is ambiguous"
                    )
                targets = records[0]["body"].split(":=", 1)[1].replace("\\\n", " ").split()
            recipe: list[str] = []
            index += 1
            while index < len(lines):
                following = lines[index].text
                if following.startswith("\t"):
                    recipe.append(following)
                    index += 1
                elif not following:
                    index += 1
                else:
                    break
            body = "\n".join([text, *recipe])
            semantic_statements.append(_statement("rule", body, targets=targets))
            if targets == [".PHONY"]:
                phony.update(rule.group(2).split())
                continue
            if default_goal is None:
                default_goal = next(
                    (target for target in targets if not target.startswith(".") and "%" not in target), None
                )
            record = {"header": text, "recipe": "\n".join(recipe), "recipe_sha256": _digest(recipe)}
            for target in targets:
                rules.setdefault(target, []).append(record)
            continue
        raise InventoryError(f"{_relative(line.path, root)}:{line.number}: unsupported top-level Make syntax: {text!r}")

    targets = sorted(rules)
    public = sorted(target for target in targets if not target.startswith(".") and "%" not in target)
    contract: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "include_order": include_order,
        "default_goal": default_goal,
        "target_count": len(targets),
        "public_target_count": len(public),
        "targets": targets,
        "public_targets": public,
        "phony_targets": sorted(phony),
        "rules": {name: rules[name] for name in targets},
        "variables": {name: variables[name] for name in sorted(variables)},
        "macros": {name: macros[name] for name in sorted(macros)},
        "semantic_statements": semantic_statements,
        "semantic_sha256": _semantic_digest(semantic_statements),
    }
    contract["contract_sha256"] = _contract_digest(contract)
    return contract


def _load_inventory(path: Path) -> dict[str, Any]:
    inventory = json.loads(path.read_text(encoding="utf-8"))
    if inventory.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError(f"{path.name}: unsupported schema version {inventory.get('schema_version')!r}")
    if inventory.get("semantic_sha256") != _semantic_digest(inventory.get("semantic_statements", [])):
        raise InventoryError(f"{path.name}: semantic digest mismatch")
    if inventory.get("contract_sha256") != _contract_digest(inventory):
        raise InventoryError(f"{path.name}: contract digest mismatch")
    return inventory


def _normalized_current_statements(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in inventory["semantic_statements"]:
        statement = dict(raw)
        body = statement["body"]
        if statement["kind"] == "variable" and statement.get("name") == RESERVED_ROOT_VARIABLE:
            continue
        if statement["kind"] == "rule" and statement.get("targets") == [INVENTORY_TARGET]:
            continue
        if statement["kind"] == "rule" and statement.get("targets") == [".PHONY"]:
            body = body.replace(f" {INVENTORY_TARGET}", "")
        if statement["kind"] == "rule" and statement.get("targets") == ["help"]:
            body = body.replace(f"\n{HELP_RECIPE_LINE}", "")
        if statement["kind"] == "rule" and statement.get("targets") == ["release-cut"]:
            for path in ADDED_RELEASE_CURATED_TESTS:
                body = body.replace(f" {path}", "")
        statement["body"] = body
        statement["sha256"] = _digest(body.splitlines())
        normalized.append(statement)
    return normalized


def _normalized_help_rule(records: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized = []
    for raw in records:
        record = dict(raw)
        recipe = record["recipe"].replace(f"\n{HELP_RECIPE_LINE}", "")
        record["recipe"] = recipe
        record["recipe_sha256"] = _digest(recipe.splitlines())
        normalized.append(record)
    return normalized


def _normalized_release_rule(records: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized = []
    for raw in records:
        record = dict(raw)
        recipe = record["recipe"]
        for path in ADDED_RELEASE_CURATED_TESTS:
            recipe = recipe.replace(f" {path}", "")
        record["recipe"] = recipe
        record["recipe_sha256"] = _digest(recipe.splitlines())
        normalized.append(record)
    return normalized


def _compare_baseline_rules(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for target, records in baseline["rules"].items():
        current_records = current["rules"].get(target, [])
        if target == "help":
            current_records = _normalized_help_rule(current_records)
        if target == "release-cut":
            current_records = _normalized_release_rule(current_records)
        if current_records != records:
            problems.append(f"rule {target} changed from monolith")
    expected_inventory_rule = [
        {
            "header": f"{INVENTORY_TARGET}:",
            "recipe": "\tuv run -- python make/check_makefile_inventory.py",
            "recipe_sha256": _digest(["\tuv run -- python make/check_makefile_inventory.py"]),
        }
    ]
    if current["rules"].get(INVENTORY_TARGET) != expected_inventory_rule:
        problems.append("inventory guard rule differs from the reviewed addition")
    return problems


def compare_migration(root: Path, actual: dict[str, Any] | None = None) -> list[str]:

    root = root.resolve()
    baseline_path = root / BASELINE_PATH
    if not baseline_path.is_file():
        return [f"monolith baseline is missing: {BASELINE_PATH}"]
    try:
        baseline = _load_inventory(baseline_path)
        current = actual or build_inventory(root)
    except (InventoryError, json.JSONDecodeError) as exc:
        return [str(exc)]

    problems: list[str] = []
    expected_targets = set(baseline["targets"]) | {INVENTORY_TARGET}
    if set(current["targets"]) != expected_targets:
        problems.append("target delta from monolith exceeds makefile-inventory-check")
    expected_public = set(baseline["public_targets"]) | {INVENTORY_TARGET}
    if set(current["public_targets"]) != expected_public:
        problems.append("public target delta from monolith exceeds makefile-inventory-check")
    expected_phony = set(baseline["phony_targets"]) | {INVENTORY_TARGET}
    if set(current["phony_targets"]) != expected_phony:
        problems.append("phony target delta from monolith exceeds makefile-inventory-check")
    if current["default_goal"] != baseline["default_goal"]:
        problems.append("default goal changed from monolith")
    if current["include_order"] != EXPECTED_INCLUDE_ORDER:
        problems.append("module include order changed")

    expected_variables = set(baseline["variables"]) | {RESERVED_ROOT_VARIABLE}
    if set(current["variables"]) != expected_variables:
        problems.append("variable names changed from monolith beyond the reserved root variable")
    for name, records in baseline["variables"].items():
        if current["variables"].get(name) != records:
            problems.append(f"variable {name} changed from monolith")
    root_records = current["variables"].get(RESERVED_ROOT_VARIABLE)
    if root_records != [
        {
            "directive": "override",
            "operator": ":=",
            "body": ("override BENCHBOX_MAKEFILE_ROOT := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))"),
        }
    ]:
        problems.append("reserved root variable definition changed")
    if current["macros"] != baseline["macros"]:
        problems.append("define macro bodies changed from monolith")

    problems.extend(_compare_baseline_rules(baseline, current))

    if _normalized_current_statements(current) != baseline["semantic_statements"]:
        problems.append("semantic statement order or content changed from monolith")
    return problems


def build_migration_proof(baseline: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:

    return {
        "schema_version": MIGRATION_PROOF_SCHEMA_VERSION,
        "monolith": {
            "contract_sha256": baseline["contract_sha256"],
            "semantic_sha256": baseline["semantic_sha256"],
            "target_count": baseline["target_count"],
            "public_target_count": baseline["public_target_count"],
            "default_goal": baseline["default_goal"],
        },
        "reviewed_split": {
            "contract_sha256": extracted["contract_sha256"],
            "semantic_sha256": extracted["semantic_sha256"],
            "normalized_semantic_sha256": _semantic_digest(_normalized_current_statements(extracted)),
            "target_count": extracted["target_count"],
            "public_target_count": extracted["public_target_count"],
            "default_goal": extracted["default_goal"],
        },
        "reviewed_delta": {
            "added_targets": [INVENTORY_TARGET],
            "added_phony_targets": [INVENTORY_TARGET],
            "added_variables": [RESERVED_ROOT_VARIABLE],
            "added_release_curated_tests": ADDED_RELEASE_CURATED_TESTS,
            "include_order": HISTORICAL_INCLUDE_ORDER,
            "help_recipe_line": HELP_RECIPE_LINE,
        },
    }


def _load_migration_proof(path: Path) -> dict[str, Any]:
    proof = json.loads(path.read_text(encoding="utf-8"))
    if proof.get("schema_version") != MIGRATION_PROOF_SCHEMA_VERSION:
        raise InventoryError(f"{path.name}: unsupported schema version {proof.get('schema_version')!r}")
    return proof


def validate_migration_proof(root: Path) -> list[str]:

    root = root.resolve()
    baseline_path = root / BASELINE_PATH
    proof_path = root / MIGRATION_PROOF_PATH
    if not baseline_path.is_file():
        return [f"monolith baseline is missing: {BASELINE_PATH}"]
    if not proof_path.is_file():
        return [f"migration proof is missing: {MIGRATION_PROOF_PATH}"]
    try:
        baseline = _load_inventory(baseline_path)
        proof = _load_migration_proof(proof_path)
    except (InventoryError, json.JSONDecodeError) as exc:
        return [str(exc)]

    expected_monolith = {
        "contract_sha256": baseline["contract_sha256"],
        "semantic_sha256": baseline["semantic_sha256"],
        "target_count": baseline["target_count"],
        "public_target_count": baseline["public_target_count"],
        "default_goal": baseline["default_goal"],
    }
    expected_delta = {
        "added_targets": [INVENTORY_TARGET],
        "added_phony_targets": [INVENTORY_TARGET],
        "added_variables": [RESERVED_ROOT_VARIABLE],
        "added_release_curated_tests": ADDED_RELEASE_CURATED_TESTS,
        "include_order": HISTORICAL_INCLUDE_ORDER,
        "help_recipe_line": HELP_RECIPE_LINE,
    }
    reviewed_split = proof.get("reviewed_split", {})
    problems: list[str] = []
    if proof.get("monolith") != expected_monolith:
        problems.append("migration proof no longer matches the immutable monolith baseline")
    if proof.get("reviewed_delta") != expected_delta:
        problems.append("migration proof reviewed delta changed")
    if reviewed_split.get("normalized_semantic_sha256") != baseline["semantic_sha256"]:
        problems.append("migration proof does not record semantic equivalence to the monolith")
    if reviewed_split.get("target_count") != baseline["target_count"] + 1:
        problems.append("migration proof target-count delta is not one")
    if reviewed_split.get("public_target_count") != baseline["public_target_count"] + 1:
        problems.append("migration proof public-target-count delta is not one")
    if reviewed_split.get("default_goal") != baseline["default_goal"]:
        problems.append("migration proof default goal changed from monolith")
    for key in ("contract_sha256", "semantic_sha256"):
        value = reviewed_split.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            problems.append(f"migration proof reviewed split {key} is invalid")
    return problems


def verify_current_migration(root: Path) -> list[str]:

    root = root.resolve()
    problems = validate_migration_proof(root)
    try:
        baseline = _load_inventory(root / BASELINE_PATH)
        proof = _load_migration_proof(root / MIGRATION_PROOF_PATH)
        current = build_inventory(root)
    except (InventoryError, json.JSONDecodeError) as exc:
        return [*problems, str(exc)]
    problems.extend(compare_migration(root, current))
    expected_proof = build_migration_proof(baseline, current)
    if proof != expected_proof:
        problems.append("current graph hashes differ from the recorded initial split")
    return list(dict.fromkeys(problems))


def write_manifest(root: Path, inventory: dict[str, Any]) -> None:
    path = root / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def evaluate_inventory(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    root = root.resolve()
    try:
        inventory = build_inventory(root)
    except InventoryError as exc:
        return None, [str(exc)]
    return inventory, validate_migration_proof(root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify-migration", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.write:
        try:
            inventory = build_inventory(root)
        except InventoryError as exc:
            print(f"Makefile inventory error: {exc}", file=sys.stderr)
            return 1
        problems = validate_migration_proof(root)
        if problems:
            print("Refusing to write while historical migration evidence is invalid:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        write_manifest(root, inventory)
        print(f"wrote {MANIFEST_PATH}: {inventory['target_count']} targets")
        return 0
    if args.verify_migration:
        problems = verify_current_migration(root)
        if problems:
            print("Makefile extraction differs from the recorded migration proof:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print("Makefile migration proof OK: split matches the reviewed monolith delta")
        return 0
    inventory, problems = evaluate_inventory(root)
    if inventory is None or problems:
        print("Makefile inventory error:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"Makefile inventory OK: {inventory['target_count']} targets, "
        f"{inventory['public_target_count']} public, default={inventory['default_goal']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
