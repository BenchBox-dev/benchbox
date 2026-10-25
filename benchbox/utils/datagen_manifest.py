from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from benchbox.utils.cloud_storage import DatabricksPath, create_path_handler
from benchbox.utils.version import get_package_version

if TYPE_CHECKING:
    from cloudpathlib import CloudPath

_CLOUD_PATH_TYPE: type[Any] | None = None


PathLike = Union[str, Path, "CloudPath"]

MANIFEST_FILENAME = "_datagen_manifest.json"


def compute_entry_size(path: Path) -> int:
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return path.stat().st_size


def require_manifest_files(file_count: int, *, label: str, output_dir: PathLike) -> None:
    if file_count <= 0:
        raise RuntimeError(
            f"{label} data generation produced no table files in {output_dir}; "
            "refusing to write a manifest that lists no tables"
        )


def _is_subpath(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _utc_now_iso() -> str:

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_cloud_path_type() -> type[Any] | None:
    global _CLOUD_PATH_TYPE
    if _CLOUD_PATH_TYPE is None:
        try:
            cloud_path_type = importlib.import_module("cloudpathlib").CloudPath
        except ImportError:
            return None
        _CLOUD_PATH_TYPE = cloud_path_type
    return _CLOUD_PATH_TYPE


@dataclass
class ManifestTableEntry:
    path: str
    size_bytes: int
    row_count: int
    checksum: str | None = None
    format: str | None = None
    metadata: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:

        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


def _ensure_path(path: PathLike) -> Path | CloudPath | DatabricksPath:

    if isinstance(path, (Path,)):
        return path
    cloud_path_type = _get_cloud_path_type()
    if cloud_path_type is not None and isinstance(path, cloud_path_type):
        return path
    return create_path_handler(path)


def _normalise_to_root(root: Path | CloudPath, path: PathLike) -> tuple[Path | CloudPath | DatabricksPath, str]:

    resolved = _ensure_path(path)
    local_root = root.resolve() if isinstance(root, Path) else root

    if isinstance(resolved, Path) and not resolved.is_absolute():
        cwd_resolved = resolved.resolve()
        if isinstance(local_root, Path) and _is_subpath(cwd_resolved, local_root):
            resolved = cwd_resolved
        elif isinstance(local_root, Path):
            resolved = (local_root / resolved).resolve()
    elif isinstance(resolved, Path):
        resolved = resolved.resolve()

    cloud_path_type = _get_cloud_path_type()
    if cloud_path_type is not None and isinstance(resolved, cloud_path_type):
        try:
            rel = resolved.relative_to(local_root)
            return resolved, rel.as_posix()
        except (ValueError, TypeError, AttributeError):
            return resolved, resolved.path

    try:
        rel_path = resolved.relative_to(local_root)
        return resolved, rel_path.as_posix()
    except (ValueError, TypeError, AttributeError):
        if hasattr(resolved, "as_posix"):
            return resolved, resolved.as_posix()
        return resolved, str(resolved)


def resolve_compression_metadata(source: Any) -> dict[str, Any]:

    enabled = False
    compression_type = None
    compression_level = None

    if hasattr(source, "should_use_compression"):
        try:
            enabled = bool(source.should_use_compression())
        except Exception:
            enabled = False

    if enabled:
        compression_type = getattr(source, "compression_type", None)
        compression_level = getattr(source, "compression_level", None)
    else:
        compression_type = None
        compression_level = None

    return {
        "enabled": enabled,
        "type": compression_type,
        "level": compression_level,
    }


class DataGenerationManifest:
    def __init__(
        self,
        *,
        output_dir: PathLike,
        benchmark: str,
        scale_factor: float,
        compression: dict[str, Any] | None = None,
        parallel: int | None = None,
        seed: int | None = None,
        extra_metadata: dict[str, Any] | None = None,
        formats: list[str] | None = None,
        stamp: bool = True,
    ) -> None:
        self._root = _ensure_path(output_dir)
        self._benchmark = benchmark
        self._stamp = stamp
        self._scale_factor = scale_factor
        self._compression = compression or {"enabled": False, "type": None, "level": None}
        self._parallel = int(parallel) if parallel not in (None, 0) else 1
        self._seed = int(seed) if seed is not None else None
        self._extra_metadata = extra_metadata or {}
        self._formats = formats or ["tbl"]
        self._tables: dict[str, dict[str, list[ManifestTableEntry]]] = {}

    @property
    def manifest_path(self) -> Path | CloudPath:

        return self._root / MANIFEST_FILENAME

    def add_entry(
        self,
        table_name: str,
        file_path: PathLike,
        *,
        row_count: int,
        size_bytes: int | None = None,
        checksum: str | None = None,
        format: str = "tbl",
        metadata: dict[str, Any] | None = None,
    ) -> None:

        resolved, manifest_path = _normalise_to_root(self._root, file_path)

        size = size_bytes
        if size is None and hasattr(resolved, "stat"):
            try:
                size = int(resolved.stat().st_size)
            except Exception:
                size = 0

        entry = ManifestTableEntry(
            path=manifest_path,
            size_bytes=int(size or 0),
            row_count=int(row_count),
            checksum=checksum,
            format=format,
            metadata=metadata,
        )

        if table_name not in self._tables:
            self._tables[table_name] = {}
        if format not in self._tables[table_name]:
            self._tables[table_name][format] = []

        self._tables[table_name][format].append(entry)

        if format not in self._formats:
            self._formats.append(format)

    def extend(self, table_name: str, entries: Iterable[ManifestTableEntry], format: str = "tbl") -> None:

        if table_name not in self._tables:
            self._tables[table_name] = {}
        if format not in self._tables[table_name]:
            self._tables[table_name][format] = []

        self._tables[table_name][format].extend(entries)

        if format not in self._formats:
            self._formats.append(format)

    def file_counts(self) -> tuple[int, int]:

        table_count = len(self._tables)
        file_count = sum(
            len(entries) for format_entries in self._tables.values() for entries in format_entries.values()
        )
        return table_count, file_count

    def to_dict(self) -> dict[str, Any]:

        tables_data = {}
        for table_name, format_entries in self._tables.items():
            tables_data[table_name] = {
                "formats": {
                    format_name: [entry.to_json() for entry in entries]
                    for format_name, entries in format_entries.items()
                }
            }

        datagen_stamp: dict[str, Any] = {}
        if self._stamp:
            try:
                from benchbox.utils.datagen_version import current_datagen_stamp

                datagen_stamp = current_datagen_stamp(self._benchmark)
            except Exception as exc:
                logging.getLogger(__name__).debug("datagen stamp unavailable for %s: %s", self._benchmark, exc)

        manifest: dict[str, Any] = {
            "version": 2,
            "benchmark": self._benchmark.lower(),
            "scale_factor": float(self._scale_factor),
            "formats": list(self._formats),
            "format_preference": list(self._formats),
            "compression": self._compression,
            "parallel": self._parallel,
            "created_at": _utc_now_iso(),
            "generator_version": get_package_version(),
            "tables": tables_data,
        }

        if self._seed is not None:
            manifest["seed"] = self._seed

        if self._extra_metadata:
            manifest.update(self._extra_metadata)

        manifest.update(datagen_stamp)

        return manifest

    def write(self) -> Path | CloudPath:

        manifest = self.to_dict()
        target = self.manifest_path

        if isinstance(target, Path):
            target.parent.mkdir(parents=True, exist_ok=True)

        with target.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=False)
            fh.write("\n")

        return target


