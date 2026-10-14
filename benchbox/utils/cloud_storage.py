# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePath
from typing import Any, Iterator, List, NamedTuple, Protocol, Union, runtime_checkable
from urllib.parse import urlparse

from benchbox.utils.dependencies import get_install_command, get_package_install_message

_UNLOADED_CLOUDPATHLIB = object()
CloudPath: Any = _UNLOADED_CLOUDPATHLIB
MissingCredentialsError: type[BaseException] = Exception


def _load_cloudpathlib() -> tuple[Any | None, type[BaseException]]:
    global CloudPath, MissingCredentialsError
    if CloudPath is _UNLOADED_CLOUDPATHLIB:
        try:
            from cloudpathlib import CloudPath as LoadedCloudPath
            from cloudpathlib.exceptions import MissingCredentialsError as LoadedMissingCredentialsError
        except ImportError:
            CloudPath = None
            MissingCredentialsError = Exception
        else:
            CloudPath = LoadedCloudPath
            MissingCredentialsError = LoadedMissingCredentialsError
    return CloudPath, MissingCredentialsError


logger = logging.getLogger(__name__)


class DatabricksPath:
    def __init__(self, local_path: Union[str, Path], dbfs_target: str):
        self._path = Path(local_path) if not isinstance(local_path, Path) else local_path
        self._dbfs_target = dbfs_target

    def __fspath__(self) -> str:
        return str(self._path)

    def __str__(self) -> str:
        return str(self._path)

    def __repr__(self) -> str:
        return f"DatabricksPath({self._path!r}, dbfs_target={self._dbfs_target!r})"

    def __truediv__(self, other: Union[str, Path]) -> Path:
        return self._path / other

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DatabricksPath):
            return self._path == other._path and self._dbfs_target == other._dbfs_target
        elif isinstance(other, (str, Path)):
            return self._path == Path(other)
        return False

    def __hash__(self) -> int:
        return hash(self._path)

    @property
    def dbfs_target(self) -> str:
        return self._dbfs_target

    def exists(self) -> bool:
        return self._path.exists()

    def mkdir(self, parents: bool = True, exist_ok: bool = True) -> None:
        self._path.mkdir(parents=parents, exist_ok=exist_ok)

    def is_dir(self) -> bool:
        return self._path.is_dir()

    def is_file(self) -> bool:
        return self._path.is_file()

    def iterdir(self):
        return self._path.iterdir()

    def glob(self, pattern: str):
        return self._path.glob(pattern)

    def rglob(self, pattern: str) -> Iterator[Path]:
        return self._path.rglob(pattern)

    @property
    def name(self) -> str:
        return self._path.name

    @property
    def parent(self) -> Path:
        return self._path.parent

    @property
    def parts(self) -> tuple:
        return self._path.parts

    def as_posix(self) -> str:
        return self._path.as_posix()

    @property
    def suffix(self) -> str:
        return self._path.suffix

    def joinpath(self, *other: Union[str, Path]) -> Path:
        return self._path.joinpath(*other)

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        return self._path.stat(follow_symlinks=follow_symlinks)

    def resolve(self, strict: bool = False) -> Path:
        return self._path.resolve(strict=strict)


class CloudStagingPath:
    def __init__(self, local_path: Union[str, Path], cloud_target: str):
        self._path = Path(local_path) if not isinstance(local_path, Path) else local_path
        self._cloud_target = cloud_target

    def __fspath__(self) -> str:
        return str(self._path)

    def __str__(self) -> str:
        return str(self._path)

    def __repr__(self) -> str:
        return f"CloudStagingPath({self._path!r}, cloud_target={self._cloud_target!r})"

    def __truediv__(self, other: Union[str, Path]) -> Path:
        return self._path / other

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CloudStagingPath):
            return self._path == other._path and self._cloud_target == other._cloud_target
        elif isinstance(other, (str, Path)):
            return self._path == Path(other)
        return False

    def __hash__(self) -> int:
        return hash(self._path)

    @property
    def cloud_target(self) -> str:
        return self._cloud_target

    def exists(self) -> bool:
        return self._path.exists()

    def mkdir(self, parents: bool = True, exist_ok: bool = True) -> None:
        self._path.mkdir(parents=parents, exist_ok=exist_ok)

    def is_dir(self) -> bool:
        return self._path.is_dir()

    def is_file(self) -> bool:
        return self._path.is_file()

    def iterdir(self):
        return self._path.iterdir()

    def glob(self, pattern: str):
        return self._path.glob(pattern)

    def rglob(self, pattern: str) -> Iterator[Path]:
        return self._path.rglob(pattern)

    @property
    def name(self) -> str:
        return self._path.name

    @property
    def parent(self) -> Path:
        return self._path.parent

    @property
    def parts(self) -> tuple:
        return self._path.parts

    @property
    def suffix(self) -> str:
        return self._path.suffix

    def joinpath(self, *other: Union[str, Path]) -> Path:
        return self._path.joinpath(*other)

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        return self._path.stat(follow_symlinks=follow_symlinks)

    def as_posix(self) -> str:
        return self._path.as_posix()

    def resolve(self, strict: bool = False) -> Path:
        return self._path.resolve(strict=strict)


