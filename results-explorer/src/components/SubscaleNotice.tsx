import { SUBSCALE_BADGE, subscaleCohortNote, subscaleTpcName } from "@/lib/subscale";

export function SubscaleBadge({ testId }: { testId?: string }) {
  return (
    <span
      class="inline-flex items-center rounded border border-[var(--bb-tone-warning-border)] bg-[var(--bb-tone-warning-bg)] px-1.5 py-0.5 text-xs font-medium text-[var(--bb-tone-warning-fg)]"
      data-testid={testId}
    >
      {SUBSCALE_BADGE}
    </span>
  );
}

export function SubscaleCohortNotice({
  benchmark,
  scaleFactor,
  testId = "subscale-cohort-notice",
}: {
  benchmark: string | null | undefined;
  scaleFactor: number | string | null | undefined;
  testId?: string;
}) {
  const name = subscaleTpcName(benchmark, scaleFactor);
  if (!name) return null;
  return (
    <div
      class="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-[var(--bb-tone-warning-border)] bg-[var(--bb-tone-warning-bg)] px-4 py-3 text-sm text-[var(--bb-tone-warning-fg)]"
      data-testid={testId}
      role="note"
    >
      <SubscaleBadge />
      <span>{subscaleCohortNote(name)}</span>
    </div>
  );
}
