import { formatLatencyMs, formatRatio } from "@/lib/metricFormatters";
import type { RoutableProps } from "preact-router";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import type { DetailResult } from "@/types";
import { getDetailResult, resolveShortId } from "@/lib/duckdbQueries";
import { errMsg, fmtMs, humanizeBenchmark } from "@/utils";
import { ErrorMessage } from "@/components/ErrorMessage";
import { CompareSummarySkeleton } from "@/components/LoadingSpinner";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Select } from "@/components/Select";
import { TableScrollHint } from "@/components/TableScrollHint";
import { summarizeQueryPasses } from "@/components/PassStrip";
import { clampReferenceIndex, resultDetailHref, withinRunCompareHref, MAX_WITHIN_RUN_BASES, MIN_WITHIN_RUN_BASES } from "@/lib/resultLinks";
import { paletteColor } from "@/lib/chartTheme";
import { geomeanMs } from "@/lib/chartMath";
import { COMPARE_TIE_THRESHOLD } from "@/lib/compareSummary";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import {
  DEFAULT_BASIS,
  WARMUP,
  availableBasesForExecutions,
  basisUnavailableLabel,
  basesEqual,
  basesSerde,
  decodeBasis,
  encodeBasis,
  formatBasisLabel,
  passSelectionsEqual,
  resolveQueryValue,
  selectComparableBasisPair,
  type BasisUnavailableReason,
  type BasisExecution,
  type MeasurementBasis,
} from "@/lib/measurementBasis";

interface CompareWithinRunProps extends RoutableProps {
  resultId?: string;
}

export const WITHIN_RUN_FIGURES_ARE_RANKABLE = false;

export function deduplicateBases(bases: readonly MeasurementBasis[]): MeasurementBasis[] {
  const unique: MeasurementBasis[] = [];
  for (const b of bases) {
    if (!unique.some((u) => basesEqual(u, b))) {
      unique.push(b);
    }
  }
  return unique;
}

export function parseBasesParam(search: string): {
  bases: MeasurementBasis[];
  referenceIndex: number;
  hasExplicitBases: boolean;
} {
  const params = new URLSearchParams(search);
  const raw = params.get("bases");
  const decoded = raw !== null ? basesSerde.decode(raw) : null;
  const unique = decoded ? deduplicateBases(decoded) : [];
  const bases =
    unique.length >= MIN_WITHIN_RUN_BASES
      ? unique.slice(0, MAX_WITHIN_RUN_BASES)
      : [DEFAULT_BASIS, { passes: WARMUP, statistic: "median" as const }];
  const refRaw = Number(params.get("ref") ?? "0");
  const referencedBasis = decoded?.[clampReferenceIndex(refRaw, decoded.length)];
  const deduplicatedReferenceIndex = referencedBasis
    ? bases.findIndex((basis) => basesEqual(basis, referencedBasis))
    : -1;
  return {
    bases,
    hasExplicitBases: decoded !== null && unique.length >= MIN_WITHIN_RUN_BASES,
    referenceIndex:
      deduplicatedReferenceIndex >= 0
        ? deduplicatedReferenceIndex
        : clampReferenceIndex(refRaw, bases.length),
  };
}

export function basesProduceOneMeasuredPass(
  executions: readonly BasisExecution[],
  displayMsByQuery: ReadonlyMap<string, number | null>,
  bases: readonly MeasurementBasis[],
): boolean {
  if (bases.length < 2) return false;
  const { rows } = buildWithinRunRows(executions, displayMsByQuery, bases);
  const hasFullyMeasuredRow = rows.some((row) => row.cells.every((cell) => cell.ms !== null));
  return hasFullyMeasuredRow && rows.every((row) => {
    const values = row.cells.map((cell) => cell.ms).filter((ms): ms is number => ms !== null);
    return values.every((ms) => ms === values[0]);
  });
}

export function withinRunComparisonStatus(ratio: number | null): "faster" | "slower" | "parity" | null {
  if (ratio === null || !Number.isFinite(ratio) || ratio <= 0) return null;
  if (Math.abs(ratio - 1) < COMPARE_TIE_THRESHOLD) return "parity";
  return ratio < 1 ? "faster" : "slower";
}

