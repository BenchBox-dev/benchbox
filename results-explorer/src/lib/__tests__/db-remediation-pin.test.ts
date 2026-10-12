import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { _EXPECTED_READ_MODEL_VERSION_FOR_TEST, _verifyReadModelVersionForTest } from "@/db";

const EXPECTED_EXPLORER_BUILD_COMMAND =
  "uv run -- python _project/scripts/explorer_publish.py build";
const CURRENT_READ_MODEL_VERSION = 15;
const NEWER_READ_MODEL_POLICY = "warn-and-continue";
const repoRoot = resolve(process.cwd(), "..");

type SnapshotConnection = Parameters<typeof _verifyReadModelVersionForTest>[0];

function connectionWithReadModelVersion(version: number): SnapshotConnection {
  return {
    query: async () => ({
      toArray: () => [{ toJSON: () => ({ read_model_version: version }) }],
    }),
  } as unknown as SnapshotConnection;
}

describe("db remediation command pin", () => {
  it("matches the live explorer build contract", () => {
    const result = spawnSync(
      "uv",
      ["run", "--no-sync", "--", "python", "_project/scripts/explorer_publish.py", "build-contract"],
      {
        cwd: repoRoot,
        encoding: "utf8",
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
      },
    );

    expect(result.status, result.stderr).toBe(0);
    const contract = JSON.parse(result.stdout) as {
      command?: string;
      read_model_version?: number;
      read_model_compatibility?: {
        minimum_supported?: number;
        newer_policy?: string;
      };
    };
    expect(contract.command).toBe(EXPECTED_EXPLORER_BUILD_COMMAND);
    expect(contract.read_model_version).toBe(CURRENT_READ_MODEL_VERSION);
    expect(contract.read_model_compatibility).toEqual({
      minimum_supported: CURRENT_READ_MODEL_VERSION,
      newer_policy: NEWER_READ_MODEL_POLICY,
    });
    expect(_EXPECTED_READ_MODEL_VERSION_FOR_TEST).toBe(CURRENT_READ_MODEL_VERSION);
  }, 30_000);

  it.each([
    { label: "older", version: CURRENT_READ_MODEL_VERSION - 1 },
    { label: "missing", version: 0 },
  ])("rejects $label incompatible snapshots clearly", async ({ version }) => {
    await expect(_verifyReadModelVersionForTest(connectionWithReadModelVersion(version))).rejects.toThrow(
      `DuckDB snapshot read-model v${version}; UI requires v${CURRENT_READ_MODEL_VERSION}.`,
    );
  });

  it("accepts the current read-model version", async () => {
    await expect(
      _verifyReadModelVersionForTest(connectionWithReadModelVersion(CURRENT_READ_MODEL_VERSION)),
    ).resolves.toBeUndefined();
  });

  it("warns and continues for a newer forward-compatible snapshot", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await expect(
      _verifyReadModelVersionForTest(connectionWithReadModelVersion(CURRENT_READ_MODEL_VERSION + 1)),
    ).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledWith(
      `DuckDB snapshot read-model v${CURRENT_READ_MODEL_VERSION + 1}; ` +
        `UI expects v${CURRENT_READ_MODEL_VERSION}. Proceeding with forward-compatible reads.`,
    );

    warn.mockRestore();
  });
});
