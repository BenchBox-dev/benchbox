import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hoisted = vi.hoisted(() => {
  interface FakeRow {
    toJSON: () => Record<string, unknown>;
  }
  interface FakeRows {
    toArray: () => FakeRow[];
  }

  function rows(data: Array<Record<string, unknown>>): FakeRows {
    return { toArray: () => data.map((row) => ({ toJSON: () => row, ...row })) };
  }

  const membershipAttempts = new Map<string, number>();
  const bundleSelections: string[] = [];
  const bundles = {
    mvp: { mainModule: "duckdb-mvp.wasm", mainWorker: "duckdb-mvp.worker.js" },
    eh: { mainModule: "duckdb-eh.wasm", mainWorker: "duckdb-eh.worker.js" },
    coi: { mainModule: "duckdb-coi.wasm", mainWorker: "duckdb-coi.worker.js", pthreadWorker: "pthread.js" },
  };
  let membershipQuery:
    ((sql: string, params: unknown[], attempt: number) => Array<Record<string, unknown>>) | null = null;

  function healthyQueryImpl(sql: string): FakeRows {
    if (/read_model_version/i.test(sql)) return rows([{ read_model_version: 15 }]);
    if (/^ATTACH/i.test(sql)) return rows([]);
    if (/^SET /i.test(sql)) return rows([]);
    if (/COUNT\(\*\)/i.test(sql)) return rows([{ n: 1 }]);
    if (/WHERE result_id\s*=/i.test(sql)) return rows([{ result_id: "r1" }]);
    if (/FROM bench\.\w+/i.test(sql)) return rows([{ result_id: "r1" }]);
    return rows([{ ok: 1 }]);
  }

  class FakeStatement {
    dead: boolean;
    query: (...params: unknown[]) => Promise<FakeRows>;
    close: () => Promise<void>;

    constructor(dead: boolean, sql: string) {
      this.dead = dead;
      this.query = (...params: unknown[]) => {
        if (this.dead) return new Promise(() => {});
        if (membershipQuery && /FROM bench\.result_detail_metrics/i.test(sql)) {
          const key = params.join(",");
          const attempt = (membershipAttempts.get(key) ?? 0) + 1;
          membershipAttempts.set(key, attempt);
          return Promise.resolve(rows(membershipQuery(sql, params, attempt)));
        }
        return Promise.resolve(healthyQueryImpl("prepared"));
      };
      this.close = () => {
        if (this.dead) return new Promise(() => {});
        return Promise.resolve();
      };
    }
  }

  class FakeConnection {
    dead: boolean;
    terminatedBy: FakeAsyncDuckDB | null;
    query: (sql: string) => Promise<FakeRows>;
    close: () => Promise<void>;
    prepare: (sql: string) => Promise<FakeStatement>;

    constructor(dead: boolean, terminatedBy: FakeAsyncDuckDB | null = null) {
      this.dead = dead;
      this.terminatedBy = terminatedBy;
      this.query = (sql: string) => {
        if (this.dead) return new Promise(() => {});
        if (this.terminatedBy) {
          this.terminatedBy.terminated = true;
          return Promise.reject(new TypeError("Cannot read properties of undefined (reading 'peek')"));
        }
        return Promise.resolve(healthyQueryImpl(sql));
      };
      this.close = () => {
        if (this.dead) return new Promise(() => {});
        return Promise.resolve();
      };
      this.prepare = async (sql: string) => new FakeStatement(this.dead, sql);
    }
  }

  type InstanceBehavior =
    | "healthy"
    | "dead-after-init"
    | "dead-during-init"
    | "buffer-error-on-metadata"
    | "buffer-error-on-readiness"
    | "coi-buffer-errors"
    | "old-snapshot"
    | "terminated-after-init"
    | "slow-query";
  const nextInstanceBehaviors: InstanceBehavior[] = [];
  const instances: FakeAsyncDuckDB[] = [];

  class FakeAsyncDuckDB {
    id: number;
    behavior: InstanceBehavior;
    bundleModule: string;
    terminate: () => Promise<void>;
    connectCount = 0;
    queryCalls: string[] = [];
    terminated = false;

    constructor() {
      this.behavior = nextInstanceBehaviors.shift() ?? "healthy";
      this.bundleModule = bundleSelections.at(-1) ?? "";
      this.id = instances.length + 1;
      this.terminate = vi.fn(async () => {
        this.terminated = true;
      });
      instances.push(this);
    }

    isDetached(): boolean {
      return this.terminated;
    }

    async instantiate(): Promise<null> {
      return null;
    }

    async registerFileURL(): Promise<void> {
      return undefined;
    }

    async connect(): Promise<FakeConnection> {
      this.connectCount += 1;
      const isDead =
        this.behavior === "dead-during-init" ||
        (this.behavior === "dead-after-init" && this.connectCount > 1);
      const terminatedBy =
        this.behavior === "terminated-after-init" && this.connectCount > 1 ? this : null;
      const conn = new FakeConnection(isDead, terminatedBy);
      const query = conn.query;
      conn.query = async (sql: string) => {
        this.queryCalls.push(sql);
        if (this.behavior === "old-snapshot" && /read_model_version/i.test(sql)) {
          return rows([{ read_model_version: 10 }]);
        }
        if (
          (this.behavior === "coi-buffer-errors" && this.bundleModule === bundles.coi.mainModule) ||
          (this.behavior === "buffer-error-on-metadata" && /read_model_version/i.test(sql)) ||
          (this.behavior === "buffer-error-on-readiness" && /^SELECT result_id FROM bench.results LIMIT 1/.test(sql))
        ) {
          throw new RangeError("offset is out of bounds");
        }
        return query(sql);
      };
      if (this.behavior === "slow-query" && this.connectCount === 2) {
        conn.query = async () => {
          await new Promise((resolve) => setTimeout(resolve, 10_000));
          return healthyQueryImpl("SELECT 1");
        };
      }
      return conn;
    }
  }

  return {
    nextInstanceBehaviors,
    bundleSelections,
    bundles,
    instances,
    FakeAsyncDuckDB,
    membershipAttempts,
    setMembershipQuery: (query: typeof membershipQuery) => {
      membershipQuery = query;
    },
  };
});

