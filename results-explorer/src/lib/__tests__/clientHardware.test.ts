import { describe, expect, it } from "vitest";
import { clientHardwareTitle, isManagedCloudRun } from "@/lib/clientHardware";

const appleSilicon = { arch: "arm64", cpu_family: "apple_silicon", memory_gb: 16 };

describe("isManagedCloudRun", () => {
  it("treats cloud and remote deployments as managed", () => {
    expect(isManagedCloudRun({ deployment_class: "cloud", cloud_provider: "aws" })).toBe(true);
    expect(isManagedCloudRun({ deployment_class: "remote", cloud_provider: null })).toBe(true);
  });

  it("treats local deployments as client-hosted even when a provider is recorded", () => {
    expect(isManagedCloudRun({ deployment_class: "local", cloud_provider: null })).toBe(false);
    expect(isManagedCloudRun({ deployment_class: "local", cloud_provider: "aws" })).toBe(false);
  });

  it("falls back to the cloud provider when no deployment class is recorded", () => {
    expect(isManagedCloudRun({ deployment_class: null, cloud_provider: "gcp" })).toBe(true);
    expect(isManagedCloudRun({ deployment_class: null, cloud_provider: null })).toBe(false);
  });
});

describe("clientHardwareTitle", () => {
  it("names the provider as the owner of warehouse hardware for cloud runs", () => {
    expect(clientHardwareTitle({ deployment_class: "cloud", ...appleSilicon })).toBe(
      "Client machine: Arm64, Apple silicon, 16 GB (the warehouse hardware is managed by the provider)",
    );
  });

  it("explains that the remote server's hardware is not recorded for remote runs", () => {
    expect(clientHardwareTitle({ deployment_class: "remote", ...appleSilicon })).toBe(
      "Client machine: Arm64, Apple silicon, 16 GB (the remote server's hardware is not recorded)",
    );
  });

  it("lists only the client fields that are recorded", () => {
    expect(clientHardwareTitle({ deployment_class: "remote", arch: "arm64", cpu_family: null, memory_gb: null })).toBe(
      "Client machine: Arm64 (the remote server's hardware is not recorded)",
    );
  });

  it("returns undefined when no client hardware is recorded", () => {
    expect(clientHardwareTitle({ deployment_class: "remote", arch: null, cpu_family: null, memory_gb: null })).toBeUndefined();
  });
});
