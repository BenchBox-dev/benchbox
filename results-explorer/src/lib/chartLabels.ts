const MONOSPACE_ADVANCE_EM = 0.62;
const ELLIPSIS = "…";
const LABEL_PADDING_PX = 12;
const LABEL_COLUMN_MIN_PX = 48;
const LABEL_COLUMN_MAX_PX = 320;
const LABEL_COLUMN_MAX_SHARE = 0.4;

export function estimateTextWidth(text: string, fontSizePx: number): number {
  return Array.from(text).length * MONOSPACE_ADVANCE_EM * fontSizePx;
}

export function fitLabel(
  text: string,
  maxWidthPx: number,
  fontSizePx: number,
): { text: string; truncated: boolean } {
  if (estimateTextWidth(text, fontSizePx) <= maxWidthPx) return { text, truncated: false };
  const advancePx = MONOSPACE_ADVANCE_EM * fontSizePx;
  const visibleChars = Math.max(0, Math.floor(maxWidthPx / advancePx) - 1);
  return { text: `${Array.from(text).slice(0, visibleChars).join("")}${ELLIPSIS}`, truncated: true };
}

export function labelColumnWidth(labels: readonly string[], fontSizePx: number, chartWidthPx: number): number {
  const longest = labels.reduce((max, label) => Math.max(max, estimateTextWidth(label, fontSizePx)), 0);
  const upper = Math.min(LABEL_COLUMN_MAX_PX, LABEL_COLUMN_MAX_SHARE * chartWidthPx);
  return Math.min(Math.max(longest + LABEL_PADDING_PX, LABEL_COLUMN_MIN_PX), Math.max(upper, LABEL_COLUMN_MIN_PX));
}

export function fitLabelMiddle(
  text: string,
  maxWidthPx: number,
  fontSizePx: number,
): { text: string; truncated: boolean } {
  if (estimateTextWidth(text, fontSizePx) <= maxWidthPx) return { text, truncated: false };
  const chars = Array.from(text);
  const budget = Math.max(2, Math.floor(maxWidthPx / (MONOSPACE_ADVANCE_EM * fontSizePx)) - 1);
  const tail = Math.ceil(budget / 2);
  const head = budget - tail;
  return { text: `${chars.slice(0, head).join("")}${ELLIPSIS}${chars.slice(chars.length - tail).join("")}`, truncated: true };
}
