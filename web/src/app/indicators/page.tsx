import type { Metadata } from "next";
import { Suspense } from "react";

import { IndicatorsDashboard } from "@/components/indicators/indicators-dashboard";

export const metadata: Metadata = { title: "Indicators" };

export default function IndicatorsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Indicators</h1>
        <p className="text-muted-foreground">
          BPS strategic indicators — latest values and change since the previous release.
        </p>
      </div>
      {/* The dashboard reads the URL (?domain=&indicator=), only known at request time. */}
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading indicators…</p>}>
        <IndicatorsDashboard />
      </Suspense>
    </div>
  );
}
