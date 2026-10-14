import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorMessage } from "@/components/ErrorMessage";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { canonicalBenchmarkSlug, formatBenchmarkLabel } from "@/lib/displayLabels";
import {
  BenchmarkSupportBadge,
  benchmarkSupportGroupLabel,
  benchmarkSupportRank,
  isBenchmarkSupportStatus,
} from "@/lib/benchmarkSupport";
import { listResults, type ResultRow } from "@/lib/duckdbQueries";
import { formatCount } from "@/lib/copyFormatters";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useUrlState, type UrlSerde } from "@/lib/useUrlState";
import { errMsg } from "@/utils";
import { SubmissionActivity } from "@/components/SubmissionActivity";
import { PageHeader } from "@/components/PageHeader";
import { RunDateChip } from "@/components/RunAge";

type SectionKind = "benchmarks" | "platforms";
type SectionSort = "name" | "results" | "recent";

interface SectionEntry {
  id: string;
  label: string;
  href: string;
  resultCount: number;
  coverageCount: number;
  latestRun: string;
  runDates: string[];
  supportStatus: string | null;
}

const sectionSortSerde: UrlSerde<SectionSort> = {
  encode: (value) => value,
  decode: (raw) => (raw === "name" || raw === "results" || raw === "recent" ? raw : null),
};

