import { useEffect, useRef, useState } from "preact/hooks";
import type { CapturedQueryPlan, QueryPlanDocument, QueryPlanNode } from "@/lib/queryPlans";
import { MAX_PLAN_SIDECAR_BYTES, parseQueryPlanDocument, plansForQuery } from "@/lib/queryPlans";

interface CapturedPlanViewerProps {
  plansUrl: string | null;
  queryId?: string;
}

type LoadStatus = "idle" | "loading" | "ready" | "unavailable";

export function CapturedPlanViewer({ plansUrl, queryId }: CapturedPlanViewerProps) {
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState<LoadStatus>("idle");
  const [document, setDocument] = useState<QueryPlanDocument | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  useEffect(() => {
    requestRef.current?.abort();
    setExpanded(false);
    setStatus("idle");
    setDocument(null);
    setSelectedId("");
  }, [plansUrl]);

  async function loadPlans() {
    if (plansUrl === null || status !== "idle") return;

    setStatus("loading");
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const response = await fetch(plansUrl, { signal: controller.signal });
      if (!response.ok) throw new Error("Plan sidecar request failed");
      const contentLength = response.headers?.get("content-length");
      if (contentLength !== null && contentLength !== undefined) {
        const normalizedLength = contentLength.trim();
        const declaredBytes = Number(normalizedLength);
        if (
          !/^\d+$/.test(normalizedLength) ||
          !Number.isSafeInteger(declaredBytes) ||
          declaredBytes > MAX_PLAN_SIDECAR_BYTES
        ) {
          await response.body?.cancel();
          throw new Error("Plan sidecar exceeds the viewer limit");
        }
      }
      const text = await readCappedSidecarText(response);
      const parsed = parseQueryPlanDocument(JSON.parse(text) as unknown);
      if (parsed.plans.length === 0) {
        setStatus("unavailable");
        return;
      }
      setDocument(parsed);
      setSelectedId(parsed.plans[0]?.id ?? "");
      setStatus("ready");
    } catch {
      if (!controller.signal.aborted) setStatus("unavailable");
    }
  }

  function toggleExpanded() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (status === "idle") void loadPlans();
  }

  const visiblePlans = document === null ? [] : queryId === undefined ? document.plans : plansForQuery(document.plans, queryId);
  const selectedPlan = visiblePlans.find((plan) => plan.id === selectedId) ?? visiblePlans[0] ?? null;

  return (
    <section id="execution-plans" class="card" aria-label="Execution plans">
      <h2 class="mb-2 text-base font-semibold text-[var(--bb-data-fg-primary)]">Execution plans</h2>
      {plansUrl === null ? (
        <p class="text-sm text-[var(--bb-data-fg-muted)]">
          Execution plans were recorded, but no published plan file is available for this result.
        </p>
      ) : (
        <>
          <p class="mb-3 text-sm text-[var(--bb-data-fg-muted)]">
            View captured plans by query. Plan details are shown as text from the published sidecar.
          </p>
          <button
            type="button"
            class="btn btn-secondary"
            aria-expanded={expanded}
            aria-controls="captured-query-plans"
            onClick={toggleExpanded}
          >
            {expanded ? "Hide query plans" : "View query plans"}
          </button>
          {expanded && (
            <div id="captured-query-plans" class="mt-4" role="region" aria-label="Captured query plans">
              {status === "loading" && <p role="status">Loading captured query plans...</p>}
              {status === "unavailable" && (
                <p role="alert" class="text-sm text-[var(--bb-tone-danger-fg)]">
                  Published query plans are unavailable. Use Download plans to get the sidecar file.
                </p>
              )}
              {status === "ready" && document && selectedPlan && (
                <PlanDetail document={document} plans={visiblePlans} plan={selectedPlan} onSelect={setSelectedId} />
              )}
              {status === "ready" && queryId !== undefined && selectedPlan === null && (
                <p role="status" class="text-sm text-[var(--bb-data-fg-muted)]">
                  {`No captured plan for query ${queryId.trim()} in this run.`}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

async function readCappedSidecarText(response: Response): Promise<string> {
  const body = response.body;
  if (!body || typeof body.getReader !== "function") {
    const text = await response.text();
    if (new TextEncoder().encode(text).byteLength > MAX_PLAN_SIDECAR_BYTES) {
      throw new Error("Plan sidecar exceeds the viewer limit");
    }
    return text;
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const chunks: string[] = [];
  let bytes = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_PLAN_SIDECAR_BYTES) {
        await reader.cancel();
        throw new Error("Plan sidecar exceeds the viewer limit");
      }
      chunks.push(decoder.decode(value, { stream: true }));
    }
  } finally {
    reader.releaseLock();
  }
  chunks.push(decoder.decode());
  return chunks.join("");
}

function PlanDetail({
  document,
  plans,
  plan,
  onSelect,
}: {
  document: QueryPlanDocument;
  plans: CapturedQueryPlan[];
  plan: CapturedQueryPlan;
  onSelect: (id: string) => void;
}) {
  return (
    <div>
      {plans.length > 1 && (
        <label class="mb-3 block text-sm font-medium text-[var(--bb-data-fg-primary)]">
          Query
          <select
            class="input mt-1 block w-full max-w-sm"
            value={plan.id}
            onChange={(event) => onSelect((event.currentTarget as HTMLSelectElement).value)}
          >
            {plans.map((queryPlan) => (
              <option key={queryPlan.id} value={queryPlan.id}>
                {queryPlan.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {document.captureFailures > 0 && (
        <p class="mb-3 text-sm text-[var(--bb-tone-warning-fg)]">
          {document.captureFailures} query plan{document.captureFailures === 1 ? " was" : "s were"} not captured.
        </p>
      )}
      {document.truncated && (
        <p class="mb-3 text-sm text-[var(--bb-data-fg-muted)]">The published plan sidecar is marked as truncated.</p>
      )}
      <div class="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--bb-data-fg-muted)]">
        {plan.platform && <span>Platform: {plan.platform}</span>}
        {plan.estimatedRows && <span>Estimated rows: {plan.estimatedRows}</span>}
        {plan.estimatedCost && <span>Estimated cost: {plan.estimatedCost}</span>}
        {plan.fingerprint && <span>Plan fingerprint: {plan.fingerprint}</span>}
      </div>
      <section
        aria-label={`Query plan for ${plan.label}`}
        class="max-w-full overflow-x-auto rounded-lg border border-[var(--bb-data-border)] p-3"
      >
        {plan.root ? (
          <ul class="space-y-2" aria-label="Operator tree">
            <PlanNodeView node={plan.root} path="root" />
          </ul>
        ) : plan.textualPlan ? (
          <pre class="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words text-xs text-[var(--bb-data-fg-primary)]">
            {plan.textualPlan}
            {plan.textualPlanTruncated ? "\n[Plan text truncated by the viewer]" : ""}
          </pre>
        ) : null}
        {plan.root && plan.textualPlan && (
          <details class="mt-3 border-t border-[var(--bb-data-border)] pt-2">
            <summary class="cursor-pointer text-sm font-medium">Raw plan output</summary>
            <pre class="mt-2 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words text-xs text-[var(--bb-data-fg-primary)]">
              {plan.textualPlan}
              {plan.textualPlanTruncated ? "\n[Plan text truncated by the viewer]" : ""}
            </pre>
          </details>
        )}
      </section>
    </div>
  );
}

function PlanNodeView({ node, path }: { node: QueryPlanNode; path: string }) {
  return (
    <li class="border-l border-[var(--bb-data-border-strong)] pl-3" data-testid="captured-plan-node">
      <div class="font-medium break-words text-[var(--bb-data-fg-primary)]">{node.operator}</div>
      {node.details.length > 0 && (
        <ul class="mt-1 space-y-1 text-xs text-[var(--bb-data-fg-muted)]">
          {node.details.map((detail, index) => (
            <li key={`${path}-detail-${index}`} class="break-words">
              {detail}
            </li>
          ))}
        </ul>
      )}
      {node.truncatedAtDepth !== null && (
        <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">Plan data was truncated at depth {node.truncatedAtDepth}.</p>
      )}
      {node.children.length > 0 && (
        <ul class="mt-2 space-y-2">
          {node.children.map((child, index) => (
            <PlanNodeView key={`${path}-${index}`} node={child} path={`${path}-${index}`} />
          ))}
        </ul>
      )}
      {node.omittedChildren > 0 && (
        <p class="mt-1 text-xs text-[var(--bb-data-fg-muted)]">{node.omittedChildren} child operator(s) omitted.</p>
      )}
    </li>
  );
}
