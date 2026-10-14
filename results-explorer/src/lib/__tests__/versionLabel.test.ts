import { describe, expect, it } from "vitest";
import { platformVersionDisplay } from "@/lib/versionLabel";

describe("platformVersionDisplay", () => {
  it("shows the platform version and puts a differing driver version in the title", () => {
    expect(platformVersionDisplay({ platform_version: "10.35.101", driver_version: "4.7.3" })).toEqual({
      text: "v10.35.101",
      driverTitle: "Driver 4.7.3",
    });
  });

  it("keeps the full platform build string as the visible version", () => {
    expect(platformVersionDisplay({ platform_version: "2.0.0-alpha38615", driver_version: "1.6.0.dev365" })).toEqual({
      text: "v2.0.0-alpha38615",
      driverTitle: "Driver 1.6.0.dev365",
    });
  });

  it("adds no driver title when the driver matches the platform version", () => {
    expect(platformVersionDisplay({ platform_version: "1.4.3", driver_version: "1.4.3" })).toEqual({
      text: "v1.4.3",
      driverTitle: null,
    });
  });

  it("shows no version when the platform version is not recorded, even if a driver version is", () => {
    expect(platformVersionDisplay({ platform_version: null, driver_version: "2.1.0" })).toEqual({
      text: null,
      driverTitle: null,
    });
  });

  it("treats a blank platform version as not recorded", () => {
    expect(platformVersionDisplay({ platform_version: "  ", driver_version: "2.1.0" })).toEqual({
      text: null,
      driverTitle: null,
    });
  });
});
