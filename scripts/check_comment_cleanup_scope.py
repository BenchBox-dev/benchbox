from __future__ import annotations

import argparse
import ast
import email
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tokenize
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

POLICY_PATH = "quality/comment-cleanup-scope.json"

COMMENT_POLICY_ENFORCEMENT_MODES = {"advisory", "blocking"}


COMMENT_POLICY_DIRECTIVES = (
    r"# noqa: [A-Z]+[0-9]+(?:, ?[A-Z]+[0-9]+)*",
    r"# type: ignore\[[a-z0-9_-]+(?:, ?[a-z0-9_-]+)*\]",
    r"# pragma: no (?:cover|branch)",
    r"# fmt: (?:off|on|skip)",
    r"# (?:ruff|flake8): noqa: [A-Z]+[0-9]+(?:, ?[A-Z]+[0-9]+)*",
    r"# shellcheck (?:disable=SC[0-9]+(?:,SC[0-9]+)*|shell=(?:bash|sh|dash|ksh))",
    r"/// <reference (?:types|path)=\"[^\"\n]+\" ?/>",
    r"// @ts-(?:expect-error|ignore|check|nocheck)",
    r"/\*\* @vitest-environment (?:jsdom|node|happy-dom) \*/",
    r"// @vitest-environment (?:jsdom|node|happy-dom)",
    r"/\*\+ [A-Z_]+\([A-Za-z0-9_., ]+\) \*/",
)


def validate_comment_policy_path(path: str) -> None:
    if not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path:
        raise ValueError(f"invalid policy path: {path!r}")
    if any(char in path for char in "*?[]"):
        raise ValueError("policy paths must be exact files or directory prefixes")


def comment_policy_matches(path: str, scope: str) -> bool:
    return path.startswith(scope) if scope.endswith("/") else path == scope


def load_comment_policy(raw: bytes) -> dict:
    policy = json.loads(raw)
    if set(policy) - {"enforcement"} != {"version", "external", "completed", "exceptions"} or policy["version"] != 1:
        raise ValueError("invalid comment-policy schema")
    enforcement = policy.get("enforcement", "blocking")
    if not isinstance(enforcement, str) or enforcement not in COMMENT_POLICY_ENFORCEMENT_MODES:
        raise ValueError("enforcement must be advisory or blocking")
    for key in ("external", "completed", "exceptions"):
        if not isinstance(policy[key], list):
            raise ValueError(f"{key} must be a list")
    for entry in policy["external"]:
        if set(entry) != {"path", "owner", "provenance"} or not all(
            isinstance(v, str) and v.strip() for v in entry.values()
        ):
            raise ValueError("external scope requires path, owner and provenance")
        validate_comment_policy_path(entry["path"])
    for path in policy["completed"]:
        validate_comment_policy_path(path)
        if any(
            comment_policy_matches(path, entry["path"]) or comment_policy_matches(entry["path"], path)
            for entry in policy["external"]
        ):
            raise ValueError("completed and external scopes cannot overlap")
    identities = set()
    for entry in policy["exceptions"]:
        required = {"path", "symbol", "text", "kind", "consumer", "necessity", "alternative", "owner", "removal"}
        fixture_fields = {"payload", "finding_kind"} if entry.get("kind") == "fixture" else set()
        required |= fixture_fields
        if (
            set(entry) - {"expires", "count"} != required
            or not all(isinstance(v, str) for k, v in entry.items() if k != "count")
            or type(entry.get("count", 1)) is not int
            or entry.get("count", 1) < 1
        ):
            raise ValueError(
                "exception requires an exact identity, consumer, necessity, alternative, owner and removal"
            )
        if any(not entry[key].strip() for key in required - {"symbol"}):
            raise ValueError("exception evidence must not be empty")
        validate_comment_policy_path(entry["path"])
        validate_comment_policy_path(entry["consumer"])
        if entry["kind"] not in {"directive", "notice", "fixture"}:
            raise ValueError("explanatory comment and docstring exceptions are prohibited")
        if entry["kind"] == "fixture" and entry["finding_kind"] not in {"comment", "payload-error"}:
            raise ValueError("fixture must identify an actual comment or malformed parser input")
        if entry["kind"] == "directive":
            if not any(re.fullmatch(pattern, entry["text"]) for pattern in COMMENT_POLICY_DIRECTIVES):
                raise ValueError("directive must match an exact registered grammar without explanatory suffixes")
            suppression = re.search(
                r"noqa|type: ignore|pragma: no|fmt: (?:off|skip)|disable=|@ts-(?:expect-error|ignore|nocheck)",
                entry["text"],
            )
            if suppression and "expires" not in entry:
                raise ValueError("directive exception needs an unexpired review date")
        identity = (
            entry["path"],
            entry["symbol"],
            entry["text"],
            entry["kind"],
            entry.get("payload", ""),
            entry.get("finding_kind", "comment"),
        )
        if identity in identities:
            raise ValueError("duplicate exception identity")
        identities.add(identity)
    return policy


@dataclass(frozen=True)
class Finding:
    code: str
    subject: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code} {self.subject}: {self.detail}"


class PolicyError(ValueError):
    pass


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def tracked_paths(root: Path, base: str) -> list[str]:
    if not base or len(base) != 40 or any(char not in "0123456789abcdef" for char in base):
        raise PolicyError("base must be a full lowercase commit SHA")
    return sorted(path for path in git(root, "ls-tree", "-r", "-z", "--name-only", base).decode().split("\0") if path)


def frozen_task_ids(root: Path, value: str) -> set[str]:
    path = (root / value).resolve()
    if root not in path.parents:
        raise PolicyError("task set must stay under the repository root")
    relative = path.relative_to(root)
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(relative)], check=False)
    if ignored.returncode != 0:
        raise PolicyError("task set must be ignored local evidence")
    task_ids = {
        line.removeprefix("TODO: ").strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("TODO: ") or line.startswith("comment-cleanup-")
    }
    if not task_ids or any(not task_id.startswith("comment-cleanup-") for task_id in task_ids):
        raise PolicyError("task set must contain comment-cleanup task IDs")
    return task_ids


def valid_path(path: object) -> bool:
    if not isinstance(path, str):
        return False
    parsed = PurePosixPath(path)
    return bool(path) and not parsed.is_absolute() and ".." not in parsed.parts and "\\" not in path


def matches(path: str, selector: dict[str, str]) -> bool:
    if set(selector) == {"path"}:
        return path == selector["path"]
    if set(selector) == {"prefix"}:
        return path.startswith(selector["prefix"])
    raise PolicyError("selector must contain exactly path or prefix")