export function formatWithinRunBasisLabel(
  basis: MeasurementBasis,
  allBases?: readonly MeasurementBasis[],
): string {
  const hasSiblingWithDiffStat = allBases?.some(
    (b) => passSelectionsEqual(b.passes, basis.passes) && b.statistic !== basis.statistic,
  );
  if (hasSiblingWithDiffStat) {
    if (basis.passes.kind === "warmup") {
      return `warmup pass (${basis.statistic})`;
    }
    if (basis.passes.kind === "warm_pass") {
      return `warm pass ${basis.passes.pass} (${basis.statistic})`;
    }
    if (basis.passes.kind === "all_warm") {
      return basis.statistic === "min" ? "fastest warm pass" : "warm passes (median)";
    }
  }
  return formatBasisLabel(basis);
}

export const availableBasesForQueries = availableBasesForExecutions;

export interface WithinRunCell {
  ms: number | null;
  unavailableReason: BasisUnavailableReason | null;
}

function signedLatencyDeltaText(deltaMs: number): string {
  const sign = deltaMs > 0 ? "+" : deltaMs < 0 ? "−" : "";
  return `${sign}${formatLatencyMs(Math.abs(deltaMs)).valueText}`;
}

export function withinRunUnavailableKind(
  reason: BasisUnavailableReason | null,
): "failed" | "excluded" | "unrecorded" {
  if (reason === null || reason === "no_warmup_recorded" || reason === "no_warm_passes_recorded" || reason === "pass_not_recorded") {
    return "unrecorded";
  }
  return reason === "no_passing_executions" ? "failed" : "excluded";
}

export interface WithinRunRow {
  queryId: string;
  cells: WithinRunCell[];
  comparable: boolean;
}

export function buildWithinRunRows(
  executions: readonly BasisExecution[],
  displayMsByQuery: ReadonlyMap<string, number | null>,
  bases: readonly MeasurementBasis[],
): { rows: WithinRunRow[]; sharedQueryIds: string[] } {
  const byQuery = new Map<string, BasisExecution[]>();
  for (const row of executions) {
    const bucket = byQuery.get(row.query_id);
    if (bucket) bucket.push(row);
    else byQuery.set(row.query_id, [row]);
  }

  const queryIds = new Set([...byQuery.keys(), ...displayMsByQuery.keys()]);
  const rows: WithinRunRow[] = [...queryIds]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .map((queryId) => {
      const queryRows = byQuery.get(queryId) ?? [];
      const cells = bases.map((basis) => {
        const value = resolveQueryValue(basis, queryRows, displayMsByQuery.get(queryId) ?? null);
        return value.kind === "value"
          ? { ms: value.ms, unavailableReason: null }
          : { ms: null, unavailableReason: value.reason };
      });
      return { queryId, cells, comparable: cells.every((c) => c.ms !== null && c.ms > 0) };
    });

  return { rows, sharedQueryIds: rows.filter((r) => r.comparable).map((r) => r.queryId) };
}

export function calculateColumnGeomeans(
  rows: readonly WithinRunRow[],
  sharedQueryIds: readonly string[],
): (number | null)[] {
  if (rows.length === 0 || rows[0] === undefined) return [];
  const columnCount = rows[0].cells.length;
  const sharedSet = new Set(sharedQueryIds);
  const sharedRows = rows.filter((r) => sharedSet.has(r.queryId));

  const result: (number | null)[] = [];
  for (let col = 0; col < columnCount; col++) {
    const values = sharedRows
      .map((r) => r.cells[col]?.ms)
      .filter((v): v is number => v !== null && v !== undefined && Number.isFinite(v) && v > 0);
    result.push(values.length === sharedRows.length && sharedRows.length > 0 ? geomeanMs(values) : null);
  }
  return result;
}

export function calculateCellDelta(
  cellMs: number | null,
  refMs: number | null,
): { ratio: number | null; deltaMs: number | null } {
  if (cellMs === null || refMs === null || refMs <= 0 || cellMs <= 0) {
    return { ratio: null, deltaMs: null };
  }
  return {
    ratio: cellMs / refMs,
    deltaMs: cellMs - refMs,
  };
}

