#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import datetime
import decimal
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = ROOT / "tests/fixtures/explorer_browser/source"
OUTPUT_ROOT_PREFIX = "benchbox-large-browser-fixture-"
PANDAS_SOURCE_BUNDLE = "tpch-pandas-sf0.01-20260826-8bde2222.json"
DUCKDB_SOURCE_BUNDLE = "tpch-duckdb-sf0.01-20260826-8a57a5a8.json"
DATAFUSION_SOURCE_BUNDLE = "tpch-datafusion-sf0.01-20260826-e8a7d048.json"
POLARS_SOURCE_BUNDLE = "tpch-polars-sf0.01-20260824-51ccc406.json"
PANDAS_STANDARD_FIXTURE_RUN_ID = "8bde2222-fixture-standard"
SYNTHETIC_CANONICAL_SOURCES = frozenset({PANDAS_SOURCE_BUNDLE})
SOURCE_BINDINGS = (
    PANDAS_SOURCE_BUNDLE,
    DUCKDB_SOURCE_BUNDLE,
    DATAFUSION_SOURCE_BUNDLE,
    POLARS_SOURCE_BUNDLE,
    "tpch-spark-sf0.01-20260826-2fd3b12b.json",
    "tpch-cedardb-sf0.01-20260825-cf0d9e4d.json",
)
FIXTURE_ROLES = {
    "8a57a5a8": "duckdb",
    "8a57a5a8-tuned": "duckdbTuned",
    "8a57a5a8-community": "duckdbCommunity",
    "8a57a5a8-sf01": "duckdbSf01",
    "8a57a5a8-env-aws-cloud": "awsCloud",
    "8a57a5a8-env-container-local": "containerLocal",
    "8a57a5a8-env-gcp-serverless": "gcpServerless",
    "8a57a5a8-zero-timing": "zeroTiming",
    PANDAS_STANDARD_FIXTURE_RUN_ID: "pandas",
    f"{PANDAS_STANDARD_FIXTURE_RUN_ID}-vendor": "pandasVendor",
    f"{PANDAS_STANDARD_FIXTURE_RUN_ID}-tuned": "pandasTuned",
    "e8a7d048": "datafusion",
    "2fd3b12b": "spark",
    "cf0d9e4d": "cedardb",
    "e8a7d048-partial-query": "datafusionPartial",
    "d4ec318a": "starSchema",
    "51ccc406": "polars",
    "51ccc406-tuned": "polarsTuned",
}
SYNTHETIC_TUNING = {
    "tuning_mode": "tuned",
    "memory_limit": "4GB",
    "threads": 4,
    "notes": "Synthetic tuning config produced by the browser-test fixture generator. "
    "Do not treat as a real contribution.",
}
UNAVAILABLE = {"source": "unavailable", "collection_status": "unavailable"}


def _embedded(connection_mode: str) -> dict:
    return {
        "deployment_type": "embedded",
        "connection_mode": connection_mode,
        "endpoint_class": "embedded_process",
        "metadata_source": "observed",
        "collection_status": "available",
    }


def _inferred_storage(table_format: str) -> dict:
    return {"table_format": table_format, "source": "inferred", "collection_status": "partial"}


LOCAL_PLATFORM_METADATA = {
    "duckdb": ("local_process", _embedded("file"), _inferred_storage("duckdb_native")),
    "datafusion": ("dataframe_process", _embedded("dataframe"), _inferred_storage("parquet")),
    "polars": ("dataframe_process", _embedded("dataframe"), _inferred_storage("parquet")),
    "pandas": ("dataframe_process", _embedded("dataframe"), _inferred_storage("parquet")),
}


@dataclass(frozen=True)
class Variant:
    source: str
    subdir: str
    derived: str
    mutate: Callable[[dict], dict]
    sidecars: dict[str, dict] = field(default_factory=dict)


def _coalesce(value, fallback):
    return fallback if value is None else value


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _suffixed_run_id(run: dict | None, suffix: str) -> str:
    return f"{_coalesce((run or {}).get('id'), 'run')}-{suffix}"


def _with_run_suffix(bundle: dict, suffix: str) -> dict:
    mutated = copy.deepcopy(bundle)
    if mutated.get("run"):
        mutated["run"]["id"] = _suffixed_run_id(mutated["run"], suffix)
    return mutated


