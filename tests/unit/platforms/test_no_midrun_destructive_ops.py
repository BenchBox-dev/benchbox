from __future__ import annotations

import ast
import functools
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest

from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

RUN_NAMESPACES = frozenset({"benchdb", "bench-project"})
NAME_SEGMENT = r"""(?:[\w#$]+|"[^"]*"|`[^`]*`|\[[^\]]*\])"""
QUALIFIED_NAME = NAME_SEGMENT + r"(?:\." + NAME_SEGMENT + ")*"
DESTRUCTIVE_STATEMENT = re.compile(r"(?:DROP|DELETE|TRUNCATE)\b", re.IGNORECASE)
TABLE_REPLACE = re.compile(r"CREATE\s+OR\s+REPLACE\s+(?:\w+\s+){0,3}?TABLE\b", re.IGNORECASE)
PARTITION_DROP = re.compile(r"ALTER\s+TABLE\s+.+?\s+DROP\s+(?:IF\s+EXISTS\s+)?PARTITION\b", re.IGNORECASE)
TABLE_RESET = re.compile(
    r"(?:DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?|TRUNCATE\s+(?:TABLE\s+)?|DELETE\s+FROM\s+)("
    + QUALIFIED_NAME
    + r")(?:\s+(?:CASCADE|PURGE|RESTRICT)|\s+WHERE\s+.*)?",
    re.IGNORECASE,
)
TABLE_CREATE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:\w+\s+){0,3}?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(" + QUALIFIED_NAME + ")",
    re.IGNORECASE,
)
DESTRUCTIVE_CALL = re.compile(
    r"^(?i:drop|delete|truncate|remove|rmtree|rmdir|rm|unlink|destroy|purge)(?:_|[A-Z]|$)",
)
RECREATING_STAGES = frozenset({"create_schema", "load_data"})
SQL_VERB = re.compile(
    r"(?:SELECT|WITH|CREATE|INSERT|ALTER|USE|SET|SHOW|DESCRIBE|COPY|MERGE|DROP|DELETE|TRUNCATE|EXPLAIN|CALL|OPTIMIZE|"
    r"ANALYZE|MSCK|REFRESH)\b",
    re.IGNORECASE,
)
EMBEDDED_SQL = re.compile(r"""\bsql\(\s*[fFrR]?(\"{3}|'{3}|\"|')(.*?)\1\s*[,)]""", re.DOTALL)
SQL_CALLS = frozenset(
    {
        "execute",
        "executemany",
        "executescript",
        "sql",
        "query",
        "command",
        "execute_query",
        "execute_statement",
        "execute_command",
        "run_query",
        "exec",
        "raw_query",
        "query_df",
        "query_arrow",
        "query_dataframe",
        "query_iter",
    }
)
FETCH_ALL = frozenset({"fetchall", "fetchmany", "fetch_all", "fetch"})
FETCH_ONE = frozenset({"fetchone", "fetch_one"})
TEXT_ATTRIBUTES = frozenset({"token", "access_token"})
FAR_FUTURE_ATTRIBUTES = frozenset({"expires_on"})
CLASS_ATTRIBUTES = frozenset({"DataFrame", "LazyFrame"})
COUNT_QUERY = re.compile(r"\bCOUNT\s*\(", re.IGNORECASE)
CONNECTION_PROBE = re.compile(r"^\s*SELECT\s+1(?:\s+AS\s+\w+)?\s*;?\s*$", re.IGNORECASE)
FAKE_VERSION = "9.9.9"
FAKE_VERSIONS = {"duckdb": "1.4.0"}
CATALOG_NAME = "benchdb"


def split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(sql):
        char = sql[index]
        pair = sql[index : index + 2]
        if quote:
            current.append(char)
            quote = "" if char == quote else quote
        elif pair == "--":
            end = sql.find("\n", index)
            index = len(sql) if end < 0 else end
            current.append(" ")
            continue
        elif pair == "/*":
            end = sql.find("*/", index + 2)
            index = len(sql) if end < 0 else end + 2
            current.append(" ")
            continue
        elif char in "'\"`":
            quote = char
            current.append(char)
        elif char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    statements.append("".join(current))
    return [" ".join(statement.split()) for statement in statements if statement.strip()]


def is_destructive_statement(statement: str) -> bool:
    return any(pattern.match(statement) for pattern in (DESTRUCTIVE_STATEMENT, TABLE_REPLACE, PARTITION_DROP))


def has_destructive_statement(sql: str) -> bool:
    return any(is_destructive_statement(statement) for statement in split_statements(sql))


def table_key(identifier: str) -> str:
    segments = [
        segment.strip('"`[]').lower() for segment in re.findall(r"\"[^\"]*\"|`[^`]*`|\[[^\]]*\]|[\w#$]+", identifier)
    ]
    while len(segments) > 1 and segments[0] in RUN_NAMESPACES:
        segments.pop(0)
    return ".".join(segments)


@dataclass(frozen=True)
class Allowance:
    adapter: str | None
    stage: str
    statement: re.Pattern[str]
    reason: str
    after_creation: bool = False

    def permits(self, adapter: str, stage: str, statement: str) -> bool:
        return self.adapter in (None, adapter) and self.stage == stage and bool(self.statement.fullmatch(statement))


TUNING_METADATA_TABLE = (
    r"[`\"]?(?:(?:" + "|".join(map(re.escape, sorted(RUN_NAMESPACES))) + r")\.){0,2}benchbox_tuning_metadata[`\"]?"
)
TUNING_METADATA_DELETE = re.compile(r"DELETE FROM " + TUNING_METADATA_TABLE + r"(?: WHERE TRUE)?", re.IGNORECASE)
TUNING_METADATA_REPLACE = re.compile(r"CREATE OR REPLACE TABLE " + TUNING_METADATA_TABLE + r" \(.*\)", re.IGNORECASE)
ALLOWANCES = (
    Allowance(
        None,
        "save_tuning_metadata",
        TUNING_METADATA_DELETE,
        "the tuning-metadata table is rewritten as one statement while saving the run's tuning",
    ),
    Allowance(
        "bigquery",
        "save_tuning_metadata",
        TUNING_METADATA_REPLACE,
        "BigQuery rewrites the metadata table's CREATE TABLE as CREATE OR REPLACE TABLE before the rows are saved",
    ),
    Allowance(
        "snowflake",
        "load_data",
        re.compile(r"TRUNCATE TABLE REGION", re.IGNORECASE),
        "Snowflake empties the benchmark table, created earlier in the run, before COPY INTO",
        after_creation=True,
    ),
    Allowance(
        "snowpark-connect",
        "load_data",
        re.compile(r"TRUNCATE TABLE region", re.IGNORECASE),
        "Snowpark Connect empties the benchmark table before loading it; its DDL step runs outside create_schema",
    ),
    Allowance(
        "athena",
        "load_data",
        re.compile(r"DROP TABLE IF EXISTS region_staging", re.IGNORECASE),
        "Athena drops the external staging table, created earlier in the run, once the data is converted",
        after_creation=True,
    ),
    Allowance(
        "fabric_dw",
        "test_connection",
        re.compile(r"DROP TABLE #benchbox_test_temp", re.IGNORECASE),
        "the write probe drops the session temporary table it created a statement earlier",
        after_creation=True,
    ),
)


class CatalogRow(tuple):
    def get(self, key: Any, default: Any = None) -> Any:
        return default

    def __getattr__(self, attribute: str) -> Any:
        if attribute.startswith("__") and attribute.endswith("__"):
            raise AttributeError(attribute)
        return CATALOG_NAME


class Payload(dict):
    def __missing__(self, key: Any) -> str:
        return f"fake-{key}"


class ValuesRow(tuple):
    def values(self) -> list[Any]:
        return list(self)


@dataclass
class Event:
    phase: str
    stage: str
    kind: str
    detail: str
    destructive: bool
    target: str = ""

    def __str__(self) -> str:
        return f"[{self.phase}/{self.stage}] {self.kind}: {self.detail}"


