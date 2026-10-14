<!-- markdownlint-disable MD024 -->

# Cloud Storage Integration API

```{tags} reference, python-api, cloud-storage
```

Complete Python API reference for BenchBox cloud storage integration.

## Overview

BenchBox provides cloud storage integration through a minimal abstraction layer built on `cloudpathlib`. The cloud storage API enables benchmarks to work with cloud storage locations (S3, GCS, Azure Blob Storage) while maintaining the same interface as local paths.

**Key Features**:

- **Unified Path Handling**: Transparent support for local and cloud paths
- **Credential Validation**: Built-in validation for cloud credentials
- **Multi-Cloud Support**: AWS S3, Google Cloud Storage, Azure Blob Storage
- **Platform Integration**: Staging wrappers for Databricks volumes, ADLS Gen2 and Snowflake stages
- **Error Handling**: Error messages that name the missing environment variables or package

The example outputs on this page show what each function does without network access or credentials.

## Quick Start

Cloud storage paths work transparently with BenchBox:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(
    scale_factor=0.01,
    output_dir="s3://my-bucket/benchbox/tpch-data"
)

benchmark.generate_data()

adapter = DuckDBAdapter()
results = adapter.run_benchmark(benchmark)
```

The benchmark takes its output directory from `output_dir`. `generate_data()` writes the data to that directory, and the adapter then runs the benchmark.

This example needs the `cloudstorage` extra and credentials for the bucket.

## Installation

Cloud storage support requires the optional `cloudstorage` dependency:

```bash
uv add benchbox --extra cloudstorage

uv pip install "benchbox[cloud]"
```

The first command installs cloud storage support only. The second installs all cloud dependencies.

## Supported Providers

| Provider | URI forms | Credentials checked by `validate_cloud_credentials` | `create_path_handler` returns |
| --- | --- | --- | --- |
| **AWS S3** | `s3://bucket/path` | `AWS_ACCESS_KEY_ID` with `AWS_SECRET_ACCESS_KEY`, or `AWS_PROFILE`, or `~/.aws/credentials`, or `~/.aws/config` | a `cloudpathlib` S3 path |
| **Google Cloud Storage** | `gs://bucket/path`, `gcs://bucket/path` | `GOOGLE_APPLICATION_CREDENTIALS` | a `cloudpathlib` GCS path; `gcs://` is rewritten to `gs://` |
| **Azure Blob Storage** | `az://container/path`, `azure://container/path` | `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY` | a `cloudpathlib` Azure Blob path; `azure://` is rewritten to `az://` |
| **Azure Data Lake Storage Gen2** | `abfss://container@account.dfs.core.windows.net/path`, `abfs://...` | `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY` | a local staging wrapper that records the URI |
| **Databricks Unity Catalog** | `dbfs:/Volumes/catalog/schema/volume/path` | none: assumed valid, checked by the Databricks adapter | a local staging wrapper that records the URI |
| **Snowflake stage** | `@~/path`, `@stage/path`, `@%table/path`, `@db.schema.stage/path` | none: assumed valid, checked by the Snowflake adapter | a local staging wrapper that records the stage |

## API Reference

### Path Detection

#### `benchbox.utils.cloud_storage.is_cloud_path`

<span id="benchbox.utils.cloud_storage.is_cloud_path"></span>

Tells whether a path names cloud storage rather than the local file system.

**Import:** `from benchbox.utils.cloud_storage import is_cloud_path` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path to classify. Any other object is converted with `str()` first. |

##### Returns

`bool`: `True` when the URI scheme is `s3`, `gs`, `gcs`, `az`, `azure`, `abfss`, `abfs` or `dbfs` (in any letter case), or when the path is a Snowflake stage reference that starts with `@`. Otherwise `False`.

The check only looks at the scheme: it does not contact a provider, read credentials or validate the rest of the path. `s3:/bucket/path` (one slash) is reported as a cloud path. Schemes such as `s3a`, `r2`, `http` and `file` return `False`.

##### Raises

Nothing it raises itself.

##### Example

```python
from benchbox.utils.cloud_storage import is_cloud_path

assert is_cloud_path("s3://bucket/path")
assert is_cloud_path("gs://bucket/path")
assert is_cloud_path("abfss://container@account.dfs.core.windows.net/path")
assert is_cloud_path("dbfs:/Volumes/catalog/schema/volume/path")
assert is_cloud_path("@~/staged/data")

assert not is_cloud_path("/local/path")
assert not is_cloud_path("./relative/path")

print(is_cloud_path("s3:/bucket/path"), is_cloud_path("s3a://bucket/path"))
```

The first five assertions use cloud paths. The next two use local paths.

```text
True False
```

### Path Creation

#### `benchbox.utils.cloud_storage.create_path_handler`

<span id="benchbox.utils.cloud_storage.create_path_handler"></span>

Returns an object that can be used like a local path for a local path, a cloud path or a remote staging target.

**Import:** `from benchbox.utils.cloud_storage import create_path_handler` · **Extras:** `cloudstorage`, needed only for `s3`, `gs`, `gcs`, `az` and `azure` paths

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | A local path, a cloud URI or a Snowflake stage reference. An object that this function returned earlier is passed back unchanged. |

##### Returns

The type depends on the path:

- **Local path:** a `pathlib.Path`. Nothing is created on disk.
- **`s3://`, `gs://`, `gcs://`, `az://`, `azure://`:** a `cloudpathlib` path (`S3Path`, `GSPath` or `AzureBlobPath`). `gcs://` becomes `gs://` and `azure://` becomes `az://`.
- **`dbfs:/Volumes/...`:** a staging wrapper around a new local temporary directory named `benchbox_dbfs_*`. Its `dbfs_target` attribute holds the URI.
- **`abfss://`, `abfs://`:** a staging wrapper around a new local temporary directory named `benchbox_abfss_*`. Its `cloud_target` attribute holds the URI.
- **Snowflake stage reference:** a staging wrapper around a new local temporary directory named `benchbox_stage_*`. Its `cloud_target` attribute holds the stage reference.

A staging wrapper works as a path-like object (`os.fspath` returns the local directory) and has `exists`, `mkdir`, `name`, `parent` and `/` for the local directory. The wrapper classes are internal. BenchBox does not delete the temporary directory when the function returns.

##### Raises

- **`ImportError`:** the path needs `cloudpathlib` and it is not installed. The message ends with the install command.
- **`ValueError`:** a `dbfs:` path does not start with `dbfs:/Volumes/`, or `cloudpathlib` rejects the cloud path, as it does for `s3:/bucket/path` (`Invalid cloud path format ...`).

##### Example

```python
import shutil
from benchbox.utils.cloud_storage import create_path_handler

local = create_path_handler("/tmp/data")
print(type(local).__name__, local)

staged = create_path_handler("abfss://container@account.dfs.core.windows.net/path")
print(type(staged).__name__, staged.cloud_target)
print(staged.exists())
shutil.rmtree(str(staged))

try:
    create_path_handler("dbfs:/other/x")
except ValueError as error:
    print(error)
```

