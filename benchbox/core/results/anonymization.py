# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import contextlib
import copy
import functools
import hashlib
import logging
import os
import platform
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import yaml

from benchbox.core.results import platform_options as _platform_options

is_secret_option_key = _platform_options.is_secret_option_key
_SECRET_KEY_PARTS = _platform_options._SECRET_KEY_PARTS

logger = logging.getLogger(__name__)

PUBLIC_REDACTED_VALUE = "<redacted>"


def _load_anonymization_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("anonymization_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_ANONYMIZATION_SPECS = _load_anonymization_specs()

_IDENTIFIER_KEYS = dict(_ANONYMIZATION_SPECS["identifier_keys"])
_ENDPOINT_KEYS = set(_ANONYMIZATION_SPECS["endpoint_keys"])
_PATH_KEYS = set(_ANONYMIZATION_SPECS["path_keys"])
_MOUNT_COLLECTION_KEYS = set(_ANONYMIZATION_SPECS["mount_collection_keys"])
_MOUNT_PATH_KEYS = set(_ANONYMIZATION_SPECS["mount_path_keys"])
_LOCAL_ENDPOINT_VALUES = set(_ANONYMIZATION_SPECS["local_endpoint_values"])
_MESSAGE_KEYS = set(_ANONYMIZATION_SPECS["message_keys"])
_PUBLIC_DROP_KEYS = frozenset(_ANONYMIZATION_SPECS["public_drop_keys"])
_PUBLIC_DROP_CHILD_KEYS = {
    parent: frozenset(children) for parent, children in _ANONYMIZATION_SPECS["public_drop_child_keys"].items()
}
_ENGINE_RECEIPT_NATIVE_PATHS = (
    ("platform", "execution_engine", "applied_native"),
    ("platform", "raw_config", "execution_engine", "applied_native"),
)


@functools.cache
def _declared_execution_engine_names() -> frozenset[str]:
    from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE, PLATFORM_MANIFEST

    names = {DEFAULT_EXECUTION_ENGINE}
    for entry in PLATFORM_MANIFEST:
        names.update(entry.capabilities.get("execution_engines", {}))
        for gateway in entry.capabilities.get("gateways", {}).values():
            names.update(gateway["routes_to"])
    return frozenset(names)


def _is_engine_receipt_native_path(key_path: tuple[str, ...]) -> bool:
    return any(key_path[: len(anchor)] == anchor for anchor in _ENGINE_RECEIPT_NATIVE_PATHS)


_OPTION_SOURCE_LABELS = frozenset(
    {
        "registered_default",
        "saved_config",
        "environment_variable",
        "cli_option",
        "runtime_override",
        "requested",
        "observed",
        "inferred",
        "unavailable",
    }
)
_PUBLIC_EMPTY_OPTIONAL_MAP_KEYS = frozenset({"clienthost"})
_TUNING_SOURCE_FILE_PATH = ("platform", "tuning", "source_file")

_TUNING_COLUMN_BEARING_KEYS = frozenset(
    {"clustering", "partitioning", "sorting", "distribution", "columns", "indexes", "order_by", "bucketing"}
)

_MESSAGE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    r"(?:~|/Users|/home|/root|/private/var|/var/folders|/var/run|/Volumes)/[^\s'\",;)]*"
    r"|[A-Za-z]:\\Users\\[^\s'\",;)]*"
    r")"
)
_TUNING_SOURCE_REFERENCE_RE = re.compile(
    r"[a-z0-9_.-]+(?:/[a-z0-9_.-]+)*(?::[0-9a-f]{16,64})?",
    re.IGNORECASE,
)
_MESSAGE_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\",;)]*", flags=re.IGNORECASE)
_MESSAGE_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b([a-z0-9][a-z0-9_.-]*)=[^&\s,;)]*",
    flags=re.IGNORECASE,
)
_PRIVATE_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:~|/Users|/home|/root|/private/var|/var/folders|/var/run|/Volumes)/[^\s'\",;)]*"
    r"|[A-Za-z]:\\Users\\[^\s'\",;)]*"
    r")",
    flags=re.IGNORECASE,
)