def nested_strings(*values: Any) -> Iterator[str]:
    for value in values:
        if isinstance(value, str):
            yield value
        elif isinstance(value, bytes):
            yield value.decode("utf-8", "replace")
        elif isinstance(value, dict):
            yield from nested_strings(*value.values())
        elif isinstance(value, (list, tuple, set)):
            yield from nested_strings(*value)


def sql_texts(*values: Any) -> Iterator[str]:
    for text in nested_strings(*values):
        yield text
        for match in EMBEDDED_SQL.finditer(text):
            yield match.group(2).replace("\\n", "\n").replace("\\t", "\t")


def looks_like_sql(text: str) -> bool:
    statements = split_statements(text)
    return bool(statements) and bool(SQL_VERB.match(statements[0]))


def temp_root() -> Path:
    return Path(tempfile.gettempdir()).resolve()


def temp_entries() -> frozenset[str]:
    return frozenset(entry.name for entry in temp_root().iterdir())


@dataclass
class Ledger:
    world: Path
    adapter: str = ""
    phase: str = "decision"
    stage: str = "first_connection"
    events: list[Event] = field(default_factory=list)
    protected: set[str] = field(default_factory=set)
    rows: list[Any] = field(default_factory=lambda: [CatalogRow((CATALOG_NAME,) * 4)])
    scripted: list[tuple[re.Pattern[str], list[Any]]] = field(default_factory=list)
    last_sql: str = ""
    temp_entries_at_start: frozenset[str] = field(default_factory=temp_entries)
    driver_calls: dict[str, int] = field(default_factory=dict)
    statements_seen: dict[str, int] = field(default_factory=dict)

    def rows_now(self) -> list[Any]:
        for pattern, rows in self.scripted:
            if pattern.search(self.last_sql):
                return rows
        return self.rows

    def record(self, kind: str, detail: str, destructive: bool, target: str = "") -> None:
        self.events.append(Event(self.phase, self.stage, kind, detail.strip()[:240], destructive, target))

    def record_sql(self, origin: str, statement: str) -> None:
        self.last_sql = statement
        self.statements_seen[self.phase] = self.statements_seen.get(self.phase, 0) + 1
        self.record("sql", f"{origin}: {statement}", has_destructive_statement(statement), statement)

    def record_call(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        leaf = name.rsplit(".", 1)[-1]
        statements = [value for value in (*args, *kwargs.values()) if isinstance(value, str)]
        self.driver_calls[self.phase] = self.driver_calls.get(self.phase, 0) + 1
        if leaf in SQL_CALLS and statements:
            self.record_sql(name, statements[0])
            return
        texts = [text for text in sql_texts(args, kwargs) if looks_like_sql(text)]
        if destructive := next((text for text in texts if has_destructive_statement(text)), None):
            self.record_sql(name, destructive)
        elif texts:
            self.statements_seen[self.phase] = self.statements_seen.get(self.phase, 0) + len(texts)
        if not destructive and DESTRUCTIVE_CALL.match(leaf):
            self.record("call", name, True)

    def may_delete_for_real(self, target: Path) -> bool:
        resolved = target.resolve()
        if self.removes_protected_path(resolved):
            return False
        if resolved.is_relative_to(self.world.resolve()):
            return True
        root = temp_root()
        return (
            resolved.is_relative_to(root)
            and resolved != root
            and resolved.relative_to(root).parts[0] not in self.temp_entries_at_start
        )

    def protect_current_files(self) -> None:
        self.protected |= {str(path.resolve()) for path in self.world.rglob("*")}

    def removes_protected_path(self, target: Path) -> bool:
        resolved = str(target.resolve())
        return any(path == resolved or path.startswith(resolved + os.sep) for path in self.protected)

    def violations(self) -> list[Event]:
        creations = self._creations()
        found: list[Event] = []
        for position, event in enumerate(self.events):
            if event.phase != "post" or not event.destructive:
                continue
            if event.kind != "sql":
                found.append(event)
                continue
            for offset, statement in enumerate(split_statements(event.target)):
                if is_destructive_statement(statement) and not self._is_sanctioned(
                    event.stage, statement, (position, offset), creations
                ):
                    found.append(Event(event.phase, event.stage, "sql", statement[:240], True, statement))
        return found

    def _creations(self) -> list[tuple[tuple[int, int], str, str]]:
        return [
            ((position, offset), event.stage, table_key(match.group(1)))
            for position, event in enumerate(self.events)
            if event.phase == "post" and event.kind == "sql"
            for offset, statement in enumerate(split_statements(event.target))
            for match in [TABLE_CREATE.match(statement)]
            if match
        ]

    def _is_sanctioned(
        self,
        stage: str,
        statement: str,
        position: tuple[int, int],
        creations: list[tuple[tuple[int, int], str, str]],
    ) -> bool:
        reset = TABLE_RESET.fullmatch(statement)
        table = table_key(reset.group(1)) if reset else None
        for allowance in ALLOWANCES:
            if allowance.permits(self.adapter, stage, statement):
                return not allowance.after_creation or any(
                    created_at < position and name == table for created_at, _, name in creations
                )
        if stage not in RECREATING_STAGES:
            return False
        if TABLE_REPLACE.match(statement):
            return True
        return table is not None and any(
            created_stage == stage and name == table for _, created_stage, name in creations
        )

    def in_phase(self, phase: str, *, destructive: bool | None = None, kind: str | None = None) -> list[Event]:
        return [
            event
            for event in self.events
            if event.phase == phase
            and (destructive is None or event.destructive == destructive)
            and (kind is None or event.kind == kind)
        ]


_EXCEPTION_TYPES: dict[str, type[Exception]] = {}


def exception_type(name: str) -> type[Exception]:
    if name not in _EXCEPTION_TYPES:
        _EXCEPTION_TYPES[name] = type(name, (Exception,), {})
    return _EXCEPTION_TYPES[name]


def fake_class(ledger: Ledger, name: str) -> type:
    def construct(cls: type, *args: Any, **kwargs: Any) -> Any:
        return Fake(ledger, name)(*args, **kwargs)

    return type(name.rsplit(".", 1)[-1], (), {"__new__": construct})


class Fake:
    def __init__(self, ledger: Ledger, name: str) -> None:
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attribute: str) -> Any:
        if attribute.startswith("__") and attribute.endswith("__"):
            raise AttributeError(attribute)
        if attribute.endswith(("Error", "Exception", "Warning")):
            return exception_type(attribute)
        if attribute in TEXT_ATTRIBUTES:
            return "fake-" + attribute
        if attribute in FAR_FUTURE_ATTRIBUTES:
            return time.time() + 10**9
        if attribute in CLASS_ATTRIBUTES:
            return fake_class(self._ledger, f"{self._name}.{attribute}")
        if attribute == "status_code":
            return 200
        return Fake(self._ledger, f"{self._name}.{attribute}")

    def __setattr__(self, attribute: str, value: Any) -> None:
        return None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._ledger.record_call(self._name, args, kwargs)
        leaf = self._name.rsplit(".", 1)[-1]
        rows = self._ledger.rows_now()
        if leaf in FETCH_ALL:
            return list(rows)
        if leaf in FETCH_ONE:
            return rows[0] if rows else None
        if leaf == "bytes":
            return b""
        if leaf == "json":
            return Payload()
        if leaf == "default":
            return Fake(self._ledger, "credentials"), CATALOG_NAME
        return Fake(self._ledger, f"{self._name}()")

    def __enter__(self) -> Fake:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False

    def __iter__(self):
        return iter(self._ledger.rows_now())

    def __len__(self) -> int:
        return len(self._ledger.rows_now())

    def __bool__(self) -> bool:
        return True

    def __getitem__(self, key: Any) -> Any:
        try:
            return self._ledger.rows_now()[key]
        except (IndexError, TypeError):
            return Fake(self._ledger, f"{self._name}[]")

    def __setitem__(self, key: Any, value: Any) -> None:
        return None

    def __str__(self) -> str:
        return "fake"

    def __format__(self, spec: str) -> str:
        return format(1, spec) if spec else "fake"

    def __int__(self) -> int:
        return 1

    def __float__(self) -> float:
        return 1.0

    def __lt__(self, other: Any) -> bool:
        return False

    __le__ = __gt__ = __ge__ = __lt__

    def __add__(self, other: Any) -> Any:
        return other + 1 if isinstance(other, (int, float)) else NotImplemented

    __radd__ = __add__

    def __hash__(self) -> int:
        return id(self)


