import { PageHeader } from "@/components/PageHeader";
import { ProvenanceLegend } from "@/components/ProvenanceLegend";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { RoutableProps } from "preact-router";
import type {
  MetaCohort,
  MetaCohortPlatform,
  MetaLeaderboard as MetaLeaderboardData,
  MetaPlatform,
  MetaRank,
} from "@/types";
import type { ResultRow } from "@/lib/duckdbQueries";
import { getMetaLeaderboardData, listResults } from "@/lib/duckdbQueries";
import { humanizeBenchmark, errMsg } from "@/utils";
import { canonicalBenchmarkSlug, formatBenchmarkLabel } from "@/lib/displayLabels";
import { MetaLeaderboardSkeleton, SkeletonBlock } from "@/components/LoadingSpinner";
import { ErrorMessage } from "@/components/ErrorMessage";
import { MetaLeaderboard, variantKeyOf, variantLabelOf } from "@/components/MetaLeaderboard";
import type { MetaLeaderboardMode } from "@/components/MetaLeaderboard";
import {
  ALL_FACET_KEYS,
  facetsToWhereClause,
  useFacetState,
  type ExplorerFacetKey,
  type FacetState,
} from "@/lib/facetModel";
import {
  appendFacetParams,
  hasActiveFacets,
  matchesFacetRow,
  singleFacetValue,
  toggleFacetValue,
  toDateWindowFacet,
} from "@/lib/facetMatching";
import { formatCountWithVerb } from "@/lib/copyFormatters";
import { formatFacetDisplayValue } from "@/lib/facetDisplay";
import { stringSerde, useUrlState } from "@/lib/useUrlState";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { usePickingState } from "@/lib/pickingState";
import {
  EXPLORER_PERFORMANCE_MARKS,
  EXPLORER_PERFORMANCE_MEASURES,
  markExplorerPerformance,
  measureExplorerPerformance,
} from "@/lib/performanceMarks";

const UNLABELLED_TUNING_VALUE = "untuned";

export const LEADERBOARD_SHELL_GEOMETRY_CLASSES = {
  heroSurface: "surface-hero border-b border-[var(--bb-border-default)]",
  heroWrapper: "mx-auto max-w-7xl px-4 py-4 sm:px-6 sm:py-7 lg:px-8 lg:py-4",
  heroIntro: "max-w-4xl",
  headline: "text-2xl font-bold sm:text-4xl",
  subtitle: "mt-2 max-w-3xl text-sm text-[var(--bb-fg-muted)] sm:mt-3 sm:text-lg",
  activeSummary:
    "mt-4 rounded-lg border border-[var(--bb-border-default)] bg-[var(--bb-bg-panel)] p-2 sm:mt-5 sm:p-3",
  activeSummaryItems: "flex flex-wrap gap-2",
  activeSummaryChip:
    "inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full border border-[var(--bb-border-subtle)] bg-[var(--bb-bg-primary)] px-2.5 py-1 text-xs",
  activeSummaryLabel: "shrink-0 font-semibold uppercase tracking-wide text-[var(--bb-fg-muted)]",
  activeSummaryValue: "truncate font-medium text-[var(--bb-fg-primary)]",
  activeFacetList: "mt-3 grid gap-2 sm:flex sm:flex-wrap",
  activeFacetChip:
    "block min-w-0 truncate rounded-full bg-[var(--bb-bg-elevated)] px-3 py-1.5 text-xs font-medium text-[var(--bb-fg-muted)]",
  rankingSelector:
    "mt-3 rounded-lg border border-[var(--bb-border-default)] bg-[var(--bb-bg-panel)] p-2 sm:mt-5 sm:p-3",
  rankingGrid: "grid grid-cols-2 gap-2 sm:gap-3 md:grid-cols-3 xl:grid-cols-5",
  scopeDetails: "mt-2 border-t border-[var(--bb-border-default)] pt-2",
  scopeSummary:
    "cursor-pointer text-xs font-medium text-[var(--bb-fg-muted)] hover:text-[var(--bb-fg-primary)]",
  advancedDetails: "mt-3 border-t border-[var(--bb-border-default)] pt-3",
  advancedSummary:
    "cursor-pointer text-sm font-medium text-[var(--bb-fg-muted)] hover:text-[var(--bb-fg-primary)]",
  dataSurface: "surface-app mx-auto max-w-7xl px-4 py-4 sm:px-6 sm:py-8 lg:px-8",
} as const;

interface TuningModeSummary {
  labelledOptions: string[];
  unlabelledCount: number;
  labelledCount: number;
  totalCount: number;
}

interface LeaderboardProps extends RoutableProps {
  notice?: string | null;
}

