const LOCAL_RESULT_PATH = "/results/local";
const COMPARE_PATH = "/results/compare";

export function legacyEntryRedirect(search: string, hash: string): string | null {
  const params = new URLSearchParams(search);
  if (params.get("mode") === "local" || hash === "#local") return LOCAL_RESULT_PATH;
  const ids = params.get("ids");
  const chart = params.get("chart");
  if (!ids && !chart && params.get("compare") === null) return null;
  const target = new URLSearchParams();
  if (ids) target.set("ids", ids);
  if (chart) target.set("chart", chart);
  const query = target.toString();
  return query === "" ? COMPARE_PATH : `${COMPARE_PATH}?${query}`;
}
