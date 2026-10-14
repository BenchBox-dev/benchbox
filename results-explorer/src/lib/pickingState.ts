import { createContext, createElement, type ComponentChildren } from "preact";
import { useContext, useEffect, useMemo, useRef, useState } from "preact/hooks";
import {
  getDetailResult,
  getExistingResultIds,
  resolveShortId,
} from "@/lib/duckdbQueries";
import { recoverCompareResults } from "@/lib/compareRecovery";
import { buildCompareUrl, MAX_COMPARE_SELECTIONS } from "@/lib/resultLinks";

const SPA_REDIRECT_KEY = "benchbox.results.redirect";
const PAGES_RESTORE_PICKING_KEY = `${SPA_REDIRECT_KEY}.picking`;

export interface PickingRecoveryDependencies {
  resolveId: typeof resolveShortId;
  findExistingIds: typeof getExistingResultIds;
  loadResult: (resolvedId: string) => Promise<unknown | null>;
}

export interface PickingRestoreOutcome {
  pickedIds: string[];
  missingIds: string[];
  failedIds: string[];
}

export interface PickingStateValue {
  pickedIds: readonly string[];
  compareHref: string | null;
  restoreNotice: string | null;
  restoring: boolean;
  pick: (id: string) => void;
  remove: (id: string) => void;
  toggle: (id: string) => void;
  replace: (ids: readonly string[]) => void;
  clear: () => void;
}

export interface PickingStateProviderProps {
  children: ComponentChildren;
  initialRestoreIds?: readonly string[];
  recoveryDependencies?: PickingRecoveryDependencies;
}

function normalizedPickingIds(ids: readonly string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const rawId of ids) {
    const id = rawId.trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    normalized.push(id);
    if (normalized.length === MAX_COMPARE_SELECTIONS) break;
  }
  return normalized;
}

function browserSessionStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function readPagesRestorePickingIds(
  storage: Storage | null = browserSessionStorage(),
): string[] {
  if (storage === null) return [];
  try {
    const restoredRoute = storage.getItem(SPA_REDIRECT_KEY);
    const serialized = storage.getItem(PAGES_RESTORE_PICKING_KEY);
    storage.removeItem(PAGES_RESTORE_PICKING_KEY);
    if (!restoredRoute?.startsWith("/results/") || serialized === null) {
      return [];
    }
    const parsed: unknown = JSON.parse(serialized);
    if (
      !Array.isArray(parsed) ||
      !parsed.every((id) => typeof id === "string")
    ) {
      return [];
    }
    return normalizedPickingIds(parsed);
  } catch {
    try {
      storage.removeItem(PAGES_RESTORE_PICKING_KEY);
    } catch {
    }
    return [];
  }
}

export function writePotentialPagesRestorePickingIds(
  ids: readonly string[],
  storage: Storage | null,
): void {
  if (storage === null) return;
  try {
    const normalized = normalizedPickingIds(ids);
    if (normalized.length === 0) storage.removeItem(PAGES_RESTORE_PICKING_KEY);
    else storage.setItem(PAGES_RESTORE_PICKING_KEY, JSON.stringify(normalized));
  } catch {
  }
}

export async function recoverPickingIds(
  ids: readonly string[],
  dependencies: PickingRecoveryDependencies,
  isCancelled?: () => boolean,
): Promise<PickingRestoreOutcome | null> {
  const recovery = await recoverCompareResults(
    [...ids],
    MAX_COMPARE_SELECTIONS,
    {
      ...dependencies,
      isCancelled,
    },
  );
  if (recovery === null) return null;
  return {
    pickedIds: recovery.recovered.map((entry) => entry.resolvedId),
    missingIds: recovery.missing.map((entry) => entry.requestedId),
    failedIds: recovery.failed.map((entry) => entry.requestedId),
  };
}