@runtime_checkable
class RemoteFileSystemAdapter(Protocol):
    def file_exists(self, remote_path: str) -> bool: ...

    def read_file(self, remote_path: str) -> bytes: ...

    def write_file(self, remote_path: str, content: bytes) -> None: ...

    def list_files(self, remote_path: str, pattern: str = "*") -> list[str]: ...


class DatabricksVolumeAdapter:
    def __init__(self, workspace_client: Any | None = None, *, host: str | None = None, token: str | None = None):
        try:
            from databricks.sdk import WorkspaceClient
        except Exception as e:
            raise ImportError(
                get_package_install_message("databricks-sdk", "databricks-sdk required for DatabricksVolumeAdapter.")
            ) from e

        if workspace_client is not None:
            self._ws = workspace_client
        else:
            self._ws = WorkspaceClient(host=(f"https://{host}" if host else None), token=token)

    def _to_ws_path(self, remote_path: str) -> str:

        return remote_path.replace("dbfs:", "")

    def file_exists(self, remote_path: str) -> bool:
        path = self._to_ws_path(remote_path)
        try:
            info = self._ws.files.get(path)
            return bool(info)
        except Exception:
            return False

    def read_file(self, remote_path: str) -> bytes:
        path = self._to_ws_path(remote_path)
        try:
            data = self._ws.files.download(path)

            if hasattr(data, "read"):
                return data.read()
            return bytes(data)
        except Exception as e:
            raise RuntimeError(f"Failed to read remote file: {remote_path}: {e}") from e

    def write_file(self, remote_path: str, content: bytes) -> None:
        path = self._to_ws_path(remote_path)
        try:
            from io import BytesIO

            self._ws.files.upload(path, BytesIO(content), overwrite=True)
        except Exception as e:
            raise RuntimeError(f"Failed to write remote file: {remote_path}: {e}") from e

    def list_files(self, remote_path: str, pattern: str = "*") -> list[str]:
        path = self._to_ws_path(remote_path)
        try:
            items = self._ws.files.list(path)
            names: List[str] = []
            for it in items or []:
                p = getattr(it, "path", None) or getattr(it, "file_path", None) or str(it)
                names.append(p)

            import fnmatch

            return [n for n in names if fnmatch.fnmatch(n.split("/")[-1], pattern)]
        except Exception:
            return []


_QUOTED_STAGE_IDENTIFIER = r'"(?:[^"]|"")+"'
_STAGE_IDENTIFIER = rf"(?:{_QUOTED_STAGE_IDENTIFIER}|[A-Za-z_][A-Za-z0-9_$]*)"
_SNOWFLAKE_STAGE_RE = re.compile(
    rf"^@(?P<stage>~|%{_STAGE_IDENTIFIER}|{_STAGE_IDENTIFIER}(?:\.{_STAGE_IDENTIFIER}){{0,2}})"
    rf"(?:/(?P<sub_path>.*))?$"
)


def _match_snowflake_stage(path: Union[str, Path]) -> Union[re.Match, None]:

    if isinstance(path, PurePath):
        path = str(path).replace("\\", "/")

    if not isinstance(path, str):
        return None

    return _SNOWFLAKE_STAGE_RE.match(path)