def summarise_manifest(manifest: dict[str, Any]) -> tuple[int, int]:

    tables = manifest.get("tables", {}) or {}
    table_count = len(tables)
    file_count = 0
    for table_data in tables.values():
        formats = table_data.get("formats", {}) if isinstance(table_data, dict) else {}
        for entries in formats.values():
            file_count += len(entries or [])

    return table_count, file_count


def load_manifest(manifest_path: Path | CloudPath) -> dict[str, Any]:
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def get_table_files(
    manifest: dict[str, Any],
    table_name: str,
    format: str | None = None,
    *,
    skip_directory_only_formats: bool = False,
) -> list[dict[str, Any]]:
    tables = manifest.get("tables", {})
    if table_name not in tables:
        return []

    table_data = tables[table_name]

    if isinstance(table_data, list):
        return table_data
    if not isinstance(table_data, dict):
        return []

    formats_dict = table_data.get("formats", {})

    if format:
        return formats_dict.get(format, [])

    preferred_order = manifest.get("format_preference") or manifest.get("formats") or []
    if not skip_directory_only_formats:
        return _select_any_entries(formats_dict, preferred_order)
    return _select_file_based_entries(formats_dict, preferred_order)


def _all_directory_entries(entries: list) -> bool:
    return bool(entries) and all(isinstance(e, dict) and e.get("is_directory", False) for e in entries)


def _select_any_entries(formats_dict: dict, preferred_order: list) -> list[dict[str, Any]]:
    if preferred_order:
        for fmt in preferred_order:
            entries = formats_dict.get(fmt)
            if entries:
                return entries
    for entries in formats_dict.values():
        if entries:
            return entries
    return []


def _select_file_based_entries(formats_dict: dict, preferred_order: list) -> list[dict[str, Any]]:
    if preferred_order:
        for fmt in preferred_order:
            entries = formats_dict.get(fmt)
            if entries and not _all_directory_entries(entries):
                return entries
    for entries in formats_dict.values():
        if entries and not _all_directory_entries(entries):
            return entries
    for entries in formats_dict.values():
        if entries:
            return entries
    return []


def get_available_formats(manifest: dict[str, Any], table_name: str | None = None) -> list[str]:
    if table_name:
        tables = manifest.get("tables", {})
        table_entry = tables.get(table_name)
        if isinstance(table_entry, dict):
            formats_section = table_entry.get("formats")
            if isinstance(formats_section, dict):
                return list(formats_section.keys())
        return []

    formats = manifest.get("formats") or []
    return list(formats) if formats else ["tbl"]