def valid_selector(selector: object, *, allow_empty_prefix: bool = False) -> bool:
    if not isinstance(selector, dict) or set(selector) not in ({"path"}, {"prefix"}):
        return False
    value = next(iter(selector.values()))
    if allow_empty_prefix and selector.keys() == {"prefix"} and value == "":
        return True
    return valid_path(value)


def require_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise PolicyError(f"{label} fields must be {sorted(fields)}")


def check_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{label} must be a non-empty string")
    return value


def check_owner(value: Any, label: str, task_ids: set[str] | None = None) -> str:
    owner = check_string(value, label)
    if not owner.startswith("comment-cleanup-"):
        raise PolicyError(f"{label} must name a comment-cleanup task")
    if task_ids is not None and owner not in task_ids:
        raise PolicyError(f"{label} is not in the frozen task set")
    return owner


def load_policy(path: Path) -> dict[str, Any]:
    return load_policy_bytes(path.read_bytes())


def load_policy_bytes(raw: bytes) -> dict[str, Any]:
    policy = json.loads(raw)
    if not isinstance(policy, dict):
        raise PolicyError("policy must be an object")
    require_fields(
        policy,
        {
            "version",
            "maintained_roots",
            "ownership_rules",
            "external_entries",
            "format_classes",
            "payloads",
            "derived_rules",
            "consumer_edges",
            "directives",
            "notices",
            "obligations",
            "review_dispositions",
        },
        "policy",
    )
    if policy["version"] != 1:
        raise PolicyError("unsupported policy version")
    for name in policy:
        if name != "version" and not isinstance(policy[name], list):
            raise PolicyError(f"{name} must be a list")
    return policy


