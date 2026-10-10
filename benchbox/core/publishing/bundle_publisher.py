# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from benchbox.core.results.loader import load_result_file
from benchbox.core.results.provenance import SOURCE_TO_TRUST_LABEL
from benchbox.core.results.status import bundle_non_clean_reason, result_non_clean_reason
from benchbox.validation.bundle import COMPANION_SUFFIXES

from .store import PublicationRecord, PublicationStore, build_reference

logger = logging.getLogger(__name__)

VALID_LABELS = (*SOURCE_TO_TRUST_LABEL.values(), "ci", "local", "unofficial-research")


@dataclass
class BundlePublishResult:
    success: bool
    record: PublicationRecord | None = None
    reference: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "pub_id": self.record.pub_id if self.record else None,
            "reference": self.reference,
            "errors": self.errors,
        }


class BundlePublisher:
    def __init__(
        self,
        destination: str | Path,
        store: PublicationStore | None = None,
        label: str = "maintainer-run",
    ) -> None:
        self.destination = str(destination)
        self.store = store or PublicationStore()
        if label not in VALID_LABELS:
            raise ValueError(f"Invalid trust label {label!r}; must be one of {VALID_LABELS}.")
        self.label = label

    def publish(self, source_bundle: str | Path, *, record_source: str | Path | None = None) -> BundlePublishResult:
        bundle_path = Path(source_bundle)

        if not bundle_path.exists():
            return BundlePublishResult(
                success=False,
                errors=[f"Source bundle not found: {source_bundle}"],
            )

        if bundle_path.suffix != ".json" or bundle_path.name.endswith(COMPANION_SUFFIXES):
            return BundlePublishResult(
                success=False,
                errors=[
                    f"Expected a primary .json result bundle, got: {bundle_path.name}. "
                    f"Companion files ({', '.join(COMPANION_SUFFIXES)}) are published automatically."
                ],
            )

        validation_errors = _validate_source_bundle_for_publication(bundle_path)
        if validation_errors:
            return BundlePublishResult(success=False, errors=validation_errors)

        metadata = _read_bundle_metadata(bundle_path)

        try:
            dest_filename = _copy_bundle(bundle_path, self.destination)
        except Exception as exc:
            return BundlePublishResult(
                success=False,
                errors=[f"Failed to copy bundle to destination: {exc}"],
            )

        reference = build_reference(self.destination, dest_filename)

        try:
            record_source_path = Path(record_source) if record_source is not None else bundle_path
            record = self.store.add(
                source_path=record_source_path,
                destination=self.destination,
                reference=reference,
                label=self.label,
                benchmark=metadata.benchmark,
                platform=metadata.platform,
                scale_factor=metadata.scale_factor,
                dataset_version=metadata.dataset_version,
                manifest_hash=metadata.manifest_hash,
                data_archive_hash=metadata.data_archive_hash,
            )
        except Exception as exc:
            return BundlePublishResult(
                success=True,
                reference=reference,
                errors=[f"Bundle published but metadata store update failed: {exc}"],
            )

        return BundlePublishResult(
            success=True,
            record=record,
            reference=reference,
        )


@dataclass(frozen=True)
class _BundleMetadata:
    benchmark: str
    platform: str
    scale_factor: float
    dataset_version: str | None = None
    manifest_hash: str | None = None
    data_archive_hash: str | None = None


def _validate_source_bundle_for_publication(bundle_path: Path) -> list[str]:
    try:
        result, data = load_result_file(bundle_path)
    except Exception as exc:
        return [f"Source bundle is not a loadable schema-v2 result bundle: {exc}"]

    reason = bundle_non_clean_reason(data) or result_non_clean_reason(result)
    if reason:
        return [f"Source bundle is not a clean pass: {reason}"]
    return []