```text
PosixPath /tmp/data
CloudStagingPath abfss://container@account.dfs.core.windows.net/path
True
Invalid dbfs:// path: dbfs:/other/x. Unity Catalog Volumes must use format: dbfs:/Volumes/catalog/schema/volume
```

With the `cloudstorage` extra installed:

```python
from benchbox.utils.cloud_storage import create_path_handler

print(repr(create_path_handler("s3://bucket/data")))
print(repr(create_path_handler("gcs://bucket/data")))
```

```text
S3Path('s3://bucket/data')
GSPath('gs://bucket/data')
```

Without it, `create_path_handler("s3://bucket/data")` raises `ImportError: cloudpathlib is required for cloud storage paths. uv pip install "benchbox[cloudstorage]"`.

### Local Staging Path Wrappers

#### `benchbox.utils.cloud_storage.DatabricksPath`

<span id="benchbox.utils.cloud_storage.DatabricksPath"></span>
<span id="benchbox.utils.cloud_storage.DatabricksPath.__init__"></span>

Associates a local file system path with a remote upload target stored in `dbfs_target`. Generators write to the local path, and the platform adapter uses the remote target.

**Import:** `from benchbox.utils.cloud_storage import DatabricksPath` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `local_path` | `str` or `Path` | required | The local path. A string is converted to `pathlib.Path`, and a `Path` is kept as given. |
| `dbfs_target` | `str` | required | The `dbfs:/Volumes/...` URI that the platform adapter uploads to. |

##### Returns

A `DatabricksPath` object. Construction does not create directories and does not upload files. The caller that supplies `local_path` owns directory persistence and cleanup.

`str(wrapper)` and `os.fspath(wrapper)` return the local path. Keep the wrapper when you pass the output directory to an adapter, because converting it to `Path` discards the remote target. The `/` operator returns a plain local `Path`, as do `joinpath()`, `parent` and `resolve()`.

Two wrappers of this class compare equal when their local paths and remote targets match. Comparison with a `str` or `Path` uses only the local path, and hashing uses only the local path. The file system queries and changes below all act on the local path.

**Attributes, methods and properties:**

<span id="benchbox.utils.cloud_storage.DatabricksPath.dbfs_target"></span>
`dbfs_target` (`str`) is the remote target supplied at construction, kept verbatim.

<span id="benchbox.utils.cloud_storage.DatabricksPath.exists"></span>
`exists()` returns `bool`: whether the local path exists.

<span id="benchbox.utils.cloud_storage.DatabricksPath.mkdir"></span>
`mkdir(parents=True, exist_ok=True)` creates the local directory and returns `None`. Both parent creation and acceptance of an existing directory default to `True`.

<span id="benchbox.utils.cloud_storage.DatabricksPath.is_dir"></span>
`is_dir()` returns `bool`: whether the local path names a directory.

<span id="benchbox.utils.cloud_storage.DatabricksPath.is_file"></span>
`is_file()` returns `bool`: whether the local path names a regular file.

<span id="benchbox.utils.cloud_storage.DatabricksPath.iterdir"></span>
`iterdir()` returns an iterator over the immediate children as plain local `Path` objects.

<span id="benchbox.utils.cloud_storage.DatabricksPath.glob"></span>
`glob(pattern: str)` returns an iterator over the matching local `Path` objects, using `pathlib` glob semantics.

<span id="benchbox.utils.cloud_storage.DatabricksPath.rglob"></span>
`rglob(pattern: str)` returns an iterator over the matching local `Path` objects, searching recursively.

<span id="benchbox.utils.cloud_storage.DatabricksPath.name"></span>
`name` is the final component of the local path, as a `str`.

<span id="benchbox.utils.cloud_storage.DatabricksPath.parent"></span>
`parent` is the local parent as a plain `Path`. The returned object does not keep the remote target.

<span id="benchbox.utils.cloud_storage.DatabricksPath.parts"></span>
`parts` is the local path components, as a `tuple`. It is a property.

<span id="benchbox.utils.cloud_storage.DatabricksPath.as_posix"></span>
`as_posix()` returns the local path with forward slashes, as a `str`.

<span id="benchbox.utils.cloud_storage.DatabricksPath.suffix"></span>
`suffix` is the suffix of the final local path component, as a `str`. It includes the leading dot when there is one.

<span id="benchbox.utils.cloud_storage.DatabricksPath.joinpath"></span>
`joinpath(*other: str | Path)` joins local components and returns a plain `Path` without the remote target.

<span id="benchbox.utils.cloud_storage.DatabricksPath.stat"></span>
`stat(*, follow_symlinks=True)` returns the local file metadata as an `os.stat_result`. It follows symbolic links unless `follow_symlinks=False`.

<span id="benchbox.utils.cloud_storage.DatabricksPath.resolve"></span>
`resolve(strict=False)` resolves the local path to an absolute plain `Path`. With `strict=False` missing components are allowed. The returned `Path` does not keep the remote target.

##### Raises

Nothing it raises itself.

#### `benchbox.utils.cloud_storage.CloudStagingPath`

<span id="benchbox.utils.cloud_storage.CloudStagingPath"></span>
<span id="benchbox.utils.cloud_storage.CloudStagingPath.__init__"></span>

Associates a local file system path with a remote upload target stored in `cloud_target`. Generators write to the local path, and the platform adapter uses the remote target.

**Import:** `from benchbox.utils.cloud_storage import CloudStagingPath` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `local_path` | `str` or `Path` | required | The local path. A string is converted to `pathlib.Path`, and a `Path` is kept as given. |
| `cloud_target` | `str` | required | The remote URI or stage reference that the platform adapter uploads to. |

##### Returns

A `CloudStagingPath` object. Construction does not create directories and does not upload files. The caller that supplies `local_path` owns directory persistence and cleanup.

`str(wrapper)` and `os.fspath(wrapper)` return the local path. Keep the wrapper when you pass the output directory to an adapter, because converting it to `Path` discards the remote target. The `/` operator returns a plain local `Path`, as do `joinpath()`, `parent` and `resolve()`.

Two wrappers of this class compare equal when their local paths and remote targets match. Comparison with a `str` or `Path` uses only the local path, and hashing uses only the local path. The file system queries and changes below all act on the local path.

**Attributes, methods and properties:**

<span id="benchbox.utils.cloud_storage.CloudStagingPath.cloud_target"></span>
`cloud_target` (`str`) is the remote target supplied at construction, kept verbatim.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.exists"></span>
`exists()` returns `bool`: whether the local path exists.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.mkdir"></span>
`mkdir(parents=True, exist_ok=True)` creates the local directory and returns `None`. Both parent creation and acceptance of an existing directory default to `True`.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.is_dir"></span>
`is_dir()` returns `bool`: whether the local path names a directory.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.is_file"></span>
`is_file()` returns `bool`: whether the local path names a regular file.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.iterdir"></span>
`iterdir()` returns an iterator over the immediate children as plain local `Path` objects.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.glob"></span>
`glob(pattern: str)` returns an iterator over the matching local `Path` objects, using `pathlib` glob semantics.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.rglob"></span>
`rglob(pattern: str)` returns an iterator over the matching local `Path` objects, searching recursively.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.name"></span>
`name` is the final component of the local path, as a `str`.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.parent"></span>
`parent` is the local parent as a plain `Path`. The returned object does not keep the remote target.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.parts"></span>
`parts` is the local path components, as a `tuple`. It is a property.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.as_posix"></span>
`as_posix()` returns the local path with forward slashes, as a `str`.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.suffix"></span>
`suffix` is the suffix of the final local path component, as a `str`. It includes the leading dot when there is one.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.joinpath"></span>
`joinpath(*other: str | Path)` joins local components and returns a plain `Path` without the remote target.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.stat"></span>
`stat(*, follow_symlinks=True)` returns the local file metadata as an `os.stat_result`. It follows symbolic links unless `follow_symlinks=False`.