def validate_roots(policy: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, root in enumerate(policy["maintained_roots"]):
        if not isinstance(root, dict):
            raise PolicyError(f"maintained_roots[{index}] must be an object")
        require_fields(root, {"id", "selector", "kind"}, f"maintained_roots[{index}]")
        for field in ("id", "kind"):
            check_string(root[field], f"maintained_roots[{index}].{field}")
        if not valid_selector(root["selector"], allow_empty_prefix=True):
            raise PolicyError(f"invalid maintained root: {root['selector']}")
        matches("", root["selector"])
        result.append(root)
    return result


def validate_rules(policy: dict[str, Any], task_ids: set[str] | None = None) -> list[dict[str, Any]]:
    result = []
    ids = set()
    for index, rule in enumerate(policy["ownership_rules"]):
        if not isinstance(rule, dict):
            raise PolicyError(f"ownership_rules[{index}] must be an object")
        require_fields(rule, {"id", "owner", "state", "blocking_disposition", "priority", "selectors"}, f"rule {index}")
        rule_id = check_string(rule["id"], f"rule {index}.id")
        if rule_id in ids:
            raise PolicyError(f"duplicate ownership rule: {rule_id}")
        ids.add(rule_id)
        check_owner(rule["owner"], f"rule {rule_id}.owner", task_ids)
        if rule["state"] not in {"ready", "blocked"}:
            raise PolicyError(f"rule {rule_id}.state must be ready or blocked")
        check_string(rule["blocking_disposition"], f"rule {rule_id}.blocking_disposition")
        if not isinstance(rule["priority"], int):
            raise PolicyError(f"rule {rule_id}.priority must be an integer")
        if not isinstance(rule["selectors"], list) or not rule["selectors"]:
            raise PolicyError(f"rule {rule_id}.selectors must be a non-empty list")
        for selector in rule["selectors"]:
            if not valid_selector(selector):
                raise PolicyError(f"rule {rule_id} has an invalid selector")
            matches("", selector)
        result.append(rule)
    return result


def base_blob(root: Path, base: str, path: str) -> bytes:
    return git(root, "show", f"{base}:{path}")


def check_evidence_field(category: str, index: int, field: str, value: Any) -> None:
    label = f"{category}[{index}].{field}"
    if (category == "notices" and field in {"byte_start", "byte_end"}) or (
        category == "directives" and field == "count"
    ):
        if not isinstance(value, int) or isinstance(value, bool) or (field == "count" and value < 1):
            raise PolicyError(f"{label} must be an integer")
    elif category == "obligations" and field in {"tracker_reference", "approved_destination"}:
        if not isinstance(value, str):
            raise PolicyError(f"{label} must be a string")
    else:
        check_string(value, label)


def check_token_evidence(category: str, index: int, entry: dict[str, Any], root: Path, base: str) -> None:
    occurrences = base_blob(root, base, entry["path"]).count(entry["token"].encode())
    if occurrences == 0:
        raise PolicyError(f"{category}[{index}] token is not present in {entry['path']} at the base")
    if category == "directives" and occurrences != entry["count"]:
        raise PolicyError(
            f"directives[{index}] counts {entry['count']} but {entry['path']} has {occurrences} at the base"
        )


def validate_evidence(
    policy: dict[str, Any], root: Path, base: str, paths: set[str], task_ids: set[str] | None = None
) -> None:
    for category, fields in {
        "directives": {"path", "token", "count", "consumer", "necessity", "alternative", "owner", "removal_trigger"},
        "notices": {
            "path",
            "blob_sha256",
            "byte_start",
            "byte_end",
            "retained_sha256",
            "governing_requirement",
            "source_identity",
            "owner",
            "blocking_disposition",
        },
        "obligations": {
            "path",
            "token",
            "destination",
            "tracker_reference",
            "approved_destination",
            "owner",
            "blocking_disposition",
        },
    }.items():
        for index, entry in enumerate(policy[category]):
            if not isinstance(entry, dict):
                raise PolicyError(f"{category}[{index}] must be an object")
            require_fields(entry, fields, f"{category}[{index}]")
            for field, value in entry.items():
                check_evidence_field(category, index, field, value)
            if entry["path"] not in paths:
                raise PolicyError(f"{category}[{index}] path is not tracked at the immutable base")
            check_owner(entry["owner"], f"{category}[{index}].owner", task_ids)
            if category in {"directives", "obligations"}:
                check_token_evidence(category, index, entry, root, base)
            if category == "obligations":
                tracker_reference = entry["tracker_reference"]
                approved_destination = entry["approved_destination"]
                if bool(tracker_reference) == bool(approved_destination):
                    raise PolicyError(
                        f"obligations[{index}] must have exactly one tracker_reference or approved_destination"
                    )
            if category == "notices":
                for field in ("blob_sha256", "retained_sha256"):
                    if len(entry[field]) != 64 or any(char not in "0123456789abcdef" for char in entry[field]):
                        raise PolicyError(f"notice {entry['path']} must use a lowercase SHA-256 digest")
                blob = base_blob(root, base, entry["path"])
                if hashlib.sha256(blob).hexdigest() != entry["blob_sha256"]:
                    raise PolicyError(f"notice {entry['path']} blob digest does not match immutable base")
                start = entry["byte_start"]
                end = entry["byte_end"]
                if start < 0 or end <= start or end > len(blob):
                    raise PolicyError(f"notice {entry['path']} has an invalid retained byte range")
                if hashlib.sha256(blob[start:end]).hexdigest() != entry["retained_sha256"]:
                    raise PolicyError(f"notice {entry['path']} retained-byte digest does not match immutable base")


def validate_payloads_and_edges(policy: dict[str, Any], paths: set[str], task_ids: set[str] | None = None) -> None:
    payload_ids = set()
    for index, payload in enumerate(policy["payloads"]):
        if not isinstance(payload, dict):
            raise PolicyError(f"payloads[{index}] must be an object")
        require_fields(
            payload, {"id", "path", "carrier", "owner", "state", "blocking_disposition"}, f"payloads[{index}]"
        )
        payload_id = check_string(payload["id"], f"payloads[{index}].id")
        if payload_id in payload_ids:
            raise PolicyError(f"duplicate payload: {payload_id}")
        payload_ids.add(payload_id)
        if payload["path"] not in paths:
            raise PolicyError(f"payload {payload_id} path is not tracked at the immutable base")
        check_string(payload["carrier"], f"payload {payload_id}.carrier")
        check_owner(payload["owner"], f"payload {payload_id}.owner", task_ids)
        if payload["state"] not in {"ready", "blocked"}:
            raise PolicyError(f"payload {payload_id}.state must be ready or blocked")
        check_string(payload["blocking_disposition"], f"payload {payload_id}.blocking_disposition")
    edge_keys = set()
    graph: dict[str, set[str]] = {}
    for index, edge in enumerate(policy["consumer_edges"]):
        if not isinstance(edge, dict):
            raise PolicyError(f"consumer_edges[{index}] must be an object")
        require_fields(
            edge,
            {"producer", "consumer", "owner", "state", "blocking_disposition", "proof"},
            f"consumer_edges[{index}]",
        )
        for field in ("producer", "consumer", "proof", "blocking_disposition"):
            check_string(edge[field], f"consumer_edges[{index}].{field}")
        check_owner(edge["owner"], f"consumer_edges[{index}].owner", task_ids)
        if edge["state"] not in {"ready", "blocked"}:
            raise PolicyError(f"consumer_edges[{index}].state must be ready or blocked")
        for endpoint in (edge["producer"], edge["consumer"]):
            if endpoint not in paths and endpoint not in payload_ids:
                raise PolicyError(f"consumer edge endpoint is neither a tracked path nor a payload: {endpoint}")
        key = edge["producer"], edge["consumer"]
        if key in edge_keys:
            raise PolicyError(f"duplicate consumer edge: {key[0]} -> {key[1]}")
        edge_keys.add(key)
        graph.setdefault(key[0], set()).add(key[1])
    active = set()
    complete = set()

    def visit(endpoint: str) -> None:
        if endpoint in active:
            raise PolicyError(f"cyclic consumer edge: {endpoint}")
        if endpoint in complete:
            return
        active.add(endpoint)
        for child in graph.get(endpoint, set()):
            visit(child)
        active.remove(endpoint)
        complete.add(endpoint)

    for endpoint in graph:
        visit(endpoint)


def validate_dispositions(policy: dict[str, Any], task_ids: set[str] | None = None) -> None:
    expected = {f"R{number}" for number in range(1, 19)}
    found = set()
    for index, item in enumerate(policy["review_dispositions"]):
        if not isinstance(item, dict):
            raise PolicyError(f"review_dispositions[{index}] must be an object")
        require_fields(
            item,
            {"id", "disposition", "requirements", "owner", "original_verdict", "rationale", "acceptance_refs"},
            f"review_dispositions[{index}]",
        )
        item_id = check_string(item["id"], f"review_dispositions[{index}].id")
        if item_id in found:
            raise PolicyError(f"duplicate review disposition: {item_id}")
        found.add(item_id)
        if item["disposition"] not in {"accepted", "narrowed", "rebutted"}:
            raise PolicyError(f"invalid disposition for {item_id}")
        if not isinstance(item["requirements"], list) or not item["requirements"]:
            raise PolicyError(f"review disposition {item_id} needs requirements")
        for requirement in item["requirements"]:
            check_string(requirement, f"review disposition {item_id} requirement")
        if item["original_verdict"] not in {"accepted", "narrowed", "rebutted"}:
            raise PolicyError(f"invalid original verdict for {item_id}")
        check_string(item["rationale"], f"review disposition {item_id} rationale")
        if not isinstance(item["acceptance_refs"], list) or not item["acceptance_refs"]:
            raise PolicyError(f"review disposition {item_id} needs acceptance references")
        for reference in item["acceptance_refs"]:
            check_string(reference, f"review disposition {item_id} acceptance reference")
        check_owner(item["owner"], f"review disposition {item_id}.owner", task_ids)
    if found != expected:
        raise PolicyError(f"review dispositions must be exactly {sorted(expected)}")


def validate_derived_rules(policy: dict[str, Any], task_ids: set[str] | None = None) -> list[dict[str, Any]]:
    result = []
    ids = set()
    for index, rule in enumerate(policy["derived_rules"]):
        if not isinstance(rule, dict):
            raise PolicyError(f"derived_rules[{index}] must be an object")
        require_fields(
            rule, {"id", "method", "state", "blocking_disposition", "priority", "selectors"}, f"derived {index}"
        )
        rule_id = check_string(rule["id"], f"derived {index}.id")
        if rule_id in ids:
            raise PolicyError(f"duplicate derived rule: {rule_id}")
        ids.add(rule_id)
        if rule["method"] != "python-imports":
            raise PolicyError(f"derived {rule_id}.method must be python-imports")
        if rule["state"] not in {"ready", "blocked"}:
            raise PolicyError(f"derived {rule_id}.state must be ready or blocked")
        check_string(rule["blocking_disposition"], f"derived {rule_id}.blocking_disposition")
        if not isinstance(rule["priority"], int):
            raise PolicyError(f"derived {rule_id}.priority must be an integer")
        if not isinstance(rule["selectors"], list) or not rule["selectors"]:
            raise PolicyError(f"derived {rule_id}.selectors must be a non-empty list")
        for selector in rule["selectors"]:
            if not valid_selector(selector):
                raise PolicyError(f"derived {rule_id} has an invalid selector")
        result.append(rule)
    return result


def validate_rule_priorities(rules: list[dict[str, Any]], derived: list[dict[str, Any]]) -> None:
    for rule in rules:
        for selector in rule["selectors"]:
            if "path" not in selector:
                continue
            for derived_rule in derived:
                if rule["priority"] <= derived_rule["priority"] and any(
                    matches(selector["path"], candidate) for candidate in derived_rule["selectors"]
                ):
                    raise PolicyError(
                        f"rule {rule['id']} names {selector['path']} at priority {rule['priority']}, "
                        f"which derived rule {derived_rule['id']} would override"
                    )


def read_blobs(root: Path, base: str, paths: list[str]) -> dict[str, bytes]:
    if not paths:
        return {}
    request = "".join(f"{base}:{path}\n" for path in paths).encode()
    output = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch"], input=request, capture_output=True, check=True
    ).stdout
    blobs = {}
    offset = 0
    for path in paths:
        end = output.index(b"\n", offset)
        header = output[offset:end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise PolicyError(f"cannot read {path} at the immutable base")
        size = int(header[2])
        blobs[path] = output[end + 1 : end + 1 + size]
        offset = end + 1 + size + 1
    return blobs


MODULE_LITERAL = re.compile(r"benchbox(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def imported_modules(source: bytes) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.split(".")[0] == "benchbox")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] == "benchbox":
                if node.module != "benchbox":
                    modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and MODULE_LITERAL.fullmatch(node.value):
            modules.add(node.value)
    return modules


def module_path(module: str, path_owners: dict[str, str | None]) -> str | None:
    parts = module.split(".")
    while parts:
        stem = "/".join(parts)
        for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
            if candidate in path_owners:
                return candidate
        parts.pop()
    return None


def import_owner(source: bytes, path_owners: dict[str, str | None]) -> str | None:
    owners = set()
    for module in imported_modules(source):
        candidate = module_path(module, path_owners)
        if candidate is None:
            continue
        if candidate == "benchbox/__init__.py":
            return None
        owner = path_owners[candidate]
        if owner is None:
            return None
        owners.add(owner)
    return owners.pop() if len(owners) == 1 else None


def apply_derived_rules(
    resolved: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    root: Path,
    base: str,
    priorities: dict[str, int],
) -> None:
    if not rules:
        return
    path_owners = {record["path"]: record["owner"] for record in resolved if record["path"].startswith("benchbox/")}
    for rule in rules:
        eligible = [
            record
            for record in resolved
            if record["path"].endswith(".py")
            and not isinstance(record["rule"], list)
            and priorities.get(record["path"], -1) < rule["priority"]
            and any(matches(record["path"], selector) for selector in rule["selectors"])
        ]
        blobs = read_blobs(root, base, [record["path"] for record in eligible])
        for record in eligible:
            owner = import_owner(blobs[record["path"]], path_owners)
            if owner is None:
                continue
            record.update(
                owner=owner, state=rule["state"], blocking_disposition=rule["blocking_disposition"], rule=rule["id"]
            )
            priorities[record["path"]] = rule["priority"]


def validate_external_entries(
    policy: dict[str, Any], paths: set[str], task_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    fields = {"selector", "owner", "provenance", "governing_requirement", "blocking_disposition"}
    entries = []
    for index, entry in enumerate(policy["external_entries"]):
        if not isinstance(entry, dict):
            raise PolicyError(f"external_entries[{index}] must be an object")
        require_fields(entry, fields, f"external_entries[{index}]")
        if not valid_selector(entry["selector"]):
            raise PolicyError(f"external_entries[{index}] has an invalid selector")
        for field in ("provenance", "governing_requirement", "blocking_disposition"):
            check_string(entry[field], f"external_entries[{index}].{field}")
        check_owner(entry["owner"], f"external_entries[{index}].owner", task_ids)
        if not any(matches(path, entry["selector"]) for path in paths):
            raise PolicyError(f"external_entries[{index}] selector matches no tracked path at the base")
        entries.append(entry)
    prefixes = [entry["selector"]["prefix"] for entry in entries if "prefix" in entry["selector"]]
    for index, prefix in enumerate(prefixes):
        if any(other.startswith(prefix) for other in prefixes[index + 1 :]) or any(
            prefix.startswith(other) for other in prefixes[:index]
        ):
            raise PolicyError(f"external entry prefix overlaps another entry: {prefix}")
    return entries


def apply_external_entries(resolved: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
    for record in resolved:
        if record["owner"] is not None or isinstance(record["rule"], list):
            continue
        for entry in entries:
            if matches(record["path"], entry["selector"]):
                record.update(
                    owner=entry["owner"],
                    state="excluded",
                    blocking_disposition=entry["blocking_disposition"],
                    rule="external",
                )
                break


JSON_COMMENT_KEYS = {"_comment", "__comment", "comment", "$comment", "//"}


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)) or JSON_COMMENT_KEYS & set(keys):
        raise ValueError("duplicate or comment key")
    return dict(pairs)


def _json_constant(name: str) -> Any:
    raise ValueError(name)


def verify_strict_json(path: str, blob: bytes) -> bool:
    try:
        text = blob.decode("utf-8")
        documents = [line for line in text.splitlines() if line.strip()] if path.endswith(".jsonl") else [text]
        for document in documents:
            json.loads(document, object_pairs_hook=_json_object, parse_constant=_json_constant)
    except (UnicodeError, ValueError):
        return False
    return bool(documents)


def verify_png_signature(path: str, blob: bytes) -> bool:
    return blob.startswith(b"\x89PNG\r\n\x1a\n")


MARKDOWN_BLOCK_PREFIX = re.compile(r"^\s*(?:>\s*)*(?:(?:[-*+]|\d+[.)])\s+)*")
MARKDOWN_LINK_REFERENCE = re.compile(r"\[(?!\^)[^\]]+\]:")
MARKDOWN_COMMENT_MARKERS = re.compile(
    r"<!--|\{/\*|\{%|\{#|<(?:pre|code|script|style)\b|\[(?://|comment)\]\s*:\s*(?:#|<>)", re.IGNORECASE
)


def verify_markdown_prose(path: str, blob: bytes) -> bool:
    try:
        lines = blob.decode("utf-8").splitlines()
    except UnicodeError:
        return False
    if lines and lines[0].strip() in {"---", "+++"}:
        closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == lines[0].strip()), None)
        if lines[0].strip() == "+++" or closing is None:
            return False
        if any(re.search(r"(^|\s)#", line) for line in lines[1:closing]):
            return False
        lines = lines[closing + 1 :]
    for line in lines:
        if line.startswith(("    ", "\t")) or line.lstrip().startswith("%"):
            return False
        if MARKDOWN_COMMENT_MARKERS.search(line):
            return False
        block = MARKDOWN_BLOCK_PREFIX.sub("", line)
        if block.startswith(("```", "~~~")) or MARKDOWN_LINK_REFERENCE.match(block):
            return False
    return True


