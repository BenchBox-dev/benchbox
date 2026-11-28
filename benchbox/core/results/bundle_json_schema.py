from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchbox.core.results.environment import HOST_LOAD_SAMPLE_FIELD_TYPES
from benchbox.core.results.provenance import PLAN_TIERS, VENDOR_PROVENANCE_FIELDS
from benchbox.core.results.query_execution import (
    ROW_COUNT_VALIDATION_BOUND_MAX_CHARS,
    ROW_COUNT_VALIDATION_CHECKS,
    ROW_COUNT_VALIDATION_FIELDS,
    ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS,
    ROW_COUNT_VALIDATION_REQUIRED_FIELDS,
    ROW_COUNT_VALIDATION_STATUSES,
    ROW_COUNT_VALIDATION_STREAM_FIELDS,
    STREAM_CHECKS_BENCHMARKS,
    STREAM_CHECKS_REFERENCE_FIELDS,
)
from benchbox.core.results.schema import SchemaV2Validator
from benchbox.core.results.schema_policy import (
    CURRENT_SCHEMA_VERSION,
    KNOWN_SCHEMA_V2_VERSIONS,
    supports_capture_and_vendor_fields,
    supports_round_trip_timing_fields,
    supports_row_count_validation,
    supports_run_timeout_marker,
    supports_stream_checks,
)
from benchbox.core.results.timing_fields import (
    CLIENT_LINK_TIMING_FIELDS,
    KNOWN_TIMING_METHODS,
    NET_ESTIMATE_ELIGIBLE_STATUS,
    NET_ESTIMATE_MIN_TIMING_METHOD,
    NET_ESTIMATE_STATUSES,
    NETWORKED_ENDPOINT_CLASSES,
    PLATFORM_REQUEST_KINDS,
    PLATFORM_TIMING_DEFINITIONS,
    PLATFORM_TIMING_DURATION_FIELDS,
    PLATFORM_TIMING_FIELDS,
    PLATFORM_TIMING_REASON_STATUSES,
    PLATFORM_TIMING_STATUSES,
    PLATFORM_TIMING_VALID_STATUS,
    QUERY_TIMING_FIELDS,
    RUN_PLATFORM_TIMING_FIELDS,
    RUN_TIMING_FIELDS,
)

SCHEMA_ID = "https://benchbox.dev/schemas/result-bundle-2.schema.json"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_FILE_NAME = "result-bundle.schema.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[3] / "quality" / SCHEMA_FILE_NAME

COMPATIBILITY_PROMISE = (
    "Compatibility promise for major version 2. Within major version 2 the schema changes only in ways that keep "
    "every bundle that was valid under an earlier 2.x version valid: new optional keys, new optional blocks, and "
    "new enum values on fields that consumers are told to treat as open. Such a change raises the minor version "
    "(2.5 to 2.6) and is published at this same URL. A change that removes or renames a required key, changes the "
    "type of an existing key, narrows an accepted value set, or changes the meaning of an existing key requires "
    "major version 3, which is published at a new URL with a 3 in its name; this URL keeps serving major version 2. "
    "Consumers of major version 2 must ignore keys they do not recognise and must not rely on key order."
)

VALIDATOR_ONLY_RULES = (
    "Checks the BenchBox validator (SchemaV2Validator) enforces that JSON Schema cannot express and that this "
    "schema therefore omits: row_count_validation.actual must equal the query's rows, and a PASSED "
    "row_count_validation with an integer expected must have equal expected and actual (both compare two values "
    "in the instance); the validator matches row_count_validation.status case-insensitively while this schema "
    "lists only the normalised upper-case form; JSON Schema cannot tell 2 from 2.0, while the validator rejects a "
    "float for run.timing_method (including the net-estimate check), request_count, result_bytes and "
    "platform_timing.requests[].attempt; row_count_validation.expected and actual are the reverse, because the "
    "validator also coerces integral floats and numeric strings while this schema requires JSON integers; the "
    "vendor publication rules over provenance (a free or trial plan_tier or any preview_features entry needs "
    "vendor_permission, preview_features are refused outright for Databricks, self_run must not be false for "
    "BigQuery, vendor_notice needs sent_on and reference on the platforms whose terms require notice) depend on "
    "the platform name and are enforced by the validator only."
)