<span id="benchbox.utils.cloud_storage.CloudStagingPath.resolve"></span>
`resolve(strict=False)` resolves the local path to an absolute plain `Path`. With `strict=False` missing components are allowed. The returned `Path` does not keep the remote target.

##### Raises

Nothing it raises itself.

### Credential Validation

#### `benchbox.utils.cloud_storage.validate_cloud_credentials`

<span id="benchbox.utils.cloud_storage.validate_cloud_credentials"></span>

Checks whether the credentials for a path look usable and reports what is missing.

**Import:** `from benchbox.utils.cloud_storage import validate_cloud_credentials` · **Extras:** `cloudstorage`, needed only for `s3`, `gs`, `gcs`, `az` and `azure` paths

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path whose provider is checked. |

##### Returns

`dict` with four keys:

- **`valid`** (`bool`): whether the credentials passed the checks below.
- **`provider`** (`str`): the URI scheme as written in `path` (`s3`, `gs`, `gcs`, `az`, `azure`, `abfss`, `abfs` or `dbfs`), `snowflake_stage` or `local`. It is `unknown` when `cloudpathlib` is missing.
- **`error`** (`str` or `None`): the reason when `valid` is `False`.
- **`env_vars`** (`list[str]`): the environment variables that apply to the provider. Empty for local paths and Snowflake stages.

By path:

- **Local path:** `valid` is `True`, `provider` is `local`.
- **`dbfs:`:** `valid` is `True` without a check, and `env_vars` lists `DATABRICKS_HOST` and `DATABRICKS_TOKEN`, which the Databricks SDK reads.
- **Snowflake stage:** `valid` is `True` without a check, and `provider` is `snowflake_stage`.
- **`abfss:`, `abfs:`:** `valid` is `True` when `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY` are both set. Otherwise `error` is `Missing environment variables: ...`. `cloudpathlib` is not needed.
- **`s3:`, `gs:`, `gcs:`, `az:`, `azure:`:** without `cloudpathlib`, `valid` is `False`, `provider` is `unknown` and `error` is `cloudpathlib not installed`. With it, the variables in the Supported Providers table are checked first (for S3, any one of the four credential sources). When they are missing, `error` names them. When they are present, the function builds the cloud path and calls `exists()` on it, which contacts the provider; a failure there sets `error` to `Credential validation failed: ...` or `Cloud path validation failed: ...`.

##### Raises

Nothing it raises itself: failures are reported in the result.

##### Example

```python
from benchbox.utils.cloud_storage import validate_cloud_credentials

for path in ["/tmp/data", "dbfs:/Volumes/c/s/v/p", "@~/data",
             "abfss://container@account.dfs.core.windows.net/path"]:
    print(validate_cloud_credentials(path))
```

With neither `AZURE_STORAGE_ACCOUNT_NAME` nor `AZURE_STORAGE_ACCOUNT_KEY` set:

```text
{'valid': True, 'provider': 'local', 'error': None, 'env_vars': []}
{'valid': True, 'provider': 'dbfs', 'error': None, 'env_vars': ['DATABRICKS_HOST', 'DATABRICKS_TOKEN']}
{'valid': True, 'provider': 'snowflake_stage', 'error': None, 'env_vars': []}
{'valid': False, 'provider': 'abfss', 'error': 'Missing environment variables: AZURE_STORAGE_ACCOUNT_NAME, AZURE_STORAGE_ACCOUNT_KEY', 'env_vars': ['AZURE_STORAGE_ACCOUNT_NAME', 'AZURE_STORAGE_ACCOUNT_KEY']}
```

For `gs://bucket/path` with the `cloudstorage` extra and no `GOOGLE_APPLICATION_CREDENTIALS`, the result is `{'valid': False, 'provider': 'gs', 'error': 'Missing environment variables: GOOGLE_APPLICATION_CREDENTIALS', 'env_vars': ['GOOGLE_APPLICATION_CREDENTIALS']}`.

##### Compatibility

A `valid` result for `s3`, `gs`, `gcs`, `az` and `azure` paths comes from a live request, so it can change with the network and the bucket. The `abfss`, `dbfs` and Snowflake stage results never contact a provider.

### Path Information

#### `benchbox.utils.cloud_storage.get_cloud_path_info`

<span id="benchbox.utils.cloud_storage.get_cloud_path_info"></span>

Splits a path into provider, bucket and path parts and reports whether its credentials are valid.

**Import:** `from benchbox.utils.cloud_storage import get_cloud_path_info` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path to analyse. |

##### Returns

`dict` with these keys for every path:

- **`is_cloud`** (`bool`): whether the path is cloud storage.
- **`provider`** (`str`): the URI scheme as written, `snowflake_stage` or `local`.
- **`bucket`** (`str` or `None`): the bucket or container; `None` for local paths and `dbfs:`. For a Snowflake stage it is the stage name (`~` for the user stage).
- **`path`** (`str`): the path inside the bucket without a leading slash. For a local path it is `str(path)`. For `dbfs:` it keeps its leading slash (`/Volumes/...`).
- **`credentials_valid`** (`bool`): always `True` for local, `dbfs:` and Snowflake stage paths. For other cloud paths it is the `valid` value of `validate_cloud_credentials`, so it can involve a live request.

Extra keys by path:

- **`account`:** for a URI path, the storage account for `abfss` and `abfs` URIs and `None` otherwise. For `abfss://container@account.dfs.core.windows.net/path`, `bucket` is `container` and `account` is `account`. Local, `dbfs:` and stage results have no `account` key.
- **`volume_info`:** for `dbfs:` paths, a dict with `catalog`, `schema` and `volume`.
- **`stage_info`:** for Snowflake stages, a dict with `stage` and `sub_path`.

##### Raises

Nothing it raises itself.

##### Example

```python
from benchbox.utils.cloud_storage import get_cloud_path_info

print(get_cloud_path_info("/tmp/data"))
print(get_cloud_path_info("dbfs:/Volumes/catalog/schema/volume/path"))
print(get_cloud_path_info("abfss://container@account.dfs.core.windows.net/path"))
```

The last result assumes `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY` are not set:

```text
{'is_cloud': False, 'provider': 'local', 'bucket': None, 'path': '/tmp/data', 'credentials_valid': True}
{'is_cloud': True, 'provider': 'dbfs', 'bucket': None, 'path': '/Volumes/catalog/schema/volume/path', 'credentials_valid': True, 'volume_info': {'catalog': 'catalog', 'schema': 'schema', 'volume': 'volume'}}
{'is_cloud': True, 'provider': 'abfss', 'bucket': 'container', 'account': 'account', 'path': 'path', 'credentials_valid': False}
```

### Directory Creation

#### `benchbox.utils.cloud_storage.ensure_cloud_directory`

<span id="benchbox.utils.cloud_storage.ensure_cloud_directory"></span>

Makes sure a directory exists and returns the path object for it.

**Import:** `from benchbox.utils.cloud_storage import ensure_cloud_directory` · **Extras:** `cloudstorage`, needed only for `s3`, `gs`, `gcs`, `az` and `azure` paths

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str`, `Path` or `CloudPath` | required | The directory. A `str` or `Path` goes through `create_path_handler`; a path object is used as it is. |

##### Returns

The path object, as `create_path_handler` returns it. The function calls `mkdir(parents=True, exist_ok=True)` on it, so a local directory is created with its parents and an existing one is left alone. For `dbfs:`, `abfss:` and Snowflake stage paths, the directory that is created is the local staging directory.

##### Raises

Whatever `create_path_handler` or the directory creation raises, after logging the failure: `ImportError` or `ValueError` for a bad cloud path, and `OSError` subclasses such as `PermissionError` for a local directory that cannot be created.

##### Example

```python
import tempfile
from pathlib import Path
from benchbox.utils.cloud_storage import ensure_cloud_directory

with tempfile.TemporaryDirectory() as root:
    results = ensure_cloud_directory(Path(root) / "benchbox" / "results")
    print(type(results).__name__, results.exists())
    print(ensure_cloud_directory(results) is results)
```

```text
PosixPath True
False
```

The last line is `False` because passing a `Path` builds a new `Path` object each time.

### Cloud Path Adapter

#### `benchbox.utils.cloud_storage.CloudPathAdapter`

<span id="benchbox.utils.cloud_storage.CloudPathAdapter"></span>
<span id="benchbox.utils.cloud_storage.CloudPathAdapter.__init__"></span>

Wraps a local or cloud path in one small interface for existence checks, directory creation, joining and name access.

**Import:** `from benchbox.utils.cloud_storage import CloudPathAdapter` · **Extras:** `cloudstorage`, needed only for `s3`, `gs`, `gcs`, `az` and `azure` paths

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | A local path, a cloud URI or a Snowflake stage reference. |

##### Returns

A `CloudPathAdapter` with these attributes:

- **`original_path`** (`str`): `str(path)`.
- **`is_cloud`** (`bool`): the `is_cloud_path` result.
- **`path_handler`**: the `create_path_handler` result.
- **`path_info`** (`dict`): the `get_cloud_path_info` result for a cloud path, and `{'is_cloud': False, 'provider': 'local'}` for a local path.

**Methods and properties:**

<span id="benchbox.utils.cloud_storage.CloudPathAdapter.exists"></span>
`exists()` returns `bool`. It returns `False` instead of raising when the check fails.

<span id="benchbox.utils.cloud_storage.CloudPathAdapter.mkdir"></span>
`mkdir(parents=True, exist_ok=True)` creates the directory and returns `None`.

<span id="benchbox.utils.cloud_storage.CloudPathAdapter.__truediv__"></span>
`adapter / "name"` returns a new `CloudPathAdapter` for the joined path. Joins can be chained.

<span id="benchbox.utils.cloud_storage.CloudPathAdapter.__str__"></span>
`str(adapter)` returns `str(path_handler)`.

<span id="benchbox.utils.cloud_storage.CloudPathAdapter.name"></span>
`name` is the last component of the path, as a `str`.

<span id="benchbox.utils.cloud_storage.CloudPathAdapter.parent"></span>
`parent` is a `CloudPathAdapter` for the parent directory.

##### Raises

The constructor raises what `create_path_handler` raises: `ImportError` for a `s3`, `gs`, `gcs`, `az` or `azure` path without `cloudpathlib`, and `ValueError` for a malformed cloud path or a `dbfs:` path outside `dbfs:/Volumes/`.

##### Example

```python
import tempfile
from benchbox.utils.cloud_storage import CloudPathAdapter

with tempfile.TemporaryDirectory() as root:
    adapter = CloudPathAdapter(root + "/data")
    print(adapter.is_cloud, adapter.path_info)
    print(adapter.exists())
    adapter.mkdir()
    print(adapter.exists())

    subdir = adapter / "benchbox" / "tpch"
    subdir.mkdir()
    print(subdir.exists(), subdir.name, subdir.parent.name)
    print(str(subdir).removeprefix(root))
```

```text
False {'is_cloud': False, 'provider': 'local'}
False
True
True tpch benchbox
/data/benchbox/tpch
```

With the `cloudstorage` extra installed, joining and naming work on cloud URIs without a request:

```python
from benchbox.utils.cloud_storage import CloudPathAdapter

adapter = CloudPathAdapter("s3://bucket/data")
print(adapter / "benchbox" / "tpch")
print(adapter.name, adapter.parent)
```

```text
s3://bucket/data/benchbox/tpch
data s3://bucket
```

##### Compatibility

- **Credential check at construction:** for a cloud URI the constructor calls `get_cloud_path_info`, which validates credentials. With credentials configured, that includes a live request.
- **Staging paths:** for `dbfs:`, `abfss:` and Snowflake stage paths, `path_handler` is a local staging wrapper. `exists()` checks the local staging directory, and `adapter / "name"` returns an adapter for a plain local path (`is_cloud` is `False`), which no longer carries the remote target.

### Cloud Storage Generator Mixin

#### `benchbox.utils.cloud_storage.CloudStorageGeneratorMixin`

<span id="benchbox.utils.cloud_storage.CloudStorageGeneratorMixin"></span>

Gives a data generator two helper methods for deciding whether its output directory is remote and for running its generation function on that directory.

**Import:** `from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin` · **Extras:** none

##### Parameters

None: the mixin takes no constructor arguments.

##### Returns

Subclass instances gain two methods. Both begin with an underscore because subclasses call them; they are the whole interface of the mixin.

<span id="is_cloud_output"></span>
`_is_cloud_output(output_dir) -> bool` returns `is_cloud_path(str(output_dir))`.

<span id="handle_cloud_or_local_generation"></span>
`_handle_cloud_or_local_generation(output_dir, local_generate_func, verbose=False)` calls `local_generate_func(output_dir)` and returns its result unchanged, which is normally a dict of table name to file path. `output_dir` is passed on as given, with no conversion, and `verbose` has no effect. The method uploads nothing.

##### Raises

Nothing it raises itself. Exceptions from `local_generate_func` propagate.

##### Example

```python
import tempfile
from pathlib import Path
from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin, create_path_handler

class CustomBenchmarkGenerator(CloudStorageGeneratorMixin):
    def __init__(self, row_count: int):
        self.row_count = row_count

    def generate_data(self, output_dir):
        def local_generate(local_dir):
            local_dir = Path(local_dir)
            local_dir.mkdir(parents=True, exist_ok=True)
            customer = local_dir / "customer.csv"
            customer.write_text("id,name\n" + "".join(f"{i},Customer{i}\n" for i in range(self.row_count)))
            return {"customer": customer}

        return self._handle_cloud_or_local_generation(output_dir, local_generate)

