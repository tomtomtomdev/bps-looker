import type { Metadata } from "next";

export const metadata: Metadata = { title: "Explorer" };

export default function ExplorerPage() {
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold tracking-tight">Explorer</h1>
      <p className="text-muted-foreground">Coming soon.</p>
    </div>
  );
}
