import type { ComponentChildren } from "preact";
import { getCurrentUrl, useRouter } from "preact-router";
import { LocalResultPicker } from "@/components/LocalResultPicker";
import { RESULTS_NAV_SECTIONS, activeResultsNavSection, isLocalResultPath } from "@/components/resultsNav";
import { SiteFooter, SiteHeader } from "@/components/SiteShell";

interface LayoutProps {
  children: ComponentChildren;
}

const RESULTS_SURFACE_PATH = "/results/";

export function Layout({ children }: LayoutProps) {
  return (
    <div class="bb-app-shell flex min-h-screen flex-col" data-testid="app-shell">
      <SiteHeader pathname={RESULTS_SURFACE_PATH} testId="benchbox-global-header" />
      <ExplorerNav />
      <main id="main" tabIndex={-1} class="bb-explorer flex-1">{children}</main>
      <SiteFooter pathname={RESULTS_SURFACE_PATH}>
        <p class="site-footer__legal">
          Maintainers review every result before publishing it. You can reproduce a run with{" "}
          <code class="rounded bg-[var(--bb-bg-elevated)] px-1 py-0.5 text-xs text-[var(--bb-fg-primary)]">benchbox run</code>.
        </p>
      </SiteFooter>
    </div>
  );
}

function ExplorerNav() {
  useRouter();
  const rawUrl = typeof window === "undefined" ? RESULTS_SURFACE_PATH : getCurrentUrl();
  const currentPath = rawUrl.split("?")[0]!.split("#")[0]!;
  const activeSection = activeResultsNavSection(currentPath);
  const inLocalResult = isLocalResultPath(currentPath);

  return (
    <div class="bb-explorer surface-hero-muted">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <nav
          aria-label="Results Explorer"
          data-testid="results-explorer-nav"
          class="flex min-w-0 min-h-12 flex-wrap items-center gap-x-5 gap-y-1 py-1 text-sm"
        >
          {RESULTS_NAV_SECTIONS.map((section) => (
            <ExplorerNavLink key={section.id} href={section.href} active={activeSection?.id === section.id}>
              {section.label}
            </ExplorerNavLink>
          ))}
          <LocalResultPicker
            className={`whitespace-nowrap rounded-sm border-b-2 bg-transparent py-3 font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--bb-focus-ring-on-dark)] ${
              inLocalResult
                ? "border-[var(--bb-accent)] text-[var(--bb-fg-primary)]"
                : "border-transparent text-[var(--bb-fg-muted)] hover:border-[var(--bb-border-default)] hover:text-[var(--bb-fg-primary)]"
            }`}
          />
        </nav>
      </div>
    </div>
  );
}

function ExplorerNavLink({
  href,
  active = false,
  children,
}: {
  href: string;
  active?: boolean;
  children: ComponentChildren;
}) {
  return (
    <a
      href={href}
      aria-current={active ? "page" : undefined}
      class={`whitespace-nowrap border-b-2 py-3 font-medium no-underline rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--bb-focus-ring-on-dark)] ${
        active
          ? "border-[var(--bb-accent)] text-[var(--bb-fg-primary)]"
          : "border-transparent text-[var(--bb-fg-muted)] hover:border-[var(--bb-border-default)] hover:text-[var(--bb-fg-primary)]"
      }`}
    >
      {children}
    </a>
  );
}