def is_snowflake_stage_path(path: Union[str, Path]) -> bool:
    return _match_snowflake_stage(path) is not None


def snowflake_stage_mode_error(path: Union[str, Path], *, table_mode: str = "native") -> str | None:
    match = _match_snowflake_stage(path)
    if match is None:
        return None

    if str(table_mode).lower() == "external":
        return (
            "Snowflake external table mode requires a cloud URI for staging_root "
            "(for example s3://bucket/path, gs://bucket/path, or azure://container/path); "
            "Snowflake stage references such as @~/... are not valid CREATE STAGE URLs."
        )

    if match.group("stage") != "~":
        return (
            "Snowflake native loads do not reuse named or table stage references as staging_root; "
            "use the documented user stage @~/... or a cloud URI for external mode."
        )

    return None


def is_cloud_path(path: Union[str, Path]) -> bool:

    if not isinstance(path, str):
        path = str(path)

    parsed = urlparse(path)
    if parsed.scheme.lower() in _SCHEME_BY_NAME:
        return True

    return is_snowflake_stage_path(path)


class CloudScheme(NamedTuple):
    canonical: str

    aliases: tuple[str, ...]

    family: str

    env_vars: tuple[str, ...]

    stages_locally: bool = False


_CLOUD_SCHEMES: tuple[CloudScheme, ...] = (
    CloudScheme("s3", (), "aws", ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")),
    CloudScheme("gs", ("gcs",), "gcp", ("GOOGLE_APPLICATION_CREDENTIALS",)),
    CloudScheme("az", ("azure",), "azure", ("AZURE_STORAGE_ACCOUNT_NAME", "AZURE_STORAGE_ACCOUNT_KEY")),
    CloudScheme(
        "abfss",
        ("abfs",),
        "azure",
        ("AZURE_STORAGE_ACCOUNT_NAME", "AZURE_STORAGE_ACCOUNT_KEY"),
        stages_locally=True,
    ),
    CloudScheme("dbfs", (), "databricks", ()),
)

_SCHEME_BY_NAME: dict[str, CloudScheme] = {
    name: scheme for scheme in _CLOUD_SCHEMES for name in (scheme.canonical, *scheme.aliases)
}


_CLOUD_SCHEME_ALIASES = {
    alias: scheme.canonical for scheme in _CLOUD_SCHEMES if not scheme.stages_locally for alias in scheme.aliases
}


def _scheme_for(path: Union[str, Path]) -> Union[CloudScheme, None]:
    if isinstance(path, Path):
        path = str(path)
    if not isinstance(path, str):
        return None
    return _SCHEME_BY_NAME.get(urlparse(path).scheme.lower())


def _normalize_cloud_scheme(path: str) -> str:
    scheme, separator, rest = path.partition("://")
    if separator and scheme.lower() in _CLOUD_SCHEME_ALIASES:
        return f"{_CLOUD_SCHEME_ALIASES[scheme.lower()]}://{rest}"
    return path


def _build_cloud_path_for_validation(path: str, provider: str, cloud_path_cls: Any) -> Any:
    normalized = _normalize_cloud_scheme(path)
    if provider.lower() not in {"az", "azure"}:
        return cloud_path_cls(normalized)

    from azure.core.credentials import AzureNamedKeyCredential
    from cloudpathlib import AzureBlobClient

    account_name = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
    account_key = os.environ["AZURE_STORAGE_ACCOUNT_KEY"]
    client = AzureBlobClient(
        account_url=f"https://{account_name}.blob.core.windows.net",
        credential=AzureNamedKeyCredential(account_name, account_key),
    )
    return cloud_path_cls(normalized, client=client)


def is_adls_path(path: Union[str, Path]) -> bool:
    scheme = _scheme_for(path)
    return scheme is not None and scheme.family == "azure" and scheme.stages_locally


def cloud_provider_family(path: Union[str, Path]) -> Union[str, None]:
    scheme = _scheme_for(path)
    return scheme.family if scheme is not None else None


def is_databricks_path(path: Union[str, Path]) -> bool:
    if isinstance(path, Path):
        path = str(path)

    if not isinstance(path, str):
        return False

    parsed = urlparse(path)
    return parsed.scheme == "dbfs"


def validate_cloud_path_support() -> bool:
    cloud_path, _ = _load_cloudpathlib()
    return cloud_path is not None


def create_path_handler(path: Union[str, Path]) -> Union[Path, CloudPath, DatabricksPath, CloudStagingPath]:

    if isinstance(path, DatabricksPath):
        return path

    if isinstance(path, CloudStagingPath):
        return path

    cloud_path_cls = CloudPath if CloudPath is not _UNLOADED_CLOUDPATHLIB else None
    if cloud_path_cls is not None and hasattr(cloud_path_cls, "__mro__") and isinstance(path, cloud_path_cls):
        return path

    if is_databricks_path(path):
        path_str = str(path)

        if not path_str.startswith("dbfs:/Volumes/"):
            raise ValueError(
                f"Invalid dbfs:// path: {path_str}. "
                f"Unity Catalog Volumes must use format: dbfs:/Volumes/catalog/schema/volume"
            )

        import tempfile

        temp_dir_str = tempfile.mkdtemp(prefix="benchbox_dbfs_")

        databricks_path = DatabricksPath(temp_dir_str, path_str)

        logger.info(f"Created temporary directory for dbfs:// path: {databricks_path}")
        logger.debug(f"Target UC Volume: {path_str}")

        return databricks_path

    if is_snowflake_stage_path(path):
        path_str = str(path)

        import tempfile

        temp_dir_str = tempfile.mkdtemp(prefix="benchbox_stage_")
        staging_path = CloudStagingPath(temp_dir_str, path_str)

        logger.info(f"Created temporary directory for Snowflake stage path: {staging_path}")
        logger.debug(f"Target stage: {path_str}")

        return staging_path

    if is_adls_path(path):
        path_str = str(path)

        import tempfile

        temp_dir_str = tempfile.mkdtemp(prefix="benchbox_abfss_")
        staging_path = CloudStagingPath(temp_dir_str, path_str)

        logger.info(f"Created temporary directory for ADLS path: {staging_path}")
        logger.debug(f"Target ADLS URI: {path_str}")

        return staging_path

    if not is_cloud_path(path):
        return Path(path)

    cloud_path_cls, _ = _load_cloudpathlib()
    if cloud_path_cls is None:
        raise ImportError(f"cloudpathlib is required for cloud storage paths. {get_install_command('cloudstorage')}")

    normalized = _normalize_cloud_scheme(str(path))
    try:
        return cloud_path_cls(normalized)
    except Exception as e:
        raise ValueError(f"Invalid cloud path format '{path}': {e}") from e


def normalize_output_dir(
    path: Union[str, Path, None],
) -> Union[Path, CloudPath, DatabricksPath, CloudStagingPath, None]:
    if path is None:
        return None
    if isinstance(path, (DatabricksPath, CloudStagingPath)):
        return path
    if isinstance(path, Path):
        return path
    return create_path_handler(path)


def get_remote_fs_adapter(remote_path: str) -> RemoteFileSystemAdapter:
    if is_databricks_path(remote_path):
        try:
            from databricks.sdk import WorkspaceClient

            ws = WorkspaceClient()
            return DatabricksVolumeAdapter(ws)
        except ImportError as e:
            raise ImportError(
                get_package_install_message(
                    "databricks-sdk", "databricks-sdk required for Databricks UC Volume operations."
                )
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize Databricks workspace client: {e}. "
                "Ensure DATABRICKS_HOST and DATABRICKS_TOKEN are set correctly."
            ) from e

    raise ValueError(f"No RemoteFileSystemAdapter available for path: {remote_path}")


def validate_cloud_credentials(path: Union[str, Path]) -> dict[str, Any]:

    if is_databricks_path(path):
        return {
            "valid": True,
            "provider": "dbfs",
            "error": None,
            "env_vars": ["DATABRICKS_HOST", "DATABRICKS_TOKEN"],
        }

    if is_snowflake_stage_path(path):
        return {
            "valid": True,
            "provider": "snowflake_stage",
            "error": None,
            "env_vars": [],
        }

    if not is_cloud_path(path):
        return {"valid": True, "provider": "local", "error": None, "env_vars": []}

    parsed = urlparse(str(path))
    provider = parsed.scheme

    scheme_entry = _SCHEME_BY_NAME.get(provider.lower())
    expected_vars = list(scheme_entry.env_vars) if scheme_entry else []

    if is_adls_path(path):
        missing_vars = [var for var in expected_vars if not os.getenv(var)]
        if missing_vars:
            return {
                "valid": False,
                "provider": provider,
                "error": f"Missing environment variables: {', '.join(missing_vars)}",
                "env_vars": expected_vars,
            }
        return {
            "valid": True,
            "provider": provider,
            "error": None,
            "env_vars": expected_vars,
        }

    cloud_path_cls, missing_credentials_error = _load_cloudpathlib()
    if cloud_path_cls is None:
        return {
            "valid": False,
            "provider": "unknown",
            "error": "cloudpathlib not installed",
            "env_vars": [],
        }

    if provider == "s3":
        has_env_creds = bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))

        has_profile = bool(os.getenv("AWS_PROFILE"))

        credentials_file = Path(os.path.expanduser("~/.aws/credentials"))
        has_credentials_file = credentials_file.exists()

        config_file = Path(os.path.expanduser("~/.aws/config"))
        has_config_file = config_file.exists()

        if not any([has_env_creds, has_profile, has_credentials_file, has_config_file]):
            return {
                "valid": False,
                "provider": provider,
                "error": (
                    "No AWS credentials found. Configure via:\n"
                    "  - aws configure (creates ~/.aws/credentials)\n"
                    "  - AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY env vars\n"
                    "  - AWS_PROFILE env var with named profile"
                ),
                "env_vars": expected_vars,
            }
    else:
        missing_vars = [var for var in expected_vars if not os.getenv(var)]

        if missing_vars:
            return {
                "valid": False,
                "provider": provider,
                "error": f"Missing environment variables: {', '.join(missing_vars)}",
                "env_vars": expected_vars,
            }

    try:
        cloud_path = _build_cloud_path_for_validation(str(path), provider, cloud_path_cls)

        _ = cloud_path.exists()
        return {
            "valid": True,
            "provider": provider,
            "error": None,
            "env_vars": expected_vars,
        }
    except Exception as e:
        if missing_credentials_error is not Exception and isinstance(e, missing_credentials_error):
            return {
                "valid": False,
                "provider": provider,
                "error": f"Credential validation failed: {e}",
                "env_vars": expected_vars,
            }
        return {
            "valid": False,
            "provider": provider,
            "error": f"Cloud path validation failed: {e}",
            "env_vars": expected_vars,
        }