generator = CustomBenchmarkGenerator(row_count=3)
print(generator._is_cloud_output("s3://bucket/data"), generator._is_cloud_output("/tmp/data"))

with tempfile.TemporaryDirectory() as root:
    files = generator.generate_data(root + "/custom")
    print({table: path.name for table, path in files.items()})

staged = create_path_handler("abfss://container@account.dfs.core.windows.net/custom")
files = generator.generate_data(staged)
print({table: path.parent == Path(str(staged)) for table, path in files.items()}, staged.cloud_target)
```

```text
True False
{'customer': 'customer.csv'}
{'customer': True} abfss://container@account.dfs.core.windows.net/custom
```

##### Compatibility

- **Pass a handler, not a URI string:** a string such as `"s3://bucket/data"` reaches `local_generate_func` as a string, so a function that builds `Path(output_dir)` writes into a local directory with that name. Convert URIs with `create_path_handler` first, as the example does for `abfss://`.
- **Staging wrappers:** for `dbfs:`, `abfss:` and Snowflake stage targets the generation function writes to the local staging directory. Uploading from there to the remote target is not done by this mixin.
- **No upload method:** <span id="generate_with_cloud_upload"></span>the mixin has no `_generate_with_cloud_upload` method.

### Usage Guide Formatting

#### `benchbox.utils.cloud_storage.format_cloud_usage_guide`

<span id="benchbox.utils.cloud_storage.format_cloud_usage_guide"></span>

Returns a short setup guide for a cloud provider.

**Import:** `from benchbox.utils.cloud_storage import format_cloud_usage_guide` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `provider` | `str` | required | One of `s3`, `gs`, `azure` or `dbfs`. |

##### Returns

`str`: the guide, with the environment variables to set and an example `benchbox run` command. The text starts and ends with a line break. For any other value, including `gcs`, `az`, `abfss` and an empty string, it returns `No setup guide available for provider: <provider>`.

##### Raises

Nothing it raises itself.

##### Example

```python
from benchbox.utils.cloud_storage import format_cloud_usage_guide

print(format_cloud_usage_guide("gs").strip())
print()
print(format_cloud_usage_guide("abfss"))
```

```text
Google Cloud Storage Setup:
1. Set up authentication:
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

2. Usage example:
   benchbox run --platform duckdb --benchmark tpch --scale 0.01 \
                 --output gs://your-bucket/benchbox/results

No setup guide available for provider: abfss
```

##### Compatibility

`validate_cloud_credentials` reports the provider as the scheme written in the path, so its `provider` value is `gcs`, `az` or `abfss` for those spellings and has no guide here. Pass `gs` or `azure` for those providers.

### Support Validation

#### `benchbox.utils.cloud_storage.validate_cloud_path_support`

<span id="benchbox.utils.cloud_storage.validate_cloud_path_support"></span>

Tells whether `cloudpathlib` can be imported.

**Import:** `from benchbox.utils.cloud_storage import validate_cloud_path_support` · **Extras:** none

##### Parameters

None.

##### Returns

`bool`: `True` when `cloudpathlib` is installed, `False` otherwise. The function does not check credentials, and `dbfs:`, `abfss:` and Snowflake stage paths do not need `cloudpathlib`.

##### Raises

Nothing it raises itself.

##### Example

```python
from benchbox.utils.cloud_storage import validate_cloud_path_support

if validate_cloud_path_support():
    print("✅ Cloud storage support is available")
else:
    print("❌ Install cloud storage support:")
    print('   uv add benchbox --extra cloudstorage')
```

Without `cloudpathlib`:

```text
❌ Install cloud storage support:
   uv add benchbox --extra cloudstorage
```

### Provider Classification and Output Preservation

#### `benchbox.utils.cloud_storage.is_snowflake_stage_path`

<span id="benchbox.utils.cloud_storage.is_snowflake_stage_path"></span>

Tells whether a path is a Snowflake stage reference.

**Import:** `from benchbox.utils.cloud_storage import is_snowflake_stage_path` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path or stage reference to classify. |

##### Returns

`bool`: `True` for a leading `@` followed by a user stage (`@~`), a table stage (`@%table`), a named stage or a stage name with up to three qualifying parts, optionally followed by a slash and a subpath. Identifiers are unquoted or double quoted, and a doubled quote escapes a quote inside a quoted identifier. A slash inside a quoted stage identifier belongs to that identifier.

`PurePath` inputs have backslashes normalized to slashes. Strings keep their spelling. An `@` inside a cloud URI does not make it a stage.

##### Raises

Nothing it raises itself.

#### `benchbox.utils.cloud_storage.snowflake_stage_mode_error`

<span id="benchbox.utils.cloud_storage.snowflake_stage_mode_error"></span>

Returns an error message when a Snowflake stage reference cannot be used with the chosen table mode.

**Import:** `from benchbox.utils.cloud_storage import snowflake_stage_mode_error` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path to check. |
| `table_mode` | `str` | `"native"` | The Snowflake table mode. It is keyword-only. |

##### Returns

`str | None`: `None` for an input that is not a stage. For a stage, the external mode (matched without regard to letter case) returns an explanatory message, because `CREATE STAGE URL` requires a cloud URI. Every other mode accepts only user stages and rejects named and table stages, because native loads upload to each table's own stage instead of reusing `staging_root`.

The function returns the message as a string. It does not raise and does not validate credentials.

##### Raises

Nothing it raises itself.

#### `benchbox.utils.cloud_storage.is_adls_path`

<span id="benchbox.utils.cloud_storage.is_adls_path"></span>

Tells whether a path uses an Azure Data Lake Storage Gen2 scheme.

**Import:** `from benchbox.utils.cloud_storage import is_adls_path` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path to classify. |

##### Returns

`bool`: `True` for the `abfss` and `abfs` schemes. These URIs carry the storage account in the authority, so converting them to `az` would lose the account. `create_path_handler` instead stages them locally and keeps the original target.

##### Raises

Nothing it raises itself.

#### `benchbox.utils.cloud_storage.is_databricks_path`

<span id="benchbox.utils.cloud_storage.is_databricks_path"></span>

Tells whether a path uses the `dbfs` scheme.

**Import:** `from benchbox.utils.cloud_storage import is_databricks_path` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path to classify. |

##### Returns

`bool`: `True` for the `dbfs` URI scheme. The function does not check the Unity Catalog Volume path shape. `create_path_handler` requires the literal `dbfs:/Volumes/` prefix.

##### Raises

Nothing it raises itself.

#### `benchbox.utils.cloud_storage.cloud_provider_family`

<span id="benchbox.utils.cloud_storage.cloud_provider_family"></span>

Returns the provider family of a cloud path.

**Import:** `from benchbox.utils.cloud_storage import cloud_provider_family` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The path to classify. |

##### Returns