def _as_synthetic_standard_pandas(bundle: dict) -> dict:
    mutated = copy.deepcopy(bundle)
    mutated["benchmark"] = {**(mutated.get("benchmark") or {}), "test_type": "standard"}
    mutated["run"] = {**(mutated.get("run") or {}), "id": PANDAS_STANDARD_FIXTURE_RUN_ID}
    return mutated


def _set_tuned(bundle: dict) -> dict:
    bundle["config"] = {**(bundle.get("config") or {}), "tuning_mode": "tuned"}
    return bundle


def _tuned(bundle: dict) -> dict:
    return _with_run_suffix(_set_tuned(copy.deepcopy(bundle)), "tuned")


def _community(bundle: dict) -> dict:
    mutated = _with_run_suffix(bundle, "community")
    mutated["provenance"] = {**(mutated.get("provenance") or {}), "funding": "employer"}
    return mutated


def _pandas_vendor(bundle: dict) -> dict:
    mutated = _as_synthetic_standard_pandas(bundle)
    mutated["run"]["id"] = f"{mutated['run']['id']}-vendor"
    return mutated


def _pandas_tuned(bundle: dict) -> dict:
    mutated = _set_tuned(_as_synthetic_standard_pandas(bundle))
    mutated["run"]["id"] = f"{mutated['run']['id']}-tuned"
    return mutated


def _scale_sf01(bundle: dict) -> dict:
    mutated = copy.deepcopy(bundle)
    if mutated.get("benchmark"):
        mutated["benchmark"]["scale_factor"] = 0.1
    return _with_run_suffix(mutated, "sf01")


def _omit_measurement_query(bundle: dict, query_id: str) -> dict:
    mutated = copy.deepcopy(bundle)
    queries = mutated.get("queries") if isinstance(mutated.get("queries"), list) else []
    remaining = [query for query in queries if str(_coalesce(_object(query).get("id"), "")) != query_id]
    mutated["queries"] = remaining
    passed = sum(1 for query in remaining if str(_coalesce(_object(query).get("status"), "")).upper() == "SUCCESS")
    summary = mutated.get("summary")
    if summary and summary.get("queries"):
        summary["queries"] = {
            **summary["queries"],
            "total": len(remaining),
            "passed": passed,
            "failed": len(remaining) - passed,
        }
    return mutated


def _partial_query(bundle: dict) -> dict:
    mutated = _omit_measurement_query(bundle, "22")
    mutated["platform"] = {**(mutated.get("platform") or {}), "name": "DataFusion Partial"}
    return _with_run_suffix(mutated, "partial-query")


def _zero_timing(bundle: dict) -> dict:
    mutated = copy.deepcopy(bundle)
    mutated["run"] = {
        **(mutated.get("run") or {}),
        "id": _suffixed_run_id(mutated.get("run"), "zero-timing"),
        "query_time_ms": 0,
    }
    mutated["platform"] = {**(mutated.get("platform") or {}), "name": "DuckDB Zero Timing"}
    mutated["queries"] = []
    mutated["summary"] = {
        **(mutated.get("summary") or {}),
        "queries": {"total": 0, "passed": 0, "failed": 0},
        "timing": {
            "total_ms": 0,
            "avg_ms": 0,
            "min_ms": 0,
            "max_ms": 0,
            "geometric_mean_ms": None,
            "stdev_ms": 0,
            "p90_ms": 0,
            "p95_ms": 0,
            "p99_ms": 0,
        },
    }
    mutated["summary"].pop("tpc_metrics", None)
    mutated.pop("environment", None)
    return mutated


@dataclass(frozen=True)
class EnvironmentFacet:
    run_suffix: str
    platform_name: str
    runtime_type: str
    deployment: dict
    cloud: dict
    compute: dict
    storage: dict
    container: dict | None = None

    def __call__(self, bundle: dict) -> dict:
        mutated = copy.deepcopy(bundle)
        environment = _object(mutated.get("environment"))
        mutated["run"] = {**(mutated.get("run") or {}), "id": _suffixed_run_id(mutated.get("run"), self.run_suffix)}
        mutated["platform"] = {
            **_object(mutated.get("platform")),
            "name": self.platform_name,
            "deployment": self.deployment,
            "cloud": self.cloud,
            "compute": self.compute,
            "storage": self.storage,
        }
        mutated["environment"] = {
            **environment,
            "platform_runtime": {
                "runtime_type": self.runtime_type,
                "collection_status": "available",
                "source": "fixture",
            },
            **({"container": self.container} if self.container else {}),
        }
        return mutated


def _fixture(**values: str) -> dict:
    return {**values, "source": "fixture", "collection_status": "available"}