function restoreNotice(outcome: PickingRestoreOutcome): string | null {
  const notices: string[] = [];
  if (outcome.missingIds.length > 0) {
    notices.push(
      `${outcome.missingIds.length} picked ${outcome.missingIds.length === 1 ? "result is" : "results are"} no longer published.`,
    );
  }
  if (outcome.failedIds.length > 0) {
    notices.push(
      `${outcome.failedIds.length} picked ${outcome.failedIds.length === 1 ? "result could" : "results could"} not be restored because result data could not be loaded.`,
    );
  }
  return notices.length > 0 ? notices.join(" ") : null;
}

const capturedPagesRestoreIds = readPagesRestorePickingIds();
const PickingStateContext = createContext<PickingStateValue | null>(null);
const DEFAULT_RECOVERY_DEPENDENCIES: PickingRecoveryDependencies = {
  resolveId: resolveShortId,
  findExistingIds: getExistingResultIds,
  loadResult: getDetailResult,
};

export function PickingStateProvider({
  children,
  initialRestoreIds = capturedPagesRestoreIds,
  recoveryDependencies = DEFAULT_RECOVERY_DEPENDENCIES,
}: PickingStateProviderProps) {
  const restoreIdsRef = useRef<readonly string[] | null>(null);
  if (restoreIdsRef.current === null) {
    restoreIdsRef.current = normalizedPickingIds(initialRestoreIds);
  }
  const restoreIds = restoreIdsRef.current;
  const [pickedIds, setPickedIds] = useState<string[]>([]);
  const [restoring, setRestoring] = useState(restoreIds.length > 0);
  const [notice, setNotice] = useState<string | null>(null);
  const pickedIdsRef = useRef<readonly string[]>(pickedIds);
  pickedIdsRef.current = pickedIds;

  useEffect(() => {
    if (restoreIds.length === 0) return;
    let cancelled = false;
    recoverPickingIds(restoreIds, recoveryDependencies, () => cancelled)
      .then((outcome) => {
        if (cancelled || outcome === null) return;
        setPickedIds(outcome.pickedIds);
        setNotice(restoreNotice(outcome));
        setRestoring(false);
      })
      .catch(() => {
        if (cancelled) return;
        setNotice(
          `${restoreIds.length} picked ${restoreIds.length === 1 ? "result could" : "results could"} not be restored because result data could not be loaded.`,
        );
        setRestoring(false);
      });
    return () => {
      cancelled = true;
    };
  }, [recoveryDependencies, restoreIds]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const storage = browserSessionStorage();
    const preserveForFallback = () =>
      writePotentialPagesRestorePickingIds(pickedIdsRef.current, storage);
    window.addEventListener("pagehide", preserveForFallback);
    return () => window.removeEventListener("pagehide", preserveForFallback);
  }, []);

  const value = useMemo<PickingStateValue>(() => {
    const replace = (ids: readonly string[]) =>
      setPickedIds(normalizedPickingIds(ids));
    return {
      pickedIds,
      compareHref: pickedIds.length >= 2 ? buildCompareUrl(pickedIds) : null,
      restoreNotice: notice,
      restoring,
      pick: (id) =>
        setPickedIds((current) => normalizedPickingIds([...current, id])),
      remove: (id) =>
        setPickedIds((current) =>
          current.filter((pickedId) => pickedId !== id),
        ),
      toggle: (id) =>
        setPickedIds((current) =>
          current.includes(id)
            ? current.filter((pickedId) => pickedId !== id)
            : normalizedPickingIds([...current, id]),
        ),
      replace,
      clear: () => setPickedIds([]),
    };
  }, [notice, pickedIds, restoring]);

  return createElement(PickingStateContext.Provider, { value }, children);
}

export function usePickingState(): PickingStateValue {
  const state = useOptionalPickingState();
  if (state === null) {
    throw new Error("usePickingState must be used within PickingStateProvider");
  }
  return state;
}

export function useOptionalPickingState(): PickingStateValue | null {
  return useContext(PickingStateContext);
}
