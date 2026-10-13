import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CompareCta } from "@/components/CompareCta";
import { FundingChip } from "@/components/FundingChip";
import { RunDateWithAge } from "@/components/RunAge";
import { TrustBadge } from "@/components/TrustBadge";
import { useIsNarrowViewport } from "@/lib/useIsNarrowViewport";

export interface CompareTrayItem {
  id: string;
  platform: string;
  benchmarkLabel: string;
  scaleFactor: string | number;
  phase: string;
  runDate: string;
  trustLabel: string;
  funding: string | null | undefined;
  visibleResultId: string;
}

interface CompareTrayProps {
  summary: ComponentChildren;
  items: CompareTrayItem[];
  allIds: readonly string[];
  buildHref: (ids: readonly string[]) => string;
  onClear: () => void;
  onRemove?: (item: CompareTrayItem) => void;
}

export function CompareTray({ summary, items, allIds, buildHref, onClear, onRemove }: CompareTrayProps) {
  const trayRef = useRef<HTMLDivElement>(null);
  const [trayHeight, setTrayHeight] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const isMobile = useIsNarrowViewport("(max-width: 639px)");

  const collapsed = isMobile && !expanded;

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const target = event.target as HTMLElement | null;
      const insideTray = target ? trayRef.current?.contains(target) : false;
      if (!insideTray) return;
      event.preventDefault();
      setExpanded((prev) => (prev ? false : prev));
      requestAnimationFrame(() => {
        const active = document.activeElement as HTMLElement | null;
        const activeHidden =
          active instanceof HTMLElement &&
          (active.hidden ||
            active.getAttribute("aria-hidden") === "true" ||
            getComputedStyle(active).display === "none" ||
            (active as HTMLElement).offsetParent === null);
        const focusInCollapsedDetails =
          active instanceof HTMLElement &&
          !!document.getElementById("compare-tray-details")?.contains(active) &&
          !!document.querySelector("[data-testid='compare-tray'][data-collapsed='true']");
        if (!active || !document.contains(active) || active === document.body || activeHidden || focusInCollapsedDetails) {
          const toggle = document.querySelector("[data-testid='compare-tray-toggle']") as HTMLElement | null;
          toggle?.focus();
          if (document.activeElement === document.body) {
            const compareLink = document.querySelector("[data-testid='compare-tray-compare-link']") as HTMLElement | null;
            compareLink?.focus();
          }
        }
      });
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => {
    const tray = trayRef.current;
    if (!tray) return;

    const measure = () => setTrayHeight(Math.ceil(tray.getBoundingClientRect().height));
    measure();

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(tray);
    return () => observer.disconnect();
  }, [collapsed, expanded, isMobile, items.length]);

  return (
    <>
      <div
        class="bb-compare-tray-spacer"
        data-testid="compare-tray-spacer"
        aria-hidden="true"
        style={{ height: `${trayHeight}px` }}
      />
      <div
        ref={trayRef}
        class="bb-compare-tray fixed bottom-0 left-0 right-0 z-50 border-t border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] px-4 pt-3 shadow-lg"
        data-testid="compare-tray"
        data-collapsed={collapsed ? "true" : "false"}
        role="region"
        aria-label="Comparison selection"
      >
        <div class="mx-auto flex max-w-7xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 text-sm text-[var(--bb-data-fg-primary)]">
              <span class="flex-1">{summary}</span>
              {isMobile && (
                <button
                  type="button"
                  data-testid="compare-tray-toggle"
                  aria-expanded={expanded ? "true" : "false"}
                  aria-controls="compare-tray-details"
                  aria-label={expanded ? "Collapse selection tray" : "Expand selection tray"}
                  class="bb-compare-tray-toggle inline-flex h-8 items-center rounded-md border border-[var(--bb-data-border)] px-2 text-xs font-medium sm:hidden"
                  onClick={() => setExpanded((v) => !v)}
                >
                  {expanded ? "Collapse" : "Expand"}
                </button>
              )}
            </div>
            <div
              id="compare-tray-details"
              class={`mt-2 flex max-h-32 flex-wrap gap-2 overflow-y-auto pr-1 ${collapsed ? "hidden sm:flex" : ""}`}
              role="list"
              aria-label="Selected compare results"
              hidden={collapsed ? true : undefined}
              data-testid="compare-tray-details"
            >
              {items.map((item) => (
                <div
                  key={item.id}
                  data-testid={`compare-tray-row-${item.id}`}
                  role="listitem"
                  class="flex max-w-full flex-wrap items-center gap-1.5 rounded-md border border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] px-2 py-1 text-xs text-[var(--bb-data-fg-muted)]"
                >
                  <span class="font-medium text-[var(--bb-data-fg-primary)]">{item.platform}</span>
                  <span>{item.benchmarkLabel}</span>
                  <span>SF {item.scaleFactor}</span>
                  <span>{item.phase}</span>
                  <RunDateWithAge runDate={item.runDate} />
                  <TrustBadge trustLabel={item.trustLabel} compact />
                  <FundingChip funding={item.funding} compact />
                  <span class="font-mono text-[var(--bb-data-fg-muted)]">Public ID {item.visibleResultId}</span>
                  {onRemove && (
                    <button
                      type="button"
                      class="ml-1 rounded px-1 text-[var(--bb-data-fg-muted)] hover:bg-[var(--bb-surface-data)] hover:text-[var(--bb-data-fg-primary)]"
                      aria-label={`Remove ${item.platform}, Public ID ${item.visibleResultId}, from comparison`}
                      onClick={() => onRemove(item)}
                    >
                      Remove
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
          <div class="flex min-w-0 flex-wrap items-center gap-3 sm:justify-end">
            {!collapsed && (
              <button
                type="button"
                class="text-sm text-[var(--bb-data-fg-muted)] hover:text-[var(--bb-data-fg-primary)]"
                onClick={onClear}
                data-testid="compare-tray-clear"
              >
                Clear
              </button>
            )}
            {isMobile && expanded && (
              <button
                type="button"
                class="text-sm text-[var(--bb-data-fg-muted)] hover:text-[var(--bb-data-fg-primary)]"
                data-testid="compare-tray-dismiss"
                aria-label="Dismiss selection tray"
                onClick={() => setExpanded(false)}
              >
                Dismiss
              </button>
            )}
            <CompareCta
              selectedIds={items.map((item) => item.id)}
              allIds={allIds}
              buildHref={buildHref}
              testId="compare-tray-compare-link"
            />
          </div>
        </div>
      </div>
    </>
  );
}
