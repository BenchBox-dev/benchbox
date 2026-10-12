import { afterEach, describe, expect, it, vi } from "vitest";
import {
  _EXPECTED_READ_MODEL_VERSION_FOR_TEST,
  _validateAttachedSnapshotForTest,
  _verifyReadModelVersionForTest,
} from "@/db";

interface QueryResult {
  toArray(): {
    result_id?: string;
    n?: number;
    toJSON(): { read_model_version?: number; result_id?: string };
  }[];
}

interface FakeConn {
  query(sql: string): Promise<QueryResult>;
}

function makeConn(version: number | null): FakeConn {
  return {
    async query(_sql: string): Promise<QueryResult> {
      if (version === null) {
        throw new Error("Catalog Error: Table with name metadata does not exist");
      }
      return {
        toArray: () => [
          {
            toJSON: () => ({ read_model_version: version }),
          },
        ],
      };
    },
  };
}

describe("read-model version guard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rejects a missing metadata table as stale v0", async () => {
    const conn = makeConn(null);
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(new RegExp(`read-model v0; UI requires v${_EXPECTED_READ_MODEL_VERSION_FOR_TEST}`));
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.not.toThrow(/is missing required columns/);
  });

  it("does not mask unexpected DuckDB failures as stale v0", async () => {
    const conn = {
      async query(_sql: string): Promise<QueryResult> {
        throw new Error("IO Error: could not read DuckDB file");
      },
    };

    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(/could not read DuckDB file/);
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.not.toThrow(/read-model v0/);
  });

  it("does not mask generic catalog metadata failures as stale v0", async () => {
    const conn = {
      async query(_sql: string): Promise<QueryResult> {
        throw new Error("Catalog Error: failed to load database metadata block");
      },
    };

    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(/failed to load database metadata block/);
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.not.toThrow(/read-model v0/);
  });

  it("rejects an older read-model version with a humane dev remediation message", async () => {
    const conn = makeConn(0);
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(new RegExp(`read-model v0; UI requires v${_EXPECTED_READ_MODEL_VERSION_FOR_TEST}`));
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(/npm run dev:snapshot/);
  });

  it("resolves when the snapshot version matches the UI contract", async () => {
    const conn = makeConn(_EXPECTED_READ_MODEL_VERSION_FOR_TEST);
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).resolves.toBeUndefined();
  });

  it("warns but proceeds when the snapshot version is newer than the UI contract", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const conn = makeConn(_EXPECTED_READ_MODEL_VERSION_FOR_TEST + 1);

    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).resolves.toBeUndefined();

    expect(warn).toHaveBeenCalledWith(expect.stringContaining("Proceeding with forward-compatible reads"));
  });

  it("validates the read-model version before probing schema readiness", async () => {
    const queries: string[] = [];
    const conn = {
      async query(sql: string): Promise<QueryResult> {
        queries.push(sql);
        if (sql.includes("bench.metadata")) {
          return {
            toArray: () => [
              {
                toJSON: () => ({ read_model_version: 0 }),
              },
            ],
          };
        }
        throw new Error("Catalog Error: Table with name results does not exist");
      },
    };

    await expect(
      _validateAttachedSnapshotForTest(conn as unknown as Parameters<typeof _validateAttachedSnapshotForTest>[0]),
    ).rejects.toThrow(new RegExp(`read-model v0; UI requires v${_EXPECTED_READ_MODEL_VERSION_FOR_TEST}`));

    expect(queries).toEqual(["SELECT read_model_version FROM bench.metadata LIMIT 1"]);
  });

  it("retries transient metadata-read failures before probing schema readiness", async () => {
    const queries: string[] = [];
    const conn = {
      async query(sql: string): Promise<QueryResult> {
        queries.push(sql);
        if (sql.includes("bench.metadata") && queries.length === 1) {
          throw new Error("Cannot read fieldsLength while the snapshot is warming");
        }
        if (sql.includes("bench.metadata")) {
          return {
            toArray: () => [
              {
                toJSON: () => ({ read_model_version: _EXPECTED_READ_MODEL_VERSION_FOR_TEST }),
              },
            ],
          };
        }
        if (/COUNT\(\*\)/i.test(sql)) {
          return { toArray: () => [{ n: 1, toJSON: () => ({}) }] };
        }
        return {
          toArray: () => [
            {
              result_id: "r1",
              toJSON: () => ({ result_id: "r1" }),
            },
          ],
        };
      },
    };

    await expect(
      _validateAttachedSnapshotForTest(conn as unknown as Parameters<typeof _validateAttachedSnapshotForTest>[0]),
    ).resolves.toBeUndefined();

    expect(queries.slice(0, 2)).toEqual([
      "SELECT read_model_version FROM bench.metadata LIMIT 1",
      "SELECT read_model_version FROM bench.metadata LIMIT 1",
    ]);
    expect(queries[2]).toBe("SELECT result_id FROM bench.results LIMIT 1");
  });

  it("refuses a v2 snapshot, which lacks the funding projections the cards read", async () => {
    const conn = makeConn(2);
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(new RegExp(`read-model v2; UI requires v${_EXPECTED_READ_MODEL_VERSION_FOR_TEST}`));
  });

  it("refuses a v14 snapshot, which lacks the template evidence column the detail view reads", async () => {
    const conn = makeConn(14);
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(new RegExp(`read-model v14; UI requires v${_EXPECTED_READ_MODEL_VERSION_FOR_TEST}`));
    await expect(
      _verifyReadModelVersionForTest(conn as unknown as Parameters<typeof _verifyReadModelVersionForTest>[0]),
    ).rejects.toThrow(/npm run dev:snapshot/);
  });
});