`str | None`: `aws` for `s3`, `gcp` for `gs` and `gcs`, `azure` for `az`, `azure`, `abfss` and `abfs`, and `databricks` for `dbfs`. It returns `None` for local paths, unknown schemes and Snowflake stages without a scheme. Provider gating uses these families instead of separate alias lists.

##### Raises

Nothing it raises itself.

#### `benchbox.utils.cloud_storage.CloudScheme`

<span id="benchbox.utils.cloud_storage.CloudScheme"></span>
<span id="benchbox.utils.cloud_storage.CloudScheme.__init__"></span>

An immutable named tuple that describes one supported URI scheme.

**Import:** `from benchbox.utils.cloud_storage import CloudScheme` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `canonical` | `str` | required | The spelling that `cloudpathlib` registers, or the retained spelling for a staged family. |
| `aliases` | `tuple[str, ...]` | required | The accepted alternative spellings. |
| `family` | `str` | required | The provider family, used by platform gates. |
| `env_vars` | `tuple[str, ...]` | required | The credential environment variables that `validate_cloud_credentials` checks. |
| `stages_locally` | `bool` | `False` | When `True`, the scheme uses generic local staging instead of `cloudpathlib`. `dbfs` leaves this `False` and uses the separate `DatabricksPath` branch. |

##### Returns

A `CloudScheme` tuple.

One shared scheme table drives recognition, alias normalization, provider families and credential checks, so adding a spelling cannot skip one of those checks. The non-staged aliases `gcs` and `azure` are rewritten to `gs` and `az`. `abfs` keeps its account-bearing URI instead of being rewritten to a `cloudpathlib` `az` path.

##### Raises

Nothing it raises itself.

#### `benchbox.utils.cloud_storage.normalize_output_dir`

<span id="benchbox.utils.cloud_storage.normalize_output_dir"></span>

Normalizes a benchmark output directory without losing a staging wrapper.

**Import:** `from benchbox.utils.cloud_storage import normalize_output_dir` · **Extras:** `cloudstorage`, needed only for `s3`, `gs`, `gcs`, `az` and `azure` paths

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str`, `Path` or `None` | required | The output directory to normalize. |

##### Returns

`Path | CloudPath | DatabricksPath | CloudStagingPath | None`: `None` and existing local `Path`, `DatabricksPath` and `CloudStagingPath` objects are returned as the same object. Any other value goes to `create_path_handler`, including its `cloudpathlib` support and errors.

Benchmark output-directory assignments must keep staging wrappers. Passing one through `Path` would keep only the local cache and discard the remote upload target.

##### Raises

Whatever `create_path_handler` raises: `ImportError` for an ordinary cloud path without `cloudpathlib`, and `ValueError` for a malformed cloud path or a `dbfs:` path outside `dbfs:/Volumes/`.

### Remote File Operations

#### `benchbox.utils.cloud_storage.RemoteFileSystemAdapter`

<span id="benchbox.utils.cloud_storage.RemoteFileSystemAdapter"></span>

A protocol for validation-time remote file operations. Inputs are opaque absolute remote paths that include their scheme. Implementations own the remote access.

**Import:** `from benchbox.utils.cloud_storage import RemoteFileSystemAdapter` · **Extras:** none

##### Parameters

None. This is a protocol, not a constructor.

##### Returns

An object that provides these methods:

<span id="benchbox.utils.cloud_storage.RemoteFileSystemAdapter.file_exists"></span>
`file_exists(remote_path: str)` returns `bool`: whether the remote file exists.

<span id="benchbox.utils.cloud_storage.RemoteFileSystemAdapter.read_file"></span>
`read_file(remote_path: str)` returns the file contents as `bytes`.

<span id="benchbox.utils.cloud_storage.RemoteFileSystemAdapter.write_file"></span>
`write_file(remote_path: str, content: bytes)` writes the bytes to the remote file and returns `None`.

<span id="benchbox.utils.cloud_storage.RemoteFileSystemAdapter.list_files"></span>
`list_files(remote_path: str, pattern: str = "*")` returns a `list[str]` of the matching paths under the remote location.

##### Raises

Nothing it raises itself. Each implementation defines its own errors.

#### `benchbox.utils.cloud_storage.DatabricksVolumeAdapter`

<span id="benchbox.utils.cloud_storage.DatabricksVolumeAdapter"></span>
<span id="benchbox.utils.cloud_storage.DatabricksVolumeAdapter.__init__"></span>

Implements `RemoteFileSystemAdapter` through the Databricks Files API.

**Import:** `from benchbox.utils.cloud_storage import DatabricksVolumeAdapter` · **Extras:** `databricks`, which provides `databricks-sdk`

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `workspace_client` | `Any \| None` | `None` | A Databricks workspace client to use. When it is `None`, the adapter builds one. |
| `host` | `str \| None` | `None` | The workspace host. It is keyword-only. A nonempty host gets an `https://` prefix before it is passed to `WorkspaceClient`. `None` leaves this setting to the SDK configuration. |
| `token` | `str \| None` | `None` | The access token passed to `WorkspaceClient`. It is keyword-only. `None` leaves this setting to the SDK configuration. |

##### Returns

A `DatabricksVolumeAdapter`. Construction imports `databricks.sdk` lazily, even when `workspace_client` is supplied.

The `dbfs:` marker is removed before each Files API call. These operations can make real remote requests.

<span id="benchbox.utils.cloud_storage.DatabricksVolumeAdapter.file_exists"></span>
`file_exists(remote_path)` returns `False` when the provider reports an error.

<span id="benchbox.utils.cloud_storage.DatabricksVolumeAdapter.read_file"></span>
`read_file(remote_path)` accepts either the downloaded bytes or a stream with `read()`, and returns `bytes`.

<span id="benchbox.utils.cloud_storage.DatabricksVolumeAdapter.write_file"></span>
`write_file(remote_path, content)` uploads the bytes in a `BytesIO` with `overwrite=True`.

<span id="benchbox.utils.cloud_storage.DatabricksVolumeAdapter.list_files"></span>
`list_files(remote_path, pattern="*")` takes the `path` attribute of each returned object, then `file_path`, then its string form. It matches the final slash-separated component against `pattern` with `fnmatch`. Dictionary entries get no special key lookup. A listing error returns an empty list.

##### Raises

- **`ImportError`:** the Databricks SDK cannot be imported, or its import fails.
- **`RuntimeError`:** `read_file` or `write_file` fails. The original exception is the cause.

#### `benchbox.utils.cloud_storage.get_remote_fs_adapter`

<span id="benchbox.utils.cloud_storage.get_remote_fs_adapter"></span>

Returns a remote file system adapter for a remote path.

**Import:** `from benchbox.utils.cloud_storage import get_remote_fs_adapter` · **Extras:** `databricks`, which provides `databricks-sdk`

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `remote_path` | `str` | required | The remote path. Only `dbfs` paths are supported. |

##### Returns

A `DatabricksVolumeAdapter`, built from a `WorkspaceClient` that is constructed lazily from the SDK environment configuration. The function does not check the Unity Catalog Volume prefix, and it adds no placeholder support for other providers.

##### Raises