def verify_sql_without_comment_markers(path: str, blob: bytes) -> bool:
    try:
        text = blob.decode("utf-8")
    except UnicodeError:
        return False
    return not any(marker in text for marker in ("--", "/*", "#"))


def verify_empty_file(path: str, blob: bytes) -> bool:
    return blob == b""


def verify_markdown_needs_review(path: str, blob: bytes) -> bool:
    return not verify_markdown_prose(path, blob)


def verify_any_content(path: str, blob: bytes) -> bool:
    return True


FORMAT_VERIFIERS = {
    "strict-json": verify_strict_json,
    "png-signature": verify_png_signature,
    "markdown-prose": verify_markdown_prose,
    "sql-without-comment-markers": verify_sql_without_comment_markers,
    "empty-file": verify_empty_file,
    "markdown-needs-review": verify_markdown_needs_review,
    "any-content": verify_any_content,
}
BLOCKING_VERIFIERS = {"markdown-needs-review", "any-content"}
FORMAT_STATES = {"comment-free", "blocked"}


def validate_format_classes(policy: dict[str, Any], task_ids: set[str] | None = None) -> list[dict[str, Any]]:
    fields = {"id", "verifier", "extensions", "selectors", "owner", "blocking_disposition"}
    classes = []
    ids = set()
    for index, entry in enumerate(policy["format_classes"]):
        if not isinstance(entry, dict):
            raise PolicyError(f"format_classes[{index}] must be an object")
        require_fields(entry, fields | ({"state"} & set(entry)), f"format_classes[{index}]")
        class_id = check_string(entry["id"], f"format_classes[{index}].id")
        if class_id in ids:
            raise PolicyError(f"duplicate format class: {class_id}")
        ids.add(class_id)
        if entry["verifier"] not in FORMAT_VERIFIERS:
            raise PolicyError(f"format class {class_id} has an unknown verifier")
        state = entry.get("state", "comment-free")
        if state not in FORMAT_STATES:
            raise PolicyError(f"format class {class_id} has an unknown state")
        if (state == "blocked") != (entry["verifier"] in BLOCKING_VERIFIERS):
            raise PolicyError(
                f"format class {class_id}: a blocked class needs a blocking verifier, and a comment-free class a proving one"
            )
        extensions = entry["extensions"]
        if not isinstance(extensions, list) or not extensions:
            raise PolicyError(f"format class {class_id} needs extensions")
        for extension in extensions:
            if not isinstance(extension, str) or not extension.startswith(".") or extension != extension.lower():
                raise PolicyError(f"format class {class_id} extensions must be lowercase and start with a dot")
        if not isinstance(entry["selectors"], list) or not entry["selectors"]:
            raise PolicyError(f"format class {class_id} needs selectors")
        if not all(valid_selector(selector) for selector in entry["selectors"]):
            raise PolicyError(f"format class {class_id} has an invalid selector")
        check_owner(entry["owner"], f"format class {class_id}.owner", task_ids)
        check_string(entry["blocking_disposition"], f"format class {class_id}.blocking_disposition")
        classes.append(entry)
    return classes


