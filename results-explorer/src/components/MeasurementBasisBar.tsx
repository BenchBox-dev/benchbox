import { Select } from "@/components/Select";
import { StatusBadge } from "@/components/StatusBadge";
import {
  ALL_WARM,
  WARMUP,
  basisUnavailableLabel,
  encodeBasis,
  formatBasisLabel,
  isDefaultBasis,
  warmPass,
  type BasisStatistic,
  type MeasurementBasis,
  type PassSelection,
} from "@/lib/measurementBasis";

export interface MeasurementBasisBarProps {
  basis: MeasurementBasis;
  onBasisChange: (basis: MeasurementBasis) => void;
  availablePasses: readonly PassSelection[];
  comparableQueryCount: number;
  totalQueryCount: number;
  runCount: number;
  statisticCollapsed: boolean;
  unavailableReason?: Parameters<typeof basisUnavailableLabel>[0] | null;
  layout?: "bar" | "card";
}

const STATISTIC_OPTIONS: { value: BasisStatistic; label: string }[] = [
  { value: "median", label: "Median" },
  { value: "min", label: "Fastest" },
];

function passToken(passes: PassSelection): string {
  return encodeBasis({ passes, statistic: "median" });
}

function passLabel(passes: PassSelection): string {
  switch (passes.kind) {
    case "all_warm":
      return "All warm passes";
    case "warmup":
      return "Warmup pass";
    case "warm_pass":
      return `Warm pass ${passes.pass}`;
  }
}

function sharedQueryCopy(sharedCount: number, totalCount: number, hiddenCount: number): string {
  if (hiddenCount <= 0) return `All ${totalCount} queries are shared, with data for every selected run.`;
  const noun = hiddenCount === 1 ? "query is" : "queries are";
  return `${sharedCount} of ${totalCount} queries are shared, with data for every selected run; ${hiddenCount} ${noun} hidden because at least one run is missing data.`;
}

function decodePassToken(token: string): PassSelection {
  if (token === "warmup") return WARMUP;
  const match = /^warm_pass_(\d+)$/.exec(token);
  if (match?.[1] !== undefined) return warmPass(Number(match[1]));
  return ALL_WARM;
}

export function MeasurementBasisBar({
  basis,
  onBasisChange,
  availablePasses,
  comparableQueryCount,
  totalQueryCount,
  runCount,
  statisticCollapsed,
  unavailableReason = null,
  layout = "bar",
}: MeasurementBasisBarProps) {
  const isCard = layout === "card";
  const excluded = totalQueryCount - comparableQueryCount;
  const measuring = isDefaultBasis(basis)
    ? "the published median over all warm passes"
    : `the ${formatBasisLabel(basis)}`;

  return (
    <section
      class={
        isCard
          ? "min-w-0"
          : "panel mb-4 flex flex-wrap items-start justify-between gap-3 px-3 py-2 shadow-sm"
      }
      aria-label="Measurement basis"
    >
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-sm font-medium text-[var(--bb-data-fg-primary)]">Measurement basis</span>
          <StatusBadge role="comparison" tone="neutral" title="All runs in this comparison share one basis">
            {`Shared across ${runCount} runs`}
          </StatusBadge>
        </div>
        <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">
          {`Latency figures use ${measuring}. ${sharedQueryCopy(comparableQueryCount, totalQueryCount, excluded)}`}
        </p>
        <p class="mt-1 text-xs text-[var(--bb-data-fg-subtle)]">
          Whole-run wall-clock totals and phase durations remain run-wide context.
        </p>
        {unavailableReason ? (
          <p class="mt-1 text-xs text-[var(--bb-data-fg-subtle)]">{basisUnavailableLabel(unavailableReason)}</p>
        ) : null}
      </div>

      <div class={`flex flex-wrap items-center gap-3 ${isCard ? "mt-2" : ""}`}>
        <div>
          <label class="text-xs font-medium text-[var(--bb-data-fg-primary)]" for="basis-passes">
            Passes
          </label>
          <Select
            id="basis-passes"
            ariaLabel="Measurement passes"
            size="sm"
            value={passToken(basis.passes)}
            onChange={(token) => onBasisChange({ passes: decodePassToken(token), statistic: basis.statistic })}
            options={availablePasses.map((passes) => ({
              value: passToken(passes),
              label: passLabel(passes),
            }))}
          />
        </div>

        {statisticCollapsed ? (
          <div>
            <span class="text-xs font-medium text-[var(--bb-data-fg-primary)]">Statistic</span>
            <p class="mt-0.5 text-xs text-[var(--bb-data-fg-muted)]" data-testid="statistic-locked">
              Single value — median and fastest are the same over one execution.
            </p>
          </div>
        ) : (
          <div>
            <label class="text-xs font-medium text-[var(--bb-data-fg-primary)]" for="basis-statistic">
              Statistic
            </label>
            <Select
              id="basis-statistic"
              ariaLabel="Measurement statistic"
              size="sm"
              value={basis.statistic}
              onChange={(value) => onBasisChange({ passes: basis.passes, statistic: value as BasisStatistic })}
              options={STATISTIC_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            />
          </div>
        )}
      </div>
    </section>
  );
}