class FakeModule(types.ModuleType):
    def __init__(self, ledger: Ledger, name: str) -> None:
        super().__init__(name)
        self.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        self.__path__ = []
        self.__version__ = FAKE_VERSIONS.get(name, FAKE_VERSION)
        self._ledger = ledger

    def __getattr__(self, attribute: str) -> Any:
        if attribute.startswith("__") and attribute.endswith("__"):
            raise AttributeError(attribute)
        if attribute.endswith(("Error", "Exception", "Warning")):
            return exception_type(attribute)
        if attribute in CLASS_ATTRIBUTES:
            return fake_class(self._ledger, f"{self.__name__}.{attribute}")
        return Fake(self._ledger, f"{self.__name__}.{attribute}")


DRIVER_ROOTS = frozenset(
    {
        "sqlite3",
        "duckdb",
        "psycopg",
        "psycopg2",
        "pymysql",
        "pyodbc",
        "clickhouse_driver",
        "clickhouse_connect",
        "chdb",
        "snowflake",
        "databricks",
        "google",
        "boto3",
        "redshift_connector",
        "trino",
        "prestodb",
        "databend_driver",
        "singlestoredb",
        "pyathena",
        "pyspark",
        "pysail",
        "requests",
        "azure",
        "firebolt",
        "influxdb_client_3",
        "influxdb3",
        "datafusion",
        "polars",
        "botocore",
        "urllib3",
        "cryptography",
        "flightsql",
        "pyiceberg",
        "vortex",
        "delta",
        "dask",
        "cudf",
        "rmm",
        "fsspec",
        "gcsfs",
        "s3fs",
    }
)
OPTIONAL_IMPORT_NAMES = frozenset(
    {
        "google_auth",
        "bigquery",
        "storage",
        "service_account",
        "DictCursor",
        "snowflake",
        "firebolt_connect",
        "ClientCredentials",
        "FireboltCore",
        "DefaultAzureCredential",
        "DataLakeServiceClient",
        "requests",
        "HTTPAdapter",
        "Retry",
        "dataproc_v1",
        "Session",
        "InfluxDBClient3",
        "FlightSQLClient",
        "databend_driver",
        "redshift_connector",
        "DatabricksSession",
        "F",
        "boto3",
        "pyodbc",
        "databricks_sql",
        "_s2",
        "SparkSession",
        "StructType",
        "StructField",
        "StringType",
        "IntegerType",
        "LongType",
        "DoubleType",
        "pl",
        "psycopg",
        "psycopg_sql",
        "psql",
        "duckdb",
        "trino",
        "prestodb",
        "athena_connect",
        "ClickHouseClient",
        "clickhouse_connect",
        "pymysql",
        "PyMySQLDictCursor",
    }
)


class FakeServiceNeverCompletes(Exception):
    pass


def sleep_never_completes(owner: int, real_sleep: Callable[[float], None]) -> Callable[[float], None]:
    def sleep(seconds: float) -> None:
        if threading.get_ident() != owner:
            return real_sleep(seconds)
        raise FakeServiceNeverCompletes(f"fake service cannot complete a {seconds}s poll")

    return sleep


class FakeFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def find_spec(self, name: str, path: Any, target: Any = None) -> importlib.machinery.ModuleSpec | None:
        if name.split(".")[0] in DRIVER_ROOTS:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        return FakeModule(self.ledger, spec.name)

    def exec_module(self, module: types.ModuleType) -> None:
        return None


@dataclass(frozen=True)
class DriverImport:
    name: str
    module: str
    attribute: str = ""

    def resolve(self) -> Any:
        module = importlib.import_module(self.module)
        return getattr(module, self.attribute) if self.attribute else module


@dataclass(frozen=True)
class Reexport:
    name: str
    module: str
    attribute: str
    level: int

    def source_name(self, importer: types.ModuleType) -> str:
        return importlib.util.resolve_name("." * self.level + self.module, importer.__package__ or "")


@dataclass(frozen=True)
class DriverBindings:
    imports: tuple[DriverImport, ...] = ()
    availability_flags: frozenset[str] = frozenset()
    reexports: tuple[Reexport, ...] = ()

    @property
    def driven_names(self) -> frozenset[str]:
        return frozenset({binding.name for binding in self.imports} | self.availability_flags)


def module_level_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    for node in body:
        yield node
        if isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody, *(handler.body for handler in node.handlers)):
                yield from module_level_statements(block)
        elif isinstance(node, ast.If) and "TYPE_CHECKING" not in ast.unparse(node.test):
            yield from module_level_statements(node.body)
            yield from module_level_statements(node.orelse)


def imports_driver(node: ast.stmt) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] in DRIVER_ROOTS for alias in node.names)
    return isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] in DRIVER_ROOTS


def driver_imports_of(node: ast.stmt) -> Iterator[DriverImport]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in DRIVER_ROOTS:
                yield DriverImport(
                    alias.asname or alias.name.split(".")[0], alias.name if alias.asname else alias.name.split(".")[0]
                )
    elif isinstance(node, ast.ImportFrom) and imports_driver(node):
        for alias in node.names:
            if alias.name != "*":
                yield DriverImport(alias.asname or alias.name, node.module or "", alias.name)


def availability_flags_of(node: ast.Try) -> Iterator[str]:
    for block in (node.body, node.orelse, *(handler.body for handler in node.handlers)):
        for child in block:
            if (
                isinstance(child, ast.Assign)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, bool)
            ):
                yield from (target.id for target in child.targets if isinstance(target, ast.Name))


def reexports_of(node: ast.stmt) -> Iterator[Reexport]:
    if isinstance(node, ast.ImportFrom) and node.module and (node.level > 0 or node.module.split(".")[0] == "benchbox"):
        for alias in node.names:
            if alias.name != "*":
                yield Reexport(alias.asname or alias.name, node.module, alias.name, node.level)


@functools.cache
def driver_bindings(source_path: str) -> DriverBindings:
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    imports: list[DriverImport] = []
    reexports: list[Reexport] = []
    flags: set[str] = set()
    for node in module_level_statements(tree.body):
        imports.extend(driver_imports_of(node))
        reexports.extend(reexports_of(node))
        if isinstance(node, ast.Try) and any(imports_driver(child) for child in node.body):
            flags.update(availability_flags_of(node))
    return DriverBindings(tuple(imports), frozenset(flags), tuple(reexports))


def bindings_of(module: types.ModuleType) -> DriverBindings:
    source_path = getattr(module, "__file__", None)
    if not source_path or not source_path.endswith(".py") or not Path(source_path).is_file():
        return DriverBindings()
    return driver_bindings(source_path)


def driver_replacement(ledger: Ledger, owner: str, name: str, value: Any) -> Any:
    if value is None:
        return Fake(ledger, f"{owner}.{name}") if name in OPTIONAL_IMPORT_NAMES else None
    if isinstance(value, types.ModuleType):
        if value.__name__.split(".")[0] in DRIVER_ROOTS:
            return importlib.import_module(value.__name__)
        return None
    if isinstance(value, type) and issubclass(value, BaseException):
        return None
    if (getattr(value, "__module__", None) or "").split(".")[0] in DRIVER_ROOTS:
        return Fake(ledger, f"{owner}.{name}")
    return None