def apply_format_classes(resolved: list[dict[str, Any]], classes: list[dict[str, Any]], root: Path, base: str) -> None:
    for entry in classes:
        eligible = [
            record
            for record in resolved
            if record["owner"] is None
            and not isinstance(record["rule"], list)
            and (PurePosixPath(record["path"]).suffix or PurePosixPath(record["path"]).name).lower()
            in entry["extensions"]
            and any(matches(record["path"], selector) for selector in entry["selectors"])
        ]
        blobs = read_blobs(root, base, [record["path"] for record in eligible])
        verifier = FORMAT_VERIFIERS[entry["verifier"]]
        for record in eligible:
            if verifier(record["path"], blobs[record["path"]]):
                record.update(
                    owner=entry["owner"],
                    state=entry.get("state", "comment-free"),
                    blocking_disposition=entry["blocking_disposition"],
                    rule=f"format:{entry['id']}",
                )


def apply_notice_owners(resolved: list[dict[str, Any]], notices: list[dict[str, Any]]) -> list[Finding]:
    by_path = {notice["path"]: notice for notice in notices}
    findings = []
    for record in resolved:
        notice = by_path.get(record["path"])
        if notice is None or isinstance(record["rule"], list):
            continue
        if record["owner"] is not None and record["owner"] != notice["owner"]:
            findings.append(Finding("SCOPE005", record["path"], "notice owner conflicts with an ownership rule"))
            continue
        record.update(
            owner=notice["owner"], state="blocked", blocking_disposition=notice["blocking_disposition"], rule="notice"
        )
        record.pop("competing_owners", None)
    return findings


DOC_CALL_READERS = {"getdoc", "getsource", "getcomments", "getsourcelines", "cleandoc", "render_doc"}
IMPLICIT_DOC_DECORATORS = {"command", "group", "tool", "resource", "prompt"}
IMPLICIT_HELP_KEYWORDS = {"help", "description"}
DIRECTIVE_COMMENT = re.compile(r"#\s*(?:noqa|type:\s*ignore|pragma:|fmt:|isort:|ruff:|pylint:|pyright:|mypy:)")
TODO_COMMENT = re.compile(r"^#+\s*(?:TODO|FIXME)\b(?!\s*\))|(?:#|;|\s{2,})\s*(?:TODO|FIXME)(?:\([^)]*\))?:")


def _target_name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Call):
        function = getattr(value.value.func, "id", "")
        return f"{function}()" if function in {"globals", "locals", "vars"} else None
    return None


def _call_carrier(node: ast.Call) -> tuple[int, str, str, str | None] | None:
    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
    arguments = [argument.value for argument in node.args if isinstance(argument, ast.Constant)]
    if name == "setattr" and "__doc__" in arguments:
        return node.lineno, "writer", "setattr", _target_name(node.args[0]) if node.args else None
    if name == "getattr" and "__doc__" in arguments:
        return node.lineno, "reader", "getattr", None
    if name in DOC_CALL_READERS:
        return node.lineno, "reader", name, None
    return None


def _decorator_carrier(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, str, str, str | None] | None:
    if ast.get_docstring(node) is None:
        return None
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        function = call.func if call else decorator
        name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
        if name in IMPLICIT_DOC_DECORATORS and not (
            call and any(keyword.arg in IMPLICIT_HELP_KEYWORDS for keyword in call.keywords)
        ):
            return node.lineno, "reader", "decorator-docstring", None
    return None


