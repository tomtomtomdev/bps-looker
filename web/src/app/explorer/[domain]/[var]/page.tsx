import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

export const metadata: Metadata = { title: "Variable" };

/** Placeholder: U3 adds the variable's metadata, dimension pickers and time-series chart. */
export default function VariablePage({ params }: PageProps<"/explorer/[domain]/[var]">) {
  return (
    <div className="space-y-4">
      <Link href="/explorer" className="text-sm text-muted-foreground hover:underline">
        ← Back to search
      </Link>
      <Suspense fallback={<h1 className="text-2xl font-semibold tracking-tight">Variable</h1>}>
        {params.then(({ domain, var: varId }) => (
          <h1 className="text-2xl font-semibold tracking-tight">
            Variable {varId} · domain {domain}
          </h1>
        ))}
      </Suspense>
      <p className="text-muted-foreground">Details and chart are coming soon.</p>
    </div>
  );
}