- **`ImportError`:** the Databricks SDK is not installed.
- **`RuntimeError`:** any other initialization failure. The message gives credential guidance.
- **`ValueError`:** the path is for a provider other than `dbfs`.

## Usage Examples

### Multi-Cloud Benchmark Execution

Run benchmarks across multiple cloud providers. The benchmark steps need credentials for each provider. Without credentials, every provider is skipped and the loop prints the error from `validate_cloud_credentials`.

For each location the loop validates the credentials before it starts, reads the path information, then creates and runs the benchmark. A summary of the results is printed at the end.

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.utils.cloud_storage import (
    validate_cloud_credentials,
    get_cloud_path_info
)

cloud_locations = {
    "aws": "s3://my-benchbox-bucket/tpch-data",
    "gcp": "gs://my-benchbox-bucket/tpch-data",
    "azure": "abfss://benchbox@myaccount.dfs.core.windows.net/tpch-data"
}

results = {}

for provider, location in cloud_locations.items():
    print(f"\n{'='*60}")
    print(f"Running TPC-H benchmark on {provider.upper()}")
    print(f"{'='*60}")

    cred_result = validate_cloud_credentials(location)

    if not cred_result["valid"]:
        print(f"⚠️  Skipping {provider}: {cred_result['error']}")
        continue

    info = get_cloud_path_info(location)
    print(f"✅ Credentials valid for {info['provider']}")
    print(f"   Bucket: {info['bucket']}")
    print(f"   Path: {info['path']}")

    benchmark = TPCH(scale_factor=0.01, output_dir=location)

    try:
        benchmark.generate_data()

        adapter = DuckDBAdapter()
        result = adapter.run_benchmark(benchmark)

        results[provider] = {
            "status": "success",
            "total_time": result.total_execution_time,
            "queries": len(result.query_results)
        }

        print(f"\n✅ {provider.upper()} completed: {result.total_execution_time:.2f}s")

    except Exception as e:
        results[provider] = {"status": "failed", "error": str(e)}
        print(f"\n❌ {provider.upper()} failed: {e}")

print(f"\n{'='*60}")
print("RESULTS SUMMARY")
print(f"{'='*60}")
for provider, result in results.items():
    if result["status"] == "success":
        print(f"{provider.upper():10s}: ✅ {result['total_time']:.2f}s ({result['queries']} queries)")
    else:
        print(f"{provider.upper():10s}: ❌ {result['error']}")
```

### Credential Validation Workflow

Validate cloud credentials before benchmark execution. `validate_and_setup_storage` returns `True` at once for a local path. For a cloud path it validates the credentials. On failure it prints the provider's setup guide and returns `False`. The call at the end proceeds with the benchmark when the setup is valid. Otherwise it shows an error:

```python
from benchbox.utils.cloud_storage import (
    validate_cloud_credentials,
    format_cloud_usage_guide,
    is_cloud_path
)

def validate_and_setup_storage(output_path: str) -> bool:

    if not is_cloud_path(output_path):
        print("✅ Using local storage - no cloud setup needed")
        return True

    print(f"Cloud storage output detected: {output_path}")

    result = validate_cloud_credentials(output_path)

    if result["valid"]:
        print(f"✅ Cloud storage credentials validated")
        print(f"   Provider: {result['provider']}")
        return True
    else:
        print(f"❌ Cloud storage credentials validation failed:")
        print(f"   Provider: {result['provider']}")
        print(f"   Error: {result['error']}")
        print()

        guide = format_cloud_usage_guide(result['provider'])
        print(guide)

        return False

if validate_and_setup_storage("s3://my-bucket/data"):
    pass
else:
    print("Please configure cloud credentials and try again")
```

With the `cloudstorage` extra installed and no AWS credentials configured, the output is:

```text
Cloud storage output detected: s3://my-bucket/data
❌ Cloud storage credentials validation failed:
   Provider: s3
   Error: No AWS credentials found. Configure via:
  - aws configure (creates ~/.aws/credentials)
  - AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY env vars
  - AWS_PROFILE env var with named profile


AWS S3 Setup:
1. Set environment variables:
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-west-2

2. Usage example:
   benchbox run --platform duckdb --benchmark tpch --scale 0.01 \
                 --output s3://your-bucket/benchbox/results

Please configure cloud credentials and try again
```

Without `cloudpathlib`, the same call reports `Provider: unknown` and `Error: cloudpathlib not installed`.

### Cloud Path Adapter Pattern

Use CloudPathAdapter for transparent local/cloud path handling. The same code works with both local and cloud paths. The function creates a benchmark directory with `results` and `data` subdirectories. The call at the end uses a local path:

```python
from benchbox.utils.cloud_storage import CloudPathAdapter

def organize_benchmark_results(base_path: str, benchmark_name: str):

    base = CloudPathAdapter(base_path)

    benchmark_dir = base / benchmark_name
    benchmark_dir.mkdir()

    results_dir = benchmark_dir / "results"
    results_dir.mkdir()

    data_dir = benchmark_dir / "data"
    data_dir.mkdir()

    print(f"Created benchmark structure at: {base}")
    print(f"  - Results: {results_dir}")
    print(f"  - Data: {data_dir}")

    return {
        "benchmark_dir": str(benchmark_dir),
        "results_dir": str(results_dir),
        "data_dir": str(data_dir)
    }

local_dirs = organize_benchmark_results(
    "/tmp/benchbox",
    "tpch"
)

print(local_dirs)
```

```text
Created benchmark structure at: /tmp/benchbox
  - Results: /tmp/benchbox/tpch/results
  - Data: /tmp/benchbox/tpch/data
{'benchmark_dir': '/tmp/benchbox/tpch', 'results_dir': '/tmp/benchbox/tpch/results', 'data_dir': '/tmp/benchbox/tpch/data'}
```

With the `cloudstorage` extra and credentials, an `s3://` base path gives the same structure with `s3://...` values.

### Custom Data Generator with Cloud Support

Create a custom data generator on top of `CloudStorageGeneratorMixin`. The generator writes a `customer` table and an `orders` table. The first call generates into a local directory. The second call passes an ADLS Gen2 staging wrapper, so the files are generated into its local staging directory:

```python
from pathlib import Path
from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin, create_path_handler

class CustomBenchmarkGenerator(CloudStorageGeneratorMixin):

    def __init__(self, row_count: int):
        self.row_count = row_count

    def generate_data(self, output_dir, verbose: bool = False):

        def local_generate(local_dir):
            import csv

            local_dir = Path(local_dir)
            local_dir.mkdir(parents=True, exist_ok=True)

            customer_path = local_dir / "customer.csv"
            with open(customer_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['id', 'name', 'region'])
                for i in range(self.row_count):
                    writer.writerow([i, f'Customer{i}', f'Region{i % 5}'])

            orders_path = local_dir / "orders.csv"
            with open(orders_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['order_id', 'customer_id', 'amount'])
                for i in range(self.row_count * 3):
                    writer.writerow([i, i % self.row_count, i * 10.5])

            return {
                "customer": customer_path,
                "orders": orders_path
            }

        return self._handle_cloud_or_local_generation(
            output_dir,
            local_generate,
            verbose
        )

generator = CustomBenchmarkGenerator(row_count=1000)

local_paths = generator.generate_data("/tmp/custom-benchmark")
print(f"Generated locally: {local_paths}")

staged = create_path_handler("abfss://container@account.dfs.core.windows.net/custom-benchmark")
staged_paths = generator.generate_data(staged)
print(sorted(staged_paths), staged.cloud_target)
```