export function Leaderboard({ notice = null }: LeaderboardProps) {
  useDocumentTitle("Compare benchmark results · BenchBox");
  const [results, setResults] = useState<ResultRow[] | null>(null);
  const [platformVersionDomainResults, setPlatformVersionDomainResults] = useState<ResultRow[] | null>(null);
  const [metaLeaderboard, setMetaLeaderboard] = useState<MetaLeaderboardData | null>(null);
  const [metaLeaderboardLoaded, setMetaLeaderboardLoaded] = useState(false);
  const retriedEmptyResults = useRef(false);
  const [emptyResultsRetryFinished, setEmptyResultsRetryFinished] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultsRetryToken, setResultsRetryToken] = useState(0);
  const { facets, where: facetWhere, setFacet, resetFacets } = useFacetState();
  const [modeRaw, setModeRaw] = useUrlState<string>("mode", "speedup", stringSerde);
  const benchmarkFilters = facets.benchmark;
  const scaleFilters = facets.scale_factor;
  const phaseFilter = singleFacetValue(facets.phase, "all");
  const streamFilters = facets.stream_count;
  const tuningFilter = singleFacetValue(facets.tuning_mode, "all");
  const trustFilter = singleFacetValue(facets.trust_tier, "all");
  const dateWindow = facets.date_window;
  const platformVersionDomainWhere = useMemo(
    () => facetsToWhereClause({ ...facets, platform_version: [] }),
    [
      facets.arch,
      facets.benchmark,
      facets.cloud_provider,
      facets.cloud_region,
      facets.cost_status,
      facets.cpu_family,
      facets.date_window,
      facets.deployment_class,
      facets.execution_mode,
      facets.instance_or_warehouse,
      facets.phase,
      facets.platform,
      facets.scale_factor,
      facets.storage_format,
      facets.stream_count,
      facets.trust_tier,
      facets.tuning_mode,
      facets.validation_status,
    ],
  );

  useEffect(() => {
    let cancelled = false;
    setError(null);
    listResults(facetWhere)
      .then((rows) => {
        if (!cancelled) setResults(rows);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errMsg(err));
      });
    return () => {
      cancelled = true;
    };
  }, [facetWhere, resultsRetryToken]);

  useEffect(() => {
    if (facets.platform_version.length === 0) {
      setPlatformVersionDomainResults(null);
      return;
    }
    let cancelled = false;
    listResults(platformVersionDomainWhere)
      .then((rows) => {
        if (!cancelled) setPlatformVersionDomainResults(rows);
      })
      .catch(() => {
        if (!cancelled) setPlatformVersionDomainResults(null);
      });
    return () => {
      cancelled = true;
    };
  }, [facets.platform_version, platformVersionDomainWhere]);

  useEffect(() => {
    let cancelled = false;
    setMetaLeaderboardLoaded(false);
    markExplorerPerformance(EXPLORER_PERFORMANCE_MARKS.HOME_LEADERBOARD_DATA_START, { once: true });
    getMetaLeaderboardData()
      .then((data) => {
        if (!cancelled) {
          markExplorerPerformance(EXPLORER_PERFORMANCE_MARKS.HOME_LEADERBOARD_DATA_READY, {
            once: true,
            detail: { cohortCount: data?.cohorts.length ?? 0 },
          });
          measureExplorerPerformance(
            EXPLORER_PERFORMANCE_MEASURES.HOME_LEADERBOARD_DATA,
            EXPLORER_PERFORMANCE_MARKS.HOME_LEADERBOARD_DATA_START,
            EXPLORER_PERFORMANCE_MARKS.HOME_LEADERBOARD_DATA_READY,
            { once: true },
          );
          setMetaLeaderboard(data);
          setMetaLeaderboardLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setMetaLeaderboardLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [resultsRetryToken]);

  const hasInconsistentEmptySnapshot =
    facetWhere.sql === "" && results !== null && results.length === 0 && metaLeaderboard !== null;
  const hasPersistentInconsistentEmptySnapshot = hasInconsistentEmptySnapshot && emptyResultsRetryFinished;

  useEffect(() => {
    if (!hasInconsistentEmptySnapshot || retriedEmptyResults.current) return;
    let cancelled = false;
    retriedEmptyResults.current = true;
    if (typeof console !== "undefined") {
      console.warn(
        "[Home] Inconsistent cold-load snapshot detected: listResults()=[] but meta-leaderboard has %d cohort(s). Retrying once.",
        metaLeaderboard?.cohorts?.length ?? 0,
      );
    }
    listResults(facetWhere)
      .then((rows) => {
        if (!cancelled) {
          setResults(rows);
          setEmptyResultsRetryFinished(true);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setEmptyResultsRetryFinished(true);
          setError(errMsg(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [facetWhere, hasInconsistentEmptySnapshot]);

  const resultById = useMemo(
    () => new Map((results ?? []).map((result) => [result.result_id, result])),
    [results],
  );

  const cohortPlatformIndex = useMemo(() => {
    const index = new Map<string, Map<string, MetaCohortPlatform[]>>();
    if (!metaLeaderboard) return index;
    for (const cohort of metaLeaderboard.cohorts) {
      const inner = new Map<string, MetaCohortPlatform[]>();
      for (const row of cohort.platforms ?? []) {
        const rows = inner.get(variantKeyOf(row)) ?? [];
        rows.push(row);
        inner.set(variantKeyOf(row), rows);
      }
      index.set(cohort.key, inner);
    }
    return index;
  }, [metaLeaderboard]);

  const filteredMetaLeaderboard = useMemo(() => {
    if (!metaLeaderboard) return null;

    const visibleCohorts = metaLeaderboard.cohorts.map((cohort) => ({
      ...cohort,
      platforms: (cohort.platforms ?? []).filter((row) => {
        const result = resultById.get(row.result_id);
        return result !== undefined && matchesFacetRow(result, facets);
      }),
    })).filter((cohort) => {
      if (
        benchmarkFilters.length > 0 &&
        !benchmarkFilters.map(canonicalBenchmarkSlug).includes(canonicalBenchmarkSlug(cohort.benchmark))
      ) return false;
      if (scaleFilters.length > 0 && !scaleFilters.includes(String(cohort.scale_factor))) return false;
      if (phaseFilter !== "all" && cohort.phase !== phaseFilter) return false;
      if (streamFilters.length > 0 && !streamFilters.includes(String(cohort.stream_count ?? ""))) return false;

      return (cohort.platforms ?? []).length > 0;
    });

    const visibleEvidencePlatformIds = new Set(
      visibleCohorts.flatMap((cohort) => (cohort.platforms ?? []).map((row) => variantKeyOf(row))),
    );
    const platforms = metaLeaderboard.platforms
      .map((platform) => {
        const ranks = Object.fromEntries(
          visibleCohorts.flatMap((cohort) => {
            const rows = cohortPlatformIndex.get(cohort.key)?.get(variantKeyOf(platform)) ?? [];
            const rankedRows = rows
              .filter((row) => {
              const result = resultById.get(row.result_id);
                return row.rank !== null && result !== undefined && matchesFacetRow(result, facets);
              })
              .sort((a, b) => (a.rank ?? Number.POSITIVE_INFINITY) - (b.rank ?? Number.POSITIVE_INFINITY));
            const row = rankedRows[0];
            if (!row || row.rank === null) return [];
            return [[cohort.key, {
              rank: row.rank,
              total: cohort.cohort_ranked_count,
              metric_value: row.metric_value,
              speedup_vs_best: row.speedup_vs_best,
            } satisfies MetaRank] as const];
          }),
        ) as Record<string, MetaRank>;

        const rankValues = Object.values(ranks).map((rank) => rank.rank);
        const avgRank = rankValues.length > 0
          ? Math.round((rankValues.reduce((sum, rank) => sum + rank, 0) / rankValues.length) * 10) / 10
          : null;

        return {
          ...platform,
          ranks,
          avg_rank: avgRank,
          n_cohorts: rankValues.length,
        } satisfies MetaPlatform;
      })
      .filter((platform) => platform.n_cohorts > 0 || visibleEvidencePlatformIds.has(variantKeyOf(platform)))
      .sort((a, b) => {
        if (a.avg_rank === null && b.avg_rank === null) return 0;
        if (a.avg_rank === null) return 1;
        if (b.avg_rank === null) return -1;
        return a.avg_rank - b.avg_rank;
      });

    return {
      ...metaLeaderboard,
      cohorts: visibleCohorts,
      platforms,
    };
  }, [
    benchmarkFilters,
    cohortPlatformIndex,
    facets,
    metaLeaderboard,
    phaseFilter,
    resultById,
    scaleFilters,
  ]);

  if (error) return <ErrorMessage title="Could not load results" message={error} onRetry={() => setResultsRetryToken((t) => t + 1)} />;
  if (hasPersistentInconsistentEmptySnapshot) {
    return (
      <ErrorMessage
        title="Could not load all results"
        message="The rankings loaded, but the run list did not. Reload the page to try again."
      />
    );
  }
  if (!results || !metaLeaderboardLoaded || hasInconsistentEmptySnapshot) {
    return (
      <LeaderboardLoadingSkeleton
        benchmark={summarizeSelection(benchmarkFilters, "All benchmarks", humanizeBenchmark)}
        scaleFactor={summarizeSelection(scaleFilters, "All scales", (value) => `SF ${value}`)}
        phase={phaseFilter === "all" ? "All phases" : phaseFilter}
        activeFacets={summarizeActiveFacets(facets, new Map())}
        showActiveSummary={
          benchmarkFilters.length > 0 ||
          scaleFilters.length > 0 ||
          phaseFilter !== "all" ||
          hasActiveFacets(facets, RENDERED_FACET_KEYS)
        }
      />
    );
  }

  const mode: MetaLeaderboardMode =
    modeRaw === "times" || modeRaw === "ranks" ? modeRaw : "speedup";
  const benchmarks = [...new Set(results.map((result) => canonicalBenchmarkSlug(result.benchmark)))].sort();
  const platformIdToName = new Map(
    (metaLeaderboard?.platforms ?? []).map((platform) => [variantKeyOf(platform), variantLabelOf(platform)]),
  );
  for (const result of results) {
    platformIdToName.set(variantKeyOf(result), variantLabelOf(result));
  }
  const platformIds = [...new Set(results.map((result) => variantKeyOf(result)))].sort();
  const activeFacetSummaries = summarizeActiveFacets(facets, platformIdToName);
  const benchmarkOptions = metaLeaderboard
    ? [...new Set(metaLeaderboard.cohorts.map((cohort) => cohort.benchmark))].sort()
    : [];
  const scaleOptions = metaLeaderboard
    ? [...new Set(metaLeaderboard.cohorts.map((cohort) => String(cohort.scale_factor)))].sort(
        (a, b) => Number(a) - Number(b),
      )
    : [];
  const phaseOptions = metaLeaderboard
    ? [...new Set(metaLeaderboard.cohorts.map((cohort) => cohort.phase))].sort()
    : [];
  const trustOptions = ["all", ...[...new Set(results.map((result) => result.trust_label))].sort()];
  const platformVersionOptions = (() => {
    const versions = new Set<string>();
    for (const r of platformVersionDomainResults ?? results) {
      if (r.platform_version) {
        versions.add(r.platform_version);
      }
    }
    return [...versions].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  })();
  const platformVersionFilters = facets.platform_version;
  const fullLeaderboardPlatformCount = metaLeaderboard?.platforms.length ?? 0;
  const rankedLeaderboardPlatformCount =
    metaLeaderboard?.platforms.filter((platform) => platform.n_cohorts > 0).length ?? 0;
  const visibleLeaderboardPlatformCount = filteredMetaLeaderboard?.platforms.length ?? 0;
  const leaderboardBenchmarkSet = new Set(benchmarkOptions);
  const publicBenchmarksOutsideLeaderboard = benchmarks.filter((benchmark) => !leaderboardBenchmarkSet.has(canonicalBenchmarkSlug(benchmark)));
  const leaderboardEvidencePlatformIds = new Set(
    (metaLeaderboard?.cohorts ?? []).flatMap((cohort) =>
      (cohort.platforms ?? []).map((platform) => variantKeyOf(platform)),
    ),
  );
  const publicPlatformIdsOutsideLeaderboard = platformIds.filter((platformId) => !leaderboardEvidencePlatformIds.has(platformId));
  const leaderboardEvidencePlatformCount = leaderboardEvidencePlatformIds.size || fullLeaderboardPlatformCount;
  const visibleRankedLeaderboardPlatformCount =
    filteredMetaLeaderboard?.platforms.filter((platform) => platform.n_cohorts > 0).length ?? 0;
  const tuningSummary = summarizeTuningModes(results);
  const tuningOptions = tuningSummaryBucketCount(tuningSummary) > 1 || tuningFilter !== "all"
    ? [
        "all",
        ...(tuningSummary.unlabelledCount > 0 ? [UNLABELLED_TUNING_VALUE] : []),
        ...tuningSummary.labelledOptions,
      ]
    : [];

  function buildCohortHref(cohort: MetaCohort): string {
    const params = new URLSearchParams();
    params.set("sf", String(cohort.scale_factor));
    params.set("phase", cohort.phase);
    if (cohort.stream_count !== null && cohort.stream_count !== undefined) {
      params.set("streams", String(cohort.stream_count));
    }
    appendFacetParams(params, facets, new Set(["benchmark", "scale_factor", "phase", "stream_count"]));
    const query = params.toString();
    return `/results/${cohort.benchmark}/${query ? `?${query}` : ""}`;
  }

  function buildPlatformHref(platformId: string): string {
    const params = new URLSearchParams();
    appendFacetParams(params, facets, new Set(["platform"]));
    const query = params.toString();
    return `/results/p/${platformId}/${query ? `?${query}` : ""}`;
  }

  const leaderboardScopeSummaryProps = {
    publicBenchmarkCount: benchmarks.length,
    leaderboardBenchmarkCount: benchmarkOptions.length,
    publicBenchmarksOutsideLeaderboard,
    publicPlatformCount: platformIds.length,
    leaderboardEvidencePlatformCount,
    fullLeaderboardPlatformCount,
    rankedLeaderboardPlatformCount,
    visibleLeaderboardPlatformCount,
    visibleRankedLeaderboardPlatformCount,
    publicPlatformIdsOutsideLeaderboard: publicPlatformIdsOutsideLeaderboard.length,
    tuningSummary,
  };

  return (
    <div>
      <section
        class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroSurface}
        data-testid="home-hero-filter-band"
        data-surface="hero"
      >
        <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroWrapper} data-testid="home-hero-wrapper">
          <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroIntro} data-testid="home-hero-intro">
            <PageHeader crumbs={[{ label: "Results", href: "/results/" }, { label: "Compare" }]} eyebrow="Compare" title="Compare benchmark results" subtitle="See how published platform runs compare across BenchBox rankings. Open any result to inspect its evidence." />
            {notice && (
              <div
                class="mt-4 rounded-md border border-[var(--bb-border-default)] bg-[var(--bb-bg-panel)] px-4 py-3 text-sm text-[var(--bb-fg-muted)]"
                role="status"
                data-testid="compare-url-notice"
              >
                {notice}
              </div>
            )}
          </div>

          {filteredMetaLeaderboard &&
            (benchmarkFilters.length > 0 ||
              scaleFilters.length > 0 ||
              phaseFilter !== "all" ||
              activeFacetSummaries.length > 0) && (
              <ActiveLeaderboardSummary
                benchmark={summarizeSelection(benchmarkFilters, "All benchmarks", humanizeBenchmark)}
                scaleFactor={summarizeSelection(scaleFilters, "All scales", (value) => `SF ${value}`)}
                phase={phaseFilter === "all" ? "All phases" : phaseFilter}
                activeFacets={activeFacetSummaries}
              />
            )}

          {filteredMetaLeaderboard && (
            <section
              aria-label="Leaderboard ranking selector"
              class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.rankingSelector}
            >
              <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.rankingGrid} data-testid="home-ranking-selector-grid">
                <MultiSelectFilter
                  label="Benchmark"
                  allLabel="All benchmarks"
                  options={benchmarkOptions}
                  current={benchmarkFilters}
                  onSelect={(value) =>
                    setFacet(
                      "benchmark",
                      value === "all" ? [] : toggleFacetValue(benchmarkFilters, value),
                    )
                  }
                  format={(value) => formatBenchmarkLabel(value)}
                  description={benchmarkFilterDescription(publicBenchmarksOutsideLeaderboard)}
                />
                <MultiSelectFilter
                  label="Scale"
                  allLabel="All scales"
                  options={scaleOptions}
                  current={scaleFilters}
                  onSelect={(value) =>
                    setFacet(
                      "scale_factor",
                      value === "all" ? [] : toggleFacetValue(scaleFilters, value),
                    )
                  }
                  format={(value) => `SF ${value}`}
                />
                <SelectFilter
                  label="Phase"
                  options={["all", ...phaseOptions]}
                  current={phaseFilter}
                  onSelect={(value) => setFacet("phase", value === "all" ? [] : [value])}
                  format={(value) => (value === "all" ? "All phases" : value)}
                />
                {platformVersionOptions.length > 0 || platformVersionFilters.length > 0 ? (
                  <MultiSelectFilter
                    label="Platform version"
                    allLabel="All versions"
                    options={platformVersionOptions}
                    current={platformVersionFilters}
                    onSelect={(value) =>
                      setFacet(
                        "platform_version",
                        value === "all" ? [] : toggleFacetValue(platformVersionFilters, value),
                      )
                    }
                    format={(value) => value}
                  />
                ) : (
                  <div aria-hidden="true" data-testid="home-engine-version-unavailable" />
                )}
                <CoverageSummary />
              </div>

              <details
                class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.scopeDetails}
                data-testid="leaderboard-scope-summary-mobile"
              >
                <summary class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.scopeSummary}>
                  What counts as a ranked result?
                </summary>
                <LeaderboardScopeSummary {...leaderboardScopeSummaryProps} />
              </details>

              <details
                class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.advancedDetails}
                data-testid="leaderboard-advanced-filters"
              >
                <summary class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.advancedSummary}>
                  Advanced filters
                </summary>
                <div class="mt-3 grid gap-3 md:grid-cols-3">
                  {tuningOptions.length > 0 ? (
                    <SingleFilterGroup
                      label="Tuning"
                      options={tuningOptions}
                      current={tuningFilter}
                      onSelect={(value) => setFacet("tuning_mode", value === "all" ? [] : [value])}
                      format={(value) => formatTuningModeOption(value)}
                      optionTitle={(value) => tuningModeOptionTitle(value, tuningSummary)}
                      description={tuningFilterDescription(tuningSummary)}
                    />
                  ) : (
                    <div data-testid="tuning-filter-unavailable">
                      <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--bb-fg-muted)]">Tuning</div>
                      <p class="text-[11px] leading-snug text-[var(--bb-fg-muted)]">
                        Tuning filter unavailable: this view has only one tuning metadata state. {tuningFilterDescription(tuningSummary)}
                      </p>
                    </div>
                  )}
                  <SingleFilterGroup
                    label="Trust"
                    options={trustOptions}
                    current={trustFilter}
                    onSelect={(value) => setFacet("trust_tier", value === "all" ? [] : [value])}
                    format={(value) => (value === "all" ? "All trust tiers" : value)}
                  />
                  <SingleFilterGroup
                    label="Date window"
                    options={["all", "30d", "90d", "365d"]}
                    current={dateWindow}
                    onSelect={(value) => setFacet("date_window", toDateWindowFacet(value))}
                    format={(value) => (value === "all" ? "All time" : `Last ${value}`)}
                  />
                </div>
              </details>
            </section>
          )}
        </div>
      </section>

      <div
        class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.dataSurface}
        data-testid="home-data-surface"
        data-surface="app"
      >
        {filteredMetaLeaderboard && (
          <MetaLeaderboard
            data={filteredMetaLeaderboard}
            mode={mode}
            onModeChange={(value) => setModeRaw(value)}
            cohortHref={buildCohortHref}
            platformHref={buildPlatformHref}
            resultMetadataById={resultById}
          />
        )}

        {filteredMetaLeaderboard && filteredMetaLeaderboard.cohorts.length === 0 && (
          <CoverageEmptyState
            activeFacets={activeFacetSummaries}
            canClearScale={scaleFilters.length > 0}
            canClearPlatform={facets.platform.length > 0}
            onClearScale={() => setFacet("scale_factor", [])}
            onClearPlatform={() => setFacet("platform", [])}
            onReset={resetFacets}
          />
        )}

        <ProvenanceLegend />
      </div>
