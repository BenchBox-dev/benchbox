import { MISSING_VALUE_GLYPH } from "@/lib/metricFormatters";
import { formatLatencyMs, formatPowerScore, formatPowerScoreExact } from "./lib/metricFormatters";

export const BENCHMARK_LABELS: Record<string, string> = {
  ai_primitives: "AI Primitives",
  amplab: "AMPLab",
  clickbench: "ClickBench",
  coffeeshop: "CoffeeShop",
  datavault: "TPC-H Data Vault",
  flightdata: "Flight Data",
  h2odb: "H2ODB",
  joinorder: "JoinOrder",
  metadata_primitives: "Metadata",
  nyctaxi: "NYC Taxi",
  read_primitives: "Read Primitives",
  star_schema: "SSB",
  ssb: "SSB",
  "tsbs-devops": "TSBS DevOps",
  tsbs_devops: "TSBS DevOps",
  tpcdi: "TPC-DI",
  tpcds: "TPC-DS",
  tpcds_obt: "TPC-DS-OBT",
  tpch: "TPC-H",
  tpch_skew: "TPC-H Skew",
  tpchavoc: "TPC-Havoc",
  transaction_primitives: "Transactions",
  vector_search: "Vector Search",
  write_primitives: "Write Primitives",
};

export function humanizeBenchmark(benchmark: string): string {
  return BENCHMARK_LABELS[benchmark] ?? benchmark.toUpperCase();
}

export function isKnownBenchmark(benchmark: string): boolean {
  return Object.prototype.hasOwnProperty.call(BENCHMARK_LABELS, benchmark);
}

export function fmtScore(score: number | null | undefined): string {
  return formatPowerScore(score).valueText;
}

export function fmtScoreCompact(score: number | null | undefined): string {
  return formatPowerScore(score).valueText;
}

export function fmtScoreExact(score: number | null | undefined): string {
  return formatPowerScoreExact(score).valueText;
}

export function fmtMs(ms: number): string {
  return formatLatencyMs(ms).valueText;
}

export function fmtGeomean(ms: number | null | undefined): string {
  return ms != null ? fmtMs(ms) : MISSING_VALUE_GLYPH;
}

export function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function shortHash(hash: string, length = 12): string {
  return hash.length > length ? hash.slice(0, length) : hash;
}

export function complianceLabel(complianceClass: string | null | undefined): string {
  switch (complianceClass) {
    case "unofficial_subscale":
      return "(subscale)";
    case "unofficial_nonstandard":
      return "(non-standard)";
    default:
      return "(unofficial)";
  }
}
