"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { HealthBadge } from "@/components/health-badge";
import { APP_NAME } from "@/lib/site";
import { cn } from "@/lib/utils";

export const NAV_ITEMS = [
  { href: "/explorer", label: "Explorer" },
  { href: "/indicators", label: "Indicators" },
  { href: "/trade", label: "Trade" },
] as const;

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "/";
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="border-b bg-background">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-4">
          <Link href="/" className="font-semibold tracking-tight">
            {APP_NAME}
          </Link>
          <nav aria-label="Main" className="flex items-center gap-1 text-sm">
            {NAV_ITEMS.map(({ href, label }) => {
              const active = isActive(pathname, href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                    active && "bg-accent font-medium text-accent-foreground",
                  )}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto">
            <HealthBadge />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
    </div>
  );
}