_NON_EMPTY_STRING = {"type": "string", "pattern": "\\S"}
_NON_NEGATIVE_NUMBER = {"type": "number", "minimum": 0}
_NON_NEGATIVE_INT = {"type": "integer", "minimum": 0}


def _version_in(versions: list[str]) -> dict[str, Any]:
    return {
        "anyOf": [
            {
                "required": ["result_schema_version"],
                "properties": {"result_schema_version": {"enum": versions}},
            },
            {
                "not": {"required": ["result_schema_version"]},
                "required": ["version"],
                "properties": {"version": {"enum": versions}},
            },
        ]
    }


def _versions_where(predicate: Any) -> list[str]:
    return [version for version in KNOWN_SCHEMA_V2_VERSIONS if predicate(version)]


def _object(required: tuple[str, ...], properties: dict[str, Any], *, closed: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "object"}
    if required:
        block["required"] = list(required)
    if properties:
        block["properties"] = properties
    if closed:
        block["additionalProperties"] = False
    return block


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _platform_timing_schema() -> dict[str, Any]:
    durations = {name: _nullable(_NON_NEGATIVE_NUMBER) for name in PLATFORM_TIMING_DURATION_FIELDS}
    request = _object(
        ("attempt", "kind", "fields"),
        {
            "id": _nullable(_NON_EMPTY_STRING),
            "attempt": {"type": "integer", "minimum": 1},
            "kind": {"enum": sorted(PLATFORM_REQUEST_KINDS)},
            "fields": {
                "type": "object",
                "additionalProperties": {"type": ["number", "boolean", "null"]},
            },
        },
        closed=True,
    )
    properties = {
        "source": _nullable(_NON_EMPTY_STRING),
        "requests": _nullable({"type": "array", "items": request}),
        "cache_hit": _nullable({"type": "boolean"}),
        "status": {"enum": sorted(PLATFORM_TIMING_STATUSES)},
        "reason": _nullable(_NON_EMPTY_STRING),
        **durations,
    }
    assert set(properties) == set(PLATFORM_TIMING_FIELDS)
    block = _object(("status",), properties, closed=True)
    block["allOf"] = [
        {
            "if": {"required": ["status"], "properties": {"status": {"const": PLATFORM_TIMING_VALID_STATUS}}},
            "then": {"required": ["platform_ms"], "properties": {"platform_ms": _NON_NEGATIVE_NUMBER}},
        },
        {
            "if": {"required": ["status"], "properties": {"status": {"enum": sorted(PLATFORM_TIMING_REASON_STATUSES)}}},
            "then": {"required": ["reason"], "properties": {"reason": _NON_EMPTY_STRING}},
        },
    ]
    return block


def _run_platform_timing_schema() -> dict[str, Any]:
    properties = {
        "source": _NON_EMPTY_STRING,
        "definition": {"enum": sorted(PLATFORM_TIMING_DEFINITIONS)},
        "coverage": {"type": "number", "minimum": 0, "maximum": 1},
    }
    assert set(properties) == set(RUN_PLATFORM_TIMING_FIELDS)
    return _object(tuple(properties), properties, closed=True)


def _run_schema(validator: type[SchemaV2Validator]) -> dict[str, Any]:
    return _object(
        validator.RUN_REQUIRED,
        {
            "timing_method": {"enum": sorted(KNOWN_TIMING_METHODS)},
            "net_estimate_status": {"enum": sorted(NET_ESTIMATE_STATUSES)},
            "platform_timing": _nullable(_run_platform_timing_schema()),
        },
    )


