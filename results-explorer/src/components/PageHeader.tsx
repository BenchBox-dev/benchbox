import type { ComponentChildren } from "preact";
import { Breadcrumb, type Crumb } from "@/components/Breadcrumb";

export const PAGE_HEADER_CLASSES = {
  eyebrow: "text-xs font-semibold uppercase tracking-wide text-[var(--bb-data-fg-muted)]",
  title: "break-words text-3xl font-bold text-[var(--bb-data-fg-primary)]",
  subtitle: "max-w-3xl text-sm text-[var(--bb-data-fg-muted)]",
} as const;

interface PageHeaderProps {
  crumbs?: Crumb[];
  eyebrow?: string;
  title: ComponentChildren;
  subtitle?: ComponentChildren;
  meta?: ComponentChildren;
  actions?: ComponentChildren;
}

export function PageHeader({ crumbs, eyebrow, title, subtitle, meta, actions }: PageHeaderProps) {
  return (
    <header class="mb-6" data-testid="page-header">
      {crumbs && crumbs.length > 0 && <Breadcrumb crumbs={crumbs} />}
      <div class={`flex flex-wrap items-start justify-between gap-x-6 gap-y-3 ${crumbs ? "mt-5" : ""}`}>
        <div class="min-w-0">
          {eyebrow && <p class={PAGE_HEADER_CLASSES.eyebrow}>{eyebrow}</p>}
          <h1 class={`${PAGE_HEADER_CLASSES.title} ${eyebrow ? "mt-1" : ""}`}>{title}</h1>
          {subtitle && <p class={`mt-2 ${PAGE_HEADER_CLASSES.subtitle}`}>{subtitle}</p>}
          {meta && (
            <div
              class="mt-3 flex flex-wrap items-center gap-2 text-sm text-[var(--bb-data-fg-muted)]"
              data-testid="page-header-meta"
            >
              {meta}
            </div>
          )}
        </div>
        {actions && <div class="flex flex-wrap items-center gap-3">{actions}</div>}
      </div>
    </header>
  );
}