def ensure_cloud_directory(
    path: Union[str, Path, CloudPath],
) -> Union[Path, CloudPath, DatabricksPath]:
    path_handler = create_path_handler(path) if isinstance(path, (str, Path)) else path

    try:
        if hasattr(path_handler, "mkdir"):
            path_handler.mkdir(parents=True, exist_ok=True)
        elif hasattr(path_handler, "exists"):
            if not path_handler.exists():
                logger.info(f"Cloud directory will be created on first file write: {path_handler}")
    except Exception as e:
        logger.error(f"Failed to ensure directory exists: {path_handler} - {e}")
        raise

    return path_handler


def get_cloud_path_info(path: Union[str, Path]) -> dict[str, Any]:

    if is_databricks_path(path):
        parsed = urlparse(str(path))

        path_parts = parsed.path.lstrip("/").split("/")
        volume_info = {}
        if len(path_parts) >= 4 and path_parts[0] == "Volumes":
            volume_info = {
                "catalog": path_parts[1] if len(path_parts) > 1 else None,
                "schema": path_parts[2] if len(path_parts) > 2 else None,
                "volume": path_parts[3] if len(path_parts) > 3 else None,
            }

        return {
            "is_cloud": True,
            "provider": "dbfs",
            "bucket": None,
            "path": parsed.path,
            "credentials_valid": True,
            "volume_info": volume_info,
        }

    stage_match = _match_snowflake_stage(path)
    if stage_match is not None:
        stage_name = stage_match.group("stage")
        sub_path = stage_match.group("sub_path") or ""
        return {
            "is_cloud": True,
            "provider": "snowflake_stage",
            "bucket": stage_name,
            "path": sub_path,
            "credentials_valid": True,
            "stage_info": {"stage": stage_name, "sub_path": sub_path},
        }

    if not is_cloud_path(path):
        return {
            "is_cloud": False,
            "provider": "local",
            "bucket": None,
            "path": str(path),
            "credentials_valid": True,
        }

    parsed = urlparse(str(path))
    scheme = parsed.scheme
    bucket = parsed.netloc
    cloud_path = parsed.path.lstrip("/")

    account = None
    if is_adls_path(path) and "@" in bucket:
        bucket, _, account_host = bucket.partition("@")
        account = account_host.split(".", 1)[0] or None

    provider = scheme

    credential_check = validate_cloud_credentials(path)

    return {
        "is_cloud": True,
        "provider": provider,
        "bucket": bucket,
        "account": account,
        "path": cloud_path,
        "credentials_valid": credential_check["valid"],
    }