def _row_count_validation_schema() -> dict[str, Any]:
    properties = {
        "status": {"enum": sorted(ROW_COUNT_VALIDATION_STATUSES)},
        "expected": _nullable(_NON_NEGATIVE_INT),
        "actual": _nullable(_NON_NEGATIVE_INT),
        "error": {"type": "string", "maxLength": ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS},
        "warning": {"type": "string", "maxLength": ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS},
        "check": {"enum": sorted(ROW_COUNT_VALIDATION_CHECKS)},
        "bound": {"type": "string", "minLength": 1, "maxLength": ROW_COUNT_VALIDATION_BOUND_MAX_CHARS},
        "seed": _nullable(_NON_NEGATIVE_INT),
        "streams_index": _nullable(_NON_NEGATIVE_INT),
    }
    assert set(properties) == set(ROW_COUNT_VALIDATION_FIELDS)
    schema = _object(tuple(sorted(ROW_COUNT_VALIDATION_REQUIRED_FIELDS)), properties, closed=True)
    schema["dependentRequired"] = {"check": ["bound"], "bound": ["check"]}
    return schema


def _validation_schema() -> dict[str, Any]:
    sha256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    reference = _object(
        tuple(sorted(STREAM_CHECKS_REFERENCE_FIELDS)),
        {
            "benchmark": {"enum": sorted(STREAM_CHECKS_BENCHMARKS)},
            "scale_factor": {"type": "number", "exclusiveMinimum": 0},
            "file": _NON_EMPTY_STRING,
            "sha256": sha256,
            "data_stamp": sha256,
            "query_generation_hash": sha256,
            "sample_draws": {"type": "integer", "minimum": 1},
        },
        closed=True,
    )
    stream_checks = _object(
        ("references", "checks"),
        {
            "references": {"type": "array", "items": reference},
            "checks": {
                "type": "object",
                "propertyNames": {"enum": sorted(ROW_COUNT_VALIDATION_CHECKS)},
                "additionalProperties": {"type": "integer", "minimum": 1},
            },
        },
        closed=True,
    )
    return _object(("stream_checks",), {"stream_checks": stream_checks}, closed=True)


def _query_schema() -> dict[str, Any]:
    return _object(
        (),
        {
            "request_count": _nullable(_NON_NEGATIVE_INT),
            "result_bytes": _nullable(_NON_NEGATIVE_INT),
            "platform_timing": _nullable(_platform_timing_schema()),
            "row_count_validation": _nullable(_row_count_validation_schema()),
        },
    )


def _host_load_sample_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, types in HOST_LOAD_SAMPLE_FIELD_TYPES.items():
        member: dict[str, Any] = {"type": list(types)}
        if "array" in types:
            member["items"] = {"type": "number"}
        properties[name] = member
    return _object(tuple(HOST_LOAD_SAMPLE_FIELD_TYPES), properties, closed=True)


def _environment_schema() -> dict[str, Any]:
    host_load = _object(("start", "end"), {point: _host_load_sample_schema() for point in ("start", "end")})
    client_link = _object(
        (),
        {
            "probed_at": _nullable(_NON_EMPTY_STRING),
            "platform_timing": _nullable(_platform_timing_schema()),
        },
    )
    return _object((), {"host_load": host_load, "client_link": client_link})


def _execution_schema() -> dict[str, Any]:
    timeout = _object(
        ("timeout_seconds", "phase"),
        {
            "timeout_seconds": {"type": "integer", "minimum": 1},
            "phase": {"type": "string", "minLength": 1},
            "query_id": {"type": "string", "minLength": 1},
        },
        closed=True,
    )
    return _object(
        (),
        {"config_capture": {"type": "integer", "minimum": 1}, "timeout": timeout},
    )


def _provenance_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "plan_tier": {"enum": list(PLAN_TIERS)},
        "preview_features": {"type": "array", "items": _NON_EMPTY_STRING},
        "vendor_permission": _NON_EMPTY_STRING,
        "vendor_notice": _object(
            (),
            {
                "sent_on": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                "reference": _NON_EMPTY_STRING,
            },
        ),
        "self_run": {"type": "boolean"},
    }
    assert set(properties) == set(VENDOR_PROVENANCE_FIELDS)
    return _object((), properties)


