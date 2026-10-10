import * as duckdb from "@duckdb/duckdb-wasm";

import { LOCAL_DUCKDB_BUNDLES } from "@/lib/duckdbBundles";
import {
  EXPLORER_PERFORMANCE_MARKS,
  EXPLORER_PERFORMANCE_MEASURES,
  markExplorerPerformance,
  measureExplorerPerformance,
} from "@/lib/performanceMarks";

let dbInstance: duckdb.AsyncDuckDB | null = null;
let initPromise: Promise<duckdb.AsyncDuckDB> | null = null;
let initFailures = 0;
let excludeCoiBundle = false;
const INIT_FAILURE_LIMIT = 3;
const SNAPSHOT_READY_ATTEMPTS = 8;
const SNAPSHOT_READY_DELAY_MS = 100;
const QUERY_RETRY_ATTEMPTS = 100;
const QUERY_ERROR_RETRY_ATTEMPTS = 3;
const QUERY_RETRY_DELAY_MS = 100;
const QUERY_EMPTY_RETRY_DELAY_MS = 50;
const COLD_SNAPSHOT_EMPTY_RETRY_WINDOW_MS = 15_000;
let snapshotReadyAt = 0;
let initError: Error | null = null;
let queryQueue: Promise<void> = Promise.resolve();

type DuckDBConnection = Awaited<ReturnType<duckdb.AsyncDuckDB["connect"]>>;

async function createCspBoundWorker(workerPath: string): Promise<Worker> {
  const workerUrl = new URL(workerPath, window.location.href);
  if (workerUrl.origin !== window.location.origin) {
    throw new Error(`DuckDB worker must be same-origin: ${workerUrl.origin}`);
  }
  const response = await fetch(workerUrl, { signal: AbortSignal.timeout(DUCKDB_INIT_TIMEOUT_MS) });
  if (!response.ok) {
    throw new Error(`DuckDB worker fetch failed: HTTP ${response.status}`);
  }
  const blobUrl = URL.createObjectURL(await response.blob());
  const worker = new Worker(blobUrl);
  const revokeBlobUrl = () => {
    URL.revokeObjectURL(blobUrl);
  };
  worker.addEventListener("message", revokeBlobUrl, { once: true });
  worker.addEventListener("error", revokeBlobUrl, { once: true });
  return worker;
}

const EXPECTED_READ_MODEL_VERSION = 15;

const SNAPSHOT_READY_SCANS = [
  {
    label: "results",
    sql: "SELECT result_id FROM bench.results LIMIT 1",
    required: true,
  },
  {
    label: "platform_index_rows",
    sql: "SELECT result_id FROM bench.platform_index_rows LIMIT 1",
    required: true,
  },
  {
    label: "benchmark_rankings",
    sql: "SELECT result_id FROM bench.benchmark_rankings LIMIT 1",
    required: true,
  },
  {
    label: "benchmark_matrix_cells",
    sql: "SELECT result_id FROM bench.benchmark_matrix_cells LIMIT 1",
    required: true,
  },
  {
    label: "result_detail_metrics",
    sql: "SELECT result_id FROM bench.result_detail_metrics LIMIT 1",
    required: true,
  },
  {
    label: "query_display_timings",
    sql: "SELECT result_id FROM bench.query_display_timings LIMIT 1",
    required: false,
  },
  {
    label: "query_executions",
    sql: "SELECT result_id FROM bench.query_executions LIMIT 1",
    required: false,
  },
  {
    label: "short_ids",
    sql: "SELECT result_id FROM bench.short_ids LIMIT 1",
    required: false,
  },
] as const;

const TRANSIENT_DUCKDB_SNAPSHOT_ERROR_PATTERNS = [
  /offset is out of bounds/i,
  /fieldsLength/i,
];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

const DUCKDB_QUERY_TIMEOUT_MS = 5_000;
const DUCKDB_INIT_TIMEOUT_MS = 30_000;
export const DUCKDB_USER_QUERY_TIMEOUT_MS = 30_000;
const DUCKDB_CLEANUP_TIMEOUT_MS = 300;

