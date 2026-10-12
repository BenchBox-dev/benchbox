import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const EXPECTED_CONTRACT_VERSION = "6";
export const EXPECTED_READ_MODEL_VERSION = 15;
const EXPECTED_NEWER_READ_MODEL_POLICY = "warn-and-continue";
const REQUIRED_FLAGS = ["--data-dir", "--output", "--trust-label", "--visibility"];
const REQUIRED_OUTPUTS = ["results.duckdb", "bundles/{result_id}.json"];
const REMOVED_LEGACY_OUTPUTS = [
  "manifest.json",
  "benchmarks/",
  "details/",
  "compare/",
  "meta_leaderboard.json",
  "short_ids.json",
  "results_schema.json",
];
export const EXPECTED_COMMAND = "uv run -- python _project/scripts/explorer_publish.py build";
const ALIGNMENT_HINT =
  "Update results-explorer/scripts/explorer-build-contract.mjs or " +
  "_project/scripts/explorer_pipeline/contract.py to re-align.";

const here = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = resolve(here, "..", "..");

function formatSpawnError(result) {
  const stderr = result.stderr?.trim();
  return stderr ? `\n${stderr}` : "";
}

export function readExplorerBuildContract() {
  const result = spawnSync("uv", ["run", "--", "python", "_project/scripts/explorer_publish.py", "build-contract"], {
    cwd: repoRoot,
    encoding: "utf8",
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });

  if (result.status !== 0) {
    throw new Error(
      "Could not read the explorer build contract via " +
      "`uv run -- python _project/scripts/explorer_publish.py build-contract`. " +
        `uv exited ${result.status ?? "unknown"}.${formatSpawnError(result)}`,
    );
  }

  let contract;
  try {
    contract = JSON.parse(result.stdout.trim());
  } catch (error) {
    throw new Error(
      "Explorer build contract was not valid JSON. " +
        `${ALIGNMENT_HINT}\n` +
        String(error),
    );
  }

  if (contract.version !== EXPECTED_CONTRACT_VERSION) {
    throw new Error(
      `Explorer build contract version ${EXPECTED_CONTRACT_VERSION} expected, got ${contract.version}. ` +
        ALIGNMENT_HINT,
    );
  }

  if (contract.command !== EXPECTED_COMMAND) {
    throw new Error(
      `Explorer build contract command ${EXPECTED_COMMAND} expected, got ${contract.command}. ` +
        ALIGNMENT_HINT,
    );
  }

  if (contract.read_model_version !== EXPECTED_READ_MODEL_VERSION) {
    throw new Error(
      `Explorer read-model version ${EXPECTED_READ_MODEL_VERSION} expected, got ${contract.read_model_version}. ` +
        ALIGNMENT_HINT,
    );
  }

  const compatibility = contract.read_model_compatibility ?? {};
  if (compatibility.minimum_supported !== EXPECTED_READ_MODEL_VERSION) {
    throw new Error(
      `Explorer minimum supported read-model version ${EXPECTED_READ_MODEL_VERSION} expected, got ` +
        `${compatibility.minimum_supported}. ${ALIGNMENT_HINT}`,
    );
  }
  if (compatibility.newer_policy !== EXPECTED_NEWER_READ_MODEL_POLICY) {
    throw new Error(
      `Explorer newer read-model policy ${EXPECTED_NEWER_READ_MODEL_POLICY} expected, got ` +
        `${compatibility.newer_policy}. ${ALIGNMENT_HINT}`,
    );
  }

  const actualFlags = new Set(contract.flags ?? []);
  for (const flag of REQUIRED_FLAGS) {
    if (!actualFlags.has(flag)) {
      throw new Error(
        `Explorer build contract missing required flag ${flag}. ` +
          ALIGNMENT_HINT,
      );
    }
  }
  const outputs = contract.outputs ?? {};
  assertIncludesAll(outputs.required, REQUIRED_OUTPUTS, "required");
  assertIncludesAll(outputs.removed_legacy, REMOVED_LEGACY_OUTPUTS, "removed_legacy");

  return {
    ...contract,
    commandArgs: contract.command.split(/\s+/),
  };
}

function assertIncludesAll(actual, expected, fieldName) {
  if (!Array.isArray(actual)) {
    throw new Error(`Explorer build contract outputs.${fieldName} must be an array. ${ALIGNMENT_HINT}`);
  }
  const actualSet = new Set(actual);
  for (const value of expected) {
    if (!actualSet.has(value)) {
      throw new Error(`Explorer build contract outputs.${fieldName} missing ${value}. ${ALIGNMENT_HINT}`);
    }
  }
}