class CloudPathAdapter:
    def __init__(self, path: Union[str, Path]):
        self.original_path = str(path)
        self.is_cloud = is_cloud_path(path)
        self.path_handler = create_path_handler(path)

        if self.is_cloud:
            self.path_info = get_cloud_path_info(path)
        else:
            self.path_info = {"is_cloud": False, "provider": "local"}

    def exists(self) -> bool:
        try:
            return self.path_handler.exists()
        except Exception:
            return False

    def mkdir(self, parents: bool = True, exist_ok: bool = True) -> None:
        if hasattr(self.path_handler, "mkdir"):
            self.path_handler.mkdir(parents=parents, exist_ok=exist_ok)

    def __str__(self) -> str:
        return str(self.path_handler)

    def __truediv__(self, other: str) -> CloudPathAdapter:
        if self.is_cloud:
            new_path = str(self.path_handler / other)
        else:
            new_path = str(self.path_handler / other)
        return CloudPathAdapter(new_path)

    @property
    def name(self) -> str:
        return self.path_handler.name

    @property
    def parent(self) -> CloudPathAdapter:
        return CloudPathAdapter(str(self.path_handler.parent))


def format_cloud_usage_guide(provider: str) -> str:
    guides = {
        "dbfs": """
Databricks DBFS / Unity Catalog Volumes Setup:
1. Set environment variables for the Databricks SDK:
   export DATABRICKS_HOST=adb-123456789.azuredatabricks.net
   export DATABRICKS_TOKEN=your_personal_access_token
   Warehouse connection settings come from `benchbox setup --platform databricks`.

2. Create UC Volume (Unity Catalog):
   CREATE VOLUME IF NOT EXISTS workspace.benchbox.data;

3. Install databricks-sdk:
   uv add databricks-sdk

4. Usage example:
   benchbox run --platform databricks --benchmark tpch --scale 0.01 \\
                 --output dbfs:/Volumes/workspace/benchbox/data

Note: Data is generated locally, then uploaded to UC Volume during load phase.
      Schema and volume are created automatically if they don't exist.
""",
        "s3": """
AWS S3 Setup:
1. Set environment variables:
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-west-2

2. Usage example:
   benchbox run --platform duckdb --benchmark tpch --scale 0.01 \\
                 --output s3://your-bucket/benchbox/results
""",
        "gs": """
Google Cloud Storage Setup:
1. Set up authentication:
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

2. Usage example:
   benchbox run --platform duckdb --benchmark tpch --scale 0.01 \\
                 --output gs://your-bucket/benchbox/results
""",
        "azure": """
Azure Blob Storage Setup:
1. Set environment variables:
   export AZURE_STORAGE_ACCOUNT_NAME=your_account
   export AZURE_STORAGE_ACCOUNT_KEY=your_key

2. Usage example:
   benchbox run --platform duckdb --benchmark tpch --scale 0.01 \\
                 --output abfss://container@account.dfs.core.windows.net/benchbox/results
""",
    }

    return guides.get(provider, f"No setup guide available for provider: {provider}")


class CloudStorageGeneratorMixin:
    def _is_cloud_output(self, output_dir) -> bool:
        return is_cloud_path(str(output_dir))

    def _handle_cloud_or_local_generation(self, output_dir, local_generate_func, verbose: bool = False):

        return local_generate_func(output_dir)
