from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import psutil
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier, principal_components
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from benchbox.utils.clock import mono_time, utc_now

AUTHORIZATION_ERROR = -32003
ADMISSION_ERROR = -32004
READ_SCOPE = "benchbox:read"
EXECUTE_SCOPE = "benchbox:execute"
WRITE_SCOPE = "benchbox:write"


def _boot_identity() -> str:
    return repr(psutil.boot_time())


class TokenIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    token_sha256: str
    client_id: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=200)
    scopes: tuple[str, ...] = (READ_SCOPE,)

    @field_validator("token_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("token_sha256 must be a 64-character hexadecimal SHA-256 digest")
        return normalized


class AdmissionLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    requests_per_minute: int = Field(default=120, ge=1, le=100_000)
    global_concurrency: int = Field(default=8, ge=1, le=10_000)
    principal_concurrency: int = Field(default=2, ge=1, le=10_000)
    queue_limit: int = Field(default=32, ge=0, le=100_000)
    queue_timeout_seconds: float = Field(default=5.0, ge=0.0, le=300.0)
    lease_seconds: float = Field(default=3_600.0, gt=0.0, le=86_400.0)


class JobLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    queue_limit: int = Field(default=32, ge=1, le=100_000)
    max_queued_per_principal: int = Field(default=8, ge=1, le=100_000)
    max_running: int = Field(default=8, ge=1, le=10_000)
    max_running_per_principal: int = Field(default=2, ge=1, le=10_000)
    lease_seconds: float = Field(default=60.0, gt=0.0, le=86_400.0)
    poll_seconds: float = Field(default=0.25, gt=0.0, le=30.0)
    max_attempts: int = Field(default=2, ge=1, le=10)
    retention_seconds: float = Field(default=604_800.0, ge=60.0, le=31_536_000.0)


class RemoteSecurityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    issuer_url: AnyHttpUrl
    resource_server_url: AnyHttpUrl
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    workspace_root: Path
    state_db: Path
    tokens: tuple[TokenIdentity, ...]
    allowed_platforms: tuple[str, ...] = ()
    allowed_benchmarks: tuple[str, ...] = ()
    max_scale_factor: float = Field(default=1.0, gt=0.0)
    admission: AdmissionLimits = Field(default_factory=AdmissionLimits)
    jobs: JobLimits = Field(default_factory=JobLimits)

    @field_validator("allowed_hosts", "allowed_origins", "tokens")
    @classmethod
    def require_nonempty(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value:
            raise ValueError("remote security allowlists and tokens must not be empty")
        return value

    @field_validator("allowed_platforms", "allowed_benchmarks")
    @classmethod
    def normalize_policy_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip().lower() for item in value if item.strip()))

    @model_validator(mode="after")
    def reject_ambiguous_token_digests(self) -> RemoteSecurityConfig:
        digests = [identity.token_sha256 for identity in self.tokens]
        if len(digests) != len(set(digests)):
            raise ValueError("token_sha256 values must be unique")
        return self

    def require_remote_tls(self) -> None:
        if self.issuer_url.scheme != "https" or self.resource_server_url.scheme != "https":
            raise ValueError("remote MCP requires HTTPS issuer_url and resource_server_url")


def load_remote_security_config(path: str | Path) -> RemoteSecurityConfig:
    config_path = Path(path).expanduser().resolve(strict=True)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load MCP security configuration ({type(exc).__name__})") from exc
    if not isinstance(raw, dict):
        raise ValueError("MCP security configuration must be a JSON object")
    for key in ("workspace_root", "state_db"):
        candidate = Path(str(raw.get(key, ""))).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        raw[key] = candidate.resolve()
    config = RemoteSecurityConfig.model_validate(raw)
    config.workspace_root.mkdir(parents=True, exist_ok=True)
    config.state_db.parent.mkdir(parents=True, exist_ok=True)
    return config


class _TransportSecurityLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if message.startswith("Invalid Host header:"):
            record.msg = "Rejected invalid Host header"
            record.args = ()
        elif message.startswith("Invalid Origin header:"):
            record.msg = "Rejected invalid Origin header"
            record.args = ()
        return True


def configure_transport_security_logging() -> None:
    logger = logging.getLogger("mcp.server.transport_security")
    if not any(isinstance(item, _TransportSecurityLogFilter) for item in logger.filters):
        logger.addFilter(_TransportSecurityLogFilter())


