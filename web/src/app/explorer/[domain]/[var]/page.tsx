import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { VariableExplorer } from "@/components/explorer/variable-explorer";

export const metadata: Metadata = { title: "Variable" };

/** Explorer variable page: metadata, dimension pickers and the time-series chart/table. */
export default function VariablePage({ params }: PageProps<"/explorer/[domain]/[var]">) {
  return (
    <div className="space-y-4">
      <Link href="/explorer" className="text-sm text-muted-foreground hover:underline">
        ← Back to search
      </Link>
      {/* Params and the selection (?vervar=…) are only known at request time. */}
      <Suspense
        fallback={
          <p className="text-sm text-muted-foreground" role="status">
            Loading variable…
          </p>
        }
      >
        {params.then(({ domain, var: varId }) => (
          <VariableExplorer domain={domain} varId={Number(varId)} />
        ))}
      </Suspense>
    </div>
  );
}
