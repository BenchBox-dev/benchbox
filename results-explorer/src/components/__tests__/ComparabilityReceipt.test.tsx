import { render, screen, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import type { DetailResult } from "@/types";
import {
  COMPARABILITY_WARNING_TARGET_ID,
  buildComparabilityFields,
  comparabilityWarningFields,
  orderWarningLabelsForSummary,
  ComparabilityReceipt,
} from "@/components/ComparabilityReceipt";

function makeDetail(overrides: Partial<DetailResult> = {}): DetailResult {
  return {
    result_id: "r1",
    benchmark: "tpch",
    scale_factor: 0.1,
    platform: "DuckDB",
    platform_id: "duckdb",
    driver_version: "1.0",
    run_date: "2026-04-01",
    total_duration_s: 60,
    geomean_ms: 10,
    display_geomean_ms: 10,
    power_score: 3000,
    has_display_timing: true,
    valid_query_count: 2,
    missing_query_count: 0,
    zero_timing_count: 0,
    display_exclusion_reason: null,
    comparison_exclusion_reason: null,
    ranking_exclusion_reason: null,
    environment: { os: "Linux", arch: "x64", cpu_count: 8, memory_gb: 32, python: "3.12" },
    queries: [],
    display_timings: [
      { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      { query_id: "Q2", display_ms: 20, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
    ],
    has_plans: false,
    has_tuning: false,
    bundle_download_url: "",
    trust_label: "maintainer-run",
    visibility: "public-curated",
    funding: "unspecified",
    platform_version: "0.10",
    execution_mode: "sql",
    tuning_mode: "default",
    tuning_hash: null,
    test_type: "power",
    validation_status: "exact",
    compliance_class: null,
    cost_usd: null,
    normalized_cost_usd: null,
    cost_model_version: "2026.05.0",
    cost_model_source: "benchbox.core.cost.pricing",
    cost_scope: "compute_only",
    cost_status: "unavailable",
    billing_unit: "unknown",
    pricing_region: "unknown",
    ...overrides,
  };
}

describe("ComparabilityReceipt", () => {
  it("renders workload matches and published cost metadata", () => {
    render(<ComparabilityReceipt results={[makeDetail(), makeDetail({ result_id: "r2", platform: "SQLite" })]} />);

    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    expect(receipt.getAttribute("id")).toBe("comparability-receipt");
    expect(within(receipt).getByText("No differences")).toBeTruthy();
    expect(receipt).toHaveTextContent("Benchmark");
    expect(receipt).toHaveTextContent("TPC-H");
    expect(receipt).toHaveTextContent("Query scope");
    expect(receipt).toHaveTextContent("2 queries");
    expect(receipt).toHaveTextContent("Cost model");
    expect(receipt).toHaveTextContent("2026.05.0");
    expect(receipt).not.toHaveTextContent("benchbox.core.cost.pricing");
  });

  it("shows execution engine and gateway routing", () => {
    const fields = buildComparabilityFields([
      makeDetail({ requested_execution_engine: "default" }),
      makeDetail({
        result_id: "r2",
        requested_execution_engine: "streaming",
        gateway_name: "greybeam",
        gateway_routed: true,
      }),
    ]);
    expect(fields.find((field) => field.label === "Execution engine")).toMatchObject({
      status: "diff",
      detail: "DuckDB: default; DuckDB: streaming",
    });
    expect(fields.find((field) => field.label === "Gateway")).toMatchObject({
      status: "diff",
      detail: "DuckDB: Native; DuckDB: Routed via greybeam",
    });
  });

  it("flags normalized cost metadata differences", () => {
    const fields = buildComparabilityFields([
      makeDetail({ normalized_cost_usd: 0.42, cost_status: "normalized" }),
      makeDetail({
        result_id: "r2",
        platform: "SQLite",
        platform_id: "sqlite",
        normalized_cost_usd: null,
        cost_status: "unavailable",
      }),
    ]);

    expect(fields.find((field) => field.label === "Normalized cost")).toMatchObject({
      status: "diff",
      detail: "DuckDB: $0.42; SQLite: Not recorded",
    });
  });

  it("warns when selected runs differ in methodology or environment fields", () => {
    const duckdb = makeDetail();
    const sqlite = makeDetail({
      result_id: "r2",
      platform: "SQLite",
      platform_id: "sqlite",
      driver_version: "2.0",
      run_date: "2026-04-03",
      environment: { os: "macOS", arch: "arm64", cpu_count: 10, memory_gb: 64, python: "3.11" },
      tuning_mode: "manual",
    });

    render(<ComparabilityReceipt results={[duckdb, sqlite]} />);

    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    expect(within(receipt).getAllByText("6 warnings")).toHaveLength(1);
    const warningTarget = screen.getByTestId("comparability-warning-target");
    expect(warningTarget.getAttribute("id")).toBe(COMPARABILITY_WARNING_TARGET_ID);
    expect(warningTarget.getAttribute("tabindex")).toBe("-1");
    expect(warningTarget).toHaveTextContent("Driver version");
    expect(warningTarget).toHaveTextContent("Date window");
    expect(warningTarget).toHaveTextContent("Architecture");
    expect(warningTarget).toHaveTextContent("CPU count");
    expect(warningTarget).toHaveTextContent("Memory");
    expect(receipt).toHaveTextContent("Driver version");
    expect(receipt).toHaveTextContent("DuckDB: 1.0; SQLite: 2.0");
    expect(receipt).toHaveTextContent("Date window");
    expect(receipt.querySelectorAll("[data-testid=run-date-chip]")).toHaveLength(4);
    expect(receipt.querySelector("[data-testid=run-date-chip]")?.getAttribute("aria-label")).toMatch(/2026-04-01.*days ago/);
    expect(receipt).toHaveTextContent("Tuning");
    expect(receipt).toHaveTextContent("DuckDB: default; SQLite: manual");
    expect(receipt).toHaveTextContent("Architecture");
    expect(receipt).toHaveTextContent("DuckDB: x64; SQLite: Arm64");
    expect(receipt).toHaveTextContent("CPU count");
    expect(receipt).toHaveTextContent("DuckDB: 8 CPU; SQLite: 10 CPU");
    expect(receipt).toHaveTextContent("Memory");
    expect(receipt).toHaveTextContent("DuckDB: 32 GB; SQLite: 64 GB");
  });

  it("builds explicit warning fields for compare-page consumers", () => {
    const fields = buildComparabilityFields([
      makeDetail(),
      makeDetail({
        result_id: "r2",
        platform: "SQLite",
        platform_id: "sqlite",
        test_type: "throughput",
        display_timings: [{ query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null }],
      }),
    ]);

    expect(fields.find((field) => field.label === "Test phase")?.status).toBe("diff");
    expect(fields.find((field) => field.label === "Query scope")?.status).toBe("diff");
    expect(comparabilityWarningFields(fields).map((field) => field.label)).toEqual(["Test phase", "Query scope"]);
  });

  it("renders the receipt's Validation row with the reader-facing label, raw status kept alongside it", () => {
    const { container } = render(
      <ComparabilityReceipt
        results={[
          makeDetail({ validation_status: "passed" }),
          makeDetail({ result_id: "r2", platform: "Pandas", platform_id: "pandas", validation_status: "not_run" }),
        ]}
      />,
    );
    const validationCard = [...container.querySelectorAll("h3")]
      .find((el) => el.textContent === "Validation")
      ?.closest("div")?.parentElement;
    expect(validationCard).toBeTruthy();
    expect(validationCard).toHaveTextContent("2 values differ");
    expect(validationCard).toHaveTextContent("DuckDB: Passed (passed); Pandas: No validation (not_run)");
  });

  describe("orderWarningLabelsForSummary", () => {
    it("sorts a Validation warning to the front, ahead of cosmetic environment fields", () => {
      const fields = buildComparabilityFields([
        makeDetail({ platform_version: "1.0", driver_version: "1.0", execution_mode: "in-memory", validation_status: "passed" }),
        makeDetail({
          result_id: "r2",
          platform: "Pandas",
          platform_id: "pandas",
          platform_version: "2.0",
          driver_version: "2.0",
          execution_mode: "lazy",
          validation_status: "not_run",
        }),
      ]);
      const warnings = comparabilityWarningFields(fields);
      expect(warnings.map((f) => f.label).indexOf("Validation")).toBeGreaterThan(0);

      const ordered = orderWarningLabelsForSummary(warnings);
      expect(ordered[0]).toBe("Validation");
      expect(ordered).toEqual(
        expect.arrayContaining(["Platform version", "Driver version", "Execution mode", "Validation"]),
      );
    });

    it("is a no-op when there is no Validation warning", () => {
      const fields = buildComparabilityFields([
        makeDetail(),
        makeDetail({
          result_id: "r2",
          platform: "SQLite",
          platform_id: "sqlite",
          test_type: "throughput",
        }),
      ]);
      const warnings = comparabilityWarningFields(fields);
      expect(orderWarningLabelsForSummary(warnings)).toEqual(warnings.map((f) => f.label));
    });
  });

  it("uses singular count copy for one warning and one query", () => {
    const duckdb = makeDetail({
      display_timings: [
        { query_id: "Q1", display_ms: 10, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      ],
    });
    const sqlite = makeDetail({
      result_id: "r2",
      platform: "SQLite",
      platform_id: "sqlite",
      platform_version: "3.45",
      display_timings: [
        { query_id: "Q1", display_ms: 15, sample_count: 3, is_valid_display_timing: true, timing_exclusion_reason: null },
      ],
    });

    render(<ComparabilityReceipt results={[duckdb, sqlite]} />);

    const receipt = screen.getByRole("region", { name: "Comparison checks" });
    expect(within(receipt).getAllByText("1 warning")).toHaveLength(1);
    expect(receipt).not.toHaveTextContent("1 warnings");
    expect(receipt).toHaveTextContent("1 query");
    expect(receipt).not.toHaveTextContent("1 queries");
  });

  it("flags benchmark and scale differences as explicit warning fields", () => {
    const fields = buildComparabilityFields([
      makeDetail(),
      makeDetail({
        result_id: "r2",
        benchmark: "clickbench",
        scale_factor: 1,
        platform: "SQLite",
        platform_id: "sqlite",
      }),
    ]);

    expect(fields.find((field) => field.label === "Benchmark")).toMatchObject({
      status: "diff",
      detail: "DuckDB: TPC-H; SQLite: ClickBench",
    });
    expect(fields.find((field) => field.label === "Scale factor")).toMatchObject({
      status: "diff",
      detail: "DuckDB: SF 0.1; SQLite: SF 1",
    });
  });

  describe("physical tuning mechanisms warning (ADR-2 §3)", () => {
    it("warns -- without failing the match -- when two tuned runs render different mechanisms", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", physical_mechanisms: ["indexes", "clustering", "distribution", "sort", "z_order", "stats"] }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "tuned", physical_mechanisms: [] }),
      ]);

      const field = fields.find((f) => f.label === "Applied tuning features");
      expect(field).toMatchObject({ status: "diff", summary: "The selected runs applied different tuning features" });
      expect(field?.detail).toContain("DuckDB: indexes, clustering, distribution, sort, z_order, stats");
      expect(field?.detail).toContain("SQLite: none");

      const warnings = comparabilityWarningFields(fields);
      expect(warnings.some((f) => f.label === "Applied tuning features")).toBe(true);
    });

    it("matches (no warning) when two tuned runs render the same mechanism set", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", physical_mechanisms: ["indexes", "clustering"] }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "tuned", physical_mechanisms: ["clustering", "indexes"] }),
      ]);

      expect(fields.find((f) => f.label === "Applied tuning features")).toMatchObject({
        status: "match",
        summary: "2 features",
      });
    });

    it("is omitted when fewer than two results are labeled tuned", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", physical_mechanisms: ["indexes"] }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "notuning" }),
      ]);

      expect(fields.find((f) => f.label === "Applied tuning features")).toBeUndefined();
    });

    it("is omitted when any tuned result predates physical_mechanisms ingest (undefined, not empty)", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", physical_mechanisms: ["indexes"] }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "tuned", physical_mechanisms: undefined }),
      ]);

      expect(fields.find((f) => f.label === "Applied tuning features")).toBeUndefined();
    });
  });

  describe("tuning policy generation warning (ADR-3 seam)", () => {
    it("warns -- without failing the match -- when two tuned runs span different generations", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", tuning_policy_generation: "adr-003" }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "tuned", tuning_policy_generation: undefined }),
      ]);

      const field = fields.find((f) => f.label === "Tuning rules version");
      expect(field).toMatchObject({ status: "diff", summary: "The selected runs used different generations of BenchBox tuning rules" });
      expect(field?.detail).toContain("DuckDB: adr-003");
      expect(field?.detail).toContain("SQLite: Earlier rules");

      const warnings = comparabilityWarningFields(fields);
      expect(warnings.some((f) => f.label === "Tuning rules version")).toBe(true);
    });

    it("matches (no warning) when two tuned runs share the same generation", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", tuning_policy_generation: "adr-003" }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "tuned", tuning_policy_generation: "adr-003" }),
      ]);

      expect(fields.find((f) => f.label === "Tuning rules version")).toMatchObject({
        status: "match",
        summary: "adr-003",
      });
    });

    it("matches (no warning) when both tuned runs are pre-seam (both markers absent)", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", tuning_policy_generation: undefined }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "tuned", tuning_policy_generation: undefined }),
      ]);

      expect(fields.find((f) => f.label === "Tuning rules version")).toMatchObject({
        status: "match",
        summary: "Earlier rules",
      });
      expect(comparabilityWarningFields(fields).some((f) => f.label === "Tuning rules version")).toBe(false);
    });

    it("is omitted when fewer than two results are labeled tuned", () => {
      const fields = buildComparabilityFields([
        makeDetail({ tuning_mode: "tuned", tuning_policy_generation: "adr-003" }),
        makeDetail({ result_id: "r2", platform: "SQLite", tuning_mode: "notuning" }),
      ]);

      expect(fields.find((f) => f.label === "Tuning policy generation")).toBeUndefined();
    });
  });

  describe("tuning identity fingerprint (ADR-1)", () => {
    it("includes the requested-config and applied-ledger hashes in the Tuning fingerprint", () => {
      const fields = buildComparabilityFields([
        makeDetail({
          has_tuning: true,
          tuning_mode: "tuned",
          requested_config_hash: "a".repeat(64),
          applied_ledger_hash: "b".repeat(64),
        }),
        makeDetail({ result_id: "r2", platform: "SQLite", platform_id: "sqlite", tuning_mode: "notuning" }),
      ]);
      const tuning = fields.find((f) => f.label === "Tuning");
      expect(tuning?.detail).toContain("requested aaaaaaaaaaaa");
      expect(tuning?.detail).toContain("applied bbbbbbbbbbbb");
    });

    it("annotates applied-statement drift when the recorded request is identical", () => {
      const fields = buildComparabilityFields([
        makeDetail({
          has_tuning: true,
          tuning_mode: "tuned",
          requested_config_hash: "a".repeat(64),
          applied_ledger_hash: "b".repeat(64),
        }),
        makeDetail({
          result_id: "r2",
          platform: "SQLite",
          platform_id: "sqlite",
          has_tuning: true,
          tuning_mode: "tuned",
          requested_config_hash: "a".repeat(64),
          applied_ledger_hash: "c".repeat(64),
        }),
      ]);

      expect(fields.find((field) => field.label === "Tuning")).toMatchObject({
        status: "diff",
        summary: "Requested configuration matches; applied statements differ",
      });
    });

    it("shows a mode-only tuning value as a plain label, never a hash fingerprint", () => {
      const fields = buildComparabilityFields([
        makeDetail({ has_tuning: true, tuning_mode: "tuned", tuning_hash: "tuning123" }),
        makeDetail({ result_id: "r2", platform: "SQLite", platform_id: "sqlite", tuning_mode: "notuning" }),
      ]);
      const tuning = fields.find((f) => f.label === "Tuning");
      expect(tuning?.detail).toContain("DuckDB: Tuned");
      expect(tuning?.detail).not.toContain("requested");
      expect(tuning?.detail).not.toContain("applied");
      expect(tuning?.detail).not.toContain("tuning123");
    });
  });

  describe("hardware axes and no-flip guarantee (w4)", () => {
    it("splits the monolithic environment row into per-axis hardware rows", () => {
      const results = [
        makeDetail({
          environment: {
            os: "macOS",
            arch: "arm64",
            cpu_family: "apple_silicon",
            cpu_model: "Apple M1 Max",
            cpu_identity_provenance: "measured",
            cpu_count: 10,
            memory_gb: 64,
            python: "3.12",
          },
        }),
        makeDetail({
          result_id: "r2",
          platform: "SQLite",
          platform_id: "sqlite",
          environment: {
            os: "macOS",
            arch: "arm64",
            cpu_family: "apple_silicon",
            cpu_model: "Apple M1 Max",
            cpu_identity_provenance: "measured",
            cpu_count: 10,
            memory_gb: 64,
            python: "3.12",
          },
        }),
      ];

      const fields = buildComparabilityFields(results);
      const labels = fields.map((f) => f.label);
      expect(labels).toContain("Architecture");
      expect(labels).toContain("CPU family");
      expect(labels).toContain("CPU model");
      expect(labels).toContain("CPU evidence");
      expect(labels).toContain("CPU count");
      expect(labels).toContain("Memory");
      expect(labels).not.toContain("Environment");

      expect(fields.find((f) => f.label === "Architecture")).toMatchObject({
        status: "match",
        summary: "Arm64",
      });
      expect(fields.find((f) => f.label === "CPU family")).toMatchObject({
        status: "match",
        summary: "Apple silicon",
      });
      expect(fields.find((f) => f.label === "CPU model")).toMatchObject({
        status: "match",
        summary: "Apple M1 Max",
      });
      expect(fields.find((f) => f.label === "CPU evidence")).toMatchObject({
        status: "match",
        summary: "Measured",
      });
      expect(fields.find((f) => f.label === "CPU count")).toMatchObject({
        status: "match",
        summary: "10 CPU",
      });
      expect(fields.find((f) => f.label === "Memory")).toMatchObject({
        status: "match",
        summary: "64 GB",
      });
    });

    it("pins the no-flip guarantee: a run without CPU metadata compared to another run reports 'not recorded' on the missing axes, not 'differs'", () => {
      const recordedRun = makeDetail({
        result_id: "r1",
        platform: "DuckDB",
        environment: {
          os: "macOS",
          arch: "arm64",
          cpu_family: "apple_silicon",
          cpu_model: "Apple M1 Max",
          cpu_count: 10,
          memory_gb: 64,
          python: "3.12",
        },
      });

      const legacyRun = makeDetail({
        result_id: "r2",
        platform: "SQLite",
        platform_id: "sqlite",
        environment: {
          os: "macOS",
          arch: "arm64",
          cpu_family: undefined,
          cpu_model: undefined,
          cpu_count: 10,
          memory_gb: 64,
          python: "3.12",
        },
      });

      const fields = buildComparabilityFields([recordedRun, legacyRun]);
      const cpuFamilyField = fields.find((f) => f.label === "CPU family")!;
      const cpuModelField = fields.find((f) => f.label === "CPU model")!;

      expect(cpuFamilyField.status).toBe("missing");
      expect(cpuFamilyField.summary).toBe("Not recorded");
      expect(cpuFamilyField.detail).toBe("DuckDB: Apple silicon; SQLite: Not recorded");

      expect(cpuModelField.status).toBe("missing");
      expect(cpuModelField.summary).toBe("Not recorded");
      expect(cpuModelField.detail).toBe("DuckDB: Apple M1 Max; SQLite: Not recorded");

      const warnings = comparabilityWarningFields(fields);
      expect(warnings.some((w) => w.label === "CPU family")).toBe(false);
      expect(warnings.some((w) => w.label === "CPU model")).toBe(false);
    });

    it("reports diff when both runs record CPU metadata but the values differ", () => {
      const appleRun = makeDetail({
        result_id: "r1",
        platform: "DuckDB",
        environment: {
          os: "macOS",
          arch: "arm64",
          cpu_family: "apple_silicon",
          cpu_model: "Apple M1 Max",
          cpu_count: 10,
          memory_gb: 64,
          python: "3.12",
        },
      });

      const gravitonRun = makeDetail({
        result_id: "r2",
        platform: "ClickHouse",
        platform_id: "clickhouse",
        environment: {
          os: "Linux",
          arch: "arm64",
          cpu_family: "graviton",
          cpu_model: "AWS Graviton 3",
          cpu_count: 16,
          memory_gb: 64,
          python: "3.12",
        },
      });

      const fields = buildComparabilityFields([appleRun, gravitonRun]);
      const cpuFamilyField = fields.find((f) => f.label === "CPU family")!;
      const cpuModelField = fields.find((f) => f.label === "CPU model")!;

      expect(cpuFamilyField.status).toBe("diff");
      expect(cpuFamilyField.summary).toBe("2 values differ");
      expect(cpuFamilyField.detail).toBe("DuckDB: Apple silicon; ClickHouse: graviton");

      expect(cpuModelField.status).toBe("diff");
      expect(cpuModelField.summary).toBe("2 values differ");
      expect(cpuModelField.detail).toBe("DuckDB: Apple M1 Max; ClickHouse: AWS Graviton 3");

      const warnings = comparabilityWarningFields(fields);
      expect(warnings.some((w) => w.label === "CPU family")).toBe(true);
      expect(warnings.some((w) => w.label === "CPU model")).toBe(true);
    });

    it("warns when client region != platform region or if locality is unknown for remote/cloud platforms", () => {
      const crossRegionRun = makeDetail({
        result_id: "r1",
        platform: "Snowflake",
        deployment_class: "cloud",
        cloud_provider: "aws",
        cloud_region: "us-east-1",
        environment: {
          client_region: "us-west-2",
        },
      });
      const collocatedRun = makeDetail({
        result_id: "r2",
        platform: "BigQuery",
        deployment_class: "cloud",
        cloud_provider: "gcp",
        cloud_region: "us-east-1",
        environment: {
          client_region: "us-east-1",
        },
      });

      let fields = buildComparabilityFields([crossRegionRun, collocatedRun]);
      let localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("diff");
      expect(localityField.detail).toContain("Cross-region: client in us-west-2, platform in us-east-1");

      const unknownLocalityRun = makeDetail({
        result_id: "r3",
        platform: "Snowflake",
        deployment_class: "cloud",
        cloud_provider: "aws",
        cloud_region: "us-east-1",
        environment: {},
      });
      fields = buildComparabilityFields([unknownLocalityRun]);
      localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("diff");
      expect(localityField.summary).toBe("Unknown client locality");

      const collocatedRun2 = makeDetail({
        result_id: "r4",
        platform: "Snowflake",
        deployment_class: "cloud",
        cloud_provider: "aws",
        cloud_region: "us-east-1",
        environment: {
          client_region: "us-east-1",
        },
      });
      fields = buildComparabilityFields([collocatedRun, collocatedRun2]);
      localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("match");
      expect(localityField.summary).toBe("Collocated (us-east-1)");
    });

    it("does not warn for provider-native spellings of the same region", () => {
      const snowflakeRun = makeDetail({
        result_id: "r1",
        platform: "Snowflake",
        deployment_class: "cloud",
        cloud_provider: "aws",
        cloud_region: "AWS_US_EAST_1",
        environment: {
          client_region: "us-east-1",
          client_cloud: "aws",
        },
      });
      const fields = buildComparabilityFields([snowflakeRun]);
      const localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("match");
      expect(localityField.summary).toBe("Collocated (us-east-1)");
    });

    it("normalizes Azure display regions before comparing", () => {
      const azureRun = makeDetail({
        result_id: "r1",
        platform: "Azure SQL",
        deployment_class: "cloud",
        cloud_provider: "azure",
        cloud_region: "East US 2",
        environment: {
          client_region: "eastus2",
          client_cloud: "azure",
        },
      });
      const fields = buildComparabilityFields([azureRun]);
      const localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("match");
    });

    it("distinguishes same-named regions across clouds", () => {
      const awsRun = makeDetail({
        result_id: "r1",
        platform: "Snowflake",
        deployment_class: "cloud",
        cloud_provider: "aws",
        cloud_region: "us-east-1",
        environment: {
          client_region: "us-east-1",
          client_cloud: "aws",
        },
      });
      const gcpRun = makeDetail({
        result_id: "r2",
        platform: "BigQuery",
        deployment_class: "cloud",
        cloud_provider: "gcp",
        cloud_region: "us-east1",
        environment: {
          client_region: "us-east1",
          client_cloud: "gcp",
        },
      });
      const fields = buildComparabilityFields([awsRun, gcpRun]);
      const localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("diff");
      expect(localityField.detail).toContain("Collocated (us-east-1)");
      expect(localityField.detail).toContain("Collocated (us-east1)");
    });

    it("warns instead of asserting collocation when the platform region is unknown", () => {
      const remoteRun = makeDetail({
        result_id: "r1",
        platform: "ClickHouse Server",
        deployment_class: "remote",
        environment: {
          client_region: "us-east-1",
          client_cloud: "aws",
        },
      });
      const fields = buildComparabilityFields([remoteRun]);
      const localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("diff");
      expect(localityField.summary).toBe("Client in us-east-1, platform locality unknown");
    });

    it("warns when client and platform clouds differ on the same region name", () => {
      const run = makeDetail({
        result_id: "r1",
        platform: "Snowflake",
        deployment_class: "cloud",
        cloud_provider: "aws",
        cloud_region: "us-east-1",
        environment: {
          client_region: "us-east-1",
          client_cloud: "gcp",
        },
      });
      const fields = buildComparabilityFields([run]);
      const localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("diff");
      expect(localityField.detail).toContain("Cross-cloud: client in us-east-1 (gcp), platform in us-east-1 (aws)");
    });

    it("appends the measured statement floor to cross-region detail", () => {
      const run = makeDetail({
        result_id: "r1",
        platform: "Snowflake",
        deployment_class: "cloud",
        cloud_provider: "aws",
        cloud_region: "us-east-1",
        environment: {
          client_region: "eu-west-1",
          client_cloud: "aws",
          statement_overhead_median_ms: 88.2,
        },
      });
      const fields = buildComparabilityFields([run]);
      const localityField = fields.find((f) => f.label === "Locality")!;
      expect(localityField.status).toBe("diff");
      expect(localityField.detail).toContain("client statement floor 88 ms");
    });
  });
});