def _endpoint_deployment(deployment_type: str, connection_mode: str, endpoint_class: str) -> dict:
    return {
        "deployment_type": deployment_type,
        "connection_mode": connection_mode,
        "endpoint_class": endpoint_class,
        "metadata_source": "fixture",
        "collection_status": "available",
    }


def _submission_manifest(bundle: str, submitted_at: str, note: str) -> dict:
    return {
        "submission-manifest.json": {
            "version": "1",
            "bundle": bundle,
            "submitted_at": submitted_at,
            "contributor": "browser-functional-test-fixture",
            "note": note,
        }
    }


VARIANTS = (
    Variant(
        PANDAS_SOURCE_BUNDLE,
        "synthetic",
        "tpch-fixture-pandas-standard-sf0.01-20260826.json",
        _as_synthetic_standard_pandas,
    ),
    Variant(
        DUCKDB_SOURCE_BUNDLE,
        "community",
        "tpch-duckdb-sf0.01-20260826-community.json",
        _community,
        _submission_manifest(
            "tpch-duckdb-sf0.01-20260826-community.json",
            "2026-04-18T00:00:00Z",
            "Synthetic community-submission variant produced by the browser-test fixture generator. "
            "Do not treat as a real contribution.",
        ),
    ),
    Variant(
        DUCKDB_SOURCE_BUNDLE,
        "tuned",
        "tpch-duckdb-sf0.01-20260826-tuned.json",
        _tuned,
        {"tpch-duckdb-sf0.01-20260826-tuned.tuning.json": SYNTHETIC_TUNING},
    ),
    Variant(PANDAS_SOURCE_BUNDLE, "vendor", "tpch-pandas-sf0.01-20260826-vendor.json", _pandas_vendor),
    Variant(
        PANDAS_SOURCE_BUNDLE,
        "tuned-pandas",
        "tpch-pandas-sf0.01-20260826-tuned.json",
        _pandas_tuned,
        {"tpch-pandas-sf0.01-20260826-tuned.tuning.json": SYNTHETIC_TUNING},
    ),
    Variant(
        POLARS_SOURCE_BUNDLE,
        "tuned-polars",
        "tpch-polars-sf0.01-20260824-tuned.json",
        _tuned,
        {"tpch-polars-sf0.01-20260824-tuned.tuning.json": SYNTHETIC_TUNING},
    ),
    Variant(DUCKDB_SOURCE_BUNDLE, "sf01", "tpch-duckdb-sf0.1-20260826-scale.json", _scale_sf01),
    Variant(
        DATAFUSION_SOURCE_BUNDLE,
        "partial-query",
        "tpch-datafusion-partial-sf0.01-20260826-query-gap.json",
        _partial_query,
    ),
    Variant(DUCKDB_SOURCE_BUNDLE, "zero-timing", "tpch-fixture-zero-timing-sf0.01-20260826.json", _zero_timing),
    Variant(
        DUCKDB_SOURCE_BUNDLE,
        "environment/aws-cloud",
        "tpch-fixture-aws-sf0.01-20260826-environment.json",
        EnvironmentFacet(
            "env-aws-cloud",
            "Fixture AWS SQL",
            "managed_cloud",
            _endpoint_deployment("managed_cloud", "cloud_endpoint", "cloud_endpoint"),
            _fixture(provider="aws", region="us-east-1"),
            _fixture(node_type="m6i.large"),
            _fixture(table_format="parquet"),
        ),
        _submission_manifest(
            "tpch-fixture-aws-sf0.01-20260826-environment.json",
            "2026-05-03T00:00:00Z",
            "Synthetic AWS environment-facet variant produced by the browser-test fixture generator. "
            "Do not treat as a real cloud benchmark result.",
        ),
    ),
    Variant(
        DUCKDB_SOURCE_BUNDLE,
        "environment/gcp-serverless",
        "tpch-fixture-gcp-sf0.01-20260826-environment.json",
        EnvironmentFacet(
            "env-gcp-serverless",
            "Fixture GCP Serverless",
            "serverless",
            _endpoint_deployment("serverless", "cloud_endpoint", "cloud_endpoint"),
            _fixture(provider="gcp", region="us-central1"),
            _fixture(serverless_slots="serverless-slots-4"),
            _fixture(table_format="parquet"),
        ),
        _submission_manifest(
            "tpch-fixture-gcp-sf0.01-20260826-environment.json",
            "2026-05-03T00:00:00Z",
            "Synthetic GCP serverless environment-facet variant produced by the browser-test fixture generator. "
            "Do not treat as a real cloud benchmark result.",
        ),
    ),
    Variant(
        DUCKDB_SOURCE_BUNDLE,
        "environment/container-local",
        "tpch-fixture-container-sf0.01-20260826-environment.json",
        EnvironmentFacet(
            "env-container-local",
            "Fixture Container SQL",
            "docker_container",
            _endpoint_deployment("embedded", "localhost", "localhost_port"),
            dict(UNAVAILABLE),
            _fixture(node_type="container-cpu-10"),
            _fixture(table_format="duckdb_native"),
            _fixture(image="benchbox/results-explorer-fixture:local", runtime="docker"),
        ),
    ),
)


