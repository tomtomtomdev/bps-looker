import type { Metadata } from "next";
import { Suspense } from "react";

import { VariableSearch } from "@/components/explorer/variable-search";

export const metadata: Metadata = { title: "Explorer" };

export default function ExplorerPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Explorer</h1>
        <p className="text-muted-foreground">
          Search BPS dynamic-table variables by title or subject.
        </p>
      </div>
      {/* The search reads the URL (?q=), which is only known at request time. */}
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading search…</p>}>
        <VariableSearch />
      </Suspense>
    </div>
  );
}
