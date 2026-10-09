"use client";

import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { api, type Health } from "@/lib/api/client";

/** GET /health: 200 and 503 both carry a `Health` body; a network error throws. */
async function fetchHealth(): Promise<Health> {
  const { data, error } = await api.GET("/health");
  const health = data ?? error;
  if (!health) throw new Error("empty /health response");
  return health;
}

export function HealthBadge() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 60_000,
  });

  let label = "Checking API…";
  let variant: "secondary" | "outline" | "destructive" = "outline";
  if (isError) {
    label = "API down";
    variant = "destructive";
  } else if (!isPending && data.database === "ok") {
    label = `API ok · rev ${data.revision ?? "none"}`;
    variant = "secondary";
  } else if (!isPending) {
    label = "API up · DB unreachable";
    variant = "destructive";
  }

  return (
    <Badge variant={variant} role="status" aria-live="polite" title="Read API health">
      {label}
    </Badge>
  );
}
