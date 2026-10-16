import { useEffect, useRef, useState } from "preact/hooks";
import { route } from "preact-router";
import { useLocalResultState } from "@/lib/localResultState";
import { errMsg } from "@/utils";

interface LocalResultPickerProps {
  label?: string;
  className?: string;
  autoFocus?: boolean;
}

export function LocalResultPicker({
  label = "Open local result",
  className = "btn btn-secondary",
  autoFocus = false,
}: LocalResultPickerProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { importFile } = useLocalResultState();

  useEffect(() => {
    if (autoFocus) buttonRef.current?.focus();
  }, [autoFocus]);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setLoading(true);
    try {
      const preview = await importFile(file);
      route(`/results/local/${encodeURIComponent(preview.detail.result_id)}`);
    } catch (reason: unknown) {
      setError(errMsg(reason));
    } finally {
      setLoading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <span class="relative inline-flex flex-col items-start gap-1">
      <input
        ref={inputRef}
        type="file"
        accept=".json,application/json"
        aria-label="BenchBox result JSON file"
        aria-hidden="true"
        tabIndex={-1}
        class="sr-only"
        data-testid="local-result-file-input"
        onChange={(event) => void handleFile(event.currentTarget.files?.[0])}
      />
      <button
        ref={buttonRef}
        type="button"
        class={className}
        disabled={loading}
        aria-busy={loading}
        onClick={() => inputRef.current?.click()}
      >
        {loading ? "Opening…" : label}
      </button>
      {error && <span role="alert" class="max-w-xs text-xs text-[var(--bb-tone-danger-fg)]">{error}</span>}
    </span>
  );
}
