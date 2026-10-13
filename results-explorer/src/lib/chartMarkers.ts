export const RUN_MARKER_SHAPE_NAMES = [
  "circle",
  "square",
  "triangle",
  "diamond",
  "triangle-down",
  "pentagon",
  "hexagon",
  "star",
  "plus",
  "cross",
] as const;

export const RUN_MARKER_SHAPE_COUNT = RUN_MARKER_SHAPE_NAMES.length;

export const RUN_MARKER_RADIUS = 7.5;

export const MISSING_MARKER_RADIUS = 4.5;

const PLUS_THICKNESS_TO_ARM_RATIO = 0.4;

function polygonPath(points: Array<readonly [number, number]>): string {
  return `M${points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" L")} Z`;
}

function regularPolygon(
  cx: number,
  cy: number,
  r: number,
  sides: number,
  rotationRadians: number,
): string {
  const points: Array<readonly [number, number]> = [];
  for (let i = 0; i < sides; i += 1) {
    const angle = rotationRadians + (i * 2 * Math.PI) / sides;
    points.push([cx + r * Math.cos(angle), cy + r * Math.sin(angle)]);
  }
  return polygonPath(points);
}

function starPath(cx: number, cy: number, r: number): string {
  const inner = r * 0.45;
  const points: Array<readonly [number, number]> = [];
  for (let i = 0; i < 10; i += 1) {
    const radius = i % 2 === 0 ? r : inner;
    const angle = -Math.PI / 2 + (i * Math.PI) / 5;
    points.push([cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]);
  }
  return polygonPath(points);
}

function plusPath(cx: number, cy: number, r: number, rotationRadians: number): string {
  const arm = r / Math.hypot(1, PLUS_THICKNESS_TO_ARM_RATIO);
  const w = arm * PLUS_THICKNESS_TO_ARM_RATIO;
  const corners: Array<readonly [number, number]> = [
    [-w, -arm],
    [w, -arm],
    [w, -w],
    [arm, -w],
    [arm, w],
    [w, w],
    [w, arm],
    [-w, arm],
    [-w, w],
    [-arm, w],
    [-arm, -w],
    [-w, -w],
  ];
  const cos = Math.cos(rotationRadians);
  const sin = Math.sin(rotationRadians);
  return polygonPath(
    corners.map(([x, y]) => [cx + x * cos - y * sin, cy + x * sin + y * cos] as const),
  );
}

function circlePath(cx: number, cy: number, r: number): string {
  const f = (value: number): string => value.toFixed(2);
  return (
    `M${f(cx + r)},${f(cy)} ` +
    `A${f(r)},${f(r)} 0 0,1 ${f(cx)},${f(cy + r)} ` +
    `A${f(r)},${f(r)} 0 0,1 ${f(cx - r)},${f(cy)} ` +
    `A${f(r)},${f(r)} 0 0,1 ${f(cx)},${f(cy - r)} ` +
    `A${f(r)},${f(r)} 0 0,1 ${f(cx + r)},${f(cy)} Z`
  );
}

export function runMarkerPath(shapeIndex: number, cx: number, cy: number, r: number): string {
  const shape = ((shapeIndex % RUN_MARKER_SHAPE_COUNT) + RUN_MARKER_SHAPE_COUNT) % RUN_MARKER_SHAPE_COUNT;
  switch (RUN_MARKER_SHAPE_NAMES[shape]) {
    case "square":
      return regularPolygon(cx, cy, r, 4, Math.PI / 4);
    case "triangle":
      return regularPolygon(cx, cy, r, 3, -Math.PI / 2);
    case "diamond":
      return regularPolygon(cx, cy, r, 4, 0);
    case "triangle-down":
      return regularPolygon(cx, cy, r, 3, Math.PI / 2);
    case "pentagon":
      return regularPolygon(cx, cy, r, 5, -Math.PI / 2);
    case "hexagon":
      return regularPolygon(cx, cy, r, 6, Math.PI / 6);
    case "star":
      return starPath(cx, cy, r);
    case "plus":
      return plusPath(cx, cy, r, 0);
    case "cross":
      return plusPath(cx, cy, r, Math.PI / 4);
    case "circle":
    default:
      return circlePath(cx, cy, r);
  }
}