def _forbid_keys(keys: tuple[str, ...], *, under: str | None = None) -> dict[str, Any]:
    forbidden = {"not": {"anyOf": [{"required": [key]} for key in keys]}}
    if under is None:
        return forbidden
    return {"properties": {under: forbidden}}


def _version_gates() -> list[dict[str, Any]]:
    without_row_count = _versions_where(lambda v: not supports_row_count_validation(v))
    without_timing = _versions_where(lambda v: not supports_round_trip_timing_fields(v))
    without_capture = _versions_where(lambda v: not supports_capture_and_vendor_fields(v))
    without_stream_checks = _versions_where(lambda v: not supports_stream_checks(v))
    with_timing = _versions_where(supports_round_trip_timing_fields)
    gates: list[dict[str, Any]] = []
    if without_row_count:
        gates.append(
            {
                "if": _version_in(without_row_count),
                "then": {
                    "properties": {
                        "queries": {"items": {"not": {"required": ["row_count_validation"]}}},
                    }
                },
            }
        )
    if without_timing:
        gates.append(
            {
                "if": _version_in(without_timing),
                "then": {
                    "properties": {
                        "run": _forbid_keys(RUN_TIMING_FIELDS),
                        "queries": {"items": _forbid_keys(QUERY_TIMING_FIELDS)},
                        "environment": _forbid_keys(CLIENT_LINK_TIMING_FIELDS, under="client_link"),
                    }
                },
            }
        )
    if without_capture:
        gates.append(
            {
                "if": _version_in(without_capture),
                "then": {
                    "properties": {
                        "execution": _forbid_keys(("config_capture",)),
                        "provenance": _forbid_keys(VENDOR_PROVENANCE_FIELDS),
                    }
                },
            }
        )
    if without_stream_checks:
        gates.append(
            {
                "if": _version_in(without_stream_checks),
                "then": {
                    "properties": {
                        "queries": {
                            "items": {
                                "properties": {
                                    "row_count_validation": _forbid_keys(
                                        tuple(sorted(ROW_COUNT_VALIDATION_STREAM_FIELDS))
                                    )
                                }
                            }
                        },
                    },
                    **_forbid_keys(("validation",)),
                },
            }
        )
    without_timeout_marker = _versions_where(lambda v: not supports_run_timeout_marker(v))
    if without_timeout_marker:
        gates.append(
            {
                "if": _version_in(without_timeout_marker),
                "then": {"properties": {"execution": _forbid_keys(("timeout",))}},
            }
        )
    if with_timing:
        networked_request_fields = {
            "if": {
                "required": ["status"],
                "properties": {
                    "status": {"const": "SUCCESS"},
                    "test_type": {"not": {"const": "maintenance"}},
                },
            },
            "then": {
                "properties": {
                    "request_count": _NON_NEGATIVE_INT,
                    "result_bytes": _NON_NEGATIVE_INT,
                },
                "required": ["request_count", "result_bytes"],
            },
        }
        gates.append(
            {
                "if": _version_in(with_timing),
                "then": {
                    "properties": {
                        "run": {
                            "required": ["timing_method", "net_estimate_status"],
                            "properties": {
                                "timing_method": {"enum": sorted(KNOWN_TIMING_METHODS)},
                                "net_estimate_status": {"enum": sorted(NET_ESTIMATE_STATUSES)},
                            },
                        }
                    },
                    "allOf": [
                        {
                            "if": {
                                "required": ["queries"],
                                "properties": {
                                    "queries": {
                                        "contains": {
                                            "required": ["platform_timing"],
                                            "properties": {"platform_timing": {"type": "object"}},
                                        }
                                    }
                                },
                            },
                            "then": {
                                "properties": {
                                    "run": {
                                        "required": ["platform_timing"],
                                        "properties": {"platform_timing": {"type": "object"}},
                                    }
                                }
                            },
                        },
                        {
                            "if": {
                                "properties": {
                                    "run": {
                                        "required": ["timing_method"],
                                        "properties": {"timing_method": {"minimum": NET_ESTIMATE_MIN_TIMING_METHOD}},
                                    }
                                }
                            },
                            "then": {"properties": {"queries": {"items": networked_request_fields}}},
                        },
                    ],
                },
            }
        )
    return gates


