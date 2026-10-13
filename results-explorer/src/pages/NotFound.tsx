import type { RoutableProps } from "preact-router";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

interface NotFoundProps extends RoutableProps {
  message?: string;
}

export function NotFound({ message }: NotFoundProps = {}) {
  useDocumentTitle("Not found · BenchBox Results");
  return (
    <div class="mx-auto max-w-7xl px-4 py-24 text-center sm:px-6 lg:px-8">
      <h1 class="text-4xl font-bold text-[var(--bb-data-fg-primary)]">Page not found</h1>
      <p class="mt-4 text-lg text-[var(--bb-data-fg-muted)]">{message ?? "We could not find that results page."}</p>
      <div class="mt-6 flex flex-wrap justify-center gap-2">
        <a href="/results/" class="btn btn-primary no-underline">Browse results</a>
        <a href="/results/compare" class="btn btn-secondary no-underline">Compare</a>
      </div>
    </div>
  );
}