def _platform_key(bundle: dict) -> str:
    name = str(_coalesce(_object(bundle.get("platform")).get("name"), "")).lower()
    return next((key for key in ("datafusion", "polars", "pandas") if key in name), "duckdb")


def _client_host_from_legacy(environment: dict) -> dict:
    keys = ("os", "arch", "cpu_count", "memory_gb", "python", "machine_id")
    return {key: environment[key] for key in keys if environment.get(key) is not None}


def normalize_environment(bundle: dict) -> dict:
    mutated = copy.deepcopy(bundle)
    runtime_type, deployment, storage = LOCAL_PLATFORM_METADATA[_platform_key(mutated)]
    environment = _object(mutated.get("environment"))
    platform = _object(mutated.get("platform"))
    mutated["environment"] = {
        **environment,
        "client_host": _coalesce(environment.get("client_host"), _client_host_from_legacy(environment)),
        "platform_runtime": _coalesce(
            environment.get("platform_runtime"),
            {"runtime_type": runtime_type, "collection_status": "available", "source": "observed"},
        ),
    }
    mutated["platform"] = {
        **platform,
        "deployment": _coalesce(platform.get("deployment"), copy.deepcopy(deployment)),
        "cloud": _coalesce(platform.get("cloud"), dict(UNAVAILABLE)),
        "compute": _coalesce(platform.get("compute"), dict(UNAVAILABLE)),
        "storage": _coalesce(platform.get("storage"), copy.deepcopy(storage)),
    }
    return mutated


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source_bindings(source_root: Path = SOURCE_ROOT) -> None:
    for fixture_bundle in SOURCE_BINDINGS:
        manifest_path = source_root / "provenance" / fixture_bundle.replace(".json", ".source.manifest.json")
        if not manifest_path.is_file():
            raise RuntimeError(f"source binding manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fixture_path = source_root / "bundles" / fixture_bundle
        source_path = ROOT / str(manifest.get("source_bundle") or "")
        if manifest.get("fixture_bundle") != fixture_bundle or not fixture_path.is_file() or not source_path.is_file():
            raise RuntimeError(f"invalid source binding in {manifest_path}")
        expected = manifest.get("bundle_hash")
        if not isinstance(expected, str) or _sha256(fixture_path) != expected or _sha256(source_path) != expected:
            raise RuntimeError(f"source binding hash mismatch for {fixture_bundle}")


def _js_number(value: float) -> str:
    if value == 0:
        return "0"
    sign, digits, exponent = decimal.Decimal(repr(value)).normalize().as_tuple()
    text = "".join(map(str, digits))
    size = len(text)
    point = exponent + size
    prefix = "-" if sign else ""
    if size <= point <= 21:
        return prefix + text + "0" * (point - size)
    if 0 < point <= 21:
        return prefix + text[:point] + "." + text[point:]
    if -6 < point <= 0:
        return prefix + "0." + "0" * -point + text
    mantissa = text[0] + ("." + text[1:] if size > 1 else "")
    return f"{prefix}{mantissa}e{'+' if point >= 1 else '-'}{abs(point - 1)}"


def js_stringify(value, depth: int = 0) -> str:
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, float):
        return _js_number(value)
    if isinstance(value, (int, str)):
        return json.dumps(value, ensure_ascii=False)
    inner = "  " * (depth + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [
            f"{inner}{json.dumps(key, ensure_ascii=False)}: {js_stringify(val, depth + 1)}"
            for key, val in value.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + "  " * depth + "}"
    if not value:
        return "[]"
    items = [inner + js_stringify(item, depth + 1) for item in value]
    return "[\n" + ",\n".join(items) + "\n" + "  " * depth + "]"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(js_stringify(payload), encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def stage_sources(source_root: Path, bundles_out: Path) -> None:
    sources = sorted(
        path
        for path in (source_root / "bundles").glob("*.json")
        if path.is_file() and path.name not in SYNTHETIC_CANONICAL_SOURCES
    )
    if not sources:
        raise RuntimeError(f"no source bundles found under {source_root / 'bundles'}")
    for path in sources:
        _write_json(bundles_out / path.name, normalize_environment(_load(path)))
    for variant in VARIANTS:
        target = bundles_out / variant.subdir
        target.mkdir(parents=True, exist_ok=True)
        bundle = _load(source_root / "bundles" / variant.source)
        _write_json(target / variant.derived, normalize_environment(variant.mutate(bundle)))
        for name, payload in variant.sidecars.items():
            _write_json(target / name, payload)


def short_ids(db_path: Path) -> dict[str, str]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute("select result_id, short_id from short_ids").fetchall()
    return dict(rows)


def fixture_ids(data_dir: Path) -> dict:
    by_role: dict[str, str] = {}
    unclaimed = []
    for path in sorted((data_dir / "bundles").glob("*.json")):
        if path.name.endswith(".tuning.json"):
            continue
        result_id = path.name.removesuffix(".json")
        run_id = _object(_load(path).get("run")).get("id")
        role = FIXTURE_ROLES.get(run_id)
        if role is None:
            unclaimed.append(f"{result_id} (run.id={run_id})")
            continue
        if role in by_role:
            raise RuntimeError(f"two fixtures claim role {role}: {by_role[role]} and {result_id}")
        by_role[role] = result_id
    if unclaimed:
        raise RuntimeError(f"fixture(s) with no role in FIXTURE_ROLES: {', '.join(unclaimed)}")
    missing = [role for role in FIXTURE_ROLES.values() if role not in by_role]
    if missing:
        raise RuntimeError(f"FIXTURE_ROLES names role(s) this build did not produce: {', '.join(missing)}")
    ids = short_ids(data_dir / "results.duckdb")
    payload: dict[str, dict[str, str]] = {"ids": {}, "shortIds": {}}
    for role, result_id in sorted(by_role.items()):
        if result_id not in ids:
            raise RuntimeError(f"the read model has no short_id for {result_id}")
        payload["ids"][role] = result_id
        payload["shortIds"][role] = ids[result_id]
    return payload


def generate(output_root: Path, source_root: Path = SOURCE_ROOT) -> Path:
    if not output_root.name.startswith(OUTPUT_ROOT_PREFIX):
        raise RuntimeError(f"the output root must be a dedicated {OUTPUT_ROOT_PREFIX}* directory")
    verify_source_bindings(source_root)
    source_out = output_root / "source"
    data_out = output_root / "data"
    for directory in (source_out, data_out):
        if directory.exists():
            shutil.rmtree(directory)
    (source_out / "bundles").mkdir(parents=True)
    data_out.mkdir(parents=True)
    stage_sources(source_root, source_out / "bundles")
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--",
            "python",
            "_project/scripts/explorer_publish.py",
            "build",
            "--data-dir",
            str(source_out),
            "--output",
            str(data_out),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"explorer publish build failed ({proc.returncode}): {(proc.stdout + proc.stderr)[-2000:]}")
    if not (data_out / "results.duckdb").is_file():
        raise RuntimeError(f"the pipeline produced no {data_out / 'results.duckdb'}")
    (data_out / "fixture-ids.json").write_text(json.dumps(fixture_ids(data_out), indent=2) + "\n", encoding="utf-8")
    return data_out


def _normalize(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(val) for key, val in sorted(value.items())}
    return value


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def digest(db_path: Path) -> dict:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    tables = sorted(row[0] for row in con.execute("SHOW TABLES").fetchall())
    payload = {}
    for table in tables:
        result = con.execute(f"SELECT * FROM {_quote_ident(table)} ORDER BY ALL")
        columns = [description[0] for description in result.description]
        rows = [[_normalize(value) for value in row] for row in result.fetchall()]
        payload[table] = {"columns": columns, "rows": rows}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {"sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "tables": tables}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browser fixture corpus for the site-inputs bundle")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").add_argument("output_root", type=Path)
    for name in ("short-ids", "digest"):
        commands.add_parser(name).add_argument("db", type=Path)
    args = parser.parse_args(argv)
    if args.command == "generate":
        print(generate(args.output_root.resolve()))
    elif args.command == "short-ids":
        sys.stdout.write(json.dumps(short_ids(args.db)))
    else:
        print(json.dumps(digest(args.db)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