vi.mock("@duckdb/duckdb-wasm", () => ({
  selectBundle: vi.fn(async (bundles: typeof hoisted.bundles) => {
    const bundle = bundles.coi ?? bundles.eh ?? bundles.mvp;
    hoisted.bundleSelections.push(bundle.mainModule);
    return bundle;
  }),
  ConsoleLogger: class {},
  LogLevel: { WARNING: 3 },
  DuckDBDataProtocol: { HTTP: 1 },
  AsyncDuckDB: hoisted.FakeAsyncDuckDB,
}));

vi.mock("@/lib/duckdbBundles", () => ({
  LOCAL_DUCKDB_BUNDLES: hoisted.bundles,
}));

vi.mock("@/lib/performanceMarks", () => ({
  EXPLORER_PERFORMANCE_MARKS: {},
  EXPLORER_PERFORMANCE_MEASURES: {},
  markExplorerPerformance: vi.fn(),
  measureExplorerPerformance: vi.fn(),
}));

import {
  _DUCKDB_QUERY_TIMEOUT_MS_FOR_TEST,
  _getInitFailuresForTest,
  _resetDbStateForTest,
  resetDuckDbInitializationFailures,
  DUCKDB_USER_QUERY_TIMEOUT_MS,
  getDb,
  queryRows,
} from "@/db";
import { getExistingResultIds } from "@/lib/duckdbQueries";

class FakeWorker {
  addEventListener(): void {}
  removeEventListener(): void {}
  terminate(): void {}
}