</div>
  );
}

function LeaderboardLoadingSkeleton({
  benchmark,
  scaleFactor,
  phase,
  activeFacets,
  showActiveSummary,
}: {
  benchmark: string;
  scaleFactor: string;
  phase: string;
  activeFacets: ActiveFacetSummary[];
  showActiveSummary: boolean;
}) {
  return (
    <div>
      <section
        class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroSurface}
        data-testid="home-hero-filter-band"
        data-surface="hero"
      >
        <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroWrapper} data-testid="home-hero-wrapper">
          <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.heroIntro} data-testid="home-hero-intro">
            <PageHeader crumbs={[{ label: "Results", href: "/results/" }, { label: "Compare" }]} eyebrow="Compare" title="Compare benchmark results" subtitle="See how published platform runs compare across BenchBox rankings. Open any result to inspect its evidence." />
          </div>

          {showActiveSummary && (
            <div
              aria-hidden="true"
              class={`${LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummary} sm:hidden`}
              data-testid="home-loading-active-summary-reserve"
            >
              <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryItems}>
                {[
                  ["Benchmark", benchmark],
                  ["Scale", scaleFactor],
                  ["Phase", phase],
                ].map(([label, value]) => (
                  <div key={label} class={`${LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryChip} bb-skeleton-dark`}>
                    <span class={`${LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryLabel} invisible`}>{label}</span>
                    <span class={`${LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryValue} invisible`}>{value}</span>
                  </div>
                ))}
              </div>
              {activeFacets.length > 0 && (
                <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeFacetList}>
                  {activeFacets.map((facet) => (
                    <span
                      key={facet.key}
                      class={`${LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeFacetChip} bb-skeleton-dark`}
                    >
                      <span class="invisible">{facet.label}: {facet.value}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          <section
            aria-label="Leaderboard ranking selector"
            class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.rankingSelector}
          >
            <div class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.rankingGrid} data-testid="home-ranking-selector-grid">
              <SkeletonSelect label="Benchmark" />
              <SkeletonSelect label="Scale" />
              <SkeletonSelect label="Phase" />
              <SkeletonSelect label="Platform version" />
              <CoverageSummary />
            </div>
            <div aria-hidden="true" class="sm:hidden">
              <div
                class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.scopeDetails}
                data-testid="home-loading-scope-details-reserve"
              >
                <SkeletonBlock className="h-4 w-40 bb-skeleton-dark" />
              </div>
              <div
                class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.advancedDetails}
                data-testid="home-loading-advanced-details-reserve"
              >
                <SkeletonBlock className="h-5 w-32 bb-skeleton-dark" />
              </div>
            </div>
          </section>
        </div>
      </section>

      <div
        class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.dataSurface}
        data-testid="home-data-surface"
        data-surface="app"
      >
        <MetaLeaderboardSkeleton />
      </div>
    </div>
  );
}

