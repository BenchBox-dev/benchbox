import json
from pathlib import Path
from typing import Any

import yaml

from benchbox.core.manifest.models import (
    ConvertedFileEntry,
    FileEntry,
    ManifestV1,
    ManifestV2,
    PlanMetadata,
    TableFormats,
)
from benchbox.utils.file_format import is_csv_format, is_parquet_format, is_tpc_format


def detect_version(manifest_dict: dict[str, Any]) -> int:
    if "version" in manifest_dict:
        return manifest_dict["version"]

    tables = manifest_dict.get("tables", {})
    if not tables:
        return 1

    first_table_value = next(iter(tables.values()))
    if isinstance(first_table_value, dict) and "formats" in first_table_value:
        return 2

    return 1


def load_manifest(manifest_path: Path) -> ManifestV1 | ManifestV2:
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)

    version = detect_version(data)

    if version == 1:
        return _parse_v1(data)
    else:
        return _parse_v2(data)


def _parse_v1(data: dict) -> ManifestV1:
    tables = {}
    for table_name, files in data.get("tables", {}).items():
        tables[table_name] = [
            FileEntry(path=f["path"], size_bytes=f["size_bytes"], row_count=f["row_count"]) for f in files
        ]

    return ManifestV1(
        benchmark=data["benchmark"],
        scale_factor=data["scale_factor"],
        tables=tables,
        compression=data.get("compression"),
        parallel=data.get("parallel"),
        created_at=data.get("created_at"),
        generator_version=data.get("generator_version"),
    )


_KNOWN_V2_TOP_LEVEL_KEYS = frozenset(
    {
        "version",
        "benchmark",
        "scale_factor",
        "tables",
        "format_preference",
        "compression",
        "parallel",
        "created_at",
        "generator_version",
        "plan_metadata",
    }
)


def _parse_v2(data: dict) -> ManifestV2:
    tables = {}
    for table_name, table_data in data.get("tables", {}).items():
        formats_dict = {}
        for format_name, files in table_data.get("formats", {}).items():
            formats_dict[format_name] = [
                ConvertedFileEntry(
                    path=f["path"],
                    size_bytes=f["size_bytes"],
                    row_count=f["row_count"],
                    converted_from=f.get("converted_from"),
                    converted_at=f.get("converted_at"),
                    compression=f.get("compression"),
                    row_groups=f.get("row_groups"),
                    is_directory=bool(f.get("is_directory", False)),
                    conversion_options=f.get("conversion_options", {}),
                    metadata=f.get("metadata", {}),
                )
                for f in files
            ]
        tables[table_name] = TableFormats(formats=formats_dict)

    plan_metadata = None
    if "plan_metadata" in data:
        plan_metadata = PlanMetadata.from_dict(data["plan_metadata"])

    extra_fields = {k: v for k, v in data.items() if k not in _KNOWN_V2_TOP_LEVEL_KEYS}

    return ManifestV2(
        version=data.get("version", 2),
        benchmark=data.get("benchmark"),
        scale_factor=data.get("scale_factor"),
        tables=tables,
        format_preference=data.get("format_preference", []),
        compression=data.get("compression"),
        parallel=data.get("parallel"),
        created_at=data.get("created_at"),
        generator_version=data.get("generator_version"),
        plan_metadata=plan_metadata,
        extra_fields=extra_fields,
    )


def upgrade_v1_to_v2(v1: ManifestV1) -> ManifestV2:
    tables = {}
    format_name = "tbl"
    for table_name, files in v1.tables.items():
        format_name = _detect_format_from_files(files)

        converted_files = [
            ConvertedFileEntry(
                path=f.path,
                size_bytes=f.size_bytes,
                row_count=f.row_count,
            )
            for f in files
        ]

        tables[table_name] = TableFormats(formats={format_name: converted_files})

    return ManifestV2(
        version=2,
        benchmark=v1.benchmark,
        scale_factor=v1.scale_factor,
        tables=tables,
        format_preference=[format_name, "tbl", "csv"],
        compression=v1.compression,
        parallel=v1.parallel,
        created_at=v1.created_at,
        generator_version=v1.generator_version,
    )


def _detect_format_from_files(files: list[FileEntry]) -> str:
    if not files:
        return "tbl"

    path = files[0].path
    if is_parquet_format(path):
        return "parquet"
    elif is_tpc_format(path):
        return "tbl"
    elif is_csv_format(path):
        return "csv"
    else:
        return "tbl"


def write_manifest(manifest: ManifestV1 | ManifestV2, path: Path) -> None:
    if isinstance(manifest, ManifestV1):
        data = _manifest_v1_to_dict(manifest)
    else:
        data = _manifest_v2_to_dict(manifest)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _manifest_v1_to_dict(manifest: ManifestV1) -> dict:
    tables = {}
    for table_name, files in manifest.tables.items():
        tables[table_name] = [{"path": f.path, "size_bytes": f.size_bytes, "row_count": f.row_count} for f in files]

    result = {
        "benchmark": manifest.benchmark,
        "scale_factor": manifest.scale_factor,
        "tables": tables,
    }

    if manifest.compression is not None:
        result["compression"] = manifest.compression
    if manifest.parallel is not None:
        result["parallel"] = manifest.parallel
    if manifest.created_at is not None:
        result["created_at"] = manifest.created_at
    if manifest.generator_version is not None:
        result["generator_version"] = manifest.generator_version

    return result


def _load_io_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("io_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_IO_SPECS = _load_io_specs()
_FILE_OPTIONAL_FIELDS = _IO_SPECS["file_optional_fields"]
_MANIFEST_OPTIONAL_FIELDS = _IO_SPECS["manifest_optional_fields"]


def _manifest_v2_to_dict(manifest: ManifestV2) -> dict:
    tables = {}
    for table_name, table_formats in manifest.tables.items():
        formats_dict = {}
        for format_name, files in table_formats.formats.items():
            file_dicts = []
            for f in files:
                file_dict = {
                    "path": f.path,
                    "size_bytes": f.size_bytes,
                    "row_count": f.row_count,
                }
                for attr in _FILE_OPTIONAL_FIELDS:
                    value = getattr(f, attr, None)
                    if value is not None and value:
                        file_dict[attr] = value
                if f.is_directory:
                    file_dict["is_directory"] = True
                file_dicts.append(file_dict)

            formats_dict[format_name] = file_dicts
        tables[table_name] = {"formats": formats_dict}

    result: dict = {
        "version": 2,
        "tables": tables,
    }

    for attr in _MANIFEST_OPTIONAL_FIELDS:
        value = getattr(manifest, attr, None)
        if value is not None and value:
            result[attr] = value

    if manifest.plan_metadata is not None:
        metadata_dict = manifest.plan_metadata.to_dict()
        if metadata_dict:
            result["plan_metadata"] = metadata_dict

    for key, value in manifest.extra_fields.items():
        if key not in result:
            result[key] = value

    return result