beforeEach(() => {
  _resetDbStateForTest();
  hoisted.nextInstanceBehaviors.length = 0;
  hoisted.instances.length = 0;
  hoisted.membershipAttempts.clear();
  hoisted.bundleSelections.length = 0;
  hoisted.setMembershipQuery(null);

  vi.stubGlobal("Worker", FakeWorker);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      blob: async () => new Blob(),
    })),
  );
  if (!URL.createObjectURL) {
    (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => "blob:fake";
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Initialization with a stale worker memory view", () => {
  it("uses the supported single-threaded bundle after persistent COI buffer errors", async () => {
    vi.useFakeTimers();
    hoisted.nextInstanceBehaviors.push("coi-buffer-errors", "coi-buffer-errors", "coi-buffer-errors");
    hoisted.setMembershipQuery((_sql, params) => params.map((result_id) => ({ result_id })));
    const pending = Promise.all([getExistingResultIds(["real-a"]), getExistingResultIds(["real-b"])])
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await pending).toEqual([new Set(["real-a"]), new Set(["real-b"])]);
    expect(hoisted.bundleSelections).toEqual([hoisted.bundles.coi.mainModule, hoisted.bundles.eh.mainModule]);
    expect(hoisted.instances[0]?.terminate).toHaveBeenCalledOnce();
    expect(hoisted.instances[1]?.terminated).toBe(false);
    resetDuckDbInitializationFailures();
    expect(await getDb()).toBe(hoisted.instances[1]);
  });

  it.each(["network", "version"] as const)("does not exclude COI after a %s failure", async (failure) => {
    if (failure === "network") {
      vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    } else {
      hoisted.nextInstanceBehaviors.push("old-snapshot");
    }
    await expect(getDb()).rejects.toThrow();
    await getDb();
    expect(hoisted.bundleSelections).toEqual([hoisted.bundles.coi.mainModule, hoisted.bundles.coi.mainModule]);
  });

  it.each(["buffer-error-on-metadata", "buffer-error-on-readiness"] as const)(
    "replaces %s promptly and preserves concurrent real-ID reads",
    async (behavior) => {
      vi.useFakeTimers();
      hoisted.nextInstanceBehaviors.push(behavior, "healthy");
      hoisted.setMembershipQuery((_sql, params) => params.map((result_id) => ({ result_id })));
      const pending = Promise.all([getExistingResultIds(["real-a"]), getExistingResultIds(["real-b"])]);
      await vi.advanceTimersByTimeAsync(0);
      expect(hoisted.instances[0]?.terminate).toHaveBeenCalledOnce();
      const failingSql = behavior === "buffer-error-on-metadata"
        ? /read_model_version/i
        : /^SELECT result_id FROM bench.results LIMIT 1/;
      expect(hoisted.instances[0]?.queryCalls.filter((sql) => failingSql.test(sql))).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(1000);
      expect(await pending).toEqual([new Set(["real-a"]), new Set(["real-b"])]);
      expect(hoisted.instances).toHaveLength(2);
      expect(hoisted.instances[1]?.terminated).toBe(false);
      expect(_getInitFailuresForTest()).toBe(0);
    },
  );

  it.each(["buffer-error-on-metadata", "buffer-error-on-readiness"] as const)(
    "bounds repeated %s failures without retrying SQL on broken workers",
    async (behavior) => {
      vi.useFakeTimers();
      hoisted.nextInstanceBehaviors.push(behavior, behavior, behavior);
      let failure: unknown;
      const pending = queryRows("SELECT 1").catch((error: unknown) => { failure = error; });
      await vi.advanceTimersByTimeAsync(1000);
      expect(failure).toBeInstanceOf(RangeError);
      await pending;
      expect(hoisted.instances).toHaveLength(3);
      expect(hoisted.instances.every((instance) => instance.terminated)).toBe(true);
      expect(_getInitFailuresForTest()).toBe(3);
      await expect(getDb()).rejects.toThrow("offset is out of bounds");
      expect(hoisted.instances).toHaveLength(3);
    },
  );
});

