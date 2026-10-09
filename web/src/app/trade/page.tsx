import type { Metadata } from "next";
import { Suspense } from "react";

import { TradeDashboard } from "@/components/trade/trade-dashboard";

export const metadata: Metadata = { title: "Trade" };

export default function TradePage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Trade</h1>
        <p className="text-muted-foreground">
          Indonesia&apos;s exports and imports by country, port and HS chapter (BPS foreign trade, US$).
        </p>
      </div>
      {/* The dashboard reads the URL (?flow=&from=&to=…), only known at request time. */}
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading trade data…</p>}>
        <TradeDashboard />
      </Suspense>
    </div>
  );
}