@dataclass
class Spec:
    config: dict[str, Any] = field(default_factory=dict)
    patches: dict[str, Any] = field(default_factory=dict)
    scripted: dict[str, list[Any]] = field(default_factory=dict)
    connection_sites: tuple[str, ...] = ()
    no_positive_control: str = ""


AWS = {
    "s3_staging_dir": "s3://bench-bucket/benchbox/",
    "aws_access_key_id": "AKIAFAKEFAKEFAKE",
    "aws_secret_access_key": "secret",
    "region": "us-east-1",
}
GCP = {
    "project_id": "bench-project",
    "region": "us-central1",
    "gcs_staging_dir": "gs://bench-bucket/benchbox",
}
ROLE_ARN = "arn:aws:iam::123456789012:role/bench"
FABRIC = {"workspace": "00000000-0000-0000-0000-000000000000"}
DUCKLAKE = {
    "catalog": "duckdb",
    "metadata_path": "{world}/db/bench.db",
    "data_path": "{world}/db/dir_db",
    "benchmark": "tpch",
    "scale_factor": 0.01,
}
DATABRICKS = Spec(
    config={
        "server_hostname": "ws.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/x",
        "access_token": "tok",
        "catalog": CATALOG_NAME,
        "schema": CATALOG_NAME,
        "staging_root": "dbfs:/Volumes/workspace/tmp",
    },
    scripted={
        r"SET use_cached_result\s*$": [("use_cached_result", "false")],
        r"SHOW TABLES IN": [(CATALOG_NAME, "region", False)],
    },
)

SPECS: dict[str, Spec] = {
    "athena": Spec(config=AWS),
    "athena-spark": Spec(
        config={**AWS, "workgroup": "spark-wg"}, patches={"_wait_for_session_ready": lambda *a, **k: None}
    ),
    "bigquery": Spec(config={"project_id": "bench-project", "dataset_id": CATALOG_NAME}),
    "clickhouse-cloud": Spec(),
    "databend": Spec(scripted={r"SHOW DATABASES": [ValuesRow((CATALOG_NAME,))]}),
    "databricks": DATABRICKS,
    "databricks-df": DATABRICKS,
    "datafusion": Spec(config={"working_dir": "{world}/db/dir_db"}),
    "dataproc": Spec(config=GCP),
    "dataproc-serverless": Spec(config=GCP),
    "ducklake": Spec(config=DUCKLAKE, scripted={r"version\(\)": [("v1.4.0",)]}),
    "emr-serverless": Spec(
        config={**AWS, "execution_role_arn": ROLE_ARN, "application_id": "app-1"},
        patches={"_ensure_application_started": lambda *a, **k: None},
    ),
    "fabric-lakehouse": Spec(config=FABRIC, connection_sites=("get_platform_info",)),
    "fabric-spark": Spec(
        config={"workspace_id": FABRIC["workspace"], "lakehouse_id": FABRIC["workspace"]},
        patches={"_ensure_session": lambda *a, **k: 1},
    ),
    "fabric_dw": Spec(
        config=FABRIC, connection_sites=("test_connection", "check_server_database_exists", "get_platform_info")
    ),
    "firebolt": Spec(),
    "glue": Spec(config={**AWS, "job_role": ROLE_ARN}),
    "influxdb": Spec(),
    "motherduck": Spec(config={"token": "tok"}, connection_sites=("execute_query", "test_connection")),
    "polars": Spec(config={"working_dir": "{world}/db/dir_db"}),
    "pyspark": Spec(),
    "quanton": Spec(config={**AWS, "api_key": "key"}),
    "snowflake": Spec(
        config={"account": "acct"},
        scripted={r"SHOW TABLES LIKE": []},
    ),
    "snowpark-connect": Spec(config={"account": "acct", "user": "user"}),
    "starrocks": Spec(connection_sites=("get_connection",)),
    "synapse": Spec(config={"server": "ws.sql.azuresynapse.net"}),
    "synapse-spark": Spec(
        config={
            "workspace_name": "ws",
            "spark_pool_name": "pool",
            "storage_account": "acct",
            "storage_container": "bench",
        },
        patches={"_ensure_session": lambda *a, **k: 1},
    ),
    "velox": Spec(config={"gluten_jar_path": "{world}/db/bench.db"}),
}

VARIANTS: dict[str, dict[str, Spec]] = {
    "ducklake": {
        "postgres_catalog": Spec(
            config={**DUCKLAKE, "catalog": "postgres"},
            scripted={r"version\(\)": [("v1.4.0",)], r"duckdb_tables\(\)": [("main", "region")]},
        )
    },
    "firebolt": {
        "cloud": Spec(
            config={
                "deployment_mode": "cloud",
                "client_id": "id",
                "client_secret": "secret",
                "account_name": "acct",
                "engine_name": "engine",
            }
        )
    },
}

REQUIRED_ADAPTERS = frozenset(
    {
        "clickhouse-local",
        "clickhouse-server",
        "clickhouse-cloud",
        "ducklake",
        "starrocks",
        "citus",
        "paradedb",
        "pg-duckdb",
        "pg-mooncake",
        "timescaledb",
        "fabric_dw",
        "fabric-lakehouse",
        "motherduck",
    }
)
DEFAULT_SPEC_ADAPTERS = frozenset(
    {
        "cedardb",
        "citus",
        "clickhouse",
        "clickhouse-local",
        "clickhouse-server",
        "doris",
        "duckdb",
        "lakesail",
        "paradedb",
        "pg-duckdb",
        "pg-mooncake",
        "postgresql",
        "presto",
        "questdb",
        "redshift",
        "singlestore",
        "spark",
        "sqlite",
        "starburst",
        "timescaledb",
        "trino",
    }
)
REQUIRED_STAGES = frozenset(
    {"create_schema", "apply_unified_tuning", "save_tuning_metadata", "load_data", "second_connection"}
)
REMOTE_SPARK_NAMESPACE = (
    "the database is a namespace in a remote Spark service that create_schema creates with IF NOT EXISTS, "
    "and create_connection never calls handle_existing_database"
)
NO_FIRST_CONNECTION_CHECK: dict[str, str] = {
    "athena-spark": REMOTE_SPARK_NAMESPACE,
    "dataproc": REMOTE_SPARK_NAMESPACE,
    "dataproc-serverless": REMOTE_SPARK_NAMESPACE,
    "emr-serverless": REMOTE_SPARK_NAMESPACE,
    "fabric-spark": REMOTE_SPARK_NAMESPACE,
    "glue": REMOTE_SPARK_NAMESPACE,
    "quanton": REMOTE_SPARK_NAMESPACE,
    "synapse-spark": REMOTE_SPARK_NAMESPACE,
    "fabric-lakehouse": "the SQL analytics endpoint is read-only and create_connection only opens an ODBC "
    "connection; it never calls handle_existing_database",
    "fabric_dw": "create_connection only opens an ODBC connection and never calls handle_existing_database; a "
    "warehouse is created in the Fabric portal, so there is no database to drop on connect and "
    "check_server_database_exists only tests that a connection opens. Tables are dropped and re-created in "
    "create_schema, which the re-create allowance covers",
    "motherduck": "create_connection opens md:<database> and never calls handle_existing_database; "
    "a requested --force-recreate fails closed in create_connection with a manual-drop error instead of "
    "silently reusing the database, which lives in the MotherDuck service",
    "snowpark-connect": "create_connection only opens a Snowpark session and never calls handle_existing_database; "
    "a requested --force-recreate fails closed in create_connection with a manual-drop error instead of being "
    "silently ignored, and create_schema only issues CREATE DATABASE and CREATE SCHEMA IF NOT EXISTS",
}
FORCE_RECREATE_FAIL_CLOSED: dict[str, str] = {
    "velox": "Velox does not support --force-recreate",
    "influxdb": "InfluxDB does not support --force-recreate",
    "firebolt": "Firebolt (Core) does not support --force-recreate",
    "snowpark-connect": "Snowpark Connect does not support --force-recreate",
    "motherduck": "MotherDuck does not support --force-recreate",
}
DEFAULT_MODE_REFUSALS: dict[str, str] = {
    "ducklake-postgres_catalog": "no BenchBox run identity",
}
FAKE_POLL = "the fake service never reports the remote job or session as finished, so the polling loop raises"
INCOMPLETE_STAGES: dict[str, tuple[frozenset[str], str]] = {
    "athena-spark": (frozenset({"load_data"}), f"load_data: {FAKE_POLL}"),
    "fabric-spark": (frozenset({"load_data"}), f"load_data: {FAKE_POLL}"),
    "synapse-spark": (
        frozenset({"create_schema", "load_data"}),
        f"create_schema and load_data: {FAKE_POLL}",
    ),
    "fabric-lakehouse": (
        frozenset({"create_schema", "load_data"}),
        "the SQL analytics endpoint is read-only, so create_schema and load_data raise ReadOnlyPlatformError by design",
    ),
}
NO_SQL_OBSERVED: dict[str, str] = {
    "polars": "the DataFrame adapter issues no SQL; only driver calls and file removals are observable",
}