describe("Compare membership with cold reads", () => {
  it("confirms a missing ID once after a complete scan", async () => {
    vi.useFakeTimers();
    hoisted.setMembershipQuery((sql, params) => {
      if (params.length === 2) return [{ result_id: "known-a" }];
      return /HAVING COUNT\(DISTINCT result_id\)/i.test(sql) ? [{ result_id: null }] : [];
    });
    const initialMatches = vi.fn();
    const pending = getExistingResultIds(["known-a", "missing-b"], initialMatches);
    await vi.runAllTimersAsync();
    expect(await pending).toEqual(new Set(["known-a"]));
    expect(initialMatches).toHaveBeenCalledWith(new Set(["known-a"]));
    expect(hoisted.membershipAttempts.get("missing-b")).toBe(1);
  });

  it("retries an incomplete confirmation and keeps the recovered real ID", async () => {
    vi.useFakeTimers();
    hoisted.setMembershipQuery((_sql, params, attempt) => {
      if (params.length === 2) return [{ result_id: "known-a" }];
      return attempt === 1 ? [] : [{ result_id: "cold-b" }];
    });
    const pending = getExistingResultIds(["known-a", "cold-b"]);
    await vi.runAllTimersAsync();
    expect(await pending).toEqual(new Set(["known-a", "cold-b"]));
    expect(hoisted.membershipAttempts.get("cold-b")).toBe(2);
  });

  it("recovers from a transient confirmation error before reporting absence", async () => {
    vi.useFakeTimers();
    hoisted.setMembershipQuery((_sql, params, attempt) => {
      if (params.length === 2) return [{ result_id: "known-a" }];
      if (attempt === 1) throw new RangeError("offset is out of bounds");
      return [{ result_id: null }];
    });
    const pending = getExistingResultIds(["known-a", "missing-b"]);
    await vi.runAllTimersAsync();
    expect(await pending).toEqual(new Set(["known-a"]));
    expect(hoisted.membershipAttempts.get("missing-b")).toBe(2);
  });
});