export function CorpusSectionIndex({ kind }: { kind: SectionKind }) {
  const isBenchmarks = kind === "benchmarks";
  const title = isBenchmarks ? "Benchmarks" : "Platforms";
  const singular = isBenchmarks ? "benchmark" : "platform";
  const [rows, setRows] = useState<ResultRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const retriedEmpty = useRef(false);
  const [sort, setSort] = useUrlState<SectionSort>("sort", "name", sectionSortSerde);
  const [rowsRetryToken, setRowsRetryToken] = useState(0);
  useDocumentTitle(`${title} · BenchBox Results`);

  useEffect(() => {
    let cancelled = false;
    setError(null);

    async function loadRows() {
      try {
        let loaded = await listResults();
        if (loaded.length === 0 && !retriedEmpty.current) {
          retriedEmpty.current = true;
          loaded = await listResults();
        }
        if (!cancelled) setRows(loaded);
      } catch (cause) {
        if (!cancelled) setError(errMsg(cause));
      }
    }

    void loadRows();
    return () => {
      cancelled = true;
    };
  }, [rowsRetryToken]);

  const entries = useMemo(() => sortEntries(buildEntries(rows ?? [], kind), sort), [kind, rows, sort]);
  const groups = useMemo(() => groupEntries(entries, kind), [entries, kind]);

  return (
    <div class="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={[{ label: "Results", href: "/results/" }, { label: title }]}
        eyebrow="Published results"
        title={title}
        subtitle={
          isBenchmarks
            ? "Choose a benchmark to see its published runs, participating platforms, and rankings."
            : "Choose a platform to see its published runs across benchmarks and scales."
        }
      />

      {rows === null && error === null ? (
        <LoadingSpinner message={`Loading ${kind}...`} />
      ) : error !== null ? (
        <ErrorMessage title={`Could not load ${kind}`} message={error} onRetry={() => setRowsRetryToken((t) => t + 1)} />
      ) : entries.length === 0 ? (
        <EmptyState
          title={`No published ${kind}`}
          description={`There are no published ${kind} to list yet.`}
          action={
            <a href="/results/" class="btn btn-secondary no-underline">
              Back to leaderboards
            </a>
          }
        />
      ) : (
        <>
          <SubmissionActivity
            subject={singular}
            rows={entries.map((entry) => ({
              id: entry.id,
              label: entry.label,
              href: entry.href,
              dates: entry.runDates,
            }))}
          />

          <div class="mb-5 flex flex-wrap items-end justify-between gap-4">
            <p class="text-sm text-[var(--bb-data-fg-muted)]">
              {formatCount(entries.length, `published ${singular}`)}
            </p>
            <label class="flex items-center gap-2 text-sm font-medium text-[var(--bb-data-fg-primary)]">
              Sort {kind} within each group
              <select
                aria-label={`Sort ${kind}`}
                value={sort}
                class="rounded-md border border-[var(--bb-data-border-strong)] bg-[var(--bb-surface-data)] px-3 py-2 text-sm text-[var(--bb-data-fg-primary)]"
                onChange={(event) => setSort(event.currentTarget.value as SectionSort)}
              >
                <option value="name">Name</option>
                <option value="results">Result count</option>
                <option value="recent">Latest result</option>
              </select>
            </label>
          </div>

          <div data-testid={`${kind}-index-list`}>
            {groups.map((group) => (
              <section key={group.key} aria-label={group.heading ?? undefined} class="mb-8 last:mb-0">
                {group.heading !== null ? (
                  <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--bb-data-fg-muted)]">
                    {group.heading}
                  </h2>
                ) : null}
                <ul class="grid grid-cols-2 gap-2 sm:grid-cols-2 sm:gap-3 lg:grid-cols-3 xl:grid-cols-4">
                  {group.entries.map((entry) => {
                    return (
                      <li key={entry.id}>
                        <div class="group block h-full rounded-lg border border-[var(--bb-data-border)] bg-[var(--bb-surface-data)] p-3 no-underline shadow-sm transition-colors hover:border-[var(--bb-accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--bb-focus-ring)] sm:p-4">
                          <h3 class="break-words text-sm font-semibold text-[var(--bb-data-fg-primary)] group-hover:text-[var(--bb-accent)] sm:text-base">
                            <a href={entry.href}>{entry.label}</a>
                          </h3>
                          {isBenchmarks && entry.supportStatus !== null ? (
                            <div class="mt-1">
                              <BenchmarkSupportBadge status={entry.supportStatus} />
                            </div>
                          ) : null}
                          <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)] sm:mt-1.5 sm:text-sm">
                            {formatCount(entry.resultCount, "run")} ·{" "}
                            {formatCount(entry.coverageCount, isBenchmarks ? "platform" : "benchmark")}
                          </p>
                          <p class="mt-2 text-xs text-[var(--bb-data-fg-muted)] sm:mt-2.5">
                            Latest <RunDateChip runDate={entry.latestRun} />
                          </p>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function buildEntries(rows: ResultRow[], kind: SectionKind): SectionEntry[] {
  const grouped = new Map<string, { label: string; supportStatus: string | null; rows: ResultRow[] }>();

  for (const row of rows) {
    const id = kind === "benchmarks" ? canonicalBenchmarkSlug(row.benchmark) : row.platform_id;
    const label = kind === "benchmarks" ? formatBenchmarkLabel(id) : row.platform;
    const supportStatus = kind === "benchmarks" ? (row.benchmark_support_status ?? null) : null;
    const existing = grouped.get(id);
    if (existing) {
      existing.rows.push(row);
      if (existing.supportStatus === null && supportStatus !== null) {
        existing.supportStatus = supportStatus;
      }
    } else {
      grouped.set(id, { label, supportStatus, rows: [row] });
    }
  }

  return Array.from(grouped, ([id, group]) => ({
    id,
    label: group.label,
    href: kind === "benchmarks" ? `/results/${encodeURIComponent(id)}/` : `/results/p/${encodeURIComponent(id)}/`,
    resultCount: group.rows.length,
    coverageCount:
      kind === "benchmarks"
        ? new Set(group.rows.map((row) => row.platform_id)).size
        : new Set(group.rows.map((row) => canonicalBenchmarkSlug(row.benchmark))).size,
    latestRun: group.rows.reduce((latest, row) => (row.run_date > latest ? row.run_date : latest), ""),
    runDates: group.rows.map((row) => row.run_date),
    supportStatus: group.supportStatus,
  }));
}

interface EntryGroup {
  key: string;
  heading: string | null;
  entries: SectionEntry[];
}

function groupEntries(entries: SectionEntry[], kind: SectionKind): EntryGroup[] {
  if (kind !== "benchmarks") {
    return [{ key: "all", heading: null, entries }];
  }
  const byStatus = new Map<string, SectionEntry[]>();
  for (const entry of entries) {
    const key = isBenchmarkSupportStatus(entry.supportStatus) ? entry.supportStatus : "";
    const group = byStatus.get(key);
    if (group) {
      group.push(entry);
    } else {
      byStatus.set(key, [entry]);
    }
  }
  return Array.from(byStatus, ([status, groupEntries]) => {
    const resolved = status === "" ? null : status;
    return {
      key: status === "" ? "other" : status,
      heading: benchmarkSupportGroupLabel(resolved),
      entries: groupEntries,
    };
  }).sort((left, right) => {
    const leftRank = benchmarkSupportRank(left.key === "other" ? null : left.key);
    const rightRank = benchmarkSupportRank(right.key === "other" ? null : right.key);
    if (leftRank !== rightRank) return leftRank - rightRank;
    return (left.heading ?? "").localeCompare(right.heading ?? "");
  });
}

function sortEntries(entries: SectionEntry[], sort: SectionSort): SectionEntry[] {
  return [...entries].sort((left, right) => {
    if (sort === "results" && left.resultCount !== right.resultCount) return right.resultCount - left.resultCount;
    if (sort === "recent" && left.latestRun !== right.latestRun) return right.latestRun.localeCompare(left.latestRun);
    return left.label.localeCompare(right.label);
  });
}