_COMPACT_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")


@functools.lru_cache(maxsize=4096)
def _compact_key(key: str) -> str:
    return _COMPACT_KEY_STRIP_RE.sub("", key.lower())


@functools.lru_cache(maxsize=4096)
def _identifier_prefix_for_key_tail(key_path: tuple[str, ...]) -> str | None:
    if not key_path:
        return None

    key = _compact_key(key_path[-1])
    parent = _compact_key(key_path[-2]) if len(key_path) >= 2 else ""

    if parent in _MOUNT_COLLECTION_KEYS and key in _MOUNT_PATH_KEYS:
        return "path"
    if key == "id" and key_path[-3:-1] == ("platform_timing", "requests"):
        return "statement"
    if key in _IDENTIFIER_KEYS:
        return _IDENTIFIER_KEYS[key]
    if key.endswith("bucket"):
        return "bucket"
    if key.endswith("prefix"):
        return "prefix"
    if key.endswith("arn"):
        return "arn"
    if key.endswith("host") or key.endswith("hostname") or key in _ENDPOINT_KEYS:
        return "host" if key in {"host", "hostname", "server", "enginehost"} else "endpoint"
    if key.endswith("url") or key.endswith("endpoint"):
        return "endpoint"
    if (
        key.endswith("path")
        or key.endswith("directory")
        or key.endswith("dir")
        or key.endswith("folder")
        or key.endswith("root")
        or key.endswith("file")
        or key.endswith("executable")
        or key in _PATH_KEYS
    ):
        return "path"
    return None


_TRANSLATION_RUNTIME_VERSION_PATH = ("execution", "translation", "runtime", "version")
_RUNTIME_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")
_PUBLIC_HASH_WIDTH = 12
_PUBLIC_HASH_DIGITS = frozenset("0123456789abcdef")


def _is_public_pseudonym(value: str, prefix: str) -> bool:
    marker = f"{prefix}_"
    if not value.startswith(marker):
        return False
    digest = value[len(marker) :]
    return len(digest) == _PUBLIC_HASH_WIDTH and all(char in _PUBLIC_HASH_DIGITS for char in digest)


PUBLIC_PSEUDONYM_SALT_ENV = "BENCHBOX_MACHINE_ID_SALT"


class MissingPublicPseudonymSaltError(ValueError):
    pass


def resolve_public_pseudonym_salt(
    *,
    explicit: Optional[str] = None,
    environ: Optional[dict[str, str]] = None,
) -> Optional[str]:
    if explicit is not None:
        stripped = str(explicit).strip()
        return stripped or None
    env = environ if environ is not None else os.environ
    raw = env.get(PUBLIC_PSEUDONYM_SALT_ENV)
    if raw is None:
        return None
    stripped = str(raw).strip()
    return stripped or None


PUBLIC_PSEUDONYM_SALT_FILENAME = "submission_salt"
PUBLIC_PSEUDONYM_SALT_DOCS_URL = "https://benchbox.dev/docs/contributing-results.html"
_SALT_READ_ATTEMPTS = 5
_SALT_READ_DELAY_SECONDS = 0.05


def public_pseudonym_salt_path() -> Path:
    try:
        return Path.home() / ".benchbox" / PUBLIC_PSEUDONYM_SALT_FILENAME
    except RuntimeError:
        raise MissingPublicPseudonymSaltError(
            "Cannot locate the home directory for the public pseudonym salt file. "
            f"Set the {PUBLIC_PSEUDONYM_SALT_ENV} environment variable to a private value. "
            f"See {PUBLIC_PSEUDONYM_SALT_DOCS_URL}."
        ) from None


def _salt_store_error(path: Path, problem: str) -> MissingPublicPseudonymSaltError:
    return MissingPublicPseudonymSaltError(
        f"The public pseudonym salt file {path} {problem}. "
        f"Fix or delete that file, or set the {PUBLIC_PSEUDONYM_SALT_ENV} environment variable "
        f"to a private value. See {PUBLIC_PSEUDONYM_SALT_DOCS_URL}."
    )