const SQL_LABEL_MAX_LENGTH = 80;

function sqlLabel(sql: string): string {
  const collapsed = sql.replace(/\s+/g, " ").trim();
  return collapsed.length > SQL_LABEL_MAX_LENGTH
    ? `${collapsed.slice(0, SQL_LABEL_MAX_LENGTH)}…`
    : collapsed;
}

class DuckDbTimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DuckDbTimeoutError";
  }
}

class DuckDbTerminatedError extends Error {
  constructor(label: string) {
    super(`DuckDB ${label} failed; its worker was terminated while the query was in flight`);
    this.name = "DuckDbTerminatedError";
  }
}

function withDuckDbTimeout<T>(
  promise: Promise<T>,
  label: string,
  timeoutMs: number = DUCKDB_QUERY_TIMEOUT_MS,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = globalThis.setTimeout(() => {
      reject(
        new DuckDbTimeoutError(
          `DuckDB ${label} did not respond within ${timeoutMs}ms`,
        ),
      );
    }, timeoutMs);
    promise.then(
      (value) => {
        globalThis.clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        globalThis.clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function queryWithTimeout(
  conn: DuckDBConnection,
  sql: string,
  timeoutMs: number = DUCKDB_QUERY_TIMEOUT_MS,
) {
  return withDuckDbTimeout(conn.query(sql), sqlLabel(sql), timeoutMs);
}

export async function _waitForSnapshotRowsForTest(
  conn: DuckDBConnection,
): Promise<void> {
  return waitForSnapshotRows(conn);
}

export async function _verifyReadModelVersionForTest(
  conn: DuckDBConnection,
): Promise<void> {
  return verifyReadModelVersion(conn);
}

export async function _validateAttachedSnapshotForTest(
  conn: DuckDBConnection,
): Promise<void> {
  return validateAttachedSnapshot(conn);
}

export const _EXPECTED_READ_MODEL_VERSION_FOR_TEST = EXPECTED_READ_MODEL_VERSION;
export const _COLD_EMPTY_READ_MAX_DELAY_MS_FOR_TEST =
  QUERY_RETRY_ATTEMPTS * QUERY_EMPTY_RETRY_DELAY_MS;
export const _DUCKDB_QUERY_TIMEOUT_MS_FOR_TEST = DUCKDB_QUERY_TIMEOUT_MS;
export const _DUCKDB_INIT_TIMEOUT_MS_FOR_TEST = DUCKDB_INIT_TIMEOUT_MS;
export const _DUCKDB_CLEANUP_TIMEOUT_MS_FOR_TEST = DUCKDB_CLEANUP_TIMEOUT_MS;

export function _getInitFailuresForTest(): number {
  return initFailures;
}

export function _resetDbStateForTest(): void {
  if (import.meta.env.PROD) return;
  dbInstance = null;
  initPromise = null;
  initFailures = 0;
  initError = null;
  excludeCoiBundle = false;
  snapshotReadyAt = 0;
  queryQueue = Promise.resolve();
}

async function verifyReadModelVersion(conn: DuckDBConnection): Promise<void> {
  const found = await readSnapshotReadModelVersion(conn);
  if (found < EXPECTED_READ_MODEL_VERSION) {
    throwReadModelVersionError(found);
  }
  if (found > EXPECTED_READ_MODEL_VERSION) {
    console.warn(
      `DuckDB snapshot read-model v${found}; UI expects v${EXPECTED_READ_MODEL_VERSION}. ` +
        "Proceeding with forward-compatible reads.",
    );
  }
}

async function readSnapshotReadModelVersion(conn: DuckDBConnection): Promise<number> {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= SNAPSHOT_READY_ATTEMPTS; attempt += 1) {
    try {
      const result = await queryWithTimeout(conn, "SELECT read_model_version FROM bench.metadata LIMIT 1");
      const row = result.toArray()[0]?.toJSON();
      const version = Number(row?.read_model_version ?? 0);
      return Number.isInteger(version) && version >= 0 ? version : 0;
    } catch (error: unknown) {
      if (isMissingReadModelMetadataError(error)) {
        return 0;
      }
      lastError = error;
      if (isDuckDbBufferBoundsError(error) || !isTransientDuckDbSnapshotError(error)) {
        throw error;
      }
      await sleep(SNAPSHOT_READY_DELAY_MS * attempt);
    }
  }
  throw lastError instanceof Error
    ? lastError
    : new Error("DuckDB snapshot read-model version did not become query-ready");
}

function isMissingReadModelMetadataError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return (
    /Table with name "?metadata"? does not exist/i.test(message) ||
    /Referenced column "?read_model_version"? not found/i.test(message) ||
    /Binder Error:.*read_model_version/i.test(message)
  );
}

async function validateAttachedSnapshot(conn: DuckDBConnection): Promise<void> {
  await verifyReadModelVersion(conn);
  await waitForSnapshotRows(conn);
}

function throwReadModelVersionError(found: number): never {
  const remediation = import.meta.env.DEV
    ? "Restart npm run dev or run npm run dev:snapshot to rebuild the local Explorer data."
    : "Refresh the published snapshot or ask a maintainer to rebuild the Explorer data.";
  throw new Error(
    `DuckDB snapshot read-model v${found}; UI requires v${EXPECTED_READ_MODEL_VERSION}. ` +
      `${remediation} Check that the published results snapshot is available.`,
  );
}

function quoteSqlLiteral(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

const SNAPSHOT_COMPLETENESS_TABLES = SNAPSHOT_READY_SCANS.map((scan) => scan.label);

async function probeSnapshotCompleteness(conn: DuckDBConnection): Promise<string | null> {
  for (const table of SNAPSHOT_COMPLETENESS_TABLES) {
    const countRows = (await queryWithTimeout(conn, `SELECT COUNT(*) AS n FROM bench.${table}`)).toArray() as Array<{
      n?: unknown;
    }>;
    const expected = Number(countRows[0]?.n ?? 0);
    if (!Number.isFinite(expected)) continue;
    const materialized = (await queryWithTimeout(conn, `SELECT result_id FROM bench.${table}`)).toArray().length;
    if (materialized !== expected) {
      return `${table} materialized ${materialized} of ${expected} row(s)`;
    }
  }
  return null;
}

async function probeKeyedLookup(conn: DuckDBConnection): Promise<string | null> {
  const idRows = (
    await queryWithTimeout(conn, "SELECT result_id FROM bench.results ORDER BY result_id DESC LIMIT 1")
  ).toArray() as Array<{ result_id?: unknown }>;
  const probeId = idRows[0]?.result_id;
  if (typeof probeId !== "string" || probeId === "") return "results returned no probe id";

  const keyed = (
    await queryWithTimeout(
      conn,
      `SELECT result_id FROM bench.result_detail_metrics WHERE result_id = ${quoteSqlLiteral(probeId)} LIMIT 1`,
    )
  ).toArray();
  if (keyed.length === 0) {
    return `keyed lookup for ${probeId} returned no rows from result_detail_metrics`;
  }
  return null;
}

async function waitForSnapshotRows(conn: DuckDBConnection): Promise<void> {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= SNAPSHOT_READY_ATTEMPTS; attempt += 1) {
    try {
      const requiredCounts: Array<readonly [string, number]> = [];
      for (const scan of SNAPSHOT_READY_SCANS) {
        const result = await queryWithTimeout(conn, scan.sql);
        if (scan.required) {
          requiredCounts.push([scan.label, result.toArray().length] as const);
        }
      }
      const emptyRequired = requiredCounts
        .filter(([, rowCount]) => rowCount === 0)
        .map(([label]) => label);
      if (emptyRequired.length === 0) {
        const failure = (await probeSnapshotCompleteness(conn)) ?? (await probeKeyedLookup(conn));
        if (failure === null) return;
        lastError = new Error(`DuckDB snapshot readiness: ${failure}`);
      } else {
        lastError = new Error(
          `DuckDB snapshot readiness returned empty required scan(s): ${emptyRequired.join(", ")}`,
        );
      }
    } catch (error: unknown) {
      lastError = error;
      if (isDuckDbBufferBoundsError(error) || !isTransientDuckDbSnapshotError(error)) throw error;
    }
    await sleep(SNAPSHOT_READY_DELAY_MS * attempt);
  }
  throw lastError instanceof Error ? lastError : new Error("DuckDB snapshot did not become query-ready");
}

export function resetDuckDbInitializationFailures(): void {
  initFailures = 0;
  initError = null;
}

if (typeof window !== "undefined") {
  window.addEventListener("online", () => {
    resetDuckDbInitializationFailures();
  });
}

function evictDb(deadInstance: duckdb.AsyncDuckDB): void {
  if (dbInstance === deadInstance) {
    dbInstance = null;
    initPromise = null;
    snapshotReadyAt = 0;
  }
  void deadInstance.terminate();
}

export async function getDb(): Promise<duckdb.AsyncDuckDB> {
  if (dbInstance) return dbInstance;
  if (initPromise) return initPromise;
  if (initFailures >= INIT_FAILURE_LIMIT && initError) {
    throw new Error(
      `DuckDB-WASM init failed ${initFailures} times; aborting. Last error: ${initError.message}`,
    );
  }

  initPromise = (async () => {
    markExplorerPerformance(EXPLORER_PERFORMANCE_MARKS.DB_INIT_START, { once: true });
    const bundles = excludeCoiBundle
      ? { mvp: LOCAL_DUCKDB_BUNDLES.mvp, eh: LOCAL_DUCKDB_BUNDLES.eh }
      : LOCAL_DUCKDB_BUNDLES;
    const bundle = await withDuckDbTimeout(duckdb.selectBundle(bundles), "selectBundle");
    const worker = await createCspBoundWorker(bundle.mainWorker!);
    const logger = new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING);
    const db = new duckdb.AsyncDuckDB(logger, worker);
    try {
      const mainModule = new URL(bundle.mainModule, window.location.href).href;
      const pthreadWorker = bundle.pthreadWorker
        ? new URL(bundle.pthreadWorker, window.location.href).href
        : undefined;
      await withDuckDbTimeout(db.instantiate(mainModule, pthreadWorker), "instantiate", DUCKDB_INIT_TIMEOUT_MS);

      const dbUrl = new URL("/results/data/results.duckdb", window.location.origin).href;
      await withDuckDbTimeout(
        db.registerFileURL("results.duckdb", dbUrl, duckdb.DuckDBDataProtocol.HTTP, true),
        "registerFileURL",
      );
      const conn = await withDuckDbTimeout(db.connect(), "connect");
      try {
        await withDuckDbTimeout(
          conn.query("ATTACH 'results.duckdb' AS bench (READ_ONLY)"),
          "ATTACH",
          DUCKDB_INIT_TIMEOUT_MS,
        );
        await validateAttachedSnapshot(conn);
        await withDuckDbTimeout(conn.query("SET enable_external_access = false"), "SET enable_external_access");
        await withDuckDbTimeout(conn.query("SET lock_configuration = true"), "SET lock_configuration");
      } finally {
        await withDuckDbTimeout(conn.close(), "connection close", DUCKDB_CLEANUP_TIMEOUT_MS).catch(() => {});
      }
    } catch (error: unknown) {
      if (bundle.mainModule === LOCAL_DUCKDB_BUNDLES.coi?.mainModule && isDuckDbBufferBoundsError(error)) {
        excludeCoiBundle = true;
      }
      void db.terminate();
      throw error;
    }

    dbInstance = db;
    initPromise = null;
    initFailures = 0;
    initError = null;
    snapshotReadyAt = Date.now();
    markExplorerPerformance(EXPLORER_PERFORMANCE_MARKS.DB_INIT_READY, { once: true });
    measureExplorerPerformance(
      EXPLORER_PERFORMANCE_MEASURES.DB_INIT,
      EXPLORER_PERFORMANCE_MARKS.DB_INIT_START,
      EXPLORER_PERFORMANCE_MARKS.DB_INIT_READY,
      { once: true },
    );
    return db;
  })().catch((error: unknown) => {
    initPromise = null;
    initFailures += 1;
    initError = error instanceof Error ? error : new Error(String(error));
    throw error;
  });

  return initPromise;
}

