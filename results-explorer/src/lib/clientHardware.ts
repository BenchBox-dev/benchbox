import { formatArchitecture, formatCpuFamily, formatMemoryGb } from "@/lib/displayLabels";

interface ClientHardwareRow {
  deployment_class?: string | null;
  cloud_provider?: string | null;
  arch?: string | null;
  cpu_family?: string | null;
  memory_gb?: number | null;
}

export function isManagedCloudRun(row: Pick<ClientHardwareRow, "deployment_class" | "cloud_provider">): boolean {
  if (row.deployment_class) return row.deployment_class === "cloud" || row.deployment_class === "remote";
  return Boolean(row.cloud_provider);
}

export function clientHardwareTitle(row: ClientHardwareRow): string | undefined {
  const parts = [
    row.arch ? formatArchitecture(row.arch) : null,
    row.cpu_family ? formatCpuFamily(row.cpu_family) : null,
    row.memory_gb != null ? formatMemoryGb(row.memory_gb) : null,
  ].filter((part): part is string => part !== null);
  if (parts.length === 0) return undefined;
  const note =
    row.deployment_class === "remote"
      ? "the remote server's hardware is not recorded"
      : "the warehouse hardware is managed by the provider";
  return `Client machine: ${parts.join(", ")} (${note})`;
}
