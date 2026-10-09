import type { Metadata } from "next";

export const metadata: Metadata = { title: "Trade" };

export default function TradePage() {
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold tracking-tight">Trade</h1>
      <p className="text-muted-foreground">Coming soon.</p>
    </div>
  );
}
