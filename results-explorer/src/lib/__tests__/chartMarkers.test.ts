import { describe, expect, it } from "vitest";
import {
  RUN_MARKER_RADIUS,
  RUN_MARKER_SHAPE_COUNT,
  RUN_MARKER_SHAPE_NAMES,
  runMarkerPath,
} from "@/lib/chartMarkers";

const MIN_RENDERED_EXTENT_PX = 10;
const MIN_PLUS_ARM_THICKNESS_PX = 3.5;

function extentOf(points: ReadonlyArray<readonly [number, number]>): { width: number; height: number } {
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  return { width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
}

function pathPoints(d: string): Array<readonly [number, number]> {
  const points: Array<readonly [number, number]> = [];
  for (const [, command, args] of d.matchAll(/([MLA])([^MLAZ]*)/g)) {
    if (args === undefined) continue;
    const values = args
      .split(/[^0-9.\-]+/)
      .filter((token) => token !== "")
      .map(Number);
    if (command === "A") {
      if (values.length >= 2) {
        points.push([values[values.length - 2]!, values[values.length - 1]!] as const);
      }
      continue;
    }
    for (let index = 0; index + 1 < values.length; index += 2) {
      points.push([values[index]!, values[index + 1]!] as const);
    }
  }
  return points;
}

describe("chartMarkers", () => {
  it("defines one shape per comparable run", () => {
    expect(RUN_MARKER_SHAPE_COUNT).toBe(10);
    expect(new Set(RUN_MARKER_SHAPE_NAMES).size).toBe(RUN_MARKER_SHAPE_COUNT);
  });

  it("keeps every shape within its radius", () => {
    for (let shape = 0; shape < RUN_MARKER_SHAPE_COUNT; shape += 1) {
      const points = pathPoints(runMarkerPath(shape, 50, 60, 8));
      expect(points.length).toBeGreaterThan(0);
      for (const [x, y] of points) {
        expect(Math.hypot(x - 50, y - 60)).toBeLessThanOrEqual(8.02);
      }
    }
  });

  it("renders every shape at least 10 px across at the query histogram radius", () => {
    for (let shape = 0; shape < RUN_MARKER_SHAPE_COUNT; shape += 1) {
      const { width, height } = extentOf(pathPoints(runMarkerPath(shape, 0, 0, RUN_MARKER_RADIUS)));
      expect(Math.min(width, height), RUN_MARKER_SHAPE_NAMES[shape]).toBeGreaterThanOrEqual(MIN_RENDERED_EXTENT_PX);
    }
  });

  it("keeps plus and cross arms at least 3.5 px thick at the query histogram radius", () => {
    const armAngles: Record<"plus" | "cross", number> = { plus: 0, cross: Math.PI / 4 };
    for (const shape of ["plus", "cross"] as const) {
      const index = RUN_MARKER_SHAPE_NAMES.indexOf(shape);
      const angle = armAngles[shape];
      const projections = [
        ...new Set(
          pathPoints(runMarkerPath(index, 0, 0, RUN_MARKER_RADIUS)).map(([x, y]) =>
            (x * Math.cos(angle) + y * Math.sin(angle)).toFixed(4),
          ),
        ),
      ]
        .map(Number)
        .sort((a, b) => a - b);
      expect(projections[2]! - projections[1]!, shape).toBeGreaterThanOrEqual(MIN_PLUS_ARM_THICKNESS_PX);
    }
  });

  it("wraps out-of-range indexes deterministically", () => {
    expect(runMarkerPath(RUN_MARKER_SHAPE_COUNT, 0, 0, 4)).toBe(runMarkerPath(0, 0, 0, 4));
    expect(runMarkerPath(-1, 0, 0, 4)).toBe(runMarkerPath(RUN_MARKER_SHAPE_COUNT - 1, 0, 0, 4));
  });
});
