import { useEffect, useRef, useState } from "preact/hooks";

interface TuningSettingsDisclosureProps {
  bundleUrl: string;
}

export function extractTuningBlock(bundle: Record<string, unknown>): Record<string, unknown> {
  const platform = bundle?.platform;
  if (platform && typeof platform === "object" && !Array.isArray(platform)) {
    const tuning = (platform as Record<string, unknown>).tuning;
    if (tuning && typeof tuning === "object" && !Array.isArray(tuning)) {
      return tuning as Record<string, unknown>;
    }
  }
  return {};
}

export function TuningSettingsDisclosure({ bundleUrl }: TuningSettingsDisclosureProps) {
  const [expanded, setExpanded] = useState(false);
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setExpanded(false);
    setSettings(null);
    setLoading(false);
    setError(null);
    return () => abortRef.current?.abort();
  }, [bundleUrl]);

  function toggle() {
    const willExpand = !expanded;
    setExpanded(willExpand);
    if (!willExpand || settings !== null || loading) return;
    setLoading(true);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    fetch(bundleUrl, { signal: controller.signal })
      .then((response) => response.json() as Promise<Record<string, unknown>>)
      .then((bundle) => {
        if (controller.signal.aborted) return;
        setSettings(extractTuningBlock(bundle));
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError("Could not load tuning settings.");
        setLoading(false);
      });
  }

  return (
    <div class="rounded-lg panel-muted px-4 py-3" data-testid="tuning-settings-disclosure">
      <div class="text-xs font-semibold uppercase tracking-wide text-[var(--bb-data-fg-subtle)]">Tuning settings</div>
      <button
        type="button"
        class="mt-1 cursor-pointer border-0 bg-transparent p-0 text-sm text-[var(--bb-accent-hover)] underline hover:text-[var(--bb-accent)]"
        onClick={toggle}
        aria-expanded={expanded}
        aria-controls="tuning-settings-region"
      >
        {expanded ? "Hide settings ↑" : "Show settings ↓"}
      </button>
      {expanded && (
        <div id="tuning-settings-region" class="mt-2" role="region" aria-label="Tuning settings">
          {loading && (
            <p role="status" aria-live="polite" class="text-xs text-[var(--bb-data-fg-subtle)]">
              Loading tuning settings...
            </p>
          )}
          {error && (
            <p role="alert" class="text-xs text-[var(--bb-tone-danger-fg)]">
              {error}
            </p>
          )}
          {settings && (
            <pre class="max-h-80 overflow-auto rounded panel-muted p-2 text-xs text-[var(--bb-data-fg-primary)]">
              {JSON.stringify(settings, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
