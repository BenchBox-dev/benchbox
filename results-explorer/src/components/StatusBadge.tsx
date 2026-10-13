import type { ComponentChildren } from "preact";

export type StatusTone = "info" | "success" | "warning" | "danger" | "neutral";
export type StatusRole =
  | "trust"
  | "funding"
  | "validation"
  | "visibility"
  | "computed"
  | "comparison"
  | "ranking"
  | "override"
  | "generic";

interface StatusBadgeProps {
  role?: StatusRole;
  tone?: StatusTone;
  title?: string;
  ariaLabel?: string;
  leading?: ComponentChildren;
  class?: string;
  children: ComponentChildren;
}

const TONE_CLASS: Record<StatusTone, string> = {
  info: "tone-info",
  success: "tone-success",
  warning: "tone-warning",
  danger: "tone-danger",
  neutral: "tone-neutral",
};

export function StatusBadge({
  role = "generic",
  tone = "neutral",
  title,
  ariaLabel,
  leading,
  class: extraClass = "",
  children,
}: StatusBadgeProps) {
  return (
    <span
      class={`badge ${TONE_CLASS[tone]} ${extraClass}`}
      data-role={role}
      data-tone={tone}
      title={title}
      aria-label={ariaLabel}
    >
      {leading ? <span class="mr-1">{leading}</span> : null}
      {children}
    </span>
  );
}

export default StatusBadge;