export function CompareWithinRun({ resultId }: CompareWithinRunProps) {
  const [detail, setDetail] = useState<DetailResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailRetryToken, setDetailRetryToken] = useState(0);
  const search = typeof window === "undefined" ? "" : window.location.search;
  const parsed = useMemo(() => parseBasesParam(search), [search]);
  const [bases, setBases] = useState<MeasurementBasis[]>(parsed.bases);
  const [referenceIndex, setReferenceIndex] = useState<number>(parsed.referenceIndex);
  const tableScrollerRef = useRef<HTMLDivElement>(null);
  useDocumentTitle(detail ? `Compare measurements · ${detail.platform} · BenchBox Results` : "Compare measurements · BenchBox Results");

  useEffect(() => {
    setBases(parsed.bases);
    setReferenceIndex(parsed.referenceIndex);
  }, [parsed]);

  useEffect(() => {
    let cancelled = false;
    if (!resultId) return;
    setLoading(true);
    setError(null);
    resolveShortId(resultId)
      .then(async (resolvedId) => {
        if (cancelled) return null;
        if (resolvedId !== resultId && typeof window !== "undefined") {
          history.replaceState(null, "", `/results/r/${encodeURIComponent(resolvedId)}/passes${window.location.search}${window.location.hash}`);
        }
        return getDetailResult(resolvedId);
      })
      .then((result) => {
        if (cancelled) return;
        setDetail(result);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(errMsg(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [resultId, detailRetryToken]);

  function updateBasesAndRef(nextBases: MeasurementBasis[], nextRef: number) {
    const unique = deduplicateBases(nextBases);
    const clampedBases = unique.slice(0, MAX_WITHIN_RUN_BASES);
    const clampedRef = clampReferenceIndex(nextRef, clampedBases.length);
    setBases(clampedBases);
    setReferenceIndex(clampedRef);
    if (resultId && typeof window !== "undefined") {
      const href = withinRunCompareHref(resultId, clampedBases.map(encodeBasis), clampedRef);
      window.history.replaceState(null, "", href);
    }
  }

  const availableBases = useMemo(
    () => (detail ? availableBasesForQueries(detail.queries, detail.display_timings) : []),
    [detail],
  );
  const comparablePair = useMemo(
    () => (detail ? selectComparableBasisPair(detail.queries, detail.display_timings, bases) : null),
    [bases, detail],
  );

  useEffect(() => {
    if (!detail) return;
    const hasUnavailableSelection = bases.some(
      (basis) => !availableBases.some((candidate) => basesEqual(candidate, basis)),
    );
    const displayMsByQuery = new Map<string, number | null>(
      detail.display_timings.map((timing) => [
        timing.query_id,
        timing.is_valid_display_timing ? timing.display_ms : null,
      ]),
    );
    const explicitSelectionHasSharedEvidence =
      bases.length >= MIN_WITHIN_RUN_BASES &&
      buildWithinRunRows(detail.queries, displayMsByQuery, bases).sharedQueryIds.length > 0;
    if (parsed.hasExplicitBases && !hasUnavailableSelection && explicitSelectionHasSharedEvidence) return;
    if (comparablePair && (bases.length !== 2 || !comparablePair.every((basis, index) => basesEqual(basis, bases[index]!)))) {
      updateBasesAndRef(comparablePair, Math.min(referenceIndex, 1));
    }
  }, [availableBases, bases, comparablePair, detail, parsed.hasExplicitBases, referenceIndex]);
  const addableBases = useMemo(
    () => availableBases.filter((b) => !bases.some((existing) => basesEqual(existing, b))),
    [availableBases, bases],
  );
  const [selectedAddKey, setSelectedAddKey] = useState<string>("");

  useEffect(() => {
    if (addableBases.length > 0 && (!selectedAddKey || !addableBases.some((b) => encodeBasis(b) === selectedAddKey))) {
      setSelectedAddKey(encodeBasis(addableBases[0]!));
    }
  }, [addableBases, selectedAddKey]);

  const effectiveAddKey =
    selectedAddKey || (addableBases[0] ? encodeBasis(addableBases[0]) : "");

  function handleAddBasis() {
    if (!effectiveAddKey) return;
    const basisToAdd = decodeBasis(effectiveAddKey);
    if (!basisToAdd) return;
    if (bases.some((b) => basesEqual(b, basisToAdd))) return;
    updateBasesAndRef([...bases, basisToAdd], referenceIndex);
  }

  function handleRemoveBasis(colIndex: number) {
    if (bases.length <= MIN_WITHIN_RUN_BASES) return;
    const nextBases = bases.filter((_, idx) => idx !== colIndex);
    const nextRef =
      colIndex === referenceIndex
        ? 0
        : colIndex < referenceIndex
          ? referenceIndex - 1
          : referenceIndex;
    updateBasesAndRef(nextBases, nextRef);
  }

  function handleSetReference(colIndex: number) {
    updateBasesAndRef(bases, colIndex);
  }

  const grid = useMemo(() => {
    if (!detail) return { rows: [], sharedQueryIds: [] };
    const displayMsByQuery = new Map<string, number | null>(
      detail.display_timings.map((t) => [t.query_id, t.is_valid_display_timing ? t.display_ms : null]),
    );
    return buildWithinRunRows(detail.queries, displayMsByQuery, bases);
  }, [detail, bases]);

  const columnGeomeans = useMemo(
    () => calculateColumnGeomeans(grid.rows, grid.sharedQueryIds),
    [grid.rows, grid.sharedQueryIds],
  );
  const refGeomean = columnGeomeans[referenceIndex] ?? null;
  const hasSufficientAggregateEvidence = grid.sharedQueryIds.length >= 2;

  if (loading) return <CompareSummarySkeleton />;
  if (error) return <ErrorMessage message={error} onRetry={() => setDetailRetryToken((t) => t + 1)} />;
  if (!detail) return <ErrorMessage message="Result not found." />;
  if (comparablePair === null) {
    return <ErrorMessage message="This run does not have two measurement bases with shared query evidence." />;
  }

  const passSummaries = summarizeQueryPasses(detail.queries);
  const reference = bases[referenceIndex] ?? bases[0]!;
  const displayMsByQuery = new Map<string, number | null>(
    detail.display_timings.map((t) => [t.query_id, t.is_valid_display_timing ? t.display_ms : null]),
  );
  const onlyOneMeasuredPass = basesProduceOneMeasuredPass(detail.queries, displayMsByQuery, availableBases);
  const onlyMeasuredPassNumber = availableBases.flatMap((basis) =>
    basis.passes.kind === "warm_pass" ? [basis.passes.pass] : [],
  )[0];
  const runHref = resultDetailHref(detail.result_id);

  return (
    <div class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        crumbs={[
          { label: "Results", href: "/results/" },
          { label: humanizeBenchmark(detail.benchmark), href: `/results/${detail.benchmark}/` },
          { label: detail.platform, href: runHref },
          { label: "Passes" },
        ]}
        eyebrow="Within one run"
        title={`Compare measurements from one ${detail.platform} run`}
        subtitle={
          <>
            Each column reads the same run in a different way. The platform, hardware, and queries stay fixed, so any
            difference comes from the selected passes or summary method.
          </>
        }
        meta={
          <>
            <StatusBadge role="comparison" tone="neutral">
              {`${grid.sharedQueryIds.length} of ${grid.rows.length} queries comparable`}
            </StatusBadge>
            <StatusBadge role="comparison" tone="neutral">
              {`Reference: ${formatWithinRunBasisLabel(reference, bases)}`}
            </StatusBadge>
            <StatusBadge role="comparison" tone="neutral">
              {`${passSummaries.length} queries in this run`}
            </StatusBadge>
          </>
        }
      />
      <p class="mb-6 text-xs text-[var(--bb-data-fg-subtle)]" data-testid="not-a-platform-result">
        These figures explain how the measurement method changes the result. They do not compare platforms and are not included in rankings.
      </p>

      {onlyOneMeasuredPass ? (
        <p class="panel mb-6 px-4 py-3 text-sm text-[var(--bb-data-fg-primary)]" data-testid="one-measured-pass-note">
          {onlyMeasuredPassNumber !== undefined
            ? `Only one measured pass: the median equals pass ${onlyMeasuredPassNumber}.`
            : "Only one measured pass: the median equals that pass."}
        </p>
      ) : (
        <>
          <div class="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {bases.map((basis, i) => {
              const color = paletteColor(i);
              const isRef = i === referenceIndex;
              const geomean = columnGeomeans[i] ?? null;
              const ratio =
                hasSufficientAggregateEvidence && refGeomean !== null && geomean !== null && refGeomean > 0
                  ? geomean / refGeomean
                  : null;
              const deltaMs =
                hasSufficientAggregateEvidence && refGeomean !== null && geomean !== null
                  ? geomean - refGeomean
                  : null;
              const comparisonStatus = withinRunComparisonStatus(ratio);
              const canRemove = bases.length > MIN_WITHIN_RUN_BASES;

              return (
                <div
                  key={encodeBasis(basis)}
                  class="card relative overflow-hidden"
                  style={{ borderTopColor: color, borderTopWidth: "3px" }}
                >
                  <div class="mb-2 flex items-center justify-between">
                    <label class="flex items-center gap-1.5 cursor-pointer text-xs font-semibold text-[var(--bb-data-fg-primary)]">
                      <input
                        type="radio"
                        name="within-run-reference-radio"
                        checked={isRef}
                        onChange={() => handleSetReference(i)}
                        aria-label={`Set ${formatWithinRunBasisLabel(basis, bases)} as reference`}
                        data-testid={`reference-radio-${encodeBasis(basis)}`}
                      />
                      <span>{formatWithinRunBasisLabel(basis, bases)}</span>
                    </label>
                    <div class="flex items-center gap-1">
                      {!isRef && comparisonStatus !== null ? (
                        <StatusBadge
                          role="ranking"
                          tone={comparisonStatus === "faster" ? "success" : "neutral"}
                        >
                          {comparisonStatus}
                        </StatusBadge>
                      ) : null}
                      {canRemove && (
                        <button
                          type="button"
                          class="text-xs text-[var(--bb-data-fg-subtle)] hover:text-[var(--bb-tone-danger-fg)] px-1 rounded"
                          onClick={() => handleRemoveBasis(i)}
                          aria-label={`Remove ${formatWithinRunBasisLabel(basis, bases)}`}
                          title="Remove column"
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  </div>

                  <dl class="space-y-1 text-sm mt-3">
                    <div class="flex justify-between">
                      <dt class="text-[var(--bb-data-fg-muted)]">Geomean query time</dt>
                      <dd class="font-mono font-medium">
                        {geomean !== null ? fmtMs(geomean) : "—"}
                      </dd>
                    </div>
                    {!isRef && (
                      <>
                        <div class="flex justify-between">
                          <dt class="text-[var(--bb-data-fg-muted)]">Ratio vs reference</dt>
                          <dd class="font-mono font-medium">
                            {formatRatio(ratio)}
                          </dd>
                        </div>
                        <div class="flex justify-between">
                          <dt class="text-[var(--bb-data-fg-muted)]">Delta vs reference</dt>
                          <dd class="font-mono text-xs">
                            {deltaMs !== null ? signedLatencyDeltaText(deltaMs) : "—"}
                          </dd>
                        </div>
                      </>
                    )}
                  </dl>
                </div>
              );
            })}
          </div>

          {bases.length < MAX_WITHIN_RUN_BASES && addableBases.length > 0 && (
            <div class="panel mb-6 flex flex-wrap items-center justify-between gap-3 px-4 py-3 shadow-sm">
              <div>
                <label class="text-sm font-medium text-[var(--bb-data-fg-primary)]" for="add-basis-select">
                  Add measurement basis
                </label>
                <p class="text-xs text-[var(--bb-data-fg-muted)]">
                  Compare another pass or reduction from this run (up to {MAX_WITHIN_RUN_BASES} columns).
                </p>
              </div>
              <div class="flex items-center gap-2">
                <Select
                  id="add-basis-select"
                  ariaLabel="Available measurement bases"
                  size="sm"
                  value={effectiveAddKey}
                  onChange={setSelectedAddKey}
                  options={addableBases.map((b) => ({
                    value: encodeBasis(b),
                    label: formatWithinRunBasisLabel(b, availableBases),
                  }))}
                />
                <button type="button" class="btn btn-primary" onClick={handleAddBasis}>
                  + Add basis
                </button>
              </div>
            </div>
          )}

          <TableScrollHint scrollerRef={tableScrollerRef} testId="within-run-scroll-hint" />
          <section class="card" aria-label="Per-query values by basis">
            <div ref={tableScrollerRef} class="overflow-x-auto" data-testid="within-run-scroll-container">
              <table class="min-w-full w-max divide-y divide-[var(--bb-data-border)] text-sm">
                <thead class="bg-[var(--bb-surface-data-muted)]">
                  <tr>
                    <th scope="col" class="table-th sticky left-0 z-10 bg-[var(--bb-surface-data-muted)] text-left">Query</th>
                    {bases.map((basis) => (
                      <th key={encodeBasis(basis)} scope="col" class="table-th whitespace-nowrap text-right">
                        {formatWithinRunBasisLabel(basis, bases)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody class="divide-y divide-[var(--bb-data-border)] bg-[var(--bb-surface-data)]">
                  {grid.rows.map((row) => {
                    const refCell = row.cells[referenceIndex];
                    return (
                      <tr key={row.queryId} class="hover:bg-[var(--bb-surface-data-muted)]">
                        <td class="table-td sticky left-0 z-10 bg-[var(--bb-surface-data)] font-mono font-medium text-left">{row.queryId}</td>
                        {row.cells.map((cell, i) => {
                          const isRef = i === referenceIndex;
                          const { ratio, deltaMs } = calculateCellDelta(cell.ms, refCell?.ms ?? null);
                          const comparisonStatus = withinRunComparisonStatus(ratio);
                          return (
                            <td key={i} class="table-td font-mono text-right whitespace-nowrap">
                              {cell.ms !== null ? (
                                <div>
                                  <div class="font-medium text-[var(--bb-data-fg-primary)]">
                                    {formatLatencyMs(cell.ms).valueText}
                                  </div>
                                  {!isRef && ratio !== null && deltaMs !== null && (
                                    <div
                                      class={`text-xs ${
                                        comparisonStatus === "faster"
                                          ? "text-[var(--bb-tone-success-fg)] font-medium"
                                          : comparisonStatus === "slower"
                                            ? "text-[var(--bb-data-fg-muted)]"
                                            : "text-[var(--bb-data-fg-subtle)]"
                                      }`}
                                    >
                                      {formatRatio(ratio)} ({signedLatencyDeltaText(deltaMs)})
                                    </div>
                                  )}
                                  {!isRef && (ratio === null || deltaMs === null) && (
                                    <div class="text-xs text-[var(--bb-data-fg-subtle)]">no ref</div>
                                  )}
                                </div>
                              ) : (
                                <span
                                  class="text-[var(--bb-data-fg-subtle)]"
                                  data-testid={`${withinRunUnavailableKind(cell.unavailableReason)}-cell`}
                                  title={cell.unavailableReason ? basisUnavailableLabel(cell.unavailableReason) : undefined}
                                >
                                  {withinRunUnavailableKind(cell.unavailableReason)}
                                </span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot class="border-t-2 border-[var(--bb-data-border)] bg-[var(--bb-surface-data-muted)] font-semibold">
                  <tr>
                    <td class="table-td sticky left-0 z-10 bg-[var(--bb-surface-data-muted)] font-mono text-left whitespace-nowrap">
                      Geomean ({grid.sharedQueryIds.length} shared)
                    </td>
                    {columnGeomeans.map((g, i) => {
                      const isRef = i === referenceIndex;
                      const ratio =
                        hasSufficientAggregateEvidence && refGeomean !== null && g !== null && refGeomean > 0
                          ? g / refGeomean
                          : null;
                      const deltaMs =
                        hasSufficientAggregateEvidence && refGeomean !== null && g !== null
                          ? g - refGeomean
                          : null;
                      return (
                        <td key={i} class="table-td font-mono text-right whitespace-nowrap">
                          {g !== null ? (
                            <div>
                              <div>{fmtMs(g)}</div>
                              {!isRef && ratio !== null && deltaMs !== null && (
                                <div class="text-xs font-normal text-[var(--bb-data-fg-muted)]">
                                  {formatRatio(ratio)} ({signedLatencyDeltaText(deltaMs)})
                                </div>
                              )}
                            </div>
                          ) : (
                            "—"
                          )}
                        </td>
                      );
                    })}
                  </tr>
                </tfoot>
              </table>
            </div>
        </section>
        </>
      )}
    </div>
  );
}