def _net_estimate_gate() -> dict[str, Any]:
    return {
        "if": {
            "required": ["run"],
            "properties": {
                "run": {
                    "required": ["net_estimate_status"],
                    "properties": {"net_estimate_status": {"const": NET_ESTIMATE_ELIGIBLE_STATUS}},
                }
            },
        },
        "then": {
            "required": ["platform"],
            "properties": {
                "run": {
                    "required": ["timing_method"],
                    "properties": {"timing_method": {"type": "integer", "minimum": NET_ESTIMATE_MIN_TIMING_METHOD}},
                },
                "platform": {
                    "required": ["deployment"],
                    "properties": {
                        "deployment": {
                            "type": "object",
                            "required": ["endpoint_class"],
                            "properties": {"endpoint_class": {"enum": sorted(NETWORKED_ENDPOINT_CLASSES)}},
                        }
                    },
                },
            },
        },
    }


def _version_equality_gates() -> list[dict[str, Any]]:
    return [
        {
            "if": {
                "required": ["result_schema_version", "version"],
                "properties": {"result_schema_version": {"const": version}},
            },
            "then": {"properties": {"version": {"const": version}}},
        }
        for version in KNOWN_SCHEMA_V2_VERSIONS
    ]


def build_result_bundle_schema() -> dict[str, Any]:
    validator = SchemaV2Validator
    required_top = tuple(key for key in validator.REQUIRED_KEYS if key != "result_schema_version")
    known_top = (*validator.REQUIRED_KEYS, *validator.OPTIONAL_KEYS)
    version_property = {"enum": list(KNOWN_SCHEMA_V2_VERSIONS)}

    properties: dict[str, Any] = {key: {} for key in sorted(known_top)}
    properties["result_schema_version"] = {
        **version_property,
        "description": "Result schema version of this bundle. Legacy bundles may carry it as 'version' alone.",
    }
    properties["version"] = {
        **version_property,
        "description": "Legacy spelling of result_schema_version; must match it when both are present.",
    }
    properties["run"] = _run_schema(validator)
    properties["benchmark"] = _object(validator.BENCHMARK_REQUIRED, {})
    properties["platform"] = _object(validator.PLATFORM_REQUIRED, {})
    properties["summary"] = _object(validator.SUMMARY_REQUIRED, {})
    properties["queries"] = {"type": "array", "items": _query_schema()}
    properties["environment"] = _environment_schema()
    properties["execution"] = _execution_schema()
    properties["provenance"] = _provenance_schema()
    properties["validation"] = _validation_schema()

    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "BenchBox result bundle",
        "result_schema_version": CURRENT_SCHEMA_VERSION,
        "description": f"{COMPATIBILITY_PROMISE} {VALIDATOR_ONLY_RULES}",
        "type": "object",
        "required": list(required_top),
        "anyOf": [{"required": ["result_schema_version"]}, {"required": ["version"]}],
        "properties": properties,
        "additionalProperties": False,
        "allOf": [*_version_gates(), _net_estimate_gate(), *_version_equality_gates()],
    }


def render_result_bundle_schema() -> str:
    return json.dumps(build_result_bundle_schema(), indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def check_result_bundle_schema(path: Path = DEFAULT_OUTPUT) -> bool:
    return path.is_file() and path.read_text(encoding="utf-8") == render_result_bundle_schema()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the result bundle JSON Schema from SchemaV2Validator.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the committed schema differs from generation")
    args = parser.parse_args(argv)
    if args.check:
        if check_result_bundle_schema(args.output):
            return 0
        sys.stderr.write(
            f"{args.output} is out of date with SchemaV2Validator; regenerate it with 'python -m benchbox.core.results.bundle_json_schema' and commit the result.\n"
        )
        return 1
    args.output.write_text(render_result_bundle_schema(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