export async function queryRows<T>(
  sql: string,
  params: unknown[] = [],
  timeoutMs: number = DUCKDB_QUERY_TIMEOUT_MS,
): Promise<T[]> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      const pending = queryQueue.then(() => queryRowsOnce<T>(sql, params, timeoutMs));
      queryQueue = pending.then(() => undefined, () => undefined);
      const rows = await pending;
      if (rows.length > 0 || !isColdEmptyRead(attempt)) return rows;
      await sleep(QUERY_EMPTY_RETRY_DELAY_MS);
      continue;
    } catch (error: unknown) {
      if (!shouldRetryTransientQueryError(error, attempt)) {
        throw error;
      }
      await sleep(QUERY_RETRY_DELAY_MS * attempt);
    }
  }
}

export function shouldRetryColdEmptyRead(
  attempt: number,
  now: number,
  readyAt: number,
): boolean {
  if (attempt >= QUERY_RETRY_ATTEMPTS) return false;
  if (readyAt === 0) return false;
  return now - readyAt <= COLD_SNAPSHOT_EMPTY_RETRY_WINDOW_MS;
}

function isColdEmptyRead(attempt: number): boolean {
  return shouldRetryColdEmptyRead(attempt, Date.now(), snapshotReadyAt);
}

export function shouldRetryTransientQueryError(error: unknown, attempt: number): boolean {
  return isTransientDuckDbSnapshotError(error) && attempt < QUERY_ERROR_RETRY_ATTEMPTS;
}

