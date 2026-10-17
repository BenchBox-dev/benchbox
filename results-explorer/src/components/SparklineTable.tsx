import type { BenchmarkSummary, PlatformRow } from "@/types";
import { buildLatencyBarScale, latencyScaleFraction } from "@/lib/chartMath";
import { paletteColor } from "@/lib/chartTheme";
import { orderRunsByDisplayGeomean } from "@/lib/chartRunRows";
import { costModelDisclosure, estimateProvenance, hasCostEstimate, normalizedCostLabel, normalizedCostShortLabel, normalizedCostValue } from "@/lib/costDisplay";
import {
  isRankable,
  isTimingDisplayable,
  isValidTimingValue,
  normalizePrimaryMetric,
  primaryMetricValue,
} from "@/lib/displayEligibility";
import { formatCompactLatencyMs, formatPowerScore } from "@/lib/metricFormatters";
import { formatRunIdentitiesForCohort } from "@/lib/runIdentity";
import { isSubscaleTpc, powerScoreTitle } from "@/lib/subscale";

interface Props {
  summary: BenchmarkSummary;
  colorIndexFor?: (platform: PlatformRow) => number;
}

function fmtScore(s: number | null): string {
  return formatPowerScore(s).valueText;
}

interface SparkProps {
  fraction: number | null;
  color: string;
}

function SparkBar({ fraction, color }: SparkProps) {
  if (fraction === null) return <span class="text-[var(--bb-data-fg-subtle)]">-</span>;
  const clamped = Math.max(0, Math.min(1, fraction));
  return (
    <span class="block w-full min-w-[3rem]">
      <span
        class="block h-2 rounded-sm"
        style={{ width: `${(clamped * 100).toFixed(1)}%`, backgroundColor: color, opacity: 0.75 }}
      />
    </span>
  );
}

export function SparklineTable({ summary, colorIndexFor }: Props) {
  const { platforms } = summary;
  if (platforms.length === 0) return null;

  const metric = normalizePrimaryMetric(summary.ranking?.primary_metric);
  const scoreMetric = metric === "throughput_at_size" ? "throughput_at_size" : "power_score";
  const scoreLabel =
    scoreMetric === "throughput_at_size"
      ? "Throughput@Size"
      : powerScoreTitle(isSubscaleTpc(summary.benchmark, summary.scale_factor));
  const showPower =
    metric !== "display_geomean_ms" && platforms.some((p) => isRankable(p) && primaryMetricValue(p, scoreMetric) !== null);
  const showP99 = platforms.some((p) => isTimingDisplayable(p) && p.percentile_stats !== null);
  const showCost = platforms.some((p) => normalizedCostValue(p) !== null || hasCostEstimate(p));

  const geomeanScale = buildLatencyBarScale(
    platforms.map((p) => (isTimingDisplayable(p) ? p.display_geomean_ms : null)),
  );
  const maxPower = Math.max(
    ...platforms.map((p) => {
      const value = primaryMetricValue(p, scoreMetric);
      return isRankable(p) && isValidTimingValue(value) ? value : 0;
    }),
    1,
  );
  const orderedRuns = orderRunsByDisplayGeomean(platforms);
  const cohortLabels = formatRunIdentitiesForCohort(
    platforms.map((platform) => ({ ...platform, scale_factor: summary.scale_factor })),
    "table",
  );

  return (
    <div class="w-full overflow-x-auto">
      <table
        class="w-full min-w-[30rem] text-xs border-collapse"
        role="grid"
        aria-label="Compact performance metrics overview"
      >
        <thead>
          <tr class="border-b border-[var(--bb-data-border)]">
            <th class="text-left px-2 py-1.5 text-[var(--bb-data-fg-muted)] font-normal w-px whitespace-normal sm:whitespace-nowrap">Platform</th>
            <th class="text-right px-2 py-1.5 text-[var(--bb-data-fg-muted)] font-normal whitespace-nowrap" colSpan={2}>
              Geomean
            </th>
            {showPower && (
              <th class="text-right px-2 py-1.5 text-[var(--bb-data-fg-muted)] font-normal whitespace-nowrap" colSpan={2}>
                {scoreLabel}
              </th>
            )}
            {showP99 && (
              <th class="text-right px-2 py-1.5 text-[var(--bb-data-fg-muted)] font-normal">P99</th>
            )}
            {showCost && (
              <th class="text-right px-2 py-1.5 text-[var(--bb-data-fg-muted)] font-normal" title={costModelDisclosure(platforms)}>
                Normalized cost
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {orderedRuns.map(({ run: p, sourceIndex: i }) => {
            const color = paletteColor(colorIndexFor?.(p) ?? p.color_slot ?? i);
            const geomeanValue =
              isTimingDisplayable(p) && isValidTimingValue(p.display_geomean_ms) ? p.display_geomean_ms : null;
            const scoreValue = primaryMetricValue(p, scoreMetric);
            const powerValue = isRankable(p) && isValidTimingValue(scoreValue) ? scoreValue : null;
            const p99Value = isTimingDisplayable(p) ? p.percentile_stats?.p99 ?? null : null;
            return (
              <tr key={p.result_id} class="border-b border-[var(--bb-data-border)] hover:bg-[var(--bb-surface-data-muted)]">
                <td class="w-px whitespace-normal sm:whitespace-nowrap px-2 py-1.5 font-medium text-[var(--bb-data-fg-primary)]">
                  <span
                    class="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
                    style={{ backgroundColor: color }}
                  />
                  {cohortLabels[i] ?? p.platform}
                </td>
                <td class="w-2/5 px-1 py-1.5">
                  <SparkBar
                    fraction={geomeanScale && geomeanValue !== null ? latencyScaleFraction(geomeanValue, geomeanScale) : null}
                    color={color}
                  />
                </td>
                <td class="w-px whitespace-nowrap px-2 py-1.5 text-right font-mono text-[var(--bb-data-fg-primary)]">
                  {formatCompactLatencyMs(geomeanValue)}
                </td>
                {showPower && (
                  <>
                    <td class="w-2/5 px-1 py-1.5">
                      <SparkBar
                        fraction={powerValue === null ? null : powerValue / maxPower}
                        color={color}
                      />
                    </td>
                    <td class="w-px whitespace-nowrap px-2 py-1.5 text-right font-mono text-[var(--bb-data-fg-primary)]">
                      {fmtScore(powerValue)}
                    </td>
                  </>
                )}
                {showP99 && (
                  <td class="px-2 py-1.5 text-right font-mono text-[var(--bb-data-fg-muted)]">
                    {formatCompactLatencyMs(p99Value)}
                  </td>
                )}
                {showCost && (
                  <td
                    class="whitespace-nowrap px-2 py-1.5 text-right font-mono text-[var(--bb-data-fg-muted)]"
                    title={estimateProvenance(p) ?? normalizedCostLabel(p)}
                  >
                    {normalizedCostShortLabel(p)}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