def doc_carriers(source: bytes) -> list[tuple[int, str, str, str | None]]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return []
    found: list[tuple[int, str, str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found.extend(
                (target.lineno, "writer", "attribute-assignment", _target_name(target.value))
                for target in targets
                if isinstance(target, ast.Attribute) and target.attr == "__doc__"
            )
        elif isinstance(node, ast.Attribute) and node.attr == "__doc__" and isinstance(node.ctx, ast.Load):
            found.append((node.lineno, "reader", "attribute-read", None))
        elif isinstance(node, ast.Name) and node.id == "__doc__" and isinstance(node.ctx, ast.Load):
            found.append((node.lineno, "reader", "module-docstring-read", None))
        elif isinstance(node, ast.Call):
            carrier = _call_carrier(node)
            if carrier:
                found.append(carrier)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            carrier = _decorator_carrier(node)
            if carrier:
                found.append(carrier)
    return sorted(found, key=lambda item: (item[0], item[1], item[2]))


def python_comment_markers(source: bytes) -> tuple[int, int]:
    directives = todos = 0
    try:
        for token in tokenize.tokenize(io.BytesIO(source).readline):
            if token.type == tokenize.COMMENT:
                directives += bool(DIRECTIVE_COMMENT.search(token.string))
                todos += bool(TODO_COMMENT.search(token.string))
    except (tokenize.TokenError, SyntaxError, IndentationError, UnicodeError):
        return 0, 0
    return directives, todos


def scan_python_sources(
    resolved: list[dict[str, Any]], root: Path, base: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_path = {record["path"]: record for record in resolved if record["path"].endswith(".py")}
    blobs = read_blobs(root, base, sorted(by_path))
    carriers = []
    markers = []
    for path, record in by_path.items():
        for line, role, form, target in doc_carriers(blobs[path]):
            carriers.append(
                {"path": path, "line": line, "role": role, "form": form, "target": target, "owner": record["owner"]}
            )
        directives, todos = python_comment_markers(blobs[path])
        if directives or todos:
            markers.append({"path": path, "directives": directives, "todos": todos, "owner": record["owner"]})
    return carriers, markers


def unregistered_markers(markers: list[dict[str, Any]], policy: dict[str, Any]) -> tuple[int, int]:
    registered_directives = sum(entry["count"] for entry in policy["directives"] if entry["path"].endswith(".py"))
    registered_todos = sum(1 for entry in policy["obligations"] if entry["path"].endswith(".py"))
    directives = sum(marker["directives"] for marker in markers) - registered_directives
    todos = sum(marker["todos"] for marker in markers) - registered_todos
    return max(directives, 0), max(todos, 0)


def _names_target(carrier_text: str, target: str) -> bool:
    if target.endswith("()"):
        return target in carrier_text
    return re.search(rf"(?<![\w]){re.escape(target)}\.__doc__", carrier_text) is not None


def carrier_findings(carriers: list[dict[str, Any]], policy: dict[str, Any]) -> list[Finding]:
    by_path: dict[str, list[str]] = {}
    for payload in policy["payloads"]:
        by_path.setdefault(payload["path"], []).append(payload["carrier"])
    findings = []
    for carrier in carriers:
        if carrier["role"] != "writer":
            continue
        registered = by_path.get(carrier["path"])
        if not registered:
            detail = f"runtime docstring write at line {carrier['line']} has no payload record"
        elif carrier.get("target") and not any(_names_target(text, carrier["target"]) for text in registered):
            detail = (
                f"runtime docstring write to {carrier['target']} at line {carrier['line']} has no payload naming it"
            )
        else:
            continue
        findings.append(Finding("SCOPE006", carrier["path"], detail))
    return findings


def evidence_owner_findings(policy: dict[str, Any], resolved: list[dict[str, Any]]) -> list[Finding]:
    owners = {record["path"]: record["owner"] for record in resolved}
    findings = []
    for category in ("directives", "obligations"):
        for entry in policy[category]:
            path_owner = owners.get(entry["path"])
            if path_owner is not None and path_owner != entry["owner"]:
                findings.append(
                    Finding(
                        "SCOPE007",
                        entry["path"],
                        f"{category} owner {entry['owner']} differs from the path owner {path_owner}",
                    )
                )
    return findings


def selected_root(path: str, roots: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [root for root in roots if matches(path, root["selector"])]
    if not candidates:
        return None
    specificity = max(len(next(iter(root["selector"].values()))) for root in candidates)
    selected = [root for root in candidates if len(next(iter(root["selector"].values()))) == specificity]
    if len(selected) != 1:
        root_ids = ", ".join(sorted(root["id"] for root in selected))
        raise PolicyError(f"maintained-root collision for {path}: {root_ids}")
    return selected[0]


def owned_paths(
    paths: list[str],
    roots: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    priorities: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[Finding]]:
    results = []
    findings = []
    for path in paths:
        root = selected_root(path, roots)
        if root is None:
            continue
        candidates = [rule for rule in rules if any(matches(path, selector) for selector in rule["selectors"])]
        if not candidates:
            findings.append(Finding("SCOPE001", path, "no ownership rule"))
            results.append(
                {
                    "path": path,
                    "kind": root["kind"],
                    "owner": None,
                    "state": "blocked",
                    "blocking_disposition": "Ownership is not frozen for this maintained path.",
                    "rule": None,
                }
            )
            continue
        priority = max(rule["priority"] for rule in candidates)
        selected = [rule for rule in candidates if rule["priority"] == priority]
        owners = {rule["owner"] for rule in selected}
        if len(selected) != 1 or len(owners) != 1:
            findings.append(Finding("SCOPE002", path, "ownership rule collision"))
            results.append(
                {
                    "path": path,
                    "kind": root["kind"],
                    "owner": None,
                    "state": "blocked",
                    "blocking_disposition": "Conflicting ownership rules require integrator resolution.",
                    "rule": sorted(rule["id"] for rule in selected),
                    "competing_owners": sorted(owners),
                }
            )
            continue
        rule = selected[0]
        if priorities is not None:
            priorities[path] = priority
        results.append(
            {
                "path": path,
                "kind": root["kind"],
                "owner": rule["owner"],
                "state": rule["state"],
                "blocking_disposition": rule["blocking_disposition"],
                "rule": rule["id"],
            }
        )
    return results, findings


def immutable_dependency_artifacts(
    root: Path,
    base: str,
    resolved: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    legacy_external: list[dict[str, str]],
    staged: bool = False,
) -> set[str]:
    by_path = {record["path"]: record for record in resolved}
    by_rule = {rule["id"]: rule for rule in rules}
    excluded = set()
    for entry in legacy_external:
        path = entry["path"]
        record = by_path.get(path)
        rule = by_rule.get(record["rule"]) if record and isinstance(record["rule"], str) else None
        external_record = record and record["rule"] == "external" and record["state"] == "excluded"
        if (
            not path.endswith(".whl")
            or not external_record
            and (not rule or any("path" in selector for selector in rule["selectors"]))
        ):
            continue
        project_path = entry["provenance"]
        if not project_path.endswith("/pyproject.toml"):
            continue
        lock_path = str(PurePosixPath(project_path).with_name("uv.lock"))
        try:
            project = tomllib.loads(base_blob(root, base, project_path).decode())
            locked = tomllib.loads(base_blob(root, base, lock_path).decode())
        except (subprocess.CalledProcessError, UnicodeError, tomllib.TOMLDecodeError):
            continue
        source_path = str(PurePosixPath(path).relative_to(PurePosixPath(project_path).parent))
        name = entry["owner"]
        binding = project.get("tool", {}).get("uv", {}).get("sources", {}).get(name)
        packages = [package for package in locked.get("package", []) if package.get("name") == name]
        dependencies = [
            value
            for value in project.get("project", {}).get("dependencies", [])
            if value == name or value.startswith(name + "[")
        ]
        if binding != {"path": source_path} or len(packages) != 1 or not dependencies:
            continue
        package = packages[0]
        raw = base_blob(root, base, path)
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if package.get("source") != binding or package.get("wheels") != [
            {"filename": PurePosixPath(path).name, "hash": digest}
        ]:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                metadata_paths = [value for value in archive.namelist() if value.endswith(".dist-info/METADATA")]
                if len(metadata_paths) != 1:
                    continue
                metadata = email.message_from_bytes(archive.read(metadata_paths[0]))
        except (zipfile.BadZipFile, KeyError):
            continue
        if metadata.get("Name") != name or metadata.get("Version") != package.get("version"):
            continue

        def candidate_bytes(candidate_path: str) -> bytes:
            return git(root, "show", ":" + candidate_path) if staged else (root / candidate_path).read_bytes()

        if candidate_bytes(path) != raw:
            raise PolicyError(f"immutable dependency artifact changed: {path}")
        current_project = tomllib.loads(candidate_bytes(project_path).decode())
        current_lock = tomllib.loads(candidate_bytes(lock_path).decode())
        current_binding = current_project.get("tool", {}).get("uv", {}).get("sources", {}).get(name)
        current_dependencies = [
            value
            for value in current_project.get("project", {}).get("dependencies", [])
            if value == name or value.startswith(name + "[")
        ]
        current_packages = [value for value in current_lock.get("package", []) if value.get("name") == name]
        if current_binding != binding or current_dependencies != dependencies or current_packages != packages:
            raise PolicyError(f"immutable dependency binding changed: {path}")
        record.update(
            owner="comment-cleanup-external-ownership",
            state="excluded",
            rule="immutable-dependency",
            blocking_disposition=f"Unchanged upstream dependency pinned by {project_path} and {lock_path} at {base}.",
        )
        excluded.add(path)
    return excluded


def immutable_external_ownership(
    root: Path, base: str, legacy_external: list[dict[str, str]], staged: bool = False
) -> tuple[set[str], list[dict[str, Any]]] | None:
    paths = tracked_paths(root, base)
    if POLICY_PATH not in paths:
        return None
    policy = load_policy_bytes(base_blob(root, base, POLICY_PATH))
    roots = validate_roots(policy)
    rules = validate_rules(policy)
    derived = validate_derived_rules(policy)
    validate_rule_priorities(rules, derived)
    external = validate_external_entries(policy, set(paths))
    validate_evidence(policy, root, base, set(paths))
    priorities: dict[str, int] = {}
    resolved, _ = owned_paths(paths, roots, rules, priorities)
    apply_derived_rules(resolved, derived, root, base, priorities)
    notice_findings = apply_notice_owners(resolved, policy["notices"])
    apply_external_entries(resolved, external)
    if notice_findings or any(isinstance(record["rule"], list) for record in resolved):
        raise PolicyError("immutable ownership contains conflicting owners")
    excluded = {record["path"] for record in resolved if record["state"] == "excluded" and record["rule"] == "external"}
    legacy_eligible = {
        record["path"]
        for record in resolved
        if record["owner"] is None
        and record["rule"] is None
        or record["owner"] == "comment-cleanup-external-ownership"
        and record["rule"] == "external-ownership-mirrors"
    }
    excluded.update(
        path
        for path in legacy_eligible
        if not any(matches(path, entry["selector"]) for entry in external)
        and any(
            matches(path, {"prefix": entry["path"]}) if entry["path"].endswith("/") else path == entry["path"]
            for entry in legacy_external
        )
    )
    excluded.update(immutable_dependency_artifacts(root, base, resolved, rules, legacy_external, staged))
    notices = [
        {
            **notice,
            "whole_file": notice["byte_start"] == 0
            and notice["byte_end"] == len(base_blob(root, base, notice["path"])),
        }
        for notice in policy["notices"]
        if any(matches(notice["path"], entry["selector"]) for entry in external)
    ]
    return excluded, notices


def dependency_findings(policy: dict[str, Any], resolved: list[dict[str, Any]]) -> list[Finding]:
    states = {record["path"]: record["state"] for record in resolved}
    states.update({payload["id"]: payload["state"] for payload in policy["payloads"]})
    findings = []
    for edge in policy["consumer_edges"]:
        producer = edge["producer"]
        consumer = edge["consumer"]
        if edge["state"] == "blocked" and states.get(producer) == "ready":
            findings.append(Finding("SCOPE003", producer, f"blocked consumer dependency: {consumer}"))
        if edge["state"] == "ready" and states.get(consumer) != "ready":
            findings.append(Finding("SCOPE004", consumer, f"unresolved consumer dependency for: {producer}"))
    return findings


def output_path(root: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = (root / value).resolve()
    if root not in path.parents:
        raise PolicyError("output must stay under the repository root")
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(path.relative_to(root))], check=False)
    if ignored.returncode != 0:
        raise PolicyError("output path must be ignored")
    return path


def resolve_drift_base(root: Path, requested: str | None) -> str:
    if requested:
        if len(requested) != 40 or any(char not in "0123456789abcdef" for char in requested):
            raise PolicyError("base must be a full lowercase commit SHA")
        return requested
    base = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--verify", "origin/develop^{commit}"],
        stderr=subprocess.PIPE,
        text=True,
    ).strip()
    merged = subprocess.check_output(
        ["git", "-C", str(root), "merge-base", base, "HEAD"], stderr=subprocess.PIPE, text=True
    ).strip()
    if len(merged) != 40 or any(char not in "0123456789abcdef" for char in merged):
        raise PolicyError("cannot resolve a drift base commit")
    return merged


def pinned_notice_drift_findings(root: Path, base: str) -> list[Finding]:
    try:
        policy = load_policy(root / POLICY_PATH)
    except (OSError, UnicodeError, json.JSONDecodeError, PolicyError) as error:
        return [Finding("SCOPE008", POLICY_PATH, f"cannot load scope policy for drift check: {error}")]
    findings = []
    for entry in policy["notices"]:
        if not isinstance(entry, dict):
            findings.append(Finding("SCOPE008", POLICY_PATH, "notice entry must be an object"))
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not valid_path(path):
            findings.append(Finding("SCOPE008", POLICY_PATH, f"notice has an invalid path: {path!r}"))
            continue
        try:
            frozen = base_blob(root, base, path)
        except subprocess.CalledProcessError:
            findings.append(Finding("SCOPE008", path, "pinned notice is not tracked at the base"))
            continue
        try:
            current = (root / path).read_bytes()
        except OSError:
            findings.append(
                Finding("SCOPE008", path, "pinned notice is missing from the working tree without a pin update")
            )
            continue
        start = entry.get("byte_start")
        end = entry.get("byte_end")
        retained = entry.get("retained_sha256")
        if (
            entry.get("blob_sha256") != hashlib.sha256(current).hexdigest()
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not isinstance(retained, str)
            or start < 0
            or end <= start
            or end > len(current)
            or hashlib.sha256(current[start:end]).hexdigest() != retained
        ):
            if current == frozen:
                detail = "digest pin does not match the pinned file content"
            else:
                detail = "pinned notice modified without updating the digest pin in quality/comment-cleanup-scope.json"
            findings.append(Finding("SCOPE008", path, detail))
    return findings


def run_drift_check(root: Path, base: str | None, policy_path: str) -> int:
    try:
        if policy_path != POLICY_PATH:
            raise PolicyError("policy location is fixed")
        resolved = resolve_drift_base(root, base)
        findings = pinned_notice_drift_findings(root, resolved)
    except (OSError, UnicodeError, PolicyError, subprocess.CalledProcessError) as error:
        print(f"comment-cleanup-scope: configuration failure: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding, file=sys.stderr)
    print(f"comment-cleanup-scope drift: {len(findings)} pinned notice findings")
    return int(bool(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=False, default=os.environ.get("BASE_REF"))
    parser.add_argument("--task-set", required=False, default=None)
    parser.add_argument("--policy", default=POLICY_PATH)
    parser.add_argument("--output")
    parser.add_argument("--check-drift", action="store_true")
    args = parser.parse_args(argv)
    if args.check_drift:
        return run_drift_check(args.root.resolve(), args.base, args.policy)
    if not args.base:
        parser.error("--base is required")
    if not args.task_set:
        parser.error("--task-set is required")
    try:
        root = args.root.resolve()
        policy_path = root / args.policy
        if args.policy != POLICY_PATH:
            raise PolicyError("policy location is fixed")
        policy = load_policy(policy_path)
        paths = tracked_paths(root, args.base)
        path_set = set(paths)
        task_ids = frozen_task_ids(root, args.task_set)
        roots = validate_roots(policy)
        rules = validate_rules(policy, task_ids)
        derived = validate_derived_rules(policy)
        validate_rule_priorities(rules, derived)
        external = validate_external_entries(policy, path_set, task_ids)
        format_classes = validate_format_classes(policy, task_ids)
        validate_evidence(policy, root, args.base, path_set, task_ids)
        validate_payloads_and_edges(policy, path_set, task_ids)
        validate_dispositions(policy, task_ids)
        priorities: dict[str, int] = {}
        resolved, findings = owned_paths(paths, roots, rules, priorities)
        apply_derived_rules(resolved, derived, root, args.base, priorities)
        notice_findings = apply_notice_owners(resolved, policy["notices"])
        apply_external_entries(resolved, external)
        if "quality/comment-policy.json" in path_set:
            try:
                trusted_legacy = load_comment_policy(base_blob(root, args.base, "quality/comment-policy.json"))[
                    "external"
                ]
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                raise PolicyError(f"invalid trusted comment-policy: {error}") from error
            snapshot = immutable_external_ownership(root, args.base, trusted_legacy)
            if snapshot is not None:
                frozen_excluded, _ = snapshot
                immutable_dependency_artifacts(
                    root,
                    args.base,
                    resolved,
                    rules,
                    [entry for entry in trusted_legacy if entry["path"] in frozen_excluded],
                )
        apply_format_classes(resolved, format_classes, root, args.base)
        owned = {record["path"] for record in resolved if record["owner"]}
        findings = [finding for finding in findings if finding.subject not in owned]
        findings.extend(notice_findings)
        findings.extend(evidence_owner_findings(policy, resolved))
        carriers, markers = scan_python_sources(resolved, root, args.base)
        findings.extend(carrier_findings(carriers, policy))
        open_directives, open_todos = unregistered_markers(markers, policy)
        findings.extend(dependency_findings(policy, resolved))
        output = output_path(root, args.output)
        report = {
            "base": args.base,
            "paths": resolved,
            "sha256": hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest(),
            "carriers": carriers,
            "carriers_sha256": hashlib.sha256(json.dumps(carriers, sort_keys=True).encode()).hexdigest(),
            "python_markers": markers,
            "unregistered_python_markers": {"directives": open_directives, "todo_fixme": open_todos},
        }
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        for finding in findings:
            print(finding, file=sys.stderr)
        excluded = sum(record["state"] == "excluded" for record in resolved)
        comment_free = sum(record["state"] == "comment-free" for record in resolved)
        print(
            f"comment-cleanup-scope: {len(resolved)} resolved paths ({excluded} excluded, {comment_free} comment-free), "
            f"{len(carriers)} docstring carriers, "
            f"{open_directives} directive and {open_todos} TODO/FIXME comments in Python sources not yet registered, "
            f"{len(findings)} findings"
        )
        return int(bool(findings))
    except (OSError, UnicodeError, json.JSONDecodeError, PolicyError, subprocess.CalledProcessError) as error:
        print(f"comment-cleanup-scope: configuration failure: {error}", file=sys.stderr)
        return 2
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        print(f"comment-cleanup-scope: configuration failure: malformed policy: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
