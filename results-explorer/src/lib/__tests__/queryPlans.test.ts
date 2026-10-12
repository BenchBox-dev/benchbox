import { describe, expect, it } from "vitest";
import { parseQueryPlanDocument } from "@/lib/queryPlans";

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
