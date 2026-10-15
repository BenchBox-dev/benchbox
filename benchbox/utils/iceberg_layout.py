# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_FIRST_NUMBER = re.compile(r"(\d+)")


def local_iceberg_location(path: str | Path) -> str:
    local_path = Path(path)
    return str(local_path.resolve()) if os.name == "nt" else local_path.as_uri()


def is_iceberg_directory(path: str | Path) -> bool:
    path = Path(path)
    metadata = path / "metadata"
    if not path.is_dir() or not metadata.is_dir():
        return False
    return (metadata / "version-hint.text").exists() or bool(list(metadata.glob("*.metadata.json")))


def _metadata_sort_key(metadata_file: Path) -> tuple[int, str]:
    match = _FIRST_NUMBER.search(metadata_file.name)
    return (int(match.group(1)), metadata_file.name) if match else (10**18, metadata_file.name)


def resolve_iceberg_metadata_file(path: str | Path) -> Path | None:
    path = Path(path)
    metadata = path / "metadata"
    if not path.is_dir() or not metadata.is_dir():
        return None
    hint = metadata / "version-hint.text"
    if hint.is_file():
        try:
            version = int(hint.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            version = None
        if version is not None:
            candidate = metadata / f"v{version}.metadata.json"
            if candidate.is_file():
                return candidate
    candidates = sorted(metadata.glob("*.metadata.json"), key=_metadata_sort_key)
    if not candidates:
        return None
    return candidates[-1]


_METADATA_VERSION_RE = re.compile(r"(\d+)-.*\.metadata\.json")


@dataclass
class RelocatedIcebergGraph:
    metadata_location: str
    graph_files: dict[str, Path] = field(default_factory=dict)
    data_files: list[str] = field(default_factory=list)


def relocate_iceberg_table(
    table_dir: str | Path,
    dest_uri: str,
    staging_dir: str | Path,
) -> RelocatedIcebergGraph:
    from pyiceberg.io.pyarrow import PyArrowFileIO
    from pyiceberg.manifest import read_manifest_list, write_manifest, write_manifest_list
    from pyiceberg.serializers import ToOutputFile
    from pyiceberg.table.metadata import TableMetadataUtil

    table_path = Path(table_dir)
    metadata_file = resolve_iceberg_metadata_file(table_path)
    if metadata_file is None:
        raise ValueError(f"Not an Iceberg table directory: {table_path}")

    dest_prefix = dest_uri.rstrip("/")
    metadata = TableMetadataUtil.parse_obj(json.loads(metadata_file.read_text(encoding="utf-8")))
    src_prefix = metadata.location.rstrip("/")
    staging = Path(staging_dir)
    (staging / "metadata").mkdir(parents=True, exist_ok=True)
    io = PyArrowFileIO()
    graph_files: dict[str, Path] = {}

    current = next((s for s in metadata.snapshots if s.snapshot_id == metadata.current_snapshot_id), None)
    if current is not None:
        specs = {spec.spec_id: spec for spec in metadata.partition_specs}
        relocated_manifests = []
        for manifest in read_manifest_list(io.new_input(current.manifest_list)):
            entries = manifest.fetch_manifest_entry(io, discard_deleted=False)
            for entry in entries:
                old_path = entry.data_file.file_path
                if old_path.startswith(src_prefix):
                    entry.data_file[1] = dest_prefix + old_path[len(src_prefix) :]
            manifest_rel = f"metadata/reloc-{uuid.uuid4().hex[:8]}.manifest.avro"
            with write_manifest(
                metadata.format_version,
                specs[manifest.partition_spec_id],
                metadata.schema(),
                io.new_output(str(staging / manifest_rel)),
                current.snapshot_id,
                "null",
            ) as writer:
                for entry in entries:
                    status = entry.status.name
                    if status == "DELETED":
                        writer.delete(entry)
                    elif status == "EXISTING":
                        writer.existing(entry)
                    else:
                        writer.add(entry)
                new_manifest = writer.to_manifest_file()
            new_manifest[0] = f"{dest_prefix}/{manifest_rel}"
            relocated_manifests.append(new_manifest)
            graph_files[manifest_rel] = staging / manifest_rel

        list_rel = f"metadata/reloc-{uuid.uuid4().hex[:8]}.avro"
        with write_manifest_list(
            metadata.format_version,
            io.new_output(str(staging / list_rel)),
            current.snapshot_id,
            current.parent_snapshot_id,
            current.sequence_number,
            "null",
        ) as list_writer:
            list_writer.add_manifests(relocated_manifests)
        graph_files[list_rel] = staging / list_rel
        new_manifest_list = f"{dest_prefix}/{list_rel}"
    else:
        new_manifest_list = None

    existing_versions = [
        int(match.group(1))
        for candidate in (table_path / "metadata").glob("*.metadata.json")
        if (match := _METADATA_VERSION_RE.fullmatch(candidate.name)) is not None
    ]
    next_version = (max(existing_versions) + 1) if existing_versions else 0
    meta_rel = f"metadata/{next_version:05d}-{uuid.uuid4().hex}.metadata.json"
    if current is not None:
        new_snapshot = current.model_copy(update={"manifest_list": new_manifest_list})
        metadata = metadata.model_copy(
            update={
                "location": dest_prefix,
                "snapshots": [new_snapshot],
                "snapshot_log": [entry for entry in metadata.snapshot_log if entry.snapshot_id == current.snapshot_id],
                "metadata_log": [],
            }
        )
    else:
        metadata = metadata.model_copy(update={"location": dest_prefix, "metadata_log": []})
    ToOutputFile.table_metadata(metadata, io.new_output(str(staging / meta_rel)))
    graph_files[meta_rel] = staging / meta_rel

    data_files = sorted(
        path.relative_to(table_path).as_posix()
        for path in table_path.rglob("*")
        if path.is_file() and path.relative_to(table_path).parts[0] != "metadata"
    )
    return RelocatedIcebergGraph(
        metadata_location=f"{dest_prefix}/{meta_rel}",
        graph_files=graph_files,
        data_files=data_files,
    )