async function queryRowsOnce<T>(
  sql: string,
  params: unknown[] = [],
  timeoutMs: number = DUCKDB_QUERY_TIMEOUT_MS,
): Promise<T[]> {
  const db = await getDb();
  let conn: DuckDBConnection | null = null;
  let statement: duckdb.AsyncPreparedStatement | null = null;
  let evicted = false;
  try {
    conn = await withDuckDbTimeout(db.connect(), "connect", timeoutMs);
    let result: Awaited<ReturnType<DuckDBConnection["query"]>>;
    if (params.length === 0) {
      result = await queryWithTimeout(conn, sql, timeoutMs);
    } else {
      statement = await withDuckDbTimeout(conn.prepare(sql), "prepare", timeoutMs);
      result = await withDuckDbTimeout(statement.query(...params), sqlLabel(sql), timeoutMs);
    }
    return result.toArray().map((row) => row.toJSON() as T);
  } catch (error: unknown) {
    if (isDuckDbTimeoutError(error)) {
      evictDb(db);
      evicted = true;
    } else if (db.isDetached()) {
      evictDb(db);
      evicted = true;
      throw new DuckDbTerminatedError(sqlLabel(sql));
    }
    throw error;
  } finally {
    if (!evicted) {
      if (statement) {
        await withDuckDbTimeout(statement.close(), "statement close", DUCKDB_CLEANUP_TIMEOUT_MS).catch(() => {});
      }
      if (conn) {
        await withDuckDbTimeout(conn.close(), "connection close", DUCKDB_CLEANUP_TIMEOUT_MS).catch(() => {});
      }
    }
  }
}

function isDuckDbTimeoutError(error: unknown): boolean {
  return error instanceof DuckDbTimeoutError;
}

function isDuckDbBufferBoundsError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /offset is out of bounds/i.test(message);
}

function isTransientDuckDbSnapshotError(error: unknown): boolean {
  if (error instanceof DuckDbTimeoutError) return false;
  if (error instanceof DuckDbTerminatedError) return true;
  const message = error instanceof Error ? error.message : String(error);
  return TRANSIENT_DUCKDB_SNAPSHOT_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}
