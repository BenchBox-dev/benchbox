import { describe, expect, it } from "vitest";
import { normalizeQueryIdForMatch, parseQueryPlanDocument, plansForQuery } from "@/lib/queryPlans";

describe("parseQueryPlanDocument", () => {
  it("reads the exported QueryPlanDAG shape as an operator tree", () => {
    const parsed = parseQueryPlanDocument({
      version: "1.0",
      capture_failures: 1,
      truncated: true,
      queries: {
        Q1: {
          plan_format: "dag",
          plan: {
            query_id: "Q1",
            platform: "duckdb",
            estimated_rows: 42,
            estimated_cost: 3.5,
            plan_fingerprint: "abc123",
            logical_root: {
              operator_type: "Join",
              join_type: "inner",
              join_conditions: ["l_orderkey = o_orderkey"],
              children: [
                {
                  operator_type: "Scan",
                  table_name: "lineitem",
                  physical_operator: { operator_type: "TABLE_SCAN" },
                  children: [],
                },
              ],
            },
            raw_explain_output: "HASH_JOIN\n  TABLE_SCAN lineitem",
          },
        },
      },
    });

    expect(parsed.captureFailures).toBe(1);
    expect(parsed.truncated).toBe(true);
    expect(parsed.plans).toHaveLength(1);
    expect(parsed.plans[0]).toMatchObject({
      label: "Q1",
      platform: "duckdb",
      estimatedRows: "42",
      estimatedCost: "3.5",
      fingerprint: "abc123",
      textualPlan: "HASH_JOIN\n  TABLE_SCAN lineitem",
    });
    expect(parsed.plans[0]?.root).toMatchObject({
      operator: "Join",
      details: ["Join: inner", "Join conditions: [\"l_orderkey = o_orderkey\"]"],
      children: [{
        operator: "Scan",
        details: ["Table: lineitem", "Physical operator: TABLE_SCAN"],
      }],
    });
  });

  it("accepts the query list and text plan shape used by the pipeline fixture", () => {
    const parsed = parseQueryPlanDocument({
      queries: [{ query_id: "Q1", plan: "SCAN tpch.lineitem" }],
    });

    expect(parsed.plans).toEqual([
      expect.objectContaining({ id: "Q1", label: "Q1", root: null, textualPlan: "SCAN tpch.lineitem" }),
    ]);
  });

  it("shows unknown plan objects as text and bounds the operator tree", () => {
    const parsed = parseQueryPlanDocument({
      queries: {
        malformed: { plan: { unexpected: true } },
        Q2: {
          plan: {
            logical_root: {
              operator_type: "Root",
              children: Array.from({ length: 550 }, (_, index) => ({
                operator_type: `Scan ${index}`,
                children: [],
              })),
            },
          },
        },
      },
    });

    expect(parsed.plans).toHaveLength(2);
    expect(parsed.plans[0]).toMatchObject({ textualPlan: '{"unexpected":true}' });
    expect(parsed.plans[1]?.root?.children).toHaveLength(499);
    expect(parsed.plans[1]?.root?.omittedChildren).toBe(51);
  });
});

describe("normalizeQueryIdForMatch", () => {
  it("treats Q-prefixed and bare numeric query ids as the same query", () => {
    expect(normalizeQueryIdForMatch("Q18")).toBe(normalizeQueryIdForMatch("18"));
    expect(normalizeQueryIdForMatch("q18")).toBe("18");
    expect(normalizeQueryIdForMatch("Q018")).toBe("18");
    expect(normalizeQueryIdForMatch(" 18 ")).toBe("18");
  });

  it("drops an iteration or stream suffix and keeps named query ids", () => {
    expect(normalizeQueryIdForMatch("Q18#2")).toBe("18");
    expect(normalizeQueryIdForMatch("Aggregation_Distinct_GroupBy")).toBe("aggregation_distinct_groupby");
  });
});

describe("plansForQuery", () => {
  it("returns only the plans whose query id matches, in either id style", () => {
    const document = parseQueryPlanDocument({
      queries: {
        "Q18#1": { plan: { query_id: "Q18", logical_root: { operator_type: "Aggregate", children: [] } } },
        "19": { plan: { query_id: "19", logical_root: { operator_type: "Filter", children: [] } } },
        Q2: { plan: { query_id: "Q2", logical_root: { operator_type: "Scan", children: [] } } },
      },
    });

    expect(document.plans.map((plan) => plan.queryId)).toEqual(["19", "Q18", "Q2"]);
    expect(plansForQuery(document.plans, "18").map((plan) => plan.id)).toEqual(["Q18#1"]);
    expect(plansForQuery(document.plans, "Q19").map((plan) => plan.id)).toEqual(["19"]);
    expect(plansForQuery(document.plans, "7")).toEqual([]);
  });
});
