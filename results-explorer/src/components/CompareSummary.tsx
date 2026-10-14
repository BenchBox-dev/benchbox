import type { ComponentChildren } from "preact";
import type { CompareDecisionSummary } from "@/lib/compareSummary";
import { formatRatio } from "@/lib/compareSummary";
import { formatUsd } from "@/lib/metricFormatters";
import { fmtGeomean, fmtMs, fmtScore } from "@/utils";
import { StatusBadge } from "@/components/StatusBadge";

interface CompareSummaryProps {
  summary: CompareDecisionSummary;
}

export function CompareSummary({ summary }: CompareSummaryProps) {
  const winnerPercentiles =
    summary.winner !== null
      ? summary.percentiles.find((entry) => entry.resultId === summary.winner?.resultId)
      : undefined;
  const hasValidationCaveat = summary.nonCleanValidation.length > 0;

  return (
    <section aria-labelledby="compare-decision-summary-title" class="card mb-8">
      <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="compare-decision-summary-title" class="text-base font-semibold text-[var(--bb-data-fg-primary)]">
            Comparison summary
          </h2>
          <p class="mt-1 text-sm font-medium text-[var(--bb-data-fg-primary)]">{summary.headline}</p>
        </div>
        <div class="flex flex-wrap items-center justify-end gap-2">
          {hasValidationCaveat && (
            <StatusBadge role="validation" tone="warning">
              Unvalidated result{summary.nonCleanValidation.length > 1 ? "s" : ""}
            </StatusBadge>
          )}
          <StatusBadge role="computed" tone={summary.claimSuppressed ? "warning" : "info"}>
            {summary.claimSuppressed ? "No winner named" : "Based on the selected runs"}
          </StatusBadge>
        </div>
      </div>

      {hasValidationCaveat && (
        <div
          class="mb-4 rounded-md border border-[var(--bb-tone-warning-border)] bg-[var(--bb-tone-warning-bg)] px-3 py-2 text-xs text-[var(--bb-tone-warning-fg)]"
          data-testid="compare-validation-caveat"
        >
          <span class="font-semibold">Validation caution:</span>{" "}
          {summary.nonCleanValidation.map((entry, index) => (
            <span key={entry.resultId}>
              {index > 0 ? "; " : ""}
              <span class="font-medium">{entry.platform}</span> is {entry.label}
            </span>
          ))}
          . This comparison rests on at least one unvalidated or non-clean result - treat the leading-run claim above as
          provisional.
        </div>
      )}

      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <SummaryCard label={`Leading run on ${summary.primaryMetricLabel}`}>
          {summary.claimSuppressed ? (
            <p class="text-sm text-[var(--bb-data-fg-muted)]">Not claimed</p>
          ) : summary.isTie ? (
            <>
              <p class="text-sm font-semibold text-[var(--bb-data-fg-primary)]">Tie</p>
              <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">No material difference on the primary metric.</p>
            </>
          ) : summary.winner ? (
            <>
              <p class="text-sm font-semibold text-[var(--bb-data-fg-primary)]">{summary.winnerLabel ?? summary.winner.platform}</p>
              <p class="mt-1 font-mono text-xs text-[var(--bb-data-fg-muted)]">
                {formatPrimaryValue(summary.winner.value, summary.primaryMetric)}
              </p>
            </>
          ) : (
            <p class="text-sm text-[var(--bb-data-fg-muted)]">Primary metric unavailable</p>
          )}
        </SummaryCard>

        {summary.rankingBlocked && summary.descriptiveGeomeanSpread !== null ? (
          <SummaryCard label="Geomean query time spread (not ranked)">
            <p class="font-mono text-sm font-semibold text-[var(--bb-data-fg-primary)]">
              {formatRatio(summary.descriptiveGeomeanSpread)}
            </p>
            <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">slowest comparable run vs fastest, descriptive only</p>
          </SummaryCard>
        ) : (
        <SummaryCard label={summary.primaryMetricLabel}>
          {summary.comparisonRatio !== null ? (
            <>
              <p class="font-mono text-sm font-semibold text-[var(--bb-data-fg-primary)]">
                {formatRatio(summary.comparisonRatio)}
              </p>
              <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">{summary.comparisonLabel}</p>
            </>
          ) : (
            <p class="text-sm text-[var(--bb-data-fg-muted)]">No comparable primary values</p>
          )}
        </SummaryCard>
        )}

        <SummaryCard label={summary.winnerLabel ? `Where ${summary.winnerLabel} wins` : "Query comparison"}>
          {summary.claimSuppressed ? (
            <>
              <p class="text-sm text-[var(--bb-data-fg-muted)]">No winner named</p>
              <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">Review the individual query measurements below.</p>
            </>
          ) : (
            <>
              <p class="font-mono text-sm font-semibold text-[var(--bb-data-fg-primary)]">
                {summary.queryRecord.wins} of {summary.queryRecord.comparableQueries} shared queries
              </p>
              <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">
                Fastest outright on {summary.queryRecord.wins} · {summary.queryRecord.losses} slower · {summary.queryRecord.ties} tied
                {summary.queryRecord.missing > 0 ? (
                  <>
                    {" · "}
                    <span data-testid="compare-query-wins-missing">
                      {summary.queryRecord.missing} excluded for missing data
                    </span>
                  </>
                ) : null}
              </p>
            </>
          )}
        </SummaryCard>

        <SummaryCard
          label={summary.winnerLabel ? `Latency profile · ${summary.winnerLabel}` : "Latency profile"}
        >
          {winnerPercentiles?.p50 !== null &&
          winnerPercentiles?.p50 !== undefined &&
          winnerPercentiles.p90 !== null &&
          winnerPercentiles.p99 !== null ? (
            <dl class="grid grid-cols-3 gap-x-2 text-center">
              {(
                [
                  ["p50", winnerPercentiles.p50],
                  ["p90", winnerPercentiles.p90],
                  ["p99", winnerPercentiles.p99],
                ] as const
              ).map(([name, value]) => (
                <div key={name}>
                  <dt class="text-xs text-[var(--bb-data-fg-muted)]">{name}</dt>
                  <dd class="font-mono text-sm font-semibold text-[var(--bb-data-fg-primary)]">{fmtMs(value)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p class="text-sm text-[var(--bb-data-fg-muted)]">Percentiles unavailable</p>
          )}
        </SummaryCard>
      </div>

      {summary.cost && (
        <div class="mt-3 rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-3 py-2 text-xs text-[var(--bb-data-fg-muted)]">
          <span class="font-semibold text-[var(--bb-data-fg-primary)]">Cost/performance:</span>{" "}
          {formatCostSummary(summary.cost)}
        </div>
      )}
    </section>
  );
}

function SummaryCard({
  label,
  children,
}: {
  label: string;
  children: ComponentChildren;
}) {
  return (
    <div class="rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-3 py-2">
      <h3 class="mb-1 text-xs font-semibold uppercase text-[var(--bb-data-fg-subtle)]">{label}</h3>
      {children}
    </div>
  );
}

function formatPrimaryValue(value: number | null, primaryMetric: CompareDecisionSummary["primaryMetric"]) {
  if (primaryMetric === "display_geomean_ms") return fmtGeomean(value);
  return fmtScore(value);
}

function formatCostSummary(cost: NonNullable<CompareDecisionSummary["cost"]>) {
  if (cost.winnerCostUsd === null || cost.winnerCostPerformanceRatioVsWorst === null) {
    const runPhrase = cost.normalizedResultCount === 1 ? "selected run has" : "selected runs have";
    return `${cost.normalizedResultCount} ${runPhrase} normalized cost; winner cost is unavailable.`;
  }
  const bestText = cost.winnerIsBestCostPerformance ? "best" : "not best";
  return `winner cost ${formatUsd(cost.winnerCostUsd).valueText}; ${formatRatio(
    cost.winnerCostPerformanceRatioVsWorst,
  )} cost/performance vs worst normalized row (${bestText}).`;
}
