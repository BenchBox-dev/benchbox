export interface Environment {
  os?: string;
  arch?: string;
  cpu_count?: number;
  memory_gb?: number;
  python?: string;
  cpu_model?: string;
  cpu_family?: string;
  cpu_identity_provenance?: "measured" | "user_attested" | "inferred";
  client_region?: string | null;
  client_cloud?: string | null;
  statement_overhead_min_ms?: number | null;
  statement_overhead_median_ms?: number | null;
  link_status?: string | null;
  [key: string]: string | number | boolean | null | undefined;
}

export interface QueryTiming {
  query_id: string;
  duration_ms: number;
  status: "pass" | "fail";
  run_type: string | null;
  iter: number | null;
  stream: number | null;
}

export interface QueryDisplayTiming {
  query_id: string;
  display_ms: number | null;
  sample_count: number;
  is_valid_display_timing: boolean;
  timing_exclusion_reason: string | null;
}

export interface CostDeploymentFields {
  normalized_cost_usd?: number | null;
  cost_model_version?: string | null;
  cost_model_source?: string | null;
  cost_scope?: string | null;
  cost_status?: string | null;
  billing_unit?: string | null;
  pricing_region?: string | null;
  deployment_class?: string | null;
  cloud_provider?: string | null;
  cloud_region?: string | null;
  instance_or_warehouse?: string | null;
  instance_type?: string | null;
  warehouse_size?: string | null;
  node_count?: number | null;
  cluster_size?: string | null;
  storage_format?: string | null;
  storage_tier?: string | null;
}

export interface DetailResult extends CostDeploymentFields {
  result_id: string;
  benchmark: string;
  scale_factor: number;
  platform: string;
  platform_id: string;
  driver_version: string | null;
  run_date: string;
  total_duration_s: number;
  geomean_ms: number | null;
  display_geomean_ms: number | null;
  power_score: number | null;
  throughput_at_size?: number | null;
  stream_count?: number | null;
  has_display_timing: boolean;
  logical_query_count?: number;
  valid_query_count: number;
  missing_query_count: number;
  zero_timing_count: number;
  display_exclusion_reason: string | null;
  comparison_exclusion_reason: string | null;
  ranking_exclusion_reason: string | null;
  environment: Environment;
  queries: QueryTiming[];
  display_timings: QueryDisplayTiming[];
  has_plans: boolean;
  plans_published?: boolean | null;
  has_tuning: boolean;
  bundle_download_url: string;
  trust_label: string;
  visibility: string;
  funding: string;
  platform_version: string | null;
  execution_mode: string | null;
  tuning_mode: string | null;
  tuning_hash: string | null;
  requested_config_hash?: string | null;
  applied_ledger_hash?: string | null;
  tuning_validation_status?: string | null;
  applied_receipt?: string | null;
  override_rules?: string | null;
  override_evidence?: string | null;
  override_approver?: string | null;
  override_expires?: string | null;
  tuning_policy_generation?: string | null;
  template_evidence?: string | null;
  test_type: string | null;
  validation_status: string | null;
  cost_usd: number | null;
  compliance_class: string | null;
  physical_mechanisms?: string[];
  physical_rendering_id?: string | null;
  client_region?: string | null;
  client_cloud?: string | null;
  statement_overhead_min_ms?: number | null;
  statement_overhead_median_ms?: number | null;
  link_status?: string | null;
}

export interface RankingConfig {
  primary_metric: string;
  secondary_metric: string;
  primary_order: "asc" | "desc";
}

export interface PercentileStats {
  p50: number;
  p90: number;
  p95: number;
  p99: number;
}

export interface PlatformRow extends CostDeploymentFields {
  result_id: string;
  short_id: string;
  platform_id: string;
  platform: string;
  platform_version: string | null;
  tuning_mode: string | null;
  tuning_hash: string | null;
  execution_mode: string | null;
  trust_label: string;
  funding: string;
  validation_status?: string | null;
  override_rules?: string | null;
  run_date: string;
  is_ranking_eligible: boolean;
  has_display_timing: boolean;
  logical_query_count?: number;
  valid_query_count: number;
  missing_query_count: number;
  zero_timing_count: number;
  display_exclusion_reason: string | null;
  comparison_exclusion_reason: string | null;
  ranking_exclusion_reason: string | null;
  power_score: number | null;
  throughput_at_size?: number | null;
  display_geomean_ms: number | null;
  sample_geomean_ms: number | null;
  cost_usd: number | null;
  compliance_class: string | null;
  percentile_stats: PercentileStats | null;
  phase_durations: Record<string, number> | null;
  timings: Record<string, number | null>;
  timing_eligibility: Record<string, Pick<QueryDisplayTiming, "is_valid_display_timing" | "timing_exclusion_reason">>;
  arch?: string | null;
  cpu_family?: string | null;
  memory_gb?: number | null;
}

export interface BenchmarkSummary {
  benchmark: string;
  scale_factor: number;
  phase: string;
  stream_count?: number | null;
  query_ids: string[];
  platforms: PlatformRow[];
  cell_reduction: string;
  ranking: RankingConfig | null;
}

export interface MetaRank {
  rank: number;
  total: number;
  metric_value?: number | null;
  speedup_vs_best?: number | null;
}

export interface MetaPlatform {
  platform_id: string;
  platform: string;
  ranks: Record<string, MetaRank>;
  avg_rank: number | null;
  n_cohorts: number;
}

export interface MetaCohortPlatform {
  platform_id: string;
  platform: string;
  result_id: string;
  rank: number | null;
  metric_value: number | null;
  speedup_vs_best: number | null;
  primary_metric: string;
  primary_order: "asc" | "desc";
  has_display_timing: boolean;
  logical_query_count?: number;
  valid_query_count: number;
  missing_query_count: number;
  zero_timing_count: number;
  display_exclusion_reason: string | null;
  comparison_exclusion_reason: string | null;
  ranking_exclusion_reason: string | null;
}

export interface MetaCohort {
  key: string;
  benchmark: string;
  scale_factor: number;
  phase: string;
  stream_count?: number | null;
  label: string;
  href: string;
  platform_count: number;
  cohort_ranked_count: number;
  cohort_ranking_exclusion_reason: string | null;
  primary_metric: string;
  primary_order: "asc" | "desc";
  platforms?: MetaCohortPlatform[];
}

export interface MetaLeaderboard {
  generated_at: string;
  cohorts: MetaCohort[];
  platforms: MetaPlatform[];
}

export type SortDirection = "asc" | "desc";

export interface SortState<K extends string> {
  key: K;
  direction: SortDirection;
}
