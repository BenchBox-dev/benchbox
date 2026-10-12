import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import {
  usePickingState,
  PickingStateProvider,
  readPagesRestorePickingIds,
  recoverPickingIds,
  writePotentialPagesRestorePickingIds,
} from "@/lib/pickingState";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();
  get length() {
    return this.values.size;
  }
  clear() {
    this.values.clear();
  }
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

function PickingHarness({ routeName }: { routeName: string }) {
  const state = usePickingState();
  return (
    <div>
      <span>{routeName}</span>
      <span data-testid="picked">{state.pickedIds.join(",")}</span>
      <span data-testid="href">{state.compareHref ?? "none"}</span>
      <button type="button" onClick={() => state.pick("first")}>
        Pick first
      </button>
      <button type="button" onClick={() => state.pick("second")}>
        Pick second
      </button>
    </div>
  );
}

function CapacityHarness() {
  const state = usePickingState();
  return (
    <div>
      <span data-testid="capacity-picked">{state.pickedIds.join(",")}</span>
      <span data-testid="capacity-href">{state.compareHref ?? "none"}</span>
      <button type="button" onClick={() => state.replace(Array.from({ length: 12 }, (_, index) => `run-${index + 1}`))}>
        Pick twelve
      </button>
    </div>
  );
}

describe("PickingStateProvider", () => {
  it("caps shared picking state at ten unique results", () => {
    render(
      <PickingStateProvider>
        <CapacityHarness />
      </PickingStateProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Pick twelve" }));

    expect(screen.getByTestId("capacity-picked").textContent?.split(",")).toHaveLength(10);
    expect(screen.getByTestId("capacity-href")).toHaveTextContent(
      "/results/compare?ids=run-1,run-2,run-3,run-4,run-5,run-6,run-7,run-8,run-9,run-10",
    );
  });

  it("keeps picking state across client-side route renders and promotes through the compare URL builder", () => {
    const view = render(
      <PickingStateProvider>
        <PickingHarness routeName="benchmark" />
      </PickingStateProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Pick first" }));
    view.rerender(
      <PickingStateProvider>
        <PickingHarness routeName="platform" />
      </PickingStateProvider>,
    );
    expect(screen.getByText("platform")).toBeTruthy();
    expect(screen.getByTestId("picked")).toHaveTextContent("first");

    fireEvent.click(screen.getByRole("button", { name: "Pick second" }));
    expect(screen.getByTestId("href")).toHaveTextContent(
      "/results/compare?ids=first,second",
    );
  });

  it("restores only results that still resolve and distinguishes absence from load failure", async () => {
    render(
      <PickingStateProvider
        initialRestoreIds={["kept", "missing", "failed"]}
        recoveryDependencies={{
          resolveId: async (id) => id,
          findExistingIds: async () => new Set(["kept", "failed"]),
          loadResult: async (id) => {
            if (id === "failed") throw new Error("dataset unavailable");
            return id === "kept" ? { result_id: id } : null;
          },
        }}
      >
        <RestoreHarness />
      </PickingStateProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("restore-state")).toHaveTextContent("ready"),
    );
    expect(screen.getByTestId("picked")).toHaveTextContent("kept");
    expect(screen.getByTestId("notice")).toHaveTextContent(
      "1 picked result is no longer published",
    );
    expect(screen.getByTestId("notice")).toHaveTextContent(
      "1 picked result could not be restored",
    );
  });
});

function RestoreHarness() {
  const state = usePickingState();
  return (
    <div>
      <span data-testid="restore-state">
        {state.restoring ? "restoring" : "ready"}
      </span>
      <span data-testid="picked">{state.pickedIds.join(",")}</span>
      <span data-testid="notice">{state.restoreNotice}</span>
    </div>
  );
}

describe("Pages fallback picking hop", () => {
  it("falls back to memory when the browser denies sessionStorage access", () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      get() {
        throw new DOMException("Access denied", "SecurityError");
      },
    });

    try {
      expect(readPagesRestorePickingIds()).toEqual([]);
    } finally {
      if (descriptor) Object.defineProperty(window, "sessionStorage", descriptor);
      else Reflect.deleteProperty(window, "sessionStorage");
    }
  });

  it("discards a pending unload snapshot on a normal reload or reopened tab", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      "benchbox.results.redirect.picking",
      JSON.stringify(["first", "second"]),
    );

    expect(readPagesRestorePickingIds(storage)).toEqual([]);
    expect(storage.getItem("benchbox.results.redirect.picking")).toBeNull();
  });

  it("consumes a pending snapshot only when the Pages fallback recorded a Results route", () => {
    const storage = new MemoryStorage();
    writePotentialPagesRestorePickingIds(["first", "second"], storage);
    storage.setItem("benchbox.results.redirect", "/results/p/duckdb/");

    expect(readPagesRestorePickingIds(storage)).toEqual(["first", "second"]);
    expect(storage.getItem("benchbox.results.redirect.picking")).toBeNull();
  });
});

describe("recoverPickingIds", () => {
  it("returns canonical, currently published IDs only", async () => {
    await expect(
      recoverPickingIds(["alias", "gone"], {
        resolveId: async (id) => (id === "alias" ? "canonical" : id),
        findExistingIds: async () => new Set(["canonical"]),
        loadResult: async (id) => ({ result_id: id }),
      }),
    ).resolves.toEqual({
      pickedIds: ["canonical"],
      missingIds: ["gone"],
      failedIds: [],
    });
  });
});