class DigestTokenVerifier(TokenVerifier):
    def __init__(self, config: RemoteSecurityConfig):
        self._config = config

    async def verify_token(self, token: str) -> AccessToken | None:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for identity in self._config.tokens:
            if hmac.compare_digest(digest, identity.token_sha256):
                return AccessToken(
                    token=token,
                    client_id=identity.client_id,
                    subject=identity.subject,
                    scopes=list(identity.scopes),
                    resource=str(self._config.resource_server_url),
                    claims={"iss": str(self._config.issuer_url)},
                )
        return None


@dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    client_id: str
    issuer: str | None
    subject: str | None
    scopes: frozenset[str]

    @classmethod
    def from_access_token(cls, token: AccessToken) -> Principal:
        client_id, issuer, subject = principal_components(token)
        material = json.dumps([client_id, issuer, subject], separators=(",", ":"), ensure_ascii=True)
        principal_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        return cls(principal_id, client_id, issuer, subject, frozenset(token.scopes))


def authenticated_principal() -> Principal:
    token = get_access_token()
    if token is None:
        raise MCPError(AUTHORIZATION_ERROR, "Authenticated principal required")
    return Principal.from_access_token(token)


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path
    results_dir: Path
    charts_dir: Path


class TenantWorkspaceProvider:
    def __init__(self, workspace_root: Path):
        self._workspace_root = workspace_root.resolve()

    def paths_for(self, principal: Principal) -> WorkspacePaths:
        return self.paths_for_principal_id(principal.principal_id)

    def paths_for_principal_id(self, principal_id: str) -> WorkspacePaths:
        root = (self._workspace_root / principal_id).resolve()
        root.relative_to(self._workspace_root)
        results_dir = root / "results"
        charts_dir = root / "charts"
        results_dir.mkdir(parents=True, exist_ok=True)
        charts_dir.mkdir(parents=True, exist_ok=True)
        return WorkspacePaths(root, results_dir, charts_dir)

    def current_results_dir(self) -> Path:
        return self.paths_for(authenticated_principal()).results_dir

    def current_charts_dir(self) -> Path:
        return self.paths_for(authenticated_principal()).charts_dir


@dataclass(frozen=True, slots=True)
class AdmissionLease:
    ticket_id: str
    principal_id: str


