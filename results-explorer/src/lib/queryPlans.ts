export interface QueryPlanNode {
  operator: string;
  details: string[];
  children: QueryPlanNode[];
  truncatedAtDepth: number | null;
  omittedChildren: number;
}

export interface CapturedQueryPlan {
  id: string;
  label: string;
  queryId: string;
  platform: string | null;
  estimatedCost: string | null;
  estimatedRows: string | null;
  fingerprint: string | null;
  root: QueryPlanNode | null;
  textualPlan: string | null;
  textualPlanTruncated: boolean;
}

export interface QueryPlanDocument {
  plans: CapturedQueryPlan[];
  captureFailures: number;
  truncated: boolean;
}

export const MAX_PLAN_SIDECAR_BYTES = 2_000_000;

const MAX_PLAN_COUNT = 500;
const MAX_PLAN_NODES = 500;
const MAX_PLAN_DEPTH = 60;
const MAX_DISPLAY_VALUE_CHARS = 2_000;
const MAX_PLAN_TEXT_CHARS = 20_000;

type JsonObject = Record<string, unknown>;

interface NodeBudget {
  remaining: number;
}

function asObject(value: unknown): JsonObject | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value as JsonObject;
}

function asText(value: unknown): string | null {
  if (typeof value === "string") return value.length > 0 ? value : null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  return null;
}

function displayValue(value: unknown): string | null {
  const text = asText(value);
  if (text !== null) return text.slice(0, MAX_DISPLAY_VALUE_CHARS);
  if (!Array.isArray(value) && asObject(value) === null) return null;
  try {
    const serialized = JSON.stringify(value);
    return serialized ? serialized.slice(0, MAX_DISPLAY_VALUE_CHARS) : null;
  } catch {
    return null;
  }
}

function planFallbackText(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return null;
  try {
    const serialized = JSON.stringify(value);
    return serialized ?? null;
  } catch {
    return null;
  }
}

function detailLines(node: JsonObject): string[] {
  const fields: Array<[string, unknown]> = [
    ["Table", node.table_name],
    ["Join", node.join_type],
    ["Join conditions", node.join_conditions],
    ["Filters", node.filter_expressions],
    ["Aggregations", node.aggregation_functions],
    ["Group by", node.group_by_keys],
    ["Sort keys", node.sort_keys],
    ["Projection", node.projection_expressions],
    ["Limit", node.limit_count],
    ["Offset", node.offset_count],
  ];
  const physical = asObject(node.physical_operator);
  if (physical) fields.push(["Physical operator", physical.operator_type]);

  return fields.flatMap(([label, value]) => {
    const text = displayValue(value);
    return text === null ? [] : [`${label}: ${text}`];
  });
}

function parseNode(value: unknown, budget: NodeBudget, depth = 0): QueryPlanNode | null {
  const node = asObject(value);
  if (!node || budget.remaining <= 0 || depth > MAX_PLAN_DEPTH) return null;

  budget.remaining -= 1;
  const children = Array.isArray(node.children) ? node.children : [];
  const parsedChildren: QueryPlanNode[] = [];
  for (const child of children) {
    if (budget.remaining <= 0 || depth >= MAX_PLAN_DEPTH) break;
    const parsed = parseNode(child, budget, depth + 1);
    if (parsed) parsedChildren.push(parsed);
  }

  const marker = node.truncated_at_depth;
  const omitted = node.children_omitted;
  const omittedChildren =
    typeof omitted === "number" && Number.isFinite(omitted) && omitted > 0
      ? Math.floor(omitted) + Math.max(0, children.length - parsedChildren.length)
      : Math.max(0, children.length - parsedChildren.length);

  return {
    operator: asText(node.operator_type)?.slice(0, MAX_DISPLAY_VALUE_CHARS) ?? "Unknown operator",
    details: detailLines(node),
    children: parsedChildren,
    truncatedAtDepth: typeof marker === "number" && Number.isFinite(marker) ? marker : null,
    omittedChildren,
  };
}

function queryEntries(rawQueries: unknown): Array<[string, unknown]> {
  if (Array.isArray(rawQueries)) {
    return rawQueries.slice(0, MAX_PLAN_COUNT).map((entry, index) => {
      const record = asObject(entry);
      return [displayValue(record?.query_id) ?? `Query ${index + 1}`, entry];
    });
  }
  const queryMap = asObject(rawQueries);
  return queryMap
    ? Object.entries(queryMap)
        .slice(0, MAX_PLAN_COUNT)
        .map(([id, value]) => [id.slice(0, MAX_DISPLAY_VALUE_CHARS), value])
    : [];
}

function textualPlan(plan: unknown, planRecord: JsonObject | null): { text: string | null; truncated: boolean } {
  const rawOutput = asText(planRecord?.raw_explain_output);
  let text = rawOutput;
  if (text === null && (typeof plan === "string" || typeof plan === "number" || typeof plan === "boolean")) {
    text = planFallbackText(plan);
  }
  if (text === null && plan !== null && planRecord && !asObject(planRecord.logical_root)) {
    text = planFallbackText(plan);
  }
  if (text === null) return { text: null, truncated: false };
  return {
    text: text.slice(0, MAX_PLAN_TEXT_CHARS),
    truncated: text.length > MAX_PLAN_TEXT_CHARS,
  };
}

function parseEntry(id: string, value: unknown): CapturedQueryPlan | null {
  const entry = asObject(value);
  const plan = entry && "plan" in entry ? entry.plan : value;
  const planRecord = asObject(plan);
  const root = planRecord ? parseNode(planRecord.logical_root, { remaining: MAX_PLAN_NODES }) : null;
  const text = textualPlan(plan, planRecord);
  if (!root && text.text === null) return null;

  const queryId = displayValue(planRecord?.query_id) ?? displayValue(entry?.query_id) ?? id;
  const label = id === queryId || id.startsWith(`${queryId}#`) ? id : `${queryId} (${id})`;
  return {
    id,
    label,
    queryId,
    platform: displayValue(planRecord?.platform),
    estimatedCost: displayValue(planRecord?.estimated_cost),
    estimatedRows: displayValue(planRecord?.estimated_rows),
    fingerprint: displayValue(planRecord?.plan_fingerprint),
    root,
    textualPlan: text.text,
    textualPlanTruncated: text.truncated,
  };
}

export function normalizeQueryIdForMatch(queryId: string): string {
  const base = queryId.trim().split("#")[0]?.trim() ?? "";
  const numeric = /^q?0*(\d+)$/i.exec(base);
  return numeric ? numeric[1]! : base.toLowerCase();
}

export function plansForQuery(plans: readonly CapturedQueryPlan[], queryId: string): CapturedQueryPlan[] {
  const wanted = normalizeQueryIdForMatch(queryId);
  return plans.filter((plan) => normalizeQueryIdForMatch(plan.queryId) === wanted);
}

export function parseQueryPlanDocument(payload: unknown): QueryPlanDocument {
  const document = asObject(payload);
  if (!document) return { plans: [], captureFailures: 0, truncated: false };

  const plans = queryEntries(document.queries)
    .map(([id, value]) => parseEntry(id, value))
    .filter((plan): plan is CapturedQueryPlan => plan !== null);
  const captureFailures = document.capture_failures;

  return {
    plans,
    captureFailures:
      typeof captureFailures === "number" && Number.isFinite(captureFailures) && captureFailures > 0
        ? Math.floor(captureFailures)
        : 0,
    truncated: document.truncated === true,
  };
}