```text
Generated locally: {'customer': PosixPath('/tmp/custom-benchmark/customer.csv'), 'orders': PosixPath('/tmp/custom-benchmark/orders.csv')}
['customer', 'orders'] abfss://container@account.dfs.core.windows.net/custom-benchmark
```

### Path Information Inspection

Inspect and analyze cloud paths programmatically. The function checks whether the path is a cloud path, gets its detailed information, and validates its credentials. The calls at the end analyze different paths:

```python
from benchbox.utils.cloud_storage import (
    is_cloud_path,
    get_cloud_path_info,
    validate_cloud_credentials
)

def analyze_storage_path(path: str):

    print(f"Analyzing path: {path}")
    print("=" * 60)

    if not is_cloud_path(path):
        print("Type: Local filesystem")
        return

    print("Type: Cloud storage")

    info = get_cloud_path_info(path)

    print(f"Provider: {info['provider'].upper()}")
    print(f"Bucket/Container: {info['bucket']}")
    print(f"Path: {info['path']}")

    cred = validate_cloud_credentials(path)

    if cred["valid"]:
        print("Credentials: ✅ Valid")
    else:
        print(f"Credentials: ❌ Invalid - {cred['error']}")
        print(f"Required environment variables: {', '.join(cred['env_vars'])}")

analyze_storage_path("abfss://container@account.dfs.core.windows.net/benchbox/tpch-data")
analyze_storage_path("/tmp/local/data")
```

With no Azure variables set:

```text
Analyzing path: abfss://container@account.dfs.core.windows.net/benchbox/tpch-data
============================================================
Type: Cloud storage
Provider: ABFSS
Bucket/Container: container
Path: benchbox/tpch-data
Credentials: ❌ Invalid - Missing environment variables: AZURE_STORAGE_ACCOUNT_NAME, AZURE_STORAGE_ACCOUNT_KEY
Required environment variables: AZURE_STORAGE_ACCOUNT_NAME, AZURE_STORAGE_ACCOUNT_KEY
Analyzing path: /tmp/local/data
============================================================
Type: Local filesystem
```

## Best Practices

1. **Always Validate Credentials**

   Validate cloud credentials before starting long-running benchmark operations:

   ```python
   from benchbox.utils.cloud_storage import validate_cloud_credentials

   result = validate_cloud_credentials(output_path)
   if not result["valid"]:
       print(f"Error: {result['error']}")
       exit(1)

   benchmark.generate_data()
   ```

2. **Use Path Adapters for Portability**

   Use CloudPathAdapter for code that works with both local and cloud storage:

   ```python
   from benchbox.utils.cloud_storage import CloudPathAdapter

   path = CloudPathAdapter(user_provided_path)
   path.mkdir()
   results_file = path / "results.json"
   ```

3. **Handle Network Errors Gracefully**

   Cloud operations can fail due to network issues - handle errors appropriately:

   ```python
   try:
       benchmark.generate_data()
   except Exception as e:
       if "credentials" in str(e).lower():
           print("Credential error - check cloud setup")
       elif "network" in str(e).lower():
           print("Network error - retry with exponential backoff")
       else:
           raise
   ```

4. **Organize Cloud Storage Efficiently**

   Use consistent naming conventions for cloud storage. The first path organizes data by benchmark and scale factor. The second includes a timestamp for results:

   ```python
   output_dir = f"s3://bucket/benchmarks/{benchmark_name}/sf{scale_factor}"

   from datetime import datetime
   timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
   results_dir = f"s3://bucket/results/{timestamp}"
   ```

5. **Reuse Generated Data**

   Cloud storage persists across runs - check for existing data before regenerating:

   ```python
   from benchbox.utils.cloud_storage import CloudPathAdapter

   output = CloudPathAdapter(output_dir)

   if output.exists():
       print("Data already exists in cloud storage - skipping generation")
   else:
       benchmark.generate_data()
   ```

   `exists()` returns `False` when the check fails, for example because of missing credentials, so a `False` result does not prove that the data is absent.

## Common Issues

### Missing cloudpathlib Dependency

**Problem**: ImportError when using cloud paths

**Solution**:

```python
from benchbox.utils.cloud_storage import validate_cloud_path_support

if not validate_cloud_path_support():
    print("Cloud storage not available. Install with:")
    print('  uv add benchbox --extra cloudstorage')
```

### Invalid Credentials

**Problem**: Cloud operations fail with credential errors

**Solution**: When validation fails, print the provider-specific setup guide:

```python
from benchbox.utils.cloud_storage import (
    validate_cloud_credentials,
    format_cloud_usage_guide
)

result = validate_cloud_credentials("s3://bucket/path")

if not result["valid"]:
    guide = format_cloud_usage_guide(result["provider"])
    print(guide)
```

### Path Format Errors

**Problem**: Invalid cloud path format

**Solution**: The first three paths are correct for AWS S3, Google Cloud Storage (`gcs://` is accepted as an alias) and Azure. `bad_s3` is missing a slash, so `create_path_handler` raises `ValueError`. `bad_s3a` is not a recognised scheme, so it is treated as a local path:

```python
s3_path = "s3://bucket/path"
gcs_path = "gs://bucket/path"
azure_path = "abfss://container@account.dfs.core.windows.net/path"

bad_s3 = "s3:/bucket/path"
bad_s3a = "s3a://bucket/path"
```

### Network Timeouts

**Problem**: Large file uploads timeout

**Solution**: For large benchmarks, test cloud connectivity with a small scale factor first, then proceed with the full scale:

```python
test_benchmark = TPCH(scale_factor=0.01, output_dir="s3://bucket/test")
test_benchmark.generate_data()

full_benchmark = TPCH(scale_factor=10.0, output_dir="s3://bucket/full")
full_benchmark.generate_data()
```

## See Also

- {doc}`/guides/cloud-storage` - Cloud storage usage guide
- {doc}`/usage/configuration` - Configuration options
- {doc}`utilities` - Other utility functions
- {doc}`/usage/troubleshooting` - Troubleshooting guide
- {doc}`platforms/databricks` - Databricks cloud integration
- {doc}`platforms/bigquery` - BigQuery cloud integration
- {doc}`platforms/snowflake` - Snowflake cloud integration

### External Resources

- [cloudpathlib Documentation](https://cloudpathlib.drivendata.org/) - Underlying cloud path library
- [AWS S3 Documentation](https://docs.aws.amazon.com/s3/) - Amazon S3 object storage
- [Google Cloud Storage Documentation](https://docs.cloud.google.com/storage/docs) - GCS documentation
- [Azure Blob Storage Documentation](https://learn.microsoft.com/en-us/azure/storage/blobs/) - Azure storage