def _read_bundle_metadata(bundle_path: Path) -> _BundleMetadata:
    try:
        text = bundle_path.read_text(encoding="utf-8")
        data: dict = json.loads(text)
        benchmark_block = data.get("benchmark", {})
        benchmark = benchmark_block.get("id", "") or data.get("benchmark_id", "") or benchmark_block.get("name", "")
        platform = data.get("platform", {}).get("name", "") or data.get("platform", "")
        if isinstance(platform, dict):
            platform = platform.get("name", "")
        raw_sf = data.get("benchmark", {}).get("scale_factor") or data.get("scale_factor") or 1.0
        scale_factor = float(raw_sf) if raw_sf else 1.0
        identity = _read_dataset_identity(str(benchmark))
        return _BundleMetadata(
            benchmark=str(benchmark),
            platform=str(platform),
            scale_factor=scale_factor,
            dataset_version=identity.dataset_version,
            manifest_hash=identity.manifest_hash,
            data_archive_hash=identity.data_archive_hash,
        )
    except Exception:
        return _BundleMetadata("", "", 1.0)


@dataclass(frozen=True)
class _DatasetIdentity:
    dataset_version: str | None
    manifest_hash: str | None
    data_archive_hash: str | None


def _read_dataset_identity(benchmark_id: str) -> _DatasetIdentity:
    try:
        from benchbox.core.benchmark_registry import get_benchmark_metadata
        from benchbox.core.data_fetch import load_manifest

        meta = get_benchmark_metadata(benchmark_id) or {}
        manifest_rel = meta.get("data_manifest")
        if not manifest_rel:
            return _DatasetIdentity(None, None, None)
        repo_root = Path(__file__).resolve().parents[3]
        manifest = load_manifest(repo_root / str(manifest_rel))
        return _DatasetIdentity(
            dataset_version=manifest.dataset_version,
            manifest_hash=manifest.manifest_hash,
            data_archive_hash=manifest.data_archive_hash,
        )
    except Exception:
        return _DatasetIdentity(None, None, None)


def _copy_bundle(source: Path, destination: str) -> str:
    from benchbox.utils.cloud_storage import is_cloud_path

    filename = source.name
    stem = source.stem
    files_to_copy = [source]
    for suffix in COMPANION_SUFFIXES:
        companion = source.parent / (stem + suffix)
        if companion.exists():
            files_to_copy.append(companion)

    if is_cloud_path(destination):
        _copy_to_cloud(files_to_copy, destination)
    else:
        _copy_to_local(files_to_copy, destination)

    return filename


def _copy_to_local(files: list[Path], destination: str) -> None:
    dest_dir = Path(destination)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for file in files:
        shutil.copy2(file, dest_dir / file.name)


def _copy_to_cloud(files: list[Path], destination: str) -> None:
    try:
        from benchbox.utils.cloud_storage import create_path_handler, is_adls_path
    except ImportError as exc:
        raise RuntimeError("Cloud storage is not available. Install cloudpathlib for cloud support.") from exc

    if is_adls_path(destination):
        _copy_to_adls(files, destination)
        return

    base = destination.rstrip("/")
    for file in files:
        cloud_path = create_path_handler(f"{base}/{file.name}")
        if not hasattr(cloud_path, "write_bytes"):
            raise RuntimeError(f"Cloud destination does not support direct writes: {destination}")
        cloud_path.write_bytes(file.read_bytes())


def _copy_to_adls(files: list[Path], destination: str) -> None:
    parsed = urlparse(destination)
    if "@" not in parsed.netloc:
        raise ValueError("ADLS publishing requires a container@account authority")
    container, account_host = parsed.netloc.split("@", 1)
    prefix = parsed.path.strip("/")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.filedatalake import DataLakeServiceClient
    except ImportError as exc:
        raise RuntimeError("ADLS publishing requires azure-identity and azure-storage-file-datalake") from exc

    account_name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
    account_key = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY")
    if account_name and account_key:
        from azure.core.credentials import AzureNamedKeyCredential

        credential = AzureNamedKeyCredential(account_name, account_key)
    else:
        credential = DefaultAzureCredential()
    service = DataLakeServiceClient(account_url=f"https://{account_host}", credential=credential)
    filesystem = service.get_file_system_client(container)
    for file in files:
        remote_path = "/".join(part for part in (prefix, file.name) if part)
        filesystem.get_file_client(remote_path).upload_data(file.read_bytes(), overwrite=True)
