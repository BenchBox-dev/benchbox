export interface ResultsNavSection {
  readonly id: "overview" | "benchmarks" | "platforms" | "compare" | "query";
  readonly label: string;
  readonly href: string;
  readonly isActivePath: (path: string) => boolean;
}

const RESERVED_RESULTS_SEGMENTS: ReadonlySet<string> = new Set([
  "benchmarks",
  "platforms",
  "compare",
  "query",
  "p",
  "r",
  "local",
]);

function firstSegment(path: string): string | null {
  const match = /^\/results\/([^/]+)\/?$/.exec(path);
  return match ? (match[1] ?? null) : null;
}

function isBenchmarkDetailPath(path: string): boolean {
  const segment = firstSegment(path);
  return segment !== null && !RESERVED_RESULTS_SEGMENTS.has(segment);
}

export const RESULTS_NAV_SECTIONS: readonly ResultsNavSection[] = [
  {
    id: "overview",
    label: "Overview",
    href: "/results/",
    isActivePath: (path) => path === "/results" || path === "/results/",
  },
  {
    id: "benchmarks",
    label: "Benchmarks",
    href: "/results/benchmarks/",
    isActivePath: (path) => path === "/results/benchmarks" || path === "/results/benchmarks/" || isBenchmarkDetailPath(path),
  },
  {
    id: "platforms",
    label: "Platforms",
    href: "/results/platforms/",
    isActivePath: (path) =>
      path === "/results/platforms" ||
      path === "/results/platforms/" ||
      path.startsWith("/results/p/"),
  },
  {
    id: "compare",
    label: "Compare",
    href: "/results/compare",
    isActivePath: (path) => path.startsWith("/results/compare"),
  },
  {
    id: "query",
    label: "Find runs",
    href: "/results/query",
    isActivePath: (path) => path.startsWith("/results/query"),
  },
];

export function activeResultsNavSection(path: string): ResultsNavSection | null {
  return RESULTS_NAV_SECTIONS.find((section) => section.isActivePath(path)) ?? null;
}

export function isLocalResultPath(path: string): boolean {
  return path === "/results/local" || path === "/results/local/" || path.startsWith("/results/local/");
}