class DurableSecurityStore:
    def __init__(self, path: Path, limits: AdmissionLimits):
        self.path = path
        self.limits = limits
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_rate_windows (
                    principal_id TEXT NOT NULL,
                    window_start INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY (principal_id, window_start)
                );
                CREATE TABLE IF NOT EXISTS mcp_admission_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('waiting', 'active')),
                    created_at REAL NOT NULL,
                    lease_expires_at REAL
                );
                CREATE INDEX IF NOT EXISTS mcp_admission_state_idx
                    ON mcp_admission_tickets (state, created_at);
                CREATE INDEX IF NOT EXISTS mcp_admission_principal_idx
                    ON mcp_admission_tickets (principal_id, state);
                CREATE TABLE IF NOT EXISTS mcp_audit_events (
                    event_id TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    execution_id TEXT,
                    artifact_ref TEXT
                );
                CREATE TABLE IF NOT EXISTS mcp_runtime_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    boot_id TEXT NOT NULL
                );
                """
            )
            boot_id = _boot_identity()
            self._begin(connection)
            metadata = connection.execute("SELECT boot_id FROM mcp_runtime_metadata WHERE singleton = 1").fetchone()
            if metadata is None:
                connection.execute(
                    "INSERT INTO mcp_runtime_metadata (singleton, boot_id) VALUES (1, ?)",
                    (boot_id,),
                )
            elif metadata["boot_id"] != boot_id:
                connection.execute("DELETE FROM mcp_admission_tickets")
                connection.execute("DELETE FROM mcp_rate_windows")
                connection.execute(
                    "UPDATE mcp_runtime_metadata SET boot_id = ? WHERE singleton = 1",
                    (boot_id,),
                )
            connection.commit()

    def _begin(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    def _cleanup_expired(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute("DELETE FROM mcp_admission_tickets WHERE state = 'active' AND lease_expires_at <= ?", (now,))

    def _check_rate(self, connection: sqlite3.Connection, principal_id: str, now: float) -> None:
        window_start = int(now // 60) * 60
        row = connection.execute(
            "SELECT request_count FROM mcp_rate_windows WHERE principal_id = ? AND window_start = ?",
            (principal_id, window_start),
        ).fetchone()
        if row is not None and int(row["request_count"]) >= self.limits.requests_per_minute:
            raise MCPError(ADMISSION_ERROR, "Request rate limit exceeded")
        connection.execute(
            """
            INSERT INTO mcp_rate_windows (principal_id, window_start, request_count) VALUES (?, ?, 1)
            ON CONFLICT (principal_id, window_start)
            DO UPDATE SET request_count = request_count + 1
            """,
            (principal_id, window_start),
        )
        connection.execute("DELETE FROM mcp_rate_windows WHERE window_start < ?", (window_start - 120,))

    def _capacity_available(self, connection: sqlite3.Connection, principal_id: str) -> bool:
        global_active = connection.execute(
            "SELECT COUNT(*) FROM mcp_admission_tickets WHERE state = 'active'"
        ).fetchone()[0]
        principal_active = connection.execute(
            "SELECT COUNT(*) FROM mcp_admission_tickets WHERE state = 'active' AND principal_id = ?",
            (principal_id,),
        ).fetchone()[0]
        return global_active < self.limits.global_concurrency and principal_active < self.limits.principal_concurrency

    def _enqueue(self, principal_id: str) -> str:
        ticket_id = uuid.uuid4().hex
        now = mono_time()
        with self._connect() as connection:
            self._begin(connection)
            self._cleanup_expired(connection, now)
            self._check_rate(connection, principal_id, now)
            state = "active" if self._capacity_available(connection, principal_id) else "waiting"
            if state == "waiting":
                waiting = connection.execute(
                    "SELECT COUNT(*) FROM mcp_admission_tickets WHERE state = 'waiting'"
                ).fetchone()[0]
                if waiting >= self.limits.queue_limit:
                    connection.rollback()
                    raise MCPError(ADMISSION_ERROR, "Admission queue is full")
            expires_at = now + self.limits.lease_seconds if state == "active" else None
            connection.execute(
                """INSERT INTO mcp_admission_tickets
                   (ticket_id, principal_id, state, created_at, lease_expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (ticket_id, principal_id, state, now, expires_at),
            )
            connection.commit()
        return ticket_id

    def _try_promote(self, ticket_id: str, principal_id: str) -> bool:
        now = mono_time()
        with self._connect() as connection:
            self._begin(connection)
            self._cleanup_expired(connection, now)
            row = connection.execute(
                "SELECT state FROM mcp_admission_tickets WHERE ticket_id = ? AND principal_id = ?",
                (ticket_id, principal_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            if row["state"] == "active":
                connection.commit()
                return True
            first_waiting = connection.execute(
                """SELECT waiting.ticket_id
                   FROM mcp_admission_tickets AS waiting
                   LEFT JOIN (
                       SELECT principal_id, COUNT(*) AS active_count
                       FROM mcp_admission_tickets
                       WHERE state = 'active'
                       GROUP BY principal_id
                   ) AS active ON active.principal_id = waiting.principal_id
                   WHERE waiting.state = 'waiting'
                     AND COALESCE(active.active_count, 0) < ?
                   ORDER BY waiting.created_at, waiting.ticket_id
                   LIMIT 1""",
                (self.limits.principal_concurrency,),
            ).fetchone()
            if (
                first_waiting is not None
                and first_waiting["ticket_id"] == ticket_id
                and self._capacity_available(connection, principal_id)
            ):
                connection.execute(
                    "UPDATE mcp_admission_tickets SET state = 'active', lease_expires_at = ? WHERE ticket_id = ?",
                    (now + self.limits.lease_seconds, ticket_id),
                )
                connection.commit()
                return True
            connection.commit()
        return False

    async def admit(self, principal_id: str) -> AdmissionLease:
        ticket_id = await anyio.to_thread.run_sync(self._enqueue, principal_id)
        deadline_start = mono_time()
        while not await anyio.to_thread.run_sync(self._try_promote, ticket_id, principal_id):
            if mono_time() - deadline_start >= self.limits.queue_timeout_seconds:
                await anyio.to_thread.run_sync(self.release, AdmissionLease(ticket_id, principal_id))
                raise MCPError(ADMISSION_ERROR, "Admission queue wait timed out")
            await anyio.sleep(0.025)
        return AdmissionLease(ticket_id, principal_id)

    def release(self, lease: AdmissionLease) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM mcp_admission_tickets WHERE ticket_id = ? AND principal_id = ?",
                (lease.ticket_id, lease.principal_id),
            )

    def renew(self, lease: AdmissionLease) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE mcp_admission_tickets SET lease_expires_at = ?
                   WHERE ticket_id = ? AND principal_id = ? AND state = 'active'""",
                (mono_time() + self.limits.lease_seconds, lease.ticket_id, lease.principal_id),
            )
        return cursor.rowcount == 1

    def audit(
        self,
        *,
        principal_id: str,
        tool_name: str,
        decision: str,
        execution_id: str | None = None,
        artifact_ref: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO mcp_audit_events
                   (event_id, recorded_at, principal_id, tool_name, decision, execution_id, artifact_ref)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex,
                    utc_now().isoformat(),
                    principal_id[:64],
                    tool_name[:120],
                    decision[:32],
                    execution_id[:120] if execution_id else None,
                    Path(artifact_ref).name[:255] if artifact_ref else None,
                ),
            )

    def audit_events(self) -> list[Mapping[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM mcp_audit_events ORDER BY recorded_at, event_id").fetchall()
        return [dict(row) for row in rows]


READ_ONLY_TOOLS = frozenset(
    {
        "list_available",
        "get_benchmark_info",
        "system_profile",
        "check_dependencies",
        "get_query_details",
        "get_results",
        "analyze_results",
        "get_query_plan",
        "validate_results",
        "suggest_charts",
        "generate_chart",
        "get_benchmark_status",
        "get_benchmark_result",
        "get_benchmark_capacity",
    }
)
READ_METHODS = frozenset(
    {
        "tools/list",
        "resources/list",
        "resources/templates/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
    }
)


class RemoteSecurityMiddleware:
    def __init__(self, config: RemoteSecurityConfig, store: DurableSecurityStore):
        self.config = config
        self.store = store

    def _authorize(self, principal: Principal, tool_name: str, arguments: Mapping[str, Any]) -> None:
        required_scope = READ_SCOPE if tool_name in READ_ONLY_TOOLS else WRITE_SCOPE
        if tool_name in {"run_benchmark", "start_benchmark", "cancel_benchmark"}:
            required_scope = EXECUTE_SCOPE
        elif tool_name == "get_results" and arguments.get("output_path"):
            required_scope = WRITE_SCOPE
        if required_scope not in principal.scopes:
            raise MCPError(AUTHORIZATION_ERROR, f"Scope required: {required_scope}")
        if tool_name not in {"run_benchmark", "start_benchmark"}:
            return
        platform = str(arguments.get("platform", "")).lower()
        benchmark = str(arguments.get("benchmark", "")).lower()
        try:
            scale_factor = float(arguments.get("scale_factor", 0.01))
        except (TypeError, ValueError) as exc:
            raise MCPError(AUTHORIZATION_ERROR, "Invalid benchmark scale factor") from exc
        if platform not in self.config.allowed_platforms:
            raise MCPError(AUTHORIZATION_ERROR, "Platform is not authorized")
        if benchmark not in self.config.allowed_benchmarks:
            raise MCPError(AUTHORIZATION_ERROR, "Benchmark is not authorized")
        if not math.isfinite(scale_factor) or scale_factor <= 0:
            raise MCPError(AUTHORIZATION_ERROR, "Benchmark scale factor must be finite and positive")
        if scale_factor > self.config.max_scale_factor:
            raise MCPError(AUTHORIZATION_ERROR, "Benchmark scale factor exceeds policy")

    @staticmethod
    def _tool_call(ctx: ServerRequestContext[Any, Any]) -> tuple[str, Mapping[str, Any]] | None:
        if ctx.method != "tools/call" or not isinstance(ctx.params, Mapping):
            return None
        name = ctx.params.get("name")
        arguments = ctx.params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            return None
        return name, arguments

    @staticmethod
    def _result_refs(result: HandlerResult) -> tuple[str | None, str | None]:
        payload: Mapping[str, Any] | None = result if isinstance(result, Mapping) else None
        if isinstance(result, CallToolResult):
            text = next((part.text for part in result.content if isinstance(part, TextContent)), None)
            if text is not None and len(text) <= 65_536:
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, Mapping):
                    payload = decoded
        if payload is None:
            return None, None
        metadata = payload.get("mcp_metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        execution = metadata_map.get("execution_id", payload.get("execution_id"))
        artifact = metadata_map.get("result_file", payload.get("file"))
        return (
            str(execution) if isinstance(execution, str) else None,
            str(artifact) if isinstance(artifact, str) else None,
        )

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.request_id is None:
            return await call_next(ctx)
        principal = authenticated_principal()
        tool_call = self._tool_call(ctx)
        if tool_call is None:
            if ctx.method in READ_METHODS:
                if READ_SCOPE not in principal.scopes:
                    await self._audit(
                        principal_id=principal.principal_id,
                        tool_name=ctx.method,
                        decision="denied",
                    )
                    raise MCPError(AUTHORIZATION_ERROR, f"Scope required: {READ_SCOPE}")
            return await self._call_admitted(principal, ctx.method, ctx, call_next)
        tool_name, arguments = tool_call
        try:
            self._authorize(principal, tool_name, arguments)
        except MCPError:
            await self._audit(principal_id=principal.principal_id, tool_name=tool_name, decision="denied")
            raise

        return await self._call_admitted(principal, tool_name, ctx, call_next)

    async def _call_admitted(
        self,
        principal: Principal,
        operation: str,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:

        lease: AdmissionLease | None = None
        try:
            lease = await self.store.admit(principal.principal_id)
            dispatch_error: Exception | None = None
            result: HandlerResult | None = None
            lease_lost = anyio.Event()
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(self._renew_lease, lease, task_group.cancel_scope, lease_lost)
                try:
                    result = await call_next(ctx)
                except Exception as exc:
                    dispatch_error = exc
                finally:
                    task_group.cancel_scope.cancel()
            if lease_lost.is_set():
                raise MCPError(ADMISSION_ERROR, "Admission lease was lost during request execution")
            if dispatch_error is not None:
                raise dispatch_error
            assert result is not None
            execution_id, artifact_ref = self._result_refs(result)
            await self._audit(
                principal_id=principal.principal_id,
                tool_name=operation,
                decision="allowed",
                execution_id=execution_id,
                artifact_ref=artifact_ref,
            )
            return result
        except MCPError:
            await self._audit(principal_id=principal.principal_id, tool_name=operation, decision="failed")
            raise
        except Exception as exc:
            await self._audit(principal_id=principal.principal_id, tool_name=operation, decision="failed")
            raise MCPError(-32603, f"Tool execution failed ({type(exc).__name__})") from exc
        finally:
            if lease is not None:
                await anyio.to_thread.run_sync(self.store.release, lease)

    async def _renew_lease(
        self,
        lease: AdmissionLease,
        request_scope: anyio.CancelScope,
        lease_lost: anyio.Event,
    ) -> None:
        interval = max(0.001, min(self.store.limits.lease_seconds / 3, 30.0))
        while True:
            await anyio.sleep(interval)
            if not await anyio.to_thread.run_sync(self.store.renew, lease):
                lease_lost.set()
                request_scope.cancel()
                return

    async def _audit(
        self,
        *,
        principal_id: str,
        tool_name: str,
        decision: str,
        execution_id: str | None = None,
        artifact_ref: str | None = None,
    ) -> None:
        await anyio.to_thread.run_sync(
            lambda: self.store.audit(
                principal_id=principal_id,
                tool_name=tool_name,
                decision=decision,
                execution_id=execution_id,
                artifact_ref=artifact_ref,
            )
        )


@dataclass(frozen=True, slots=True)
class RemoteSecurityRuntime:
    config: RemoteSecurityConfig
    verifier: DigestTokenVerifier
    workspaces: TenantWorkspaceProvider
    store: DurableSecurityStore
    middleware: RemoteSecurityMiddleware

    @classmethod
    def from_file(cls, path: str | Path) -> RemoteSecurityRuntime:
        config = load_remote_security_config(path)
        store = DurableSecurityStore(config.state_db, config.admission)
        return cls(
            config=config,
            verifier=DigestTokenVerifier(config),
            workspaces=TenantWorkspaceProvider(config.workspace_root),
            store=store,
            middleware=RemoteSecurityMiddleware(config, store),
        )

    def auth_settings(self) -> AuthSettings:
        return AuthSettings(
            issuer_url=self.config.issuer_url,
            resource_server_url=self.config.resource_server_url,
            required_scopes=[],
        )


PathProvider = Path | Callable[[], Path]


def resolve_path_provider(provider: PathProvider) -> Path:
    if isinstance(provider, Path):
        return provider
    return Path(provider())


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
