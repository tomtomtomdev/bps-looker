import Link from "next/link";

import { Button } from "@/components/ui/button";

const SECTIONS = [
  {
    href: "/explorer",
    title: "Explorer",
    text: "Search BPS dynamic-table variables and chart their time series by region.",
  },
  {
    href: "/indicators",
    title: "Indicators",
    text: "Latest strategic indicators (inflation, growth, poverty …) per province.",
  },
  {
    href: "/trade",
    title: "Trade",
    text: "Exports and imports by HS chapter, country and port since 2014.",
  },
] as const;

export default function HomePage() {
  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">BPS data, browsable</h1>
        <p className="text-muted-foreground">
          Statistics Indonesia (BPS) data crawled into Postgres, served by the read API.
        </p>
      </div>
      <ul className="grid gap-4 sm:grid-cols-3">
        {SECTIONS.map(({ href, title, text }) => (
          <li key={href} className="flex flex-col gap-3 rounded-lg border p-4">
            <h2 className="font-medium">{title}</h2>
            <p className="flex-1 text-sm text-muted-foreground">{text}</p>
            <Button asChild variant="outline" size="sm" className="self-start">
              <Link href={href}>Open {title}</Link>
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