@dataclass(frozen=True)
class Case:
    key: str
    variant: str
    spec: Spec

    @property
    def id(self) -> str:
        return f"{self.key}-{self.variant}" if self.variant else self.key


def adapter_names() -> list[str]:
    return sorted(PlatformRegistry.get_available_platforms())


def build_cases() -> list[Case]:
    cases = []
    for name in adapter_names():
        cases.append(Case(name, "", SPECS.get(name, Spec())))
        cases.extend(Case(name, variant, spec) for variant, spec in VARIANTS.get(name, {}).items())
    return cases


CASES = build_cases()
CASE_PARAMS = [pytest.param(case, id=case.id) for case in CASES]


def path_operation(ledger: Ledger, label: str, original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        target_arg = args[0] if args else kwargs.get("path", "")
        try:
            target = Path(os.fspath(target_arg))
        except TypeError:
            return original(*args, **kwargs)
        if ledger.may_delete_for_real(target):
            ledger.record("fs", f"{label} {target} (created during this run)", False, str(target))
            return original(*args, **kwargs)
        ledger.record("fs", f"{label} {target}", True, str(target))
        return None

    return wrapper


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "world"
    (root / "db" / "dir_db").mkdir(parents=True)
    (root / "db" / "bench.db").write_bytes(b"stub")
    for stub in ("stub.bin", "stub.parquet", "stub.csv"):
        (root / "db" / "dir_db" / stub).write_bytes(b"stub")
    (root / "data").mkdir()
    (root / "data" / "region.tbl").write_text("0|AFRICA|comment|\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("JAVA_HOME", os.environ.get("JAVA_HOME", str(root)))
    return root


def holds_driver_fakes(name: str, module: Any) -> bool:
    return isinstance(module, types.ModuleType) and (
        isinstance(module, FakeModule)
        or name.split(".")[0] in DRIVER_ROOTS
        or (
            name.startswith("benchbox.")
            and any(isinstance(value, (Fake, FakeModule)) for value in list(vars(module).values()))
        )
    )


def restore_modules(before: dict[str, Any]) -> None:
    for name in [name for name in sys.modules if name not in before and holds_driver_fakes(name, sys.modules[name])]:
        module = sys.modules.pop(name)
        parent, _, child = name.rpartition(".")
        if parent in sys.modules and vars(sys.modules[parent]).get(child) is module:
            delattr(sys.modules[parent], child)
    for name, module in before.items():
        if sys.modules.get(name) is not module:
            sys.modules[name] = module


def force_driver_bindings(monkeypatch: pytest.MonkeyPatch, modules: dict[str, types.ModuleType]) -> None:
    driven: set[tuple[str, str]] = set()
    for module_name, module in modules.items():
        bindings = bindings_of(module)
        for binding in bindings.imports:
            monkeypatch.setattr(module, binding.name, binding.resolve(), raising=False)
        for flag in bindings.availability_flags:
            monkeypatch.setattr(module, flag, True, raising=False)
        driven.update((module_name, name) for name in bindings.driven_names)
    changed = True
    while changed:
        changed = False
        for module_name, module in modules.items():
            for reexport in bindings_of(module).reexports:
                source_name = reexport.source_name(module)
                if (source_name, reexport.attribute) in driven and (module_name, reexport.name) not in driven:
                    monkeypatch.setattr(module, reexport.name, getattr(modules[source_name], reexport.attribute))
                    driven.add((module_name, reexport.name))
                    changed = True


@pytest.fixture
def ledger(world: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Ledger]:
    modules_before = dict(sys.modules)
    ledger = Ledger(world=world)
    ledger.scripted.append((CONNECTION_PROBE, [(1,)]))
    ledger.scripted.append((COUNT_QUERY, [(1,)]))
    ledger.protect_current_files()

    for module, attribute in (
        (os, "remove"),
        (os, "unlink"),
        (os, "rmdir"),
        (os, "removedirs"),
        (shutil, "rmtree"),
    ):
        original = getattr(module, attribute)
        monkeypatch.setattr(module, attribute, path_operation(ledger, f"{module.__name__}.{attribute}", original))

    path_unlink = Path.unlink
    path_rmdir = Path.rmdir

    def unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        return path_operation(ledger, "Path.unlink", lambda *a, **k: path_unlink(self, *args, **kwargs))(self)

    def rmdir(self: Path, *args: Any, **kwargs: Any) -> None:
        return path_operation(ledger, "Path.rmdir", lambda *a, **k: path_rmdir(self, *args, **kwargs))(self)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(Path, "rmdir", rmdir)

    for module_name in [name for name in sys.modules if name.split(".")[0] in DRIVER_ROOTS]:
        monkeypatch.delitem(sys.modules, module_name)
    monkeypatch.setattr(sys, "meta_path", [FakeFinder(ledger), *sys.meta_path])
    monkeypatch.setattr(time, "sleep", sleep_never_completes(threading.get_ident(), time.sleep))
    benchbox_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name.startswith("benchbox.") and module is not None
    }
    force_driver_bindings(monkeypatch, benchbox_modules)
    for module_name, module in benchbox_modules.items():
        for name, value in list(vars(module).items()):
            replacement = driver_replacement(ledger, module_name, name, value)
            if replacement is not None:
                monkeypatch.setattr(module, name, replacement)
        for name, stand_in in NETWORK_STAND_INS.items():
            if name in vars(module):
                monkeypatch.setattr(module, name, stand_in)
    session_manager = importlib.import_module("benchbox.platforms.pyspark.session").SparkSessionManager
    for attribute, value in SPARK_SESSION_STATE.items():
        monkeypatch.setattr(session_manager, attribute, value)
    yield ledger
    restore_modules(modules_before)


SPARK_SESSION_STATE: dict[str, Any] = {"_session": None, "_config": None, "_refcount": 0, "_java_validated": True}
NETWORK_STAND_INS: dict[str, Callable[..., Any]] = {
    "is_spark_connect_reachable": lambda *a, **k: True,
    "check_platform_dependencies": lambda *a, **k: (True, []),
    "_ensure_compatible_java": lambda *a, **k: None,
}


def make_adapter(case: Case, world: Path, ledger: Ledger, *, force_recreate: bool) -> Any:
    config: dict[str, Any] = {
        "database_path": str(world / "db" / "bench.db"),
        "host": "db.test",
        "username": "user",
        "password": "secret",
        "database": CATALOG_NAME,
        "schema": CATALOG_NAME,
        "catalog": CATALOG_NAME,
        "force_recreate": force_recreate,
        "tuning_enabled": True,
        "unified_tuning_configuration": UnifiedTuningConfiguration(),
    }
    config.update(
        {key: value.format(world=world) if isinstance(value, str) else value for key, value in case.spec.config.items()}
    )
    ledger.scripted[:0] = [(re.compile(pattern, re.IGNORECASE), rows) for pattern, rows in case.spec.scripted.items()]
    ledger.adapter = case.key
    adapter = PlatformRegistry.get_adapter_class(case.key)(**config)
    for attribute, replacement in case.spec.patches.items():
        setattr(adapter, attribute, replacement)
    return adapter


def make_benchmark(world: Path) -> Any:
    data_dir = world / "data"
    return SimpleNamespace(
        output_dir=data_dir,
        scale_factor=0.01,
        tables={"region": data_dir / "region.tbl"},
        _name="tpch",
        get_table_names=lambda: ["region"],
        get_schema=lambda: {"region": {"r_regionkey": "INTEGER", "r_name": "VARCHAR(25)"}},
        get_create_tables_sql=lambda *a, **k: "CREATE TABLE region (r_regionkey INTEGER, r_name VARCHAR(25));",
        get_data_source_benchmark=lambda: None,
    )


@dataclass
class Outcome:
    stage_errors: dict[str, str] = field(default_factory=dict)
    stages_ran: list[str] = field(default_factory=list)
    connections: dict[str, int] = field(default_factory=dict)
    decision_calls: int = 0
    metadata_saved: bool | None = None


MIDRUN_HELPERS = (
    "get_platform_info",
    "test_connection",
    "check_server_database_exists",
    "get_connection",
    "get_connection_from_pool",
)


def instrument(adapter: Any, ledger: Ledger, outcome: Outcome) -> Callable[..., Any]:
    original_create = adapter.create_connection
    original_handle = adapter.handle_existing_database
    depth = 0

    @functools.wraps(original_create)
    def create_connection(*args: Any, **kwargs: Any) -> Any:
        nonlocal depth
        depth += 1
        if ledger.phase == "post":
            outcome.connections[ledger.stage] = outcome.connections.get(ledger.stage, 0) + 1
        try:
            return original_create(*args, **kwargs)
        finally:
            depth -= 1
            if depth == 0 and ledger.phase == "decision":
                ledger.phase = "post"
                ledger.protect_current_files()

    def handle_existing_database(*args: Any, **kwargs: Any) -> None:
        if ledger.phase == "decision":
            outcome.decision_calls += 1
        return original_handle(*args, **kwargs)

    adapter.create_connection = create_connection
    adapter.handle_existing_database = handle_existing_database
    return create_connection


def run_stage(ledger: Ledger, outcome: Outcome, name: str, call: Callable[[], Any]) -> Any:
    ledger.stage = name
    try:
        result = call()
    except Exception as exc:
        outcome.stage_errors[name] = f"{type(exc).__name__}: {exc}"
        return None
    outcome.stages_ran.append(name)
    return result


def drive_lifecycle(adapter: Any, benchmark: Any, ledger: Ledger) -> Outcome:
    outcome = Outcome()
    create_connection = instrument(adapter, ledger, outcome)

    adapter._reset_run_scoped_state()
    adapter.benchmark = benchmark
    adapter._drift_validation_result = None
    connection = create_connection()
    adapter.connection = connection
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    adapter._applied_layout_operations = []
    tuning = adapter.get_effective_tuning_configuration()
    data_dir = benchmark.output_dir

    def stage(name: str, call: Callable[[], Any]) -> Any:
        return run_stage(ledger, outcome, name, call)

    stage("create_schema", lambda: adapter.create_schema(benchmark, connection))
    stage("apply_unified_tuning", lambda: adapter.apply_unified_tuning(tuning, connection))
    outcome.metadata_saved = stage("save_tuning_metadata", lambda: adapter.save_tuning_metadata(connection))
    stage("load_data", lambda: adapter.load_data(benchmark, connection, data_dir))
    stage("second_connection", create_connection)
    stage("new_stream_connection", lambda: adapter.new_stream_connection(connection))
    for helper in MIDRUN_HELPERS:
        if callable(getattr(adapter, helper, None)):
            adapter.connection = None
            stage(helper, getattr(adapter, helper))
    adapter.connection = None
    stage("execute_query", lambda: adapter.execute_query(None, "SELECT 1", "probe"))
    adapter.connection = connection
    stage("close_connection", lambda: adapter.close_connection(connection))
    return outcome


@dataclass(frozen=True)
class Reach:
    stages_ran: tuple[str, ...]
    stages_failed: tuple[str, ...]
    statements: int
    driver_calls: int
    reached_decision: bool


def reach_of(outcome: Outcome, ledger: Ledger) -> Reach:
    return Reach(
        stages_ran=tuple(outcome.stages_ran),
        stages_failed=tuple(outcome.stage_errors),
        statements=ledger.statements_seen.get("post", 0),
        driver_calls=ledger.driver_calls.get("post", 0),
        reached_decision=bool(outcome.decision_calls),
    )


def describe(events: list[Event]) -> str:
    return "\n".join(str(event) for event in events)


def run_case(case: Case, world: Path, ledger: Ledger, *, force_recreate: bool = True) -> tuple[Any, Outcome]:
    adapter = make_adapter(case, world, ledger, force_recreate=force_recreate)
    return adapter, drive_lifecycle(adapter, make_benchmark(world), ledger)


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_no_destructive_ops_after_first_connection_decision(case, world, ledger):
    fail_closed = FORCE_RECREATE_FAIL_CLOSED.get(case.id)
    if fail_closed is not None:
        with pytest.raises(RuntimeError, match=re.escape(fail_closed)):
            run_case(case, world, ledger)
        assert not ledger.violations(), describe(ledger.violations())
        return
    _, outcome = run_case(case, world, ledger)

    assert not ledger.violations(), describe(ledger.violations())
    assert "save_tuning_metadata" not in outcome.stage_errors, outcome.stage_errors
    assert not outcome.connections.get("save_tuning_metadata"), "tuning metadata opened its own connection"
    for site in case.spec.connection_sites:
        assert outcome.connections.get(site, 0) >= 1, f"{site} did not open a mid-run connection"


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_no_destructive_ops_in_default_reuse_mode(case, world, ledger):
    refusal = DEFAULT_MODE_REFUSALS.get(case.id)
    if refusal is not None:
        with pytest.raises(RuntimeError, match=re.escape(refusal)):
            run_case(case, world, ledger, force_recreate=False)
        assert not ledger.violations(), describe(ledger.violations())
        return
    _, outcome = run_case(case, world, ledger, force_recreate=False)

    assert not ledger.violations(), describe(ledger.violations())
    assert "save_tuning_metadata" not in outcome.stage_errors, outcome.stage_errors
    assert not outcome.connections.get("save_tuning_metadata"), "tuning metadata opened its own connection"
    for site in case.spec.connection_sites:
        assert outcome.connections.get(site, 0) >= 1, f"{site} did not open a mid-run connection"
    assert bool(outcome.decision_calls) == (case.key not in NO_FIRST_CONNECTION_CHECK), (
        f"existing-database check reached: {bool(outcome.decision_calls)}; "
        f"exemption: {NO_FIRST_CONNECTION_CHECK.get(case.key, 'none')}"
    )


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_force_recreate_still_acts_at_the_first_connection(case, world, ledger):
    fail_closed = FORCE_RECREATE_FAIL_CLOSED.get(case.id)
    if fail_closed is not None:
        with pytest.raises(RuntimeError, match=re.escape(fail_closed)):
            run_case(case, world, ledger)
        return
    adapter, outcome = run_case(case, world, ledger)

    if not outcome.decision_calls or getattr(adapter, "skip_database_management", False):
        assert not case.spec.no_positive_control, "stale exemption: adapter makes no first-connection decision"
        return
    acted = ledger.in_phase("decision", destructive=True)
    if case.spec.no_positive_control:
        assert not acted, f"exemption is stale, the adapter now acts: {describe(acted)}"
    else:
        assert acted, "force_recreate did not remove anything at the first connection"


def test_pyspark_force_recreate_drops_existing_database_only_once(world, ledger):
    case = next(case for case in CASES if case.key == "pyspark" and not case.variant)
    adapter = make_adapter(case, world, ledger, force_recreate=True)
    outcome = Outcome()
    create_connection = instrument(adapter, ledger, outcome)

    adapter._reset_run_scoped_state()
    first = create_connection()
    drops = [
        event for event in ledger.in_phase("decision", destructive=True) if "DROP DATABASE" in event.target.upper()
    ]
    assert drops, "force_recreate did not drop the existing database at the first connection:\n" + describe(
        ledger.events
    )

    second = create_connection()
    adapter.close_connection(first)
    adapter.close_connection(second)

    assert outcome.decision_calls == 1, f"existing-database decision ran {outcome.decision_calls} times"
    assert not ledger.violations(), describe(ledger.violations())


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_case_is_not_silently_vacuous(case, world, ledger):
    fail_closed = FORCE_RECREATE_FAIL_CLOSED.get(case.id)
    if fail_closed is not None:
        with pytest.raises(RuntimeError, match=re.escape(fail_closed)):
            run_case(case, world, ledger)
        return
    _, outcome = run_case(case, world, ledger)
    reach = reach_of(outcome, ledger)

    expected_failures, _ = INCOMPLETE_STAGES.get(case.key, (frozenset(), ""))
    assert set(REQUIRED_STAGES) - set(reach.stages_ran) == set(expected_failures), (
        f"stages that did not run {sorted(set(REQUIRED_STAGES) - set(reach.stages_ran))} differ from the exempt "
        f"{sorted(expected_failures)}: {outcome.stage_errors}"
    )
    assert reach.driver_calls >= 1, "no driver call was observed after the first connection"
    assert (reach.statements >= 1) == (case.key not in NO_SQL_OBSERVED), (
        f"statements observed after the first connection: {reach.statements}; "
        f"exemption: {NO_SQL_OBSERVED.get(case.key, 'none')}"
    )
    assert reach.reached_decision == (case.key not in NO_FIRST_CONNECTION_CHECK), (
        f"existing-database check reached: {reach.reached_decision}; "
        f"exemption: {NO_FIRST_CONNECTION_CHECK.get(case.key, 'none')}"
    )


def spec_coverage_problems(
    registered: set[str],
    specs: dict[str, Any],
    variants: dict[str, Any],
    defaults: frozenset[str],
    required: frozenset[str],
) -> list[str]:
    return [
        *(f"SPECS names an unregistered adapter: {name}" for name in sorted(set(specs) - registered)),
        *(f"VARIANTS names an unregistered adapter: {name}" for name in sorted(set(variants) - registered)),
        *(f"default-spec list names an unregistered adapter: {name}" for name in sorted(defaults - registered)),
        *(f"{name} has a spec and is also on the default-spec list" for name in sorted(set(specs) & defaults)),
        *(
            f"{name} is registered but has no spec and is not a default-spec adapter"
            for name in sorted(registered - set(specs) - defaults)
        ),
        *(f"required adapter is not registered: {name}" for name in sorted(required - registered)),
    ]


def test_every_registered_adapter_has_a_case():
    registered = set(PlatformRegistry.get_available_platforms())

    assert not spec_coverage_problems(registered, SPECS, VARIANTS, DEFAULT_SPEC_ADAPTERS, REQUIRED_ADAPTERS)
    assert {case.key for case in CASES} == registered


def test_exemptions_name_registered_adapters():
    registered = set(PlatformRegistry.get_available_platforms())

    for table in (NO_FIRST_CONNECTION_CHECK, INCOMPLETE_STAGES, NO_SQL_OBSERVED):
        assert set(table) <= registered
    assert set(FORCE_RECREATE_FAIL_CLOSED) <= {case.id for case in CASES}
    assert set(DEFAULT_MODE_REFUSALS) <= {case.id for case in CASES}
    assert all(stages <= REQUIRED_STAGES for stages, _ in INCOMPLETE_STAGES.values())


def test_spec_coverage_rejects_stale_misspelled_and_missing_entries():
    registered = {"alpha", "beta", "gamma"}

    assert not spec_coverage_problems(registered, {"alpha": 1}, {"beta": 1}, frozenset({"beta", "gamma"}), frozenset())
    problems = spec_coverage_problems(
        registered,
        {"alpha": 1, "alpha_renamed": 1},
        {"removed": 1},
        frozenset({"beta", "alpha", "ghost"}),
        frozenset({"delta"}),
    )
    assert problems == [
        "SPECS names an unregistered adapter: alpha_renamed",
        "VARIANTS names an unregistered adapter: removed",
        "default-spec list names an unregistered adapter: ghost",
        "alpha has a spec and is also on the default-spec list",
        "gamma is registered but has no spec and is not a default-spec adapter",
        "required adapter is not registered: delta",
    ]


def test_failed_decision_is_not_retried(world, ledger, monkeypatch):
    case = next(case for case in CASES if case.key == "sqlite" and not case.variant)
    adapter = make_adapter(case, world, ledger, force_recreate=True)
    adapter._reset_run_scoped_state()
    calls = 0

    def flaky_check(**kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("probe failure inside the decision")
        return False

    monkeypatch.setattr(adapter, "check_database_exists", flaky_check)
    with pytest.raises(RuntimeError, match="probe failure inside the decision"):
        adapter.create_connection()
    assert adapter._existing_db_decided is True

    second = adapter.create_connection()
    adapter.close_connection(second)

    assert calls == 1, "the failed first-connection decision ran again on a later connection"


@pytest.mark.parametrize("name", ["sqlite", "postgresql", "clickhouse-server", "duckdb"])
def test_harness_flags_a_repeated_decision(name, world, ledger):
    case = next(case for case in CASES if case.key == name and not case.variant)
    adapter = make_adapter(case, world, ledger, force_recreate=True)
    outcome = Outcome()
    create_connection = instrument(adapter, ledger, outcome)
    create_connection()
    assert not ledger.violations()

    adapter._existing_db_decided = False
    ledger.stage = "repeated_decision"
    create_connection()

    assert ledger.violations()


CREATE_REGION = ("CREATE TABLE region (r_regionkey INTEGER)",)
UNSAFE_SEQUENCES = {
    "reset-in-second-connection": (
        "",
        [("second_connection", ["DROP TABLE IF EXISTS benchdb.region", *CREATE_REGION])],
    ),
    "same-table-name-in-other-schema": (
        "",
        [("new_stream_connection", ["DROP TABLE prod.lineitem", "CREATE TABLE scratch.lineitem (l INTEGER)"])],
    ),
    "other-schema-in-create-schema": (
        "",
        [("create_schema", ["DROP TABLE prod.lineitem", "CREATE TABLE scratch.lineitem (l INTEGER)"])],
    ),
    "second-statement-after-table-reset": (
        "",
        [("create_schema", [*CREATE_REGION, "DROP TABLE region; DROP DATABASE benchdb"])],
    ),
    "second-statement-after-metadata-delete": (
        "",
        [("save_tuning_metadata", ["DELETE FROM benchbox_tuning_metadata; DROP DATABASE benchdb"])],
    ),
    "metadata-delete-with-filter": (
        "",
        [("save_tuning_metadata", ["DELETE FROM benchbox_tuning_metadata WHERE table_name = 'region'"])],
    ),
    "metadata-delete-in-load": ("", [("load_data", ["DELETE FROM benchbox_tuning_metadata WHERE TRUE"])]),
    "metadata-delete-in-other-schema": ("", [("save_tuning_metadata", ["DELETE FROM prod.benchbox_tuning_metadata"])]),
    "several-tables-in-one-drop": (
        "",
        [("create_schema", [*CREATE_REGION, "DROP TABLE region, prod.customer"])],
    ),
    "block-comment-before-drop": ("", [("second_connection", ["/* cleanup */ DROP DATABASE benchdb"])]),
    "line-comment-before-drop": ("", [("second_connection", ["-- cleanup\nDROP DATABASE benchdb"])]),
    "drop-after-quoted-semicolon": ("", [("second_connection", ["SELECT ';' ; DROP DATABASE benchdb"])]),
    "replace-table-outside-load": ("", [("apply_unified_tuning", ["CREATE OR REPLACE TABLE region AS SELECT 1"])]),
    "drop-partition": ("", [("second_connection", ["ALTER TABLE region DROP PARTITION (p = 1)"])]),
    "drop-partition-if-exists": ("", [("second_connection", ["ALTER TABLE region DROP IF EXISTS PARTITION p1"])]),
    "named-allowance-for-another-adapter": (
        "postgresql",
        [("create_schema", list(CREATE_REGION)), ("load_data", ["TRUNCATE TABLE REGION"])],
    ),
    "named-allowance-in-another-stage": (
        "snowflake",
        [("create_schema", list(CREATE_REGION)), ("second_connection", ["TRUNCATE TABLE REGION"])],
    ),
    "named-allowance-without-creation": ("fabric_dw", [("test_connection", ["DROP TABLE #benchbox_test_temp"])]),
    "creation-in-another-stage": (
        "",
        [("create_schema", list(CREATE_REGION)), ("load_data", ["DROP TABLE region"])],
    ),
}
SAFE_SEQUENCES = {
    "recreate-in-create-schema": (
        "",
        [("create_schema", ["DROP TABLE IF EXISTS [benchdb].[region]", "CREATE TABLE [region] (r_regionkey INTEGER)"])],
    ),
    "recreate-in-load-data": (
        "",
        [("load_data", ["DROP TABLE IF EXISTS region", "CREATE TABLE region AS SELECT 1"])],
    ),
    "replace-in-create-schema": ("", [("create_schema", ["CREATE OR REPLACE TABLE region (r_regionkey INTEGER)"])]),
    "commented-recreate": (
        "",
        [("create_schema", ["/* reset */ DROP TABLE region;", "-- rebuild\nCREATE TABLE region (r INTEGER)"])],
    ),
    "metadata-delete-in-save": ("", [("save_tuning_metadata", ["DELETE FROM benchbox_tuning_metadata WHERE TRUE"])]),
    "metadata-delete-with-quoted-qualifier": (
        "",
        [("save_tuning_metadata", ["DELETE FROM `bench-project.benchdb.BENCHBOX_TUNING_METADATA` WHERE TRUE"])],
    ),
    "snowflake-truncates-the-table-it-created": (
        "snowflake",
        [("create_schema", ["CREATE OR REPLACE TABLE region (r INTEGER)"]), ("load_data", ["TRUNCATE TABLE REGION"])],
    ),
    "athena-drops-its-staging-table": (
        "athena",
        [
            ("create_schema", ["CREATE EXTERNAL TABLE IF NOT EXISTS region_staging (r STRING)"]),
            ("load_data", ["DROP TABLE IF EXISTS region_staging"]),
        ],
    ),
    "fabric-write-probe": (
        "fabric_dw",
        [("test_connection", ["CREATE TABLE #benchbox_test_temp (id INT)", "DROP TABLE #benchbox_test_temp"])],
    ),
}


def ledger_with(tmp_path: Path, adapter: str, steps: list[tuple[str, list[str]]]) -> Ledger:
    world_dir = tmp_path / "world"
    world_dir.mkdir()
    probe = Ledger(world=world_dir, adapter=adapter, phase="post")
    for stage, statements in steps:
        probe.stage = stage
        for statement in statements:
            probe.record_sql("probe", statement)
    return probe


@pytest.mark.parametrize("name", UNSAFE_SEQUENCES)
def test_ledger_rejects_unsafe_sequence(name, tmp_path):
    adapter, steps = UNSAFE_SEQUENCES[name]

    assert ledger_with(tmp_path, adapter, steps).violations()


@pytest.mark.parametrize("name", SAFE_SEQUENCES)
def test_ledger_allows_run_owned_rewrite(name, tmp_path):
    adapter, steps = SAFE_SEQUENCES[name]

    assert not ledger_with(tmp_path, adapter, steps).violations()


def test_ledger_names_the_offending_statement_of_a_script(tmp_path):
    found = ledger_with(tmp_path, "", [("create_schema", [*CREATE_REGION, "DROP TABLE region; DROP DATABASE benchdb"])])

    assert [event.target for event in found.violations()] == ["DROP DATABASE benchdb"]


@pytest.mark.parametrize(
    "call",
    [
        "client.dropTable",
        "bucket.deleteObject",
        "fs.rm",
        "fs.rm_file",
        "blob.delete",
        "shutil.rmtree",
        "client.purge_table",
    ],
)
def test_ledger_flags_destructive_driver_calls(call, tmp_path):
    probe = ledger_with(tmp_path, "", [])
    probe.stage = "second_connection"
    probe.record_call(call, ("target",), {})

    assert probe.violations()


@pytest.mark.parametrize("library", ["fsspec", "gcsfs", "s3fs"])
def test_filesystem_library_removals_are_observed(library, ledger):
    module = importlib.import_module(library)
    assert isinstance(module, FakeModule), f"{library} was not faked by the harness"
    ledger.phase = "post"
    ledger.stage = "second_connection"
    module.fs.rm("s3://bench-bucket/benchbox/staging/part-0")

    assert ledger.violations(), f"a {library} removal was not observed"


@pytest.mark.parametrize("call", ["client.deleted", "frame.dropna", "text.removeprefix", "client.get_table"])
def test_ledger_ignores_harmless_driver_calls(call, tmp_path):
    probe = ledger_with(tmp_path, "", [])
    probe.record_call(call, ("target",), {})

    assert not probe.violations()


def test_ledger_reads_destructive_sql_inside_job_payloads(tmp_path):
    probe = ledger_with(tmp_path, "", [])
    probe.stage = "second_connection"
    probe.record_call("requests.post", (), {"json": {"code": 'spark.sql("DROP TABLE region")'}})
    probe.record_call("client.submit", ({"statements": ["SELECT 1", "DROP DATABASE benchdb"]},), {})

    assert [event.target for event in probe.violations()] == ["DROP TABLE region", "DROP DATABASE benchdb"]


def test_files_are_removed_for_real_only_when_created_during_the_run(tmp_path):
    existing_temp = Path(tempfile.mkdtemp())
    world_dir = tmp_path / "world"
    world_dir.mkdir()
    (world_dir / "kept.db").write_bytes(b"stub")
    probe = Ledger(world=world_dir)
    probe.protect_current_files()
    new_temp = Path(tempfile.mkdtemp())
    (world_dir / "scratch.tmp").write_bytes(b"stub")
    try:
        assert probe.may_delete_for_real(world_dir / "scratch.tmp")
        assert probe.may_delete_for_real(new_temp)
        assert probe.may_delete_for_real(new_temp / "staging" / "part-0")
        assert not probe.may_delete_for_real(world_dir / "kept.db")
        assert not probe.may_delete_for_real(world_dir)
        assert not probe.may_delete_for_real(existing_temp)
        assert not probe.may_delete_for_real(existing_temp / "child")
        assert not probe.may_delete_for_real(temp_root())
        assert not probe.may_delete_for_real(Path.home())
    finally:
        shutil.rmtree(existing_temp)
        shutil.rmtree(new_temp)


def test_restore_modules_drops_modules_that_hold_fakes(ledger):
    before = dict(sys.modules)
    leaked = types.ModuleType("benchbox._leak_probe")
    leaked.driver = Fake(ledger, "requests")
    unrelated = types.ModuleType("benchbox._plain_probe")
    sys.modules.update(
        {"benchbox._leak_probe": leaked, "benchbox._plain_probe": unrelated, "pyodbc": FakeModule(ledger, "pyodbc")}
    )
    sys.modules.pop("sqlite3", None)

    restore_modules(before)

    assert "benchbox._leak_probe" not in sys.modules
    assert sys.modules["benchbox._plain_probe"] is unrelated
    assert sys.modules.get("pyodbc") is before.get("pyodbc")
    assert sys.modules["sqlite3"] is before["sqlite3"]
    del sys.modules["benchbox._plain_probe"]
