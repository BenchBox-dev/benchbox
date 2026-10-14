import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { NotFound } from "@/pages/NotFound";

describe("NotFound", () => {
  it("sets the not-found document title", () => {
    render(<NotFound />);

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(document.title).toBe("Not found · BenchBox Results");
  });

  it("links to the results overview and compare page using the site's own terms", () => {
    render(<NotFound />);

    expect(screen.getByRole("link", { name: "Browse results" })).toHaveAttribute("href", "/results/");
    expect(screen.getByRole("link", { name: "Compare" })).toHaveAttribute("href", "/results/compare");
    expect(screen.queryByText("Browse leaderboards")).toBeNull();
  });
});
