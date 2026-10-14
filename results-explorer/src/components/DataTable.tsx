import type { ComponentChildren } from "preact";
import { useRef } from "preact/hooks";
import { TableScrollHint } from "@/components/TableScrollHint";

interface DataTableProps {
  ariaLabel?: string;
  ariaColCount?: number;
  caption?: ComponentChildren;
  scrollable?: boolean;
  class?: string;
  children: ComponentChildren;
}

interface RunIdentityLabelProps {
  label: string;
  href?: string;
  class?: string;
  title?: string;
}

export function RunIdentityLabel({ label, href, class: extraClass = "", title = label }: RunIdentityLabelProps) {
  const className = `font-medium ${extraClass}`;
  if (href) {
    return (
      <a href={href} class={`${className} no-underline`} data-testid="run-identity-label" title={title}>
        {label}
      </a>
    );
  }
  return (
    <span class={className} data-testid="run-identity-label" title={title}>
      {label}
    </span>
  );
}

export function RankingEligibilityLegend() {
  return (
    <p
      class="mb-2 text-xs text-[var(--bb-data-fg-muted)]"
      data-testid="ranking-eligibility-legend"
    >
      <span aria-hidden="true" class="font-semibold">*</span>{" "}
      Not eligible for ranking; hover or focus the marker for the reason.
    </p>
  );
}

export function DataTable({ ariaLabel, ariaColCount, caption, scrollable = false, class: extraClass = "", children }: DataTableProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const inner = (
    <table aria-label={ariaLabel} aria-colcount={ariaColCount} class={`w-full text-sm ${extraClass}`}>
      {caption && <caption class="text-left text-xs uppercase tracking-wide text-[var(--bb-data-fg-subtle)] py-2">{caption}</caption>}
      {children}
    </table>
  );
  if (!scrollable) return inner;
  return (
    <div>
      <TableScrollHint
        scrollerRef={scrollerRef}
        label="Scroll table for more columns →"
        wrapperClassName="flex justify-end"
        className="mb-2"
      />
      <div ref={scrollerRef} class="overflow-x-auto">{inner}</div>
    </div>
  );
}

interface TableHeadProps {
  sticky?: boolean;
  class?: string;
  children: ComponentChildren;
}

export function TableHead({ sticky = false, class: extraClass = "", children }: TableHeadProps) {
  const stickyClass = sticky
    ? `sticky top-0 z-[1]`
    : "";
  return (
    <thead
      class={`bg-[var(--bb-surface-data-muted)] text-[var(--bb-data-fg-subtle)] ${stickyClass} ${extraClass}`}
    >
      {children}
    </thead>
  );
}

interface TableProps {
  class?: string;
  children: ComponentChildren;
}

export function TableBody({ class: extraClass = "", children }: TableProps) {
  return <tbody class={`divide-y divide-[var(--bb-data-border)] ${extraClass}`}>{children}</tbody>;
}

export default DataTable;