def _read_salt_file(path: Path) -> Optional[str]:
    for attempt in range(_SALT_READ_ATTEMPTS):
        try:
            stripped = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            raise _salt_store_error(path, f"cannot be read ({type(exc).__name__})") from None
        if stripped:
            return stripped
        if attempt + 1 < _SALT_READ_ATTEMPTS:
            time.sleep(_SALT_READ_DELAY_SECONDS)
    raise _salt_store_error(path, "is empty")


def _write_salt_exclusive(path: Path, value: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    return True


def _create_salt_file(path: Path) -> Optional[str]:
    value = secrets.token_hex(32)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            temp_path.chmod(0o600)
        try:
            os.link(temp_path, path)
        except FileExistsError:
            return None
        except OSError:
            return value if _write_salt_exclusive(path, value) else None
        return value
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink()


def require_public_pseudonym_salt(
    *,
    explicit: Optional[str] = None,
    environ: Optional[dict[str, str]] = None,
) -> str:
    salt = resolve_public_pseudonym_salt(explicit=explicit, environ=environ)
    if salt is not None:
        return salt
    path = public_pseudonym_salt_path()
    stored = _read_salt_file(path)
    if stored is None:
        raise MissingPublicPseudonymSaltError(
            "Community publish requires a non-empty public pseudonym salt. "
            f"Set the {PUBLIC_PSEUDONYM_SALT_ENV} environment variable to a "
            f"deployment-private value, or place one in {path}. "
            f"See {PUBLIC_PSEUDONYM_SALT_DOCS_URL}."
        )
    return stored


def ensure_public_pseudonym_salt(
    *,
    explicit: Optional[str] = None,
    environ: Optional[dict[str, str]] = None,
) -> tuple[str, bool]:
    salt = resolve_public_pseudonym_salt(explicit=explicit, environ=environ)
    if salt is not None:
        return salt, False
    path = public_pseudonym_salt_path()
    stored = _read_salt_file(path)
    if stored is not None:
        return stored, False
    try:
        created = _create_salt_file(path)
    except OSError as exc:
        raise _salt_store_error(path, f"cannot be created ({type(exc).__name__})") from None
    if created is not None:
        return created, True
    winner = _read_salt_file(path)
    if winner is None:
        raise _salt_store_error(path, "disappeared while it was being created")
    return winner, False


@dataclass
class AnonymizationConfig:
    machine_id_salt: Optional[str] = field(default=None, repr=False)

    pii_patterns: list[str] = field(
        default_factory=lambda: [
            r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            r"\b\d{3}-\d{2}-\d{4}\b",
        ]
    )

    custom_sanitizers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_public_environ(
        cls,
        *,
        environ: Optional[dict[str, str]] = None,
        require_salt: bool = False,
    ) -> "AnonymizationConfig":
        if require_salt:
            salt: Optional[str] = require_public_pseudonym_salt(environ=environ)
        else:
            salt = resolve_public_pseudonym_salt(environ=environ)
        return cls(machine_id_salt=salt)


class AnonymizationManager:
    def __init__(self, config: Optional[AnonymizationConfig] = None):
        self.config = config or AnonymizationConfig()
        self._machine_id_cache: Optional[str] = None

    def _get_macos_platform_uuid(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "IOPlatformUUID" in line:
                        parts = line.split("=")
                        if len(parts) >= 2:
                            uuid = parts[1].strip().strip('"')
                            logger.debug(f"Found macOS IOPlatformUUID: {uuid[:8]}...")
                            return uuid
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug(f"Failed to get macOS platform UUID: {e}")
        return None

    def _get_linux_machine_id(self) -> Optional[str]:
        for machine_id_path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
            try:
                if os.path.exists(machine_id_path):
                    with open(machine_id_path, encoding="utf-8") as f:
                        machine_id = f.read().strip()
                        if machine_id:
                            logger.debug(f"Found Linux machine-id from {machine_id_path}")
                            return machine_id
            except (OSError, PermissionError) as e:
                logger.debug(f"Failed to read {machine_id_path}: {e}")
                continue
        return None

    def _get_windows_machine_guid(self) -> Optional[str]:
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Cryptography", 0, winreg.KEY_READ)
            machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            logger.debug(f"Found Windows MachineGuid: {machine_guid[:8]}...")
            return machine_guid
        except (ImportError, OSError, Exception) as e:
            logger.debug(f"Failed to get Windows MachineGuid: {e}")
        return None

    def _get_os_machine_id(self) -> Optional[str]:
        system = platform.system()

        if system == "Darwin":
            return self._get_macos_platform_uuid()
        elif system == "Linux":
            return self._get_linux_machine_id()
        elif system == "Windows":
            return self._get_windows_machine_guid()
        else:
            logger.debug(f"Unknown OS: {system}, no OS-level machine ID available")
            return None

    def _get_stable_mac_address(self) -> str:
        try:
            import uuid

            mac = uuid.getnode()

            mac_hex = f"{mac:012x}".upper()

            first_octet = int(mac_hex[:2], 16)
            if first_octet & 0x02:
                logger.debug("MAC address appears to be locally administered/random")

            return mac_hex
        except Exception as e:
            logger.debug(f"Failed to get MAC address: {e}")
            return "unknown_mac"

    def _get_hardware_fingerprint(self) -> str:
        fingerprint_data = []

        try:
            fingerprint_data.append(platform.machine())

            fingerprint_data.append(platform.system())

            fingerprint_data.append(str(os.cpu_count() or 0))

            fingerprint_data.append(self._get_stable_mac_address())

        except Exception as e:
            logger.warning(f"Failed to collect hardware fingerprint: {e}")
            fingerprint_data = ["fallback_fingerprint"]

        return "|".join(fingerprint_data)

    def get_anonymous_machine_id(self) -> str:
        if self._machine_id_cache:
            return self._machine_id_cache

        machine_string = None

        os_machine_id = self._get_os_machine_id()
        if os_machine_id:
            machine_string = f"os_id|{os_machine_id}"
            logger.debug("Using OS-level machine identifier")
        else:
            logger.debug("OS machine ID unavailable, using hardware fingerprint")
            hardware_fingerprint = self._get_hardware_fingerprint()
            machine_string = f"hw_fingerprint|{hardware_fingerprint}"

        if not machine_string or machine_string == "hw_fingerprint|fallback_fingerprint":
            logger.warning(
                "Unable to generate stable machine ID from system. "
                "Machine ID may not be consistent across runs. "
                "This can happen on systems with restricted permissions or unusual configurations."
            )
            import uuid

            fallback_data = f"{platform.system()}|{platform.machine()}|{uuid.getnode()}"
            machine_string = f"fallback|{fallback_data}"

        if self.config.machine_id_salt:
            machine_string += f"|{self.config.machine_id_salt}"

        hasher = hashlib.sha256(machine_string.encode("utf-8"))
        anonymous_id = f"machine_{hasher.hexdigest()[:16]}"

        self._machine_id_cache = anonymous_id
        logger.debug(f"Generated anonymous machine ID: {anonymous_id}")
        return anonymous_id

    def anonymize_result_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        anonymized = self._anonymize_public_value(payload, ())
        platform_block = anonymized.get("platform") if isinstance(anonymized, dict) else None
        tuning_block = platform_block.get("tuning") if isinstance(platform_block, dict) else None
        if isinstance(tuning_block, dict) and isinstance(tuning_block.get("applied"), dict):
            _redact_applied_tuning(tuning_block["applied"])
        return anonymized

    def anonymize_tuning_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_file = payload.get("source_file")
        working = dict(payload)
        working.pop("source_file", None)

        requested = working.get("requested")
        constraints = None
        table_tunings = None
        if isinstance(requested, dict):
            requested = dict(requested)
            constraints = requested.get("constraints")
            table_tunings = requested.pop("table_tunings", None)
            working["requested"] = requested

        anonymized = self.anonymize_result_payload(working)
        if source_file is not None:
            anonymized["source_file"] = (
                source_file
                if self._is_normalized_tuning_source_reference(source_file)
                else self._hash_public_identifier(str(source_file), "path")
            )
        if constraints is not None or table_tunings is not None:
            anonymized_requested = anonymized.setdefault("requested", {})
            if isinstance(anonymized_requested, dict):
                if constraints is not None:
                    anonymized_requested["constraints"] = self._anonymize_tuning_constraints(constraints)
                if table_tunings is not None:
                    anonymized_requested["table_tunings"] = self._anonymize_tuning_table_tunings(table_tunings)
        return anonymized

    @staticmethod
    def _is_normalized_tuning_source_reference(value: Any) -> bool:
        if not isinstance(value, str) or not _TUNING_SOURCE_REFERENCE_RE.fullmatch(value):
            return False
        reference = value.rpartition(":")[0] if ":" in value else value
        return bool(reference) and all(part not in {"", ".", ".."} for part in reference.split("/"))

    _CONSTRAINT_TABLE_SCALAR_KEYS = frozenset(
        {
            "table",
            "table_name",
            "referenced_table",
            "referenced_table_name",
            "local_table",
            "references_table",
        }
    )
    _CONSTRAINT_COLUMN_SCALAR_KEYS = frozenset(
        {
            "column",
            "column_name",
            "name",
            "referenced_column",
            "referenced_column_name",
            "references_column",
            "local_column",
        }
    )
    _CONSTRAINT_TABLE_COLLECTION_KEYS = frozenset({"tables", "table_names", "referenced_tables"})
    _CONSTRAINT_COLUMN_COLLECTION_KEYS = frozenset(
        {"columns", "column_names", "referenced_columns", "referenced_column_names"}
    )

    def _anonymize_tuning_constraints(self, value: Any) -> Any:
        if isinstance(value, dict):
            anonymized: dict[str, Any] = {}
            for key, child in value.items():
                if _compact_key(str(key)) in _PUBLIC_DROP_KEYS:
                    continue
                if key in self._CONSTRAINT_TABLE_SCALAR_KEYS:
                    anonymized[key] = self._anonymize_constraint_identifier_value(child, "table")
                elif key in self._CONSTRAINT_COLUMN_SCALAR_KEYS:
                    anonymized[key] = self._anonymize_constraint_identifier_value(child, "column")
                elif key in self._CONSTRAINT_TABLE_COLLECTION_KEYS:
                    anonymized[key] = self._anonymize_constraint_identifier_collection(child, "table")
                elif key in self._CONSTRAINT_COLUMN_COLLECTION_KEYS:
                    anonymized[key] = self._anonymize_constraint_identifier_collection(child, "column")
                else:
                    if isinstance(child, (dict, list, tuple)):
                        anonymized[key] = self._anonymize_tuning_constraints(child)
                    else:
                        walked = self._anonymize_public_value({str(key): child}, ())
                        if str(key) in walked:
                            anonymized[key] = walked[str(key)]
            return anonymized
        if isinstance(value, list):
            return [self._anonymize_tuning_constraints(item) for item in value]
        if isinstance(value, tuple):
            return [self._anonymize_tuning_constraints(item) for item in value]
        return value

    def _anonymize_constraint_identifier_value(self, value: Any, prefix: str) -> Any:
        if isinstance(value, (dict, list, tuple)):
            return self._anonymize_constraint_identifier_collection(value, prefix)
        if value in (None, ""):
            return value
        return self._hash_public_identifier(str(value), prefix)

    def _anonymize_constraint_identifier_collection(self, value: Any, prefix: str) -> Any:
        if isinstance(value, dict):
            return {
                self._hash_public_identifier(str(key), prefix): self._anonymize_constraint_columns(child)
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._anonymize_constraint_identifier_item(item, prefix) for item in value]
        if isinstance(value, str):
            return self._hash_public_identifier(value, prefix)
        return value

    def _anonymize_constraint_identifier_item(self, item: Any, prefix: str) -> Any:
        if isinstance(item, str):
            return self._hash_public_identifier(item, prefix)
        if isinstance(item, dict):
            return self._anonymize_tuning_constraints(item)
        if isinstance(item, (list, tuple)):
            return [self._anonymize_constraint_identifier_item(child, prefix) for child in item]
        return item

    def _anonymize_constraint_columns(self, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return [self._anonymize_constraint_identifier_item(item, "column") for item in value]
        if isinstance(value, dict):
            return self._anonymize_tuning_constraints(value)
        if isinstance(value, str):
            return self._hash_public_identifier(value, "column")
        return value

    def _anonymize_tuning_table_tunings(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                self._hash_public_identifier(str(key), "table"): self._anonymize_tuning_value(child)
                for key, child in value.items()
            }
        return self._anonymize_tuning_value(value)

    def _anonymize_tuning_value(self, value: Any, *, column_context: bool = False) -> Any:
        if isinstance(value, dict):
            anonymized: dict[str, Any] = {}
            for key, child in value.items():
                if _compact_key(str(key)) in _PUBLIC_DROP_KEYS:
                    continue
                if key in {"table", "table_name"}:
                    anonymized[key] = self._hash_public_identifier(str(child), "table")
                elif key in {"column", "column_name", "name"}:
                    anonymized[key] = self._hash_public_identifier(str(child), "column")
                else:
                    anonymized[key] = self._anonymize_tuning_value(
                        child, column_context=key in _TUNING_COLUMN_BEARING_KEYS
                    )
            return anonymized
        if isinstance(value, (list, tuple)):
            return [self._anonymize_tuning_value(item, column_context=column_context) for item in value]
        if column_context and isinstance(value, str) and value:
            return self._hash_public_identifier(value, "column")
        return value

    def _anonymize_public_value(self, value: Any, key_path: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            anonymized: dict[str, Any] = {}
            dropped_children = _PUBLIC_DROP_CHILD_KEYS.get(_compact_key(key_path[-1]), frozenset()) if key_path else ()
            for key, child in value.items():
                compact = _compact_key(str(key))
                if compact in _PUBLIC_DROP_KEYS or compact in dropped_children:
                    continue
                child_path = (*key_path, str(key))
                if self._is_secret_metadata_key(child_path):
                    child_value = PUBLIC_REDACTED_VALUE if child not in (None, "") else child
                elif child_path == _TUNING_SOURCE_FILE_PATH and self._is_normalized_tuning_source_reference(child):
                    child_value = child
                else:
                    child_value = self._anonymize_public_value(child, child_path)
                if (
                    isinstance(child_value, dict)
                    and not child_value
                    and _compact_key(str(key)) in _PUBLIC_EMPTY_OPTIONAL_MAP_KEYS
                ):
                    continue
                anonymized[key] = child_value
            return anonymized

        if isinstance(value, (set, frozenset)):
            value = sorted(value, key=repr)

        if isinstance(value, (list, tuple, set, frozenset)):
            return [self._anonymize_public_value(item, key_path) for item in value]

        return self._anonymize_public_scalar(value, key_path)

    def _anonymize_public_scalar(self, value: Any, key_path: tuple[str, ...]) -> Any:
        if value in (None, ""):
            return value

        if (
            isinstance(value, str)
            and value in _OPTION_SOURCE_LABELS
            and len(key_path) >= 2
            and _compact_key(key_path[-2]) == "platformoptionsources"
        ):
            return value

        if isinstance(value, str) and self._looks_like_connection_string(value):
            return PUBLIC_REDACTED_VALUE

        if isinstance(value, str) and self._is_message_metadata_key(key_path):
            return self._sanitize_public_message(value)

        if isinstance(value, str):
            value = _PRIVATE_LOCAL_PATH_RE.sub(
                lambda match: self._hash_public_identifier(match.group(0), "path"),
                value,
            )

        prefix = self._identifier_prefix_for_key_path(key_path)
        if prefix is not None:
            if prefix == "host" and isinstance(value, str) and self._is_local_endpoint_value(value):
                return value
            if prefix == "engine" and self._is_declared_engine_in_receipt(value, key_path):
                return value
            return self._hash_public_identifier(str(value), prefix)

        if (
            isinstance(value, str)
            and key_path == _TRANSLATION_RUNTIME_VERSION_PATH
            and _RUNTIME_VERSION_RE.fullmatch(value)
        ):
            return value

        if isinstance(value, str):
            return self.remove_pii(value)

        return value

    def _is_declared_engine_in_receipt(self, value: Any, key_path: tuple[str, ...]) -> bool:
        return (
            isinstance(value, str)
            and _compact_key(key_path[-1]) == "engine"
            and _is_engine_receipt_native_path(key_path)
            and value in _declared_execution_engine_names()
        )

    def _is_secret_metadata_key(self, key_path: tuple[str, ...]) -> bool:
        return bool(key_path) and is_secret_option_key(key_path[-1])

    def _is_message_metadata_key(self, key_path: tuple[str, ...]) -> bool:
        key = _compact_key(key_path[-1]) if key_path else ""
        return key in _MESSAGE_KEYS or key.endswith("message") or key.endswith("error") or key.endswith("errors")

    def _identifier_prefix_for_key_path(self, key_path: tuple[str, ...]) -> str | None:
        return _identifier_prefix_for_key_tail(key_path[-3:])

    def _hash_public_identifier(self, value: str, prefix: str) -> str:
        if _is_public_pseudonym(value, prefix):
            return value
        salt = self.config.machine_id_salt or ""
        digest = hashlib.sha256(f"{salt}|{prefix}|{value}".encode()).hexdigest()[:_PUBLIC_HASH_WIDTH]
        return f"{prefix}_{digest}"

    @staticmethod
    def _is_local_endpoint_value(value: str) -> bool:
        value = value.strip()
        parsed = urlparse(value if "://" in value else f"//{value}")
        host = (parsed.hostname or value.split("/", 1)[0].split(":", 1)[0]).lower()
        return host in _LOCAL_ENDPOINT_VALUES

    @staticmethod
    def _looks_like_connection_string(value: str) -> bool:
        if re.search(r"://[^/@\s:]+:[^/@\s]+@", value):
            return True
        return any(is_secret_option_key(match.group(1)) for match in _MESSAGE_SECRET_ASSIGNMENT_RE.finditer(value))

    def _sanitize_public_message(self, value: str) -> str:
        cleaned = self.remove_pii(value)
        cleaned = _MESSAGE_SECRET_ASSIGNMENT_RE.sub(
            lambda match: (
                f"{match.group(1)}={PUBLIC_REDACTED_VALUE}" if is_secret_option_key(match.group(1)) else match.group(0)
            ),
            cleaned,
        )
        cleaned = _MESSAGE_URL_RE.sub(lambda match: self._hash_public_identifier(match.group(0), "endpoint"), cleaned)
        cleaned = _MESSAGE_PATH_RE.sub(lambda match: self._hash_public_identifier(match.group(0), "path"), cleaned)
        return cleaned

    def remove_pii(self, text: str) -> str:
        if not text:
            return text

        cleaned_text = text

        for pattern in self.config.pii_patterns:
            cleaned_text = re.sub(pattern, "[REDACTED]", cleaned_text, flags=re.IGNORECASE)

        for pattern, replacement in self.config.custom_sanitizers.items():
            cleaned_text = re.sub(pattern, replacement, cleaned_text, flags=re.IGNORECASE)

        return cleaned_text


def find_public_path_leaks(value: Any, key_path: tuple[str, ...] = ()) -> list[str]:

    leaks: list[str] = []

    if isinstance(value, dict):
        for key, child in value.items():
            label = str(key)
            if isinstance(key, str) and _PRIVATE_LOCAL_PATH_RE.search(key):
                label = "<key>"
                leaks.append(".".join((*key_path, label)))
            leaks.extend(find_public_path_leaks(child, (*key_path, label)))
        return leaks

    if isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(value):
            leaks.extend(find_public_path_leaks(child, (*key_path, str(index))))
        return leaks

    if isinstance(value, str) and _PRIVATE_LOCAL_PATH_RE.search(value):
        leaks.append(".".join(key_path) or "<root>")
    return leaks


_APPLIED_STATEMENT_DROP_KEYS = ("statement", "error", "table")
_APPLIED_RECEIPT_ENTRY_DROP_KEYS = (
    "statement",
    "diff",
    "detail",
    "evidence",
    "table",
    "name",
    "expected_columns",
    "observed_columns",
)
_APPLIED_RECEIPT_OBSERVED_DROP_KEYS = ("table", "name", "columns", "evidence")
_APPLIED_DRIFT_DROP_KEYS = ("errors", "warnings", "configuration_mismatches", "missing_tables", "extra_tables")


def _redact_applied_intents(payload: Any, key: str) -> None:
    if not isinstance(payload, dict) or not payload.get(key):
        return
    items = payload[key]
    count = len(items) if isinstance(items, list) else 1
    payload[key] = [{"redacted": True} for _ in range(count)]


def _redact_applied_drift_check(drift_check: Any) -> None:
    if not isinstance(drift_check, dict):
        return
    present = [key for key in _APPLIED_DRIFT_DROP_KEYS if key in drift_check]
    for key in present:
        drift_check.pop(key)
    if present:
        drift_check["drift_redacted"] = True


def _redact_applied_receipt(receipt: Any) -> None:
    if not isinstance(receipt, dict):
        return
    if "error" in receipt:
        receipt.pop("error")
        receipt["error_redacted"] = True
    for entry in receipt.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        for key in _APPLIED_RECEIPT_ENTRY_DROP_KEYS:
            entry.pop(key, None)
        for key in ("reason", "error"):
            if key in entry:
                entry.pop(key)
                entry[f"{key}_redacted"] = True
        entry["statement_redacted"] = True
    for observed in receipt.get("observed") or []:
        if isinstance(observed, dict):
            for key in _APPLIED_RECEIPT_OBSERVED_DROP_KEYS:
                observed.pop(key, None)
            observed["redacted"] = True
    _redact_applied_intents(receipt, "dropped")


def _redact_applied_tuning(applied: dict[str, Any]) -> None:
    statements = applied.get("statements")
    if isinstance(statements, list):
        for entry in statements:
            if isinstance(entry, dict):
                for key in _APPLIED_STATEMENT_DROP_KEYS:
                    entry.pop(key, None)
                entry["statement_redacted"] = True
    _redact_applied_intents(applied, "dropped")
    _redact_applied_intents(applied, "satisfied")
    _redact_applied_receipt(applied.get("receipt"))
    _redact_applied_drift_check(applied.get("drift_check"))


def anonymize_applied_tuning_payload(applied_payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(applied_payload)
    _redact_applied_tuning(sanitized)
    return sanitized


def _strip_operator_platform_metadata(node: Any) -> None:
    if not isinstance(node, dict):
        return
    physical_operator = node.get("physical_operator")
    if isinstance(physical_operator, dict) and physical_operator.get("platform_metadata"):
        physical_operator["platform_metadata"] = {}
    for child in node.get("children") or []:
        _strip_operator_platform_metadata(child)


def anonymize_plans_payload(plans_payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(plans_payload)
    queries = sanitized.get("queries")
    if not isinstance(queries, dict):
        return sanitized
    for entry in queries.values():
        if not isinstance(entry, dict):
            continue
        plan = entry.get("plan")
        if not isinstance(plan, dict):
            continue
        if plan.get("raw_explain_output") is not None:
            plan["raw_explain_output"] = None
        _strip_operator_platform_metadata(plan.get("logical_root"))
    return sanitized


__all__ = [
    "PUBLIC_PSEUDONYM_SALT_ENV",
    "PUBLIC_REDACTED_VALUE",
    "AnonymizationConfig",
    "AnonymizationManager",
    "MissingPublicPseudonymSaltError",
    "anonymize_applied_tuning_payload",
    "anonymize_plans_payload",
    "ensure_public_pseudonym_salt",
    "find_public_path_leaks",
    "public_pseudonym_salt_path",
    "require_public_pseudonym_salt",
    "resolve_public_pseudonym_salt",
]
