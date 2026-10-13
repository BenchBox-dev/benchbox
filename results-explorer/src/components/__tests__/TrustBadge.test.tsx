import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { OverrideBadge, TrustBadge, ValidationBadge } from "@/components/TrustBadge";

describe("TrustBadge", () => {

  it("maintainer-run renders with success tone", () => {
    const { container } = render(<TrustBadge trustLabel="maintainer-run" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("success");
    expect(badge?.className).toContain("tone-success");
    expect(badge?.textContent).toBe("Maintainer");
  });

  it("community-submission renders with info tone", () => {
    const { container } = render(<TrustBadge trustLabel="community-submission" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("info");
    expect(badge?.className).toContain("tone-info");
    expect(badge?.textContent).toBe("Community");
  });

  it("ci-verified renders with neutral tone", () => {
    const { container } = render(<TrustBadge trustLabel="ci-verified" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("neutral");
    expect(badge?.className).toContain("tone-neutral");
  });

  it("ci-validated (legacy label) renders with neutral tone", () => {
    const { container } = render(<TrustBadge trustLabel="ci-validated" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("neutral");
  });

  it("local-run renders with neutral tone", () => {
    const { container } = render(<TrustBadge trustLabel="local-run" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("neutral");
  });

  it("compact=true shows first word only for maintainer-run", () => {
    render(<TrustBadge trustLabel="maintainer-run" compact />);
    expect(screen.getByText("Maintainer")).toBeTruthy();
  });

  it("compact=true shows first word only for community-submission", () => {
    render(<TrustBadge trustLabel="community-submission" compact />);
    expect(screen.getByText("Community")).toBeTruthy();
  });

  it("compact=false shows full label", () => {
    render(<TrustBadge trustLabel="maintainer-run" compact={false} />);
    expect(screen.getByText("Maintainer")).toBeTruthy();
  });

  it("unknown label falls back to neutral tone with raw label as text", () => {
    const { container } = render(<TrustBadge trustLabel="some-new-tier" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("neutral");
    expect(badge?.textContent).toBe("some-new-tier");
  });

  it("unknown label tooltip explains it is unrecognised", () => {
    const { container } = render(<TrustBadge trustLabel="some-new-tier" />);
    const badge = container.querySelector(".badge");
    const title = badge?.getAttribute("title") ?? "";
    expect(title).toContain("some-new-tier");
    expect(title).toContain("not recognized");
  });

  it("empty trustLabel renders an explicit Unknown badge (not nothing)", () => {
    const { container } = render(<TrustBadge trustLabel="" />);
    const badge = container.querySelector(".badge");
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toBe("Unknown");
    expect(badge?.getAttribute("data-tone")).toBe("neutral");
  });

  const PUBLISHER_VALID_LABELS = [
    "maintainer-run",
    "community-submission",
    "vendor-supplied",
    "ci",
    "local",
    "unofficial-research",
  ] as const;

  it.each(PUBLISHER_VALID_LABELS)("publisher label %s renders a curated (non-unrecognised) badge", (label) => {
    const { container } = render(<TrustBadge trustLabel={label} />);
    const badge = container.querySelector(".badge");
    expect(badge).not.toBeNull();
    expect(badge?.getAttribute("title") ?? "").not.toContain("unrecognised");
    expect(badge?.textContent).not.toBe(label);
  });

  it("unofficial-research uses a cautionary warning tone", () => {
    const { container } = render(<TrustBadge trustLabel="unofficial-research" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("warning");
    expect(badge?.textContent).toBe("Unofficial");
  });

  it("maintainer-run badge has a title attribute for tooltip", () => {
    const { container } = render(<TrustBadge trustLabel="maintainer-run" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("title")).toBeTruthy();
    expect(badge?.getAttribute("title")).toContain("maintainer");
  });

  it("community-submission badge has a title attribute for tooltip", () => {
    const { container } = render(<TrustBadge trustLabel="community-submission" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("title")).toBeTruthy();
  });

  it("community-submission does not use warning or danger tone", () => {
    const { container } = render(<TrustBadge trustLabel="community-submission" />);
    const badge = container.querySelector(".badge");
    expect(badge?.className).not.toContain("tone-warning");
    expect(badge?.className).not.toContain("tone-danger");
  });

  it("maintainer-run does not use neutral tone (not de-emphasised)", () => {
    const { container } = render(<TrustBadge trustLabel="maintainer-run" />);
    const badge = container.querySelector(".badge");
    expect(badge?.className).not.toContain("tone-neutral");
  });

  it("emits data-role=trust for downstream targeting", () => {
    const { container } = render(<TrustBadge trustLabel="maintainer-run" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-role")).toBe("trust");
  });
});

describe("ValidationBadge", () => {
  it("renders exact validation with info tone distinct from trust success", () => {
    const { container } = render(<ValidationBadge validationStatus="exact" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe("info");
    expect(badge?.textContent).toBe("Exact");
    expect(badge?.getAttribute("title")).toContain("Recorded status: exact");
  });

  it("emits data-role=validation", () => {
    const { container } = render(<ValidationBadge validationStatus="exact" />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-role")).toBe("validation");
  });

  it("suppresses missing status unless requested", () => {
    const hidden = render(<ValidationBadge validationStatus={null} />);
    expect(hidden.container.querySelector(".badge")).toBeNull();

    const shown = render(<ValidationBadge validationStatus={null} showMissing />);
    expect(shown.getByText("Not recorded")).toBeTruthy();
  });

  it("renders not_run as plain language, not the raw enum, with a warning tone", () => {
    const { container } = render(<ValidationBadge validationStatus="not_run" />);
    const badge = container.querySelector(".badge");
    expect(badge?.textContent).toBe("No validation");
    expect(badge?.getAttribute("data-tone")).toBe("warning");
    const title = badge?.getAttribute("title") ?? "";
    expect(title).toContain("Recorded status: not_run");
    expect(title.toLowerCase()).toContain("was not run");
  });

  it.each([
    ["failed", "danger"],
    ["interrupted", "danger"],
    ["partial", "danger"],
    ["error", "danger"],
    ["not_run", "warning"],
    ["not_validated", "warning"],
    ["uncertain", "warning"],
    ["unknown", "warning"],
  ] as const)("%s never falls through to the neutral tone (%s)", (status, expectedTone) => {
    const { container } = render(<ValidationBadge validationStatus={status} />);
    const badge = container.querySelector(".badge");
    expect(badge?.getAttribute("data-tone")).toBe(expectedTone);
    expect(badge?.getAttribute("data-tone")).not.toBe("neutral");
  });

  it("passed still renders as a clean, interpretable label", () => {
    const { container } = render(<ValidationBadge validationStatus="passed" />);
    const badge = container.querySelector(".badge");
    expect(badge?.textContent).toBe("Passed");
    expect(badge?.getAttribute("data-tone")).toBe("info");
  });
});

describe("ValidationBadge with overrideRules", () => {
  it("never renders clean for an overridden run, even with a passing status", () => {
    const { container } = render(<ValidationBadge validationStatus="passed" overrideRules={["timing-plateau"]} />);
    const badge = container.querySelector(".badge");
    expect(badge?.textContent).toBe("Timing-plateau");
    expect(badge?.getAttribute("data-tone")).toBe("warning");
    expect(badge?.getAttribute("title")).toContain("timing-plateau");
    expect(badge?.getAttribute("title")).toContain("Recorded status: passed.");
    expect(badge?.getAttribute("aria-label")).toContain("Overridden: timing-plateau.");
    expect(badge?.getAttribute("aria-label")).toContain("Recorded status: passed.");
  });

  it("badges an override even when no status was recorded", () => {
    const { container } = render(<ValidationBadge validationStatus={null} overrideRules={["scale-invariant"]} />);
    const badge = container.querySelector(".badge");
    expect(badge?.textContent).toBe("Scale-invariant");
    expect(badge?.getAttribute("aria-label")).toContain("Overridden: scale-invariant.");
    expect(badge?.getAttribute("data-tone")).toBe("warning");
  });

  it("stays silent without a status or an override", () => {
    const { container } = render(<ValidationBadge validationStatus={null} />);
    expect(container.textContent).toBe("");
  });
});

describe("OverrideBadge", () => {
  it("renders nothing when no override was accepted", () => {
    const { container } = render(<OverrideBadge rules={[]} />);
    expect(container.textContent).toBe("");
    const { container: empty } = render(<OverrideBadge rules={null} />);
    expect(empty.textContent).toBe("");
  });

  it("names the covered rules and the audit fields", () => {
    render(
      <OverrideBadge
        rules={["timing-plateau"]}
        approver="reviewer"
        evidence="https://example.test/pr/1"
        expires="2099-01-01"
      />,
    );
    const badge = screen.getByText("Timing-plateau");
    expect(badge.getAttribute("data-tone")).toBe("warning");
    expect(badge.getAttribute("data-role")).toBe("override");
    expect(badge.getAttribute("aria-label")).toContain("Overridden: timing-plateau.");
    expect(badge.getAttribute("aria-label")).toContain("not a clean pass");
    expect(screen.getByText("by reviewer")).toBeTruthy();
    expect(screen.getByText("https://example.test/pr/1")).toBeTruthy();
    expect(screen.getByText("expires 2099-01-01")).toBeTruthy();
  });

  it("compacts to a bare badge", () => {
    render(<OverrideBadge rules={["timing-plateau"]} approver="reviewer" compact />);
    const badge = screen.getByText("Overridden");
    expect(badge.getAttribute("aria-label")).toContain("Overridden: timing-plateau.");
    expect(screen.queryByText("by reviewer")).toBeNull();
  });
});