function SkeletonSelect({ label }: { label: string }) {
  return (
    <div>
      <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--bb-fg-muted)]">{label}</div>
      <SkeletonBlock className="h-9 w-full bb-skeleton-dark" />
    </div>
  );
}

interface ActiveFacetSummary {
  key: ExplorerFacetKey;
  label: string;
  value: string;
}

const RENDERED_FACET_KEYS = ALL_FACET_KEYS;

const FACET_LABELS: Record<ExplorerFacetKey, string> = {
  benchmark: "Benchmark",
  scale_factor: "Scale factor",
  phase: "Phase",
  stream_count: "Streams",
  platform: "Platform",
  execution_mode: "Execution",
  tuning_mode: "Tuning",
  trust_tier: "Trust",
  validation_status: "Validation",
  deployment_class: "Deployment",
  cloud_provider: "Cloud provider",
  cloud_region: "Cloud region",
  instance_or_warehouse: "Instance / warehouse",
  storage_format: "Storage format",
  cost_status: "Cost status",
  date_window: "Date window",
  platform_version: "Platform version",
  arch: "Architecture",
  cpu_family: "CPU family",
  memory_gb: "Memory",
};

function ActiveLeaderboardSummary({
  benchmark,
  scaleFactor,
  phase,
  activeFacets,
}: {
  benchmark: string;
  scaleFactor: string;
  phase: string;
  activeFacets: ActiveFacetSummary[];
}) {
  const items = [
    { label: "Benchmark", value: benchmark },
    { label: "Scale", value: scaleFactor },
    { label: "Phase", value: phase },
  ];

  return (
    <section
      aria-label="Active leaderboard filters"
      class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummary}
    >
      <dl class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryItems}>
        {items.map((item) => (
          <div
            key={item.label}
            class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryChip}
            title={`${item.label}: ${item.value}`}
          >
            <dt class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryLabel}>{item.label}</dt>
            <dd class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeSummaryValue}>{item.value}</dd>
          </div>
        ))}
      </dl>
      {activeFacets.length > 0 && (
        <div aria-label="Active filter chips" class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeFacetList}>
          {activeFacets.map((facet) => (
            <span
              key={facet.key}
              class={LEADERBOARD_SHELL_GEOMETRY_CLASSES.activeFacetChip}
            >
              {facet.label}: {facet.value}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function CoverageEmptyState({
  activeFacets,
  canClearScale,
  canClearPlatform,
  onClearScale,
  onClearPlatform,
  onReset,
}: {
  activeFacets: ActiveFacetSummary[];
  canClearScale: boolean;
  canClearPlatform: boolean;
  onClearScale: () => void;
  onClearPlatform: () => void;
  onReset: () => void;
}) {
  return (
    <section
      aria-labelledby="coverage-empty-title"
      class="mb-12 rounded-lg border border-dashed border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] p-8 text-[var(--bb-data-fg-muted)]"
    >
      <div class="mx-auto max-w-3xl text-center">
        <h2 id="coverage-empty-title" class="text-lg font-semibold text-[var(--bb-data-fg-primary)]">
          No leaderboard cells match the current filters
        </h2>
        <p class="mt-2 text-sm text-[var(--bb-data-fg-muted)]">
          The current facet combination removed every published ranking cell.
        </p>
      </div>

      {activeFacets.length > 0 ? (
        <dl
          aria-label="Active filters removing leaderboard cells"
          class="mx-auto mt-5 grid max-w-3xl gap-2 sm:grid-cols-2 lg:grid-cols-3"
        >
          {activeFacets.map((facet) => (
            <div key={facet.key} class="rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-3 py-2 text-left">
              <dt class="text-[11px] font-semibold uppercase tracking-wide text-[var(--bb-data-fg-subtle)]">{facet.label}</dt>
              <dd class="mt-1 text-sm font-medium text-[var(--bb-data-fg-primary)]">{facet.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p class="mt-5 text-center text-sm text-[var(--bb-data-fg-muted)]">
          No active facets are applied; the public corpus has no leaderboard rankings for this view.
        </p>
      )}

      <div class="mt-5 flex flex-wrap justify-center gap-2">
        <button
          type="button"
          class="rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-2 text-sm font-medium text-[var(--bb-data-fg-primary)] hover:bg-[var(--bb-surface-data-muted)] disabled:cursor-not-allowed disabled:opacity-50"
          onClick={onClearScale}
          disabled={!canClearScale}
        >
          Clear scale factor
        </button>
        <button
          type="button"
          class="rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-2 text-sm font-medium text-[var(--bb-data-fg-primary)] hover:bg-[var(--bb-surface-data-muted)] disabled:cursor-not-allowed disabled:opacity-50"
          onClick={onClearPlatform}
          disabled={!canClearPlatform}
        >
          Clear platform
        </button>
        <button
          type="button"
          class="btn btn-primary"
          onClick={onReset}
        >
          Reset all
        </button>
      </div>
    </section>
  );
}

function LeaderboardScopeSummary({
  publicBenchmarkCount,
  leaderboardBenchmarkCount,
  publicBenchmarksOutsideLeaderboard,
  publicPlatformCount,
  leaderboardEvidencePlatformCount,
  fullLeaderboardPlatformCount,
  rankedLeaderboardPlatformCount,
  visibleLeaderboardPlatformCount,
  visibleRankedLeaderboardPlatformCount,
  publicPlatformIdsOutsideLeaderboard,
  tuningSummary,
}: {
  publicBenchmarkCount: number;
  leaderboardBenchmarkCount: number;
  publicBenchmarksOutsideLeaderboard: string[];
  publicPlatformCount: number;
  leaderboardEvidencePlatformCount: number;
  fullLeaderboardPlatformCount: number;
  rankedLeaderboardPlatformCount: number;
  visibleLeaderboardPlatformCount: number;
  visibleRankedLeaderboardPlatformCount: number;
  publicPlatformIdsOutsideLeaderboard: number;
  tuningSummary: TuningModeSummary;
}) {
  const hiddenBenchmarkCopy =
    publicBenchmarksOutsideLeaderboard.length > 0
      ? `${formatCountWithVerb(publicBenchmarksOutsideLeaderboard.length, "published benchmark", "has", "have")} runs but no ranking: ${publicBenchmarksOutsideLeaderboard.map(formatBenchmarkLabel).join(", ")}.`
      : "Every benchmark with published runs has a ranking.";
  const publicOnlyPlatformCopy =
    publicPlatformIdsOutsideLeaderboard > 0
      ? `${formatCountWithVerb(publicPlatformIdsOutsideLeaderboard, "published platform", "is", "are")} not represented in the rankings.`
      : "Every published platform is represented in a ranking.";
  const visiblePublishedUnrankedPlatformCount = Math.max(
    0,
    visibleLeaderboardPlatformCount - visibleRankedLeaderboardPlatformCount,
  );
  const fullPublishedUnrankedPlatformCount = Math.max(
    0,
    fullLeaderboardPlatformCount - rankedLeaderboardPlatformCount,
  );

  return (
    <div class="mt-3 space-y-1 border-t border-[var(--bb-border-default)] pt-3 text-xs text-[var(--bb-fg-muted)]">
      <p>
        Ranked results cover {leaderboardBenchmarkCount.toLocaleString()} of{" "}
        {publicBenchmarkCount.toLocaleString()} published {plural(publicBenchmarkCount, "benchmark", "benchmarks")}.{" "}
        {hiddenBenchmarkCopy}
      </p>
      <p>
        The current table includes {visibleLeaderboardPlatformCount.toLocaleString()} of {leaderboardEvidencePlatformCount.toLocaleString()} platforms found in the selected rankings: {visibleRankedLeaderboardPlatformCount.toLocaleString()} ranked and {visiblePublishedUnrankedPlatformCount.toLocaleString()} unranked. Across all rankings, {rankedLeaderboardPlatformCount.toLocaleString()} of {fullLeaderboardPlatformCount.toLocaleString()} platforms are ranked and {fullPublishedUnrankedPlatformCount.toLocaleString()} {fullPublishedUnrankedPlatformCount === 1 ? "is" : "are"} unranked. There are {publicPlatformCount.toLocaleString()} published {plural(publicPlatformCount, "platform", "platforms")} in total. {publicOnlyPlatformCopy}
      </p>
      <p>
        Tuning was recorded for {tuningSummary.labelledCount.toLocaleString()} {plural(tuningSummary.labelledCount, "run", "runs")} across {tuningSummary.labelledOptions.length.toLocaleString()} {plural(tuningSummary.labelledOptions.length, "setting", "settings")}. It was not recorded for {tuningSummary.unlabelledCount.toLocaleString()} {plural(tuningSummary.unlabelledCount, "run", "runs")}.
      </p>
    </div>
  );
}

function summarizeSelection(
  values: string[],
  allLabel: string,
  format: (value: string) => string,
): string {
  if (values.length === 0) return allLabel;
  if (values.length === 1) return format(values[0] ?? "");
  return `${values.length} selected`;
}

function summarizeActiveFacets(
  facets: FacetState,
  platformIdToName: ReadonlyMap<string, string>,
): ActiveFacetSummary[] {
  const summaries: ActiveFacetSummary[] = [];
  for (const key of RENDERED_FACET_KEYS) {
    const value = facets[key];
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      summaries.push({
        key,
        label: FACET_LABELS[key],
        value: value.map((item) => formatFacetValue(key, item, platformIdToName)).join(", "),
      });
    } else if (value !== "all") {
      summaries.push({
        key,
        label: FACET_LABELS[key],
        value: formatFacetValue(key, value, platformIdToName),
      });
    }
  }
  return summaries;
}

function formatFacetValue(
  key: ExplorerFacetKey,
  value: string,
  platformIdToName: ReadonlyMap<string, string>,
): string {
  if (key === "benchmark") return humanizeBenchmark(value);
  if (key === "platform") return platformIdToName.get(value) ?? value;
  if (key === "tuning_mode" && value === UNLABELLED_TUNING_VALUE) return "not recorded";
  return formatFacetDisplayValue(key, value);
}

function summarizeTuningModes(results: readonly ResultRow[]): TuningModeSummary {
  const labelled = new Map<string, number>();
  let unlabelledCount = 0;
  for (const result of results) {
    const value = result.tuning_mode;
    if (value !== null && value !== undefined && value.trim() !== "") {
      labelled.set(value, (labelled.get(value) ?? 0) + 1);
    } else {
      unlabelledCount += 1;
    }
  }
  const labelledOptions = [...labelled.keys()].sort();
  const labelledCount = [...labelled.values()].reduce((sum, count) => sum + count, 0);
  return {
    labelledOptions,
    unlabelledCount,
    labelledCount,
    totalCount: results.length,
  };
}

function benchmarkFilterDescription(publicBenchmarksOutsideLeaderboard: readonly string[]): string {
  if (publicBenchmarksOutsideLeaderboard.length === 0) {
    return "Only benchmarks with ranked leaderboards appear here.";
  }
  return "Choose a benchmark with ranked results. Open “What counts as a ranked result?” to see other published benchmarks.";
}

function formatTuningModeOption(value: string): string {
  if (value === "all") return "All tuning labels";
  if (value === UNLABELLED_TUNING_VALUE) return "Not Recorded";
  return formatFacetDisplayValue("tuning_mode", value);
}

function tuningModeOptionTitle(value: string, summary: TuningModeSummary): string {
  if (value === "all") return "Include runs with and without recorded tuning details.";
  if (value === UNLABELLED_TUNING_VALUE) {
    return `${formatCountWithVerb(summary.unlabelledCount, "public result", "has", "have")} no recorded tuning mode.`;
  }
  return `Filter to tuning label: ${formatFacetDisplayValue("tuning_mode", value)}.`;
}

function tuningFilterDescription(summary: TuningModeSummary): string {
  if (summary.unlabelledCount === 0) return "All public results in this view have a recorded tuning mode.";
  if (summary.labelledOptions.length === 0) {
    return `${formatCountWithVerb(summary.unlabelledCount, "public result", "has", "have")} no recorded tuning mode.`;
  }
  return `${summary.unlabelledCount.toLocaleString()} of ${summary.totalCount.toLocaleString()} public ${plural(summary.totalCount, "result", "results")} ${plural(summary.unlabelledCount, "is", "are")} not recorded for tuning.`;
}

function tuningSummaryBucketCount(summary: TuningModeSummary): number {
  return summary.labelledOptions.length + (summary.unlabelledCount > 0 ? 1 : 0);
}

function plural(count: number, singular: string, pluralValue: string): string {
  return count === 1 ? singular : pluralValue;
}

function MultiSelectFilter({
  label,
  allLabel,
  options,
  current,
  onSelect,
  format,
  description,
}: {
  label: string;
  allLabel: string;
  options: string[];
  current: string[];
  onSelect: (value: string) => void;
  format: (value: string) => string;
  description?: string;
}) {
  if (options.length === 0) return null;
  const value = current.length === 0 ? "all" : current.length === 1 ? current[0] : "__multiple";

  return (
    <label class="block">
      <span class="mb-2 block text-xs font-semibold uppercase tracking-wide text-[var(--bb-fg-muted)]">{label}</span>
      <select
        aria-label={label}
        class="h-9 w-full rounded-md border border-[var(--bb-border-default)] bg-[var(--bb-bg-primary)] px-3 text-sm font-medium text-[var(--bb-fg-primary)]"
        value={value}
        onChange={(event) => onSelect((event.currentTarget as HTMLSelectElement).value)}
      >
        <option value="all">{allLabel}</option>
        {current.length > 1 && (
          <option value="__multiple" disabled>
            Multiple selected
          </option>
        )}
        {options.map((option) => (
          <option key={option} value={option}>
            {format(option)}
          </option>
        ))}
      </select>
      {description && <p class="mt-1 hidden text-[11px] leading-snug text-[var(--bb-fg-muted)] sm:block">{description}</p>}
    </label>
  );
}

function SelectFilter({
  label,
  options,
  current,
  onSelect,
  format,
}: {
  label: string;
  options: string[];
  current: string;
  onSelect: (value: string) => void;
  format: (value: string) => string;
}) {
  if (options.length === 0) return null;
  return (
    <label class="block">
      <span class="mb-2 block text-xs font-semibold uppercase tracking-wide text-[var(--bb-fg-muted)]">{label}</span>
      <select
        aria-label={label}
        class="h-9 w-full rounded-md border border-[var(--bb-border-default)] bg-[var(--bb-bg-primary)] px-3 text-sm font-medium text-[var(--bb-fg-primary)]"
        value={current}
        onChange={(event) => onSelect((event.currentTarget as HTMLSelectElement).value)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {format(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function CoverageSummary() {
  let pickedCount = 0;
  let compareHref: string | null = null;
  let pickedIds: readonly string[] = [];
  try {
    const picking = usePickingState();
    pickedCount = picking.pickedIds.length;
    pickedIds = picking.pickedIds;
    compareHref = picking.compareHref;
  } catch {
  }
  const pickingLabel =
    pickedCount === 0 ? "Compare →" : pickedCount === 1 ? "Compare 1 selected →" : `Compare ${pickedCount} selected →`;
  if (!compareHref && pickedIds.length === 1) {
    compareHref = `/results/compare?ids=${encodeURIComponent(pickedIds[0]!)}`;
  }
  return (
    <div>
      <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--bb-fg-muted)]">Ranking scope</div>
      <div class="flex flex-wrap gap-2">
        <span class="rounded-full bg-[var(--bb-bg-elevated)] px-3 py-1.5 text-xs font-medium text-[var(--bb-fg-muted)]">
          Ranked results only
        </span>
        <a
          href={compareHref ?? "/results/compare/"}
          data-testid="home-compare-entrypoint"
          class="rounded-full border border-[var(--bb-border-default)] bg-[var(--bb-bg-primary)] px-3 py-1.5 text-xs font-medium text-[var(--bb-fg-primary)] no-underline shadow-sm hover:bg-[var(--bb-bg-elevated)]"
        >
          {pickingLabel}
        </a>
      </div>
    </div>
  );
}

function SingleFilterGroup({
  label,
  options,
  current,
  onSelect,
  format,
  optionTitle,
  description,
}: {
  label: string;
  options: string[];
  current: string;
  onSelect: (value: string) => void;
  format: (value: string) => string;
  optionTitle?: (value: string) => string;
  description?: string;
}) {
  if (options.length === 0) return null;
  return (
    <div>
      <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--bb-fg-muted)]">{label}</div>
      <div class="flex flex-wrap gap-2">
        {options.map((value) => {
          const active = current === value;
          return (
            <button
              key={value}
              class={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                active
                  ? "bg-[var(--bb-tone-info-bg)] text-[var(--bb-tone-info-fg)]"
                  : "bg-[var(--bb-bg-elevated)] text-[var(--bb-fg-muted)] hover:bg-[var(--bb-border-default)]"
              }`}
              onClick={() => onSelect(value)}
              aria-pressed={active}
              title={optionTitle?.(value)}
            >
              {format(value)}
            </button>
          );
        })}
      </div>
      {description && <p class="mt-1 text-[11px] leading-snug text-[var(--bb-fg-muted)]">{description}</p>}
    </div>
  );
}