describe("DuckDB instance eviction and recovery", () => {
  it("T1: after a query times out, it evicts the instance so the *next* call gets a different AsyncDuckDB instance", async () => {
    vi.useFakeTimers();
    hoisted.nextInstanceBehaviors.push("dead-after-init", "healthy");

    const db1 = await getDb();
    expect(hoisted.instances).toHaveLength(1);

    const firstCall = queryRows("SELECT 1 AS ok");
    const firstAssertion = expect(firstCall).rejects.toThrow(/did not respond within/i);
    await vi.runAllTimersAsync();
    await firstAssertion;

    expect(hoisted.instances[0]).toBe(db1);
    expect(hoisted.instances[0]?.terminate).toHaveBeenCalled();

    const secondCall = queryRows("SELECT 1 AS ok");
    await vi.runAllTimersAsync();
    const result = await secondCall;
    expect(result).toEqual([{ ok: 1 }]);
    expect(hoisted.instances).toHaveLength(2);

    const db2 = await getDb();
    expect(db2).not.toBe(db1);
    expect((db2 as unknown as { id: number }).id).toBe(2);
  });

  it("T2: a hang during init settles, counts as a failure, and a later call recovers", async () => {
    vi.useFakeTimers();
    hoisted.nextInstanceBehaviors.push("dead-during-init");

    expect(_getInitFailuresForTest()).toBe(0);

    const initPromise = getDb();
    const initAssertion = expect(initPromise).rejects.toThrow(/did not respond within/i);
    await vi.runAllTimersAsync();
    await initAssertion;

    expect(_getInitFailuresForTest()).toBe(1);
    expect(hoisted.instances).toHaveLength(1);
    expect(hoisted.instances[0]?.terminate).toHaveBeenCalled();

    hoisted.nextInstanceBehaviors.push("healthy");
    const recovered = await getDb();
    expect(recovered).toBeTruthy();
    expect(hoisted.instances).toHaveLength(2);
  });

  it("T3: one queryRows() call against a dead worker costs one query timeout, not three attempts plus cleanup", async () => {
    vi.useFakeTimers();
    hoisted.nextInstanceBehaviors.push("dead-after-init");

    const start = Date.now();
    const queryPromise = queryRows("SELECT 1 AS ok WHERE 1 = ?", [1]);
    const assertion = expect(queryPromise).rejects.toThrow(/did not respond within/i);
    await vi.runAllTimersAsync();
    await assertion;
    const elapsedMs = Date.now() - start;

    expect(elapsedMs).toBe(_DUCKDB_QUERY_TIMEOUT_MS_FOR_TEST);
    expect(elapsedMs).toBeLessThan(8_000);

    hoisted.nextInstanceBehaviors.push("healthy");
    const recovered = await queryRows("SELECT 1 AS ok");
    expect(recovered).toEqual([{ ok: 1 }]);
    expect(hoisted.instances).toHaveLength(2);
  });

  it("R2: a query terminated by a sibling's eviction while in flight is reclassified and retried, not thrown as a raw TypeError", async () => {
    vi.useFakeTimers();
    hoisted.nextInstanceBehaviors.push("terminated-after-init", "healthy");

    const db1 = await getDb();
    expect(hoisted.instances).toHaveLength(1);
    expect((db1 as unknown as { isDetached: () => boolean }).isDetached()).toBe(false);

    const resultPromise = queryRows("SELECT 1 AS ok");
    await vi.runAllTimersAsync();
    const result = await resultPromise;

    expect(result).toEqual([{ ok: 1 }]);
    expect(hoisted.instances).toHaveLength(2);
    expect(hoisted.instances[0]?.terminate).toHaveBeenCalled();
  });

  it("starts a queued page read's timeout after a slow user query finishes", async () => {
    vi.useFakeTimers();
    hoisted.nextInstanceBehaviors.push("slow-query");
    const db = await getDb();
    const slow = queryRows("SELECT expensive_operation()", [], DUCKDB_USER_QUERY_TIMEOUT_MS);
    const pageRead = queryRows("SELECT 1 AS ok");
    await vi.advanceTimersByTimeAsync(6_000);
    expect(hoisted.instances[0]?.terminate).not.toHaveBeenCalled();
    expect(hoisted.instances[0]?.connectCount).toBe(2);
    await vi.advanceTimersByTimeAsync(4_000);
    expect(await slow).toEqual([{ ok: 1 }]);
    expect(await pageRead).toEqual([{ ok: 1 }]);
    expect(await getDb()).toBe(db);
    expect(hoisted.instances[0]?.terminate).not.toHaveBeenCalled();
  });

  it("terminates user SQL at its hard cutoff and releases queued reads to a fresh worker", async () => {
    vi.useFakeTimers();
    hoisted.nextInstanceBehaviors.push("dead-after-init", "healthy");
    await getDb();
    const start = Date.now();
    const slow = queryRows("SELECT expensive_operation()", [], DUCKDB_USER_QUERY_TIMEOUT_MS);
    const assertion = expect(slow).rejects.toThrow(/did not respond within/i);
    const pageRead = queryRows("SELECT 1 AS ok");
    await vi.runAllTimersAsync();
    await assertion;
    expect(Date.now() - start).toBe(DUCKDB_USER_QUERY_TIMEOUT_MS);
    expect(hoisted.instances[0]?.terminate).toHaveBeenCalled();
    expect(await pageRead).toEqual([{ ok: 1 }]);
    expect(hoisted.instances).toHaveLength(2);
  });

  it("allows an explicit retry after three initialization failures without resetting healthy state", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("HTTP unavailable"));
    for (let i = 0; i < 3; i++) {
      await expect(getDb()).rejects.toThrow("HTTP unavailable");
    }
    vi.mocked(fetch).mockResolvedValue({ ok: true, blob: async () => new Blob() } as Response);
    await expect(queryRows("SELECT 1")).rejects.toThrow("init failed 3 times; aborting");
    expect(fetch).toHaveBeenCalledTimes(3);
    resetDuckDbInitializationFailures();
    expect(await queryRows("SELECT 1")).toEqual([{ ok: 1 }]);
    const healthy = await getDb();
    resetDuckDbInitializationFailures();
    expect(await getDb()).toBe(healthy);
    expect(hoisted.instances).toHaveLength(1);
  });
});

it("does not retry a timeout whose SQL text resembles a transient error", async () => {
  vi.useFakeTimers();
  hoisted.nextInstanceBehaviors.push("dead-after-init", "healthy");
  await getDb();
  const pending = queryRows("SELECT fieldsLength FROM slow_table", [], DUCKDB_USER_QUERY_TIMEOUT_MS);
  const assertion = expect(pending).rejects.toThrow(/did not respond within/);
  await vi.runAllTimersAsync();
  await assertion;
  expect(hoisted.instances).toHaveLength(1);
});
